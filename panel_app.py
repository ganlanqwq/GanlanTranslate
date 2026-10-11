import sys
import ctypes
import ctypes.wintypes
import webview
import logging
from urllib.parse import parse_qs, urlparse

DEFAULT_PANEL_WIDTH = 600
WINDOW_TITLE_BAR_HEIGHT = 32
PANEL_CONTENT_HEIGHT_TWO_LINES = 150
PANEL_CONTENT_HEIGHT_THREE_LINES = 175
QUICK_LANG_BAR_HEIGHT = 27


def _parse_panel_width(raw_value):
    try:
        return max(300, int(raw_value))
    except Exception:
        return DEFAULT_PANEL_WIDTH


def _parse_optional_int(raw_value):
    if raw_value is None:
        return None
    text = str(raw_value).strip().lower()
    if text in {"", "auto", "none", "default"}:
        return None
    try:
        return int(text)
    except Exception:
        return None


def _panel_role_from_url(url):
    try:
        params = parse_qs(urlparse(url).query)
        raw = str((params.get("role") or ["self"])[0]).strip().lower()
        if raw in ("others", "screen"):
            return raw
    except Exception:
        pass
    return "self"


_PANEL_TITLES = {
    "zh-CN": ("甘岚翻译", {"self": "自我", "others": "他人", "screen": "屏幕"}),
    "en": ("Ganlan Translate", {"self": "Me", "others": "Others", "screen": "Screen"}),
    "ja": ("甘嵐翻訳", {"self": "自分", "others": "他人", "screen": "画面"}),
    "ko": ("간란 번역", {"self": "나", "others": "다른 사람", "screen": "화면"}),
}


def _panel_window_title(panel_role, url):
    """窗口标题跟界面语言走；语言由 panel_launcher 放在 URL 的 ui_lang 里。"""
    import ui_locale
    lang = None
    try:
        lang = ui_locale.normalize((parse_qs(urlparse(url).query).get("ui_lang") or [None])[0])
    except Exception:
        pass
    app_name, roles = _PANEL_TITLES.get(lang or ui_locale.system_ui_language(), _PANEL_TITLES["en"])
    return f"{app_name} - {roles.get(panel_role, roles['self'])}"


def _should_show_quick_lang_bar(url):
    try:
        params = parse_qs(urlparse(url).query)
        raw_value = (params.get('quick_lang_bar') or [None])[0]
        if raw_value is None:
            return True
        return raw_value.strip().lower() not in {'', '0', 'false', 'no', 'off'}
    except Exception:
        return True


PANEL_CORNER_RADIUS = 14  # 逻辑像素，按窗口 DPI 放大


def _apply_rounded_corners(window_title):
    """把无边框悬浮窗裁成圆角。

    Windows 10 上 pywebview 的 transparent 不生效，窗口是方的；
    DWM 的圆角偏好只有 Windows 11 才有，所以直接用 SetWindowRgn 裁窗口区域。
    """
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW(None, window_title)
        if not hwnd:
            return
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return
        try:
            scale = user32.GetDpiForWindow(ctypes.c_void_p(hwnd)) / 96.0 or 1.0
        except Exception:
            scale = 1.0
        diameter = int(round(PANEL_CORNER_RADIUS * 2 * scale))
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter)
        # 成功后区域归系统所有，不要自己删
        user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(region), True)
    except Exception as exc:
        logging.getLogger(__name__).debug("rounded corners failed: %s", exc)


class PanelApi:
    def __init__(self):
        self._window = None
        self._locked = False
        self.rounded = False  # 无边框悬浮模式下裁成圆角

    def attach_window(self, window):
        self._window = window

    def close_window(self):
        if self._window is not None:
            self._window.destroy()

    def set_bounds(self, x, y, width, height):
        """调整窗口尺寸/位置（供前端 resize 手柄调用）。"""
        window = self._window
        if window is None:
            return
        try:
            window.move(int(x), int(y))
        except Exception:
            pass
        try:
            window.resize(int(width), int(height))
        except Exception:
            pass
        if self.rounded:
            _apply_rounded_corners(window.title)

    def move_window(self, x, y):
        """移动窗口到绝对坐标（供前端拖动调用）；锁定状态下忽略。"""
        if self._locked:
            return
        window = self._window
        if window is None:
            return
        try:
            window.move(int(x), int(y))
        except Exception:
            pass

    def set_locked(self, locked):
        """锁定/解锁窗口位置（锁定后忽略移动请求）。"""
        self._locked = bool(locked)

    def get_locked(self):
        return self._locked


def main(argv=None):
    args = argv if argv is not None else sys.argv

    if len(args) < 2:
        url = "http://127.0.0.1:5001/panel"
    else:
        url = args[1]

    show_reverse_translation = len(args) >= 3 and args[2] == "reverse-on"
    floating_mode = len(args) >= 4 and args[3] == "floating-on"
    panel_width = _parse_panel_width(args[4]) if len(args) >= 5 else DEFAULT_PANEL_WIDTH
    window_x = _parse_optional_int(args[5]) if len(args) >= 6 else None
    window_y = _parse_optional_int(args[6]) if len(args) >= 7 else None
    panel_role = _panel_role_from_url(url)
    if panel_role in ("others", "screen"):
        show_reverse_translation = False
    show_quick_lang_bar = _should_show_quick_lang_bar(url)
    if panel_role == "others":
        content_height = 220
    elif panel_role == "screen":
        content_height = 300
    else:
        content_height = PANEL_CONTENT_HEIGHT_THREE_LINES if show_reverse_translation else PANEL_CONTENT_HEIGHT_TWO_LINES
    if not show_quick_lang_bar:
        content_height = max(80, content_height - QUICK_LANG_BAR_HEIGHT)
    initial_height = content_height if floating_mode else content_height + WINDOW_TITLE_BAR_HEIGHT

    panel_api = PanelApi()
    window_title = _panel_window_title(panel_role, url)

    # 先隐藏创建，等 resize 到正确物理像素尺寸后再显示，避免闪烁
    window_kwargs = {
        "title": window_title,
        "url": url,
        "width": panel_width,
        "height": initial_height,
        "frameless": floating_mode,
        "on_top": floating_mode,
        "resizable": True,
        "hidden": True,
        "js_api": panel_api,
        "easy_drag": False,
        "shadow": floating_mode,
        "transparent": floating_mode,
    }
    if window_x is not None:
        window_kwargs["x"] = window_x
    if window_y is not None:
        window_kwargs["y"] = window_y
    window = webview.create_window(**window_kwargs)
    panel_api.attach_window(window)

    panel_api.rounded = floating_mode

    def on_started():
        # pywebview 的 AutoScaleMode.Dpi 缩放行为不一致，
        # 这里用 resize() (直接调 SetWindowPos) 强制设为正确的尺寸
        window.resize(panel_width, initial_height)
        window.show()
        if floating_mode:
            _apply_rounded_corners(window_title)
            # 拖边框改大小后重新裁圆角
            window.events.resized += lambda *_: _apply_rounded_corners(window_title)

    logging.getLogger('pywebview').setLevel(logging.ERROR)
    webview.start(func=on_started)

if __name__ == '__main__':
    main()
