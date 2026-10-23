"""
屏幕翻译：截 VRChat.exe 窗口 → 图像识别模型取文字 → LLM 翻译 → 屏幕悬浮窗。
只给自己看，不发 OSC。

流程：服务启动后等待请求（悬浮窗按钮 / 全局热键），每收到一次请求就
截一次窗口、识别、翻译，把结果写进 state.screen_state 供悬浮窗轮询。
"""
from __future__ import annotations

import asyncio
import base64
import ctypes
import io
import logging
import re
import threading
import time
import unicodedata
from ctypes import wintypes
from typing import Callable, Optional

import config
from text_processor import canonicalize_output_language, normalize_lang_code

logger = logging.getLogger(__name__)

DEFAULT_DASHSCOPE_COMPAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
INTL_DASHSCOPE_COMPAT_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

OCR_PROMPT = (
    "Read all legible text in this VRChat game screenshot. "
    "Group it into blocks: each chat bubble, sign, paragraph or separate UI label is one block. "
    "Write each block on a single line: when a sentence or paragraph wraps onto several lines "
    "in the image, join those lines into one line. Put an empty line between blocks. "
    "List the blocks in natural reading order (top to bottom, left to right). "
    "Keep the original language exactly; do not translate, explain, number, or add anything. "
    "If there is no readable text, output nothing."
)

# 长边超过这个值就缩小再上传，避免图太大拖慢识别
MAX_UPLOAD_SIDE = 2560
# 同时翻译的段数
TRANSLATE_CONCURRENCY = 4


def new_screen_state() -> dict:
    return {
        "status": "idle",   # idle / capturing / recognizing / translating / done / error
        "message": "",
        "items": [],        # [{"original", "lang", "translated"}]
        "busy": False,
        "updated_at": 0.0,
        "hotkey": "",
    }


def screen_target_language() -> str:
    return canonicalize_output_language(getattr(config, "SCREEN_TARGET_LANGUAGE", None)) or "zh-CN"


def screen_context_prefix(target_lang: str) -> str:
    return (
        "You are translating text that appears on the local user's VRChat screen "
        "(signs, menus, chat bubbles, UI). "
        f"Translate into {target_lang} only. Do not use any other output language. "
        "Output ONLY the translation text with no quotes, labels, or explanations."
    )


# ---------------------------------------------------------------------------
# 窗口采集（PrintWindow + PW_RENDERFULLCONTENT：窗口被挡住也能截到，最小化不行）
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0
GW_OWNER = 4
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.GetWindowDC.argtypes = [wintypes.HWND]
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


_gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
]


class ScreenCaptureError(RuntimeError):
    pass


def _find_vrchat_window() -> Optional[int]:
    from vrchat_detector import get_vrchat_pids

    pids = set(get_vrchat_pids())
    if not pids:
        return None

    candidates: list[tuple[int, int, int]] = []  # (is_unity, area, hwnd)

    def _cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, GW_OWNER):
            return True
        rect = wintypes.RECT()
        _user32.GetClientRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        cls = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, cls, 64)
        candidates.append((1 if cls.value == "UnityWndClass" else 0, area, hwnd))
        return True

    _user32.EnumWindows(_EnumWindowsProc(_cb), 0)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def capture_vrchat_window():
    """截 VRChat 窗口客户区，返回 PIL.Image(RGB)。"""
    from PIL import Image

    old_ctx = None
    try:
        old_ctx = _user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except Exception:
        pass
    try:
        hwnd = _find_vrchat_window()
        if not hwnd:
            raise ScreenCaptureError("没有找到 VRChat 窗口（VRChat.exe 没在运行？）")
        if _user32.IsIconic(hwnd):
            raise ScreenCaptureError("VRChat 窗口已最小化，最小化时截不到画面")

        win = wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(win))
        width, height = win.right - win.left, win.bottom - win.top
        if width <= 0 or height <= 0:
            raise ScreenCaptureError("VRChat 窗口尺寸为 0")

        hdc_win = _user32.GetWindowDC(hwnd)
        mem_dc = _gdi32.CreateCompatibleDC(hdc_win)
        bmp = _gdi32.CreateCompatibleBitmap(hdc_win, width, height)
        try:
            old_obj = _gdi32.SelectObject(mem_dc, bmp)
            ok = _user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
            _gdi32.SelectObject(mem_dc, old_obj)
            if not ok:
                raise ScreenCaptureError("PrintWindow 失败")

            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height  # 自上而下
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            buf = ctypes.create_string_buffer(width * height * 4)
            rows = _gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
            if rows != height:
                raise ScreenCaptureError("读取截图像素失败")
        finally:
            _gdi32.DeleteObject(bmp)
            _gdi32.DeleteDC(mem_dc)
            _user32.ReleaseDC(hwnd, hdc_win)

        image = Image.frombuffer("RGB", (width, height), buf, "raw", "BGRX", 0, 1)

        # 裁掉标题栏和边框，只留客户区
        client = wintypes.RECT()
        _user32.GetClientRect(hwnd, ctypes.byref(client))
        origin = wintypes.POINT(0, 0)
        _user32.ClientToScreen(hwnd, ctypes.byref(origin))
        left = max(0, origin.x - win.left)
        top = max(0, origin.y - win.top)
        right = min(width, left + client.right)
        bottom = min(height, top + client.bottom)
        if right - left > 16 and bottom - top > 16:
            image = image.crop((left, top, right, bottom))

        lo, hi = image.convert("L").getextrema()
        if hi < 8:
            raise ScreenCaptureError("截到的画面全黑（VRChat 可能是独占全屏，请改成窗口或无边框）")
        return image
    finally:
        if old_ctx:
            try:
                _user32.SetThreadDpiAwarenessContext(old_ctx)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 图像识别（OpenAI 兼容视觉接口，默认通义 qwen-vl-ocr）
# ---------------------------------------------------------------------------

def _ocr_base_url() -> str:
    url = (getattr(config, "SCREEN_OCR_BASE_URL", "") or "").strip() or DEFAULT_DASHSCOPE_COMPAT_URL
    if url.rstrip("/") == DEFAULT_DASHSCOPE_COMPAT_URL and getattr(config, "USE_INTERNATIONAL_ENDPOINT", False):
        return INTL_DASHSCOPE_COMPAT_URL
    return url


# 阿里云已经下线的模型名 → 现在的名字。2026-10-11 岚本机：qwen-vl-ocr-latest 返回 404「Model not exist」，
# 官方文档里只剩 qwen-vl-ocr（稳定版）、qwen-vl-ocr-2025-11-20、qwen3.5-ocr。浏览器里存的旧名字照样能用。
_RETIRED_OCR_MODELS = {"qwen-vl-ocr-latest": "qwen-vl-ocr"}
DEFAULT_OCR_MODEL = "qwen-vl-ocr"


def _ocr_model() -> str:
    model = (getattr(config, "SCREEN_OCR_MODEL", "") or "").strip() or DEFAULT_OCR_MODEL
    return _RETIRED_OCR_MODELS.get(model, model)


def _ocr_api_key() -> str:
    key = str(getattr(config, "SCREEN_OCR_API_KEY", "") or "").strip()
    if key:
        return key
    return str(getattr(config, "DASHSCOPE_API_KEY", "") or "").strip()


def _image_to_data_url(image) -> str:
    longest = max(image.size)
    if longest > MAX_UPLOAD_SIDE:
        scale = MAX_UPLOAD_SIDE / float(longest)
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        )
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def recognize_text(image) -> list[str]:
    """把截图交给视觉模型，返回按阅读顺序排列的文字段。"""
    from openai import OpenAI
    from proxy_detector import refresh_system_proxy_env

    api_key = _ocr_api_key()
    if not api_key:
        raise RuntimeError("图像识别 Key 未设置（核心设置 → 图像识别模型，或填 DashScope Key）")
    model = _ocr_model()
    timeout = float(getattr(config, "SCREEN_OCR_TIMEOUT", 30) or 30)

    refresh_system_proxy_env()
    client = OpenAI(api_key=api_key, base_url=_ocr_base_url(), timeout=timeout)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _image_to_data_url(image)}},
                    {"type": "text", "text": OCR_PROMPT},
                ],
            }],
        )
    except Exception as exc:
        text = str(exc)
        if "model_not_found" in text or "Model not exist" in text:
            raise RuntimeError(
                f"图像识别模型「{model}」不存在（可能已被服务商下线或名字拼错），"
                f"请在「核心设置 → 图像识别模型」改成 {DEFAULT_OCR_MODEL} 或其他支持图片的模型"
            ) from exc
        raise
    finally:
        try:
            client.close()
        except Exception:
            pass
    content = (resp.choices[0].message.content or "") if resp.choices else ""
    return split_blocks(content)


# 识别模型（尤其 qwen-vl-ocr）常常不管提示词，照画面一行一行输出，一句话被折行拆成好几段。
# split_blocks 把看起来是同一句被折开的相邻行拼回去；空行、纯数字行、列表项开头都当分界。

# 以这些结尾的行算完整的一句（或标题），不再往下拼
_LINE_END = tuple("。！？!?…．.；;:：」』】〉》）)\"”’～~♪")
# 以这些结尾说明句子还没完
_LINE_CONTINUE = tuple(",，、-‐–—/(（「『【《“")
# 下一行这样开头是新的一条（列表、注释）
_ITEM_START = re.compile(
    r"^(?:\d{1,3}\s*[.)．、:：]|[\(（]\d{1,3}[\)）]|[①-⑳]|[・•●○◆◇■□※★☆▶►▷→\-*])"
)
# 英文行末是这些词时句子肯定没完
_EN_TRAILING_WORDS = {
    "a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with",
    "from", "by", "is", "are", "was", "be", "my", "your", "our", "this", "that", "if", "so",
}
# 中日文一行至少这么多字才像是被折行（再短像按钮、菜单项）
_CJK_WRAP_CHARS = 8
# 英文、韩文一行至少这么多字符，且下一行不是新句子时才拼
_SPACED_WRAP_CHARS = 28
# 画面上单独的「AFK」（VRChat 名牌上方的离开标记）不用翻译，整行丢掉；
# 全角、大小写、外面套括号、一行好几个都算
_IGNORED_LINE = re.compile(r"^[\W_]*(?:afk[\W_]*)+$", re.IGNORECASE)


def _ignored_line(line: str) -> bool:
    return bool(_IGNORED_LINE.match(unicodedata.normalize("NFKC", line)))

# 拼出来的一段最长多少字符
_MAX_BLOCK_CHARS = 400


def _is_cjk(ch: str) -> bool:
    """汉字、假名、全角符号：拼接时中间不加空格。"""
    code = ord(ch)
    return (
        0x3000 <= code <= 0x30FF      # CJK 标点、平假名、片假名
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF   # 全角
        or 0x31F0 <= code <= 0x31FF
    )


def _is_hangul(ch: str) -> bool:
    code = ord(ch)
    return 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F


def _continues(prev: str, line: str) -> bool:
    """prev 是不是被折行截断、line 是它的下半句。"""
    if prev.endswith(_LINE_END) or _ITEM_START.match(line):
        return False
    if prev.endswith(_LINE_CONTINUE):
        return True
    last, first = prev[-1], line[0]
    if _is_cjk(last):
        return len(prev) >= _CJK_WRAP_CHARS and (_is_cjk(first) or first.isalnum())
    if _is_hangul(last):
        return len(prev) >= _SPACED_WRAP_CHARS // 2 and (_is_hangul(first) or first.isalnum())
    if not last.isalnum():
        return False
    if first.islower():
        return True
    words = prev.split()
    if words and words[-1].lower() in _EN_TRAILING_WORDS:
        return True
    return len(prev) >= _SPACED_WRAP_CHARS and first.isalnum()


def _join(prev: str, line: str) -> str:
    if _is_cjk(prev[-1]) or _is_cjk(line[0]) or prev.endswith(("-", "‐", "/")):
        return prev + line
    return prev + " " + line


def split_blocks(text: str) -> list[str]:
    from openai_compat_client import OpenAICompatClientBase

    cleaned = OpenAICompatClientBase.clean_response(text or "")
    merged: list[str] = []
    current = ""          # 正在拼的一段
    last_line = ""        # 这一段的最后一行（判断折行看的是它）
    for raw in cleaned.splitlines():
        line = raw.strip().strip("`").strip()
        marked = line.startswith(("- ", "* ", "• "))
        if marked:
            line = line[2:].strip()
        # 空行、纯数字 / 符号（时间、帧数、坐标）、单独的 AFK 不用翻译，也是分界
        if not line or not any(ch.isalpha() for ch in line) or _ignored_line(line):
            if current:
                merged.append(current)
            current = last_line = ""
            continue
        if (current and not marked and _continues(last_line, line)
                and len(current) + len(line) < _MAX_BLOCK_CHARS):
            current = _join(current, line)
        else:
            if current:
                merged.append(current)
            current = line
        last_line = line
    if current:
        merged.append(current)

    blocks: list[str] = []
    seen: set[str] = set()
    for block in merged:
        if block not in seen:
            seen.add(block)
            blocks.append(block)
    return blocks


# ---------------------------------------------------------------------------
# 一次完整的请求：采集 → 识别 → 翻译 → 写回
# ---------------------------------------------------------------------------

def _set_status(state, status: str, message: str = "") -> None:
    st = state.screen_state
    st["status"] = status
    st["message"] = message
    st["updated_at"] = time.time()


def _detect_lang(state, text: str) -> str:
    detector = getattr(state, "language_detector", None)
    if detector is None:
        return "und"
    try:
        info = detector.detect(text) or {}
        raw = info.get("language") if isinstance(info, dict) else None
        lang = normalize_lang_code(raw or "und")
        return "und" if lang in ("auto", "") else lang
    except Exception:
        return "und"


async def translate_screen_once(state) -> None:
    st = state.screen_state
    if st.get("busy"):
        return
    st["busy"] = True
    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    try:
        print("[Screen] 收到请求，开始采集 VRChat 窗口")
        _set_status(state, "capturing", "采集画面…")
        try:
            image = await loop.run_in_executor(state.executor, capture_vrchat_window)
        except ScreenCaptureError as exc:
            print(f"[Screen] 采集失败: {exc}")
            _set_status(state, "error", str(exc))
            return
        t_cap = time.perf_counter()

        _set_status(state, "recognizing", "识别文字…")
        try:
            blocks = await loop.run_in_executor(state.executor, recognize_text, image)
        except Exception as exc:
            print(f"[Screen] 识别失败: {exc}")
            _set_status(state, "error", f"识别失败: {exc}")
            return
        t_ocr = time.perf_counter()

        if not blocks:
            print("[Screen] 画面里没有识别到文字")
            st["items"] = []
            _set_status(state, "done", "画面里没有识别到文字")
            return
        max_blocks = max(1, int(getattr(config, "SCREEN_MAX_BLOCKS", 20) or 20))
        if len(blocks) > max_blocks:
            print(f"[Screen] 识别到 {len(blocks)} 段，只翻译前 {max_blocks} 段")
            blocks = blocks[:max_blocks]

        target_lang = screen_target_language()
        items = [{"original": b, "lang": _detect_lang(state, b), "translated": ""} for b in blocks]
        st["items"] = items
        _set_status(state, "translating", f"翻译中…（{len(items)} 段）")

        translator = getattr(state, "translator", None)
        if translator is None:
            _set_status(state, "error", "翻译器未初始化，请在网页里重新启动服务")
            return

        from streaming_translation import translate_with_backend

        sem = asyncio.Semaphore(TRANSLATE_CONCURRENCY)

        async def _translate(item: dict) -> None:
            lang = item["lang"]
            if lang != "und" and normalize_lang_code(lang) == normalize_lang_code(target_lang):
                item["translated"] = item["original"]
                return

            def _do():
                return translate_with_backend(
                    translator,
                    getattr(state, "deepl_fallback_translator", None),
                    item["original"],
                    target_lang,
                    detected_source_language=None if lang == "und" else lang,
                    source_language="auto",
                    context_prefix=screen_context_prefix(target_lang),
                    record_history=False,
                    include_history=False,
                )

            async with sem:
                try:
                    out = await loop.run_in_executor(state.executor, _do)
                    item["translated"] = (out or "").strip() or item["original"]
                except Exception as exc:
                    logger.warning("[Screen] 翻译失败: %s", exc)
                    item["translated"] = f"（翻译失败：{exc}）"
            st["updated_at"] = time.time()

        await asyncio.gather(*(_translate(item) for item in items))
        _set_status(state, "done", "")
        try:
            import plugin_manager
            plugin_manager.emit('screen_result', {
                'target_lang': target_lang,
                'items': [dict(item) for item in items],
            })
        except Exception:
            pass
        for item in items:
            print(f"[Screen] {item['original']}[{item['lang']}] → {item['translated']}")
        print(
            f"[耗时] 屏幕 采集 {int((t_cap - t0) * 1000)}ms "
            f"识别 {int((t_ocr - t_cap) * 1000)}ms "
            f"翻译 {int((time.perf_counter() - t_ocr) * 1000)}ms"
        )
    except Exception as exc:
        logger.exception("[Screen] 屏幕翻译异常: %s", exc)
        _set_status(state, "error", f"屏幕翻译异常: {exc}")
    finally:
        st["busy"] = False


def request_screen_translation(state, loop: asyncio.AbstractEventLoop) -> tuple[bool, str]:
    """供 Flask 线程 / 热键线程调用：把一次请求排到服务事件循环上。"""
    if state is None or loop is None or not loop.is_running():
        return False, "服务未启动"
    task = getattr(state, "mode_tasks", {}).get("screen")
    if task is None or task.done():
        return False, "屏幕模式没有启动"
    if state.screen_state.get("busy"):
        return False, "上一次还没完成"
    asyncio.run_coroutine_threadsafe(translate_screen_once(state), loop)
    return True, "已开始"


# ---------------------------------------------------------------------------
# 全局热键（RegisterHotKey，VRChat 在前台时也能触发）
# ---------------------------------------------------------------------------

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
_HOTKEY_ID = 0x5C7E

_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "pause": 0x13, "insert": 0x2D, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "`": 0xC0, "-": 0xBD, "=": 0xBB,
    "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}


def parse_hotkey(text: str) -> Optional[tuple[int, int]]:
    """'Ctrl+Alt+T' → (modifiers, vk)；格式不对返回 None。"""
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if not parts:
        return None
    mods, vk = 0, None
    for part in parts:
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "windows", "meta"):
            mods |= MOD_WIN
        elif vk is not None:
            return None
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        elif part.startswith("num") and part[3:].isdigit() and len(part) == 4:
            vk = 0x60 + int(part[3:])
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        else:
            return None
    if vk is None:
        return None
    return mods, vk


class GlobalHotkey:
    def __init__(self, combo: str, callback: Callable[[], None]):
        self.combo = combo
        self._callback = callback
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False
        self.error = ""

    def start(self) -> bool:
        parsed = parse_hotkey(self.combo)
        if parsed is None:
            self.error = f"热键格式不对：{self.combo}"
            return False
        self._thread = threading.Thread(
            target=self._run, args=parsed, name="ganlan-screen-hotkey", daemon=True
        )
        self._thread.start()
        self._ready.wait(2.0)
        return self.registered

    def _run(self, mods: int, vk: int) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        try:
            self.registered = bool(_user32.RegisterHotKey(None, _HOTKEY_ID, mods | MOD_NOREPEAT, vk))
            if not self.registered:
                self.error = f"热键 {self.combo} 注册失败（可能被别的程序占用）"
                return
        finally:
            self._ready.set()
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    try:
                        self._callback()
                    except Exception as exc:
                        print(f"[Screen] 热键回调出错: {exc}")
        finally:
            _user32.UnregisterHotKey(None, _HOTKEY_ID)

    def stop(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=1.0)


async def screen_translation_task(state) -> None:
    """屏幕模式：运行期间挂着全局热键；按钮请求走 /api/screen-translate。停止时被取消。"""
    loop = asyncio.get_running_loop()
    combo = (getattr(config, "SCREEN_HOTKEY", "") or "").strip()
    hotkey: Optional[GlobalHotkey] = None
    if combo:
        def _on_hotkey():
            ok, msg = request_screen_translation(state, loop)
            if not ok:
                print(f"[Screen] 热键请求被忽略：{msg}")

        hotkey = GlobalHotkey(combo, _on_hotkey)
        if hotkey.start():
            state.screen_state["hotkey"] = combo
            print(f"[Screen] 屏幕翻译已就绪，热键 {combo}")
        else:
            state.screen_state["hotkey"] = ""
            print(f"[Screen] {hotkey.error}；仍可用悬浮窗按钮")
            _set_status(state, "idle", hotkey.error)
    else:
        print("[Screen] 屏幕翻译已就绪（未设热键，只能用悬浮窗按钮）")
    try:
        await state.stop_event.wait()
    finally:
        if hotkey is not None:
            hotkey.stop()
        state.screen_state["hotkey"] = ""
        print("[Screen] 屏幕翻译已停止")
