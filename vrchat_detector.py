"""VRChat 麦克风状态检测

按优先级确定 VRChat 麦克风状态：
1. 用户手动覆盖
2. VRChat 自己的 OSCQuery 服务：mDNS 找到 `VRChat-Client-XXXXXX._oscjson._tcp`，
   再 GET `http://127.0.0.1:<端口>/avatar/parameters/MuteSelf`，返回
   `{"TYPE":"T","VALUE":[false]}`。这是真实的静音状态，随时可查，不用等 VRChat 推 OSC。
   需要 VRChat 开着 OSC；Windows 上 VRChat 只允许本机访问这个 HTTP 服务。
3. VRChat 的配置文件里的 mic 字段（基本找不到，留作兜底）
4. 进程在跑就当作开着
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


VRCHAT_PROCESS_NAMES = ("VRChat.exe", "vrchat.exe")
# VRChat 进程可执行路径中常含的关键词（用于在任务列表中匹配）
VRCHAT_PATH_KEYWORDS = ("VRChat", "vrchat")


@dataclass
class MicStatus:
    """麦克风状态综合判断结果"""

    is_running: bool           # VRChat 进程是否在运行
    is_voice_active: bool      # 是否有任何自动启用迹象（config / 注册表）
    has_user_override: bool     # 用户是否手动设置过
    user_assumed_open: Optional[bool]  # 用户手动设置（True=开启 / False=关闭）
    config_hint: Optional[bool]  # 从 config 文件找到的暗示
    detail: str                 # 描述信息
    osc_mute_self: Optional[bool] = None  # OSCQuery 读到的 MuteSelf；None=没读到
    pushed_mute_self: Optional[bool] = None  # VRChat 最近一次 OSC 推来的 MuteSelf；None=还没推过

    @property
    def should_translate(self) -> bool:
        """综合判断是否应该翻译"""
        # 进程没跑，肯定不翻译
        if not self.is_running:
            return False
        # 用户手动设置过，以用户为准
        if self.has_user_override and self.user_assumed_open is not None:
            return self.user_assumed_open
        # VRChat OSCQuery 的真实静音状态
        if self.osc_mute_self is not None:
            return not self.osc_mute_self
        # 读不到时用 VRChat 最近一次推来的状态。2026-10-11 岚本机：OSCQuery 树里没有 MuteSelf，主动读
        # 永远失败，以前就当成开麦——闭麦推送只管 2 秒，3 秒轮询又把识别打开了。
        if self.pushed_mute_self is not None:
            return not self.pushed_mute_self
        # 配置文件有明确暗示，以配置为准
        if self.config_hint is not None:
            return self.config_hint
        # 否则只要进程在跑就翻译（合理默认）
        return True

    @property
    def source(self) -> str:
        if not self.is_running:
            return "process"
        if self.has_user_override and self.user_assumed_open is not None:
            return "user"
        if self.osc_mute_self is not None:
            return "oscquery"
        if self.pushed_mute_self is not None:
            return "oscquery"   # 也是 VRChat 自己报的，网页按同一类显示
        if self.config_hint is not None:
            return "config"
        return "default"

    def to_dict(self) -> dict:
        return {
            "is_running": self.is_running,
            "is_voice_active": self.is_voice_active,
            "has_user_override": self.has_user_override,
            "user_assumed_open": self.user_assumed_open,
            "config_hint": self.config_hint,
            "osc_mute_self": self.osc_mute_self,
            "pushed_mute_self": self.pushed_mute_self,
            "source": self.source,
            "detail": self.detail,
            "should_translate": self.should_translate,
        }


# ---------------------------------------------------------------------------
# VRChat OSCQuery：读真实的 MuteSelf
# ---------------------------------------------------------------------------

OSCQUERY_SERVICE_TYPE = "_oscjson._tcp.local."
VRCHAT_SERVICE_PREFIX = "vrchat-client-"


class _VRChatOSCQueryLocator:
    """常驻 mDNS 浏览器，记住 VRChat-Client 的 OSCQuery HTTP 端口。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._zc = None
        self._browser = None
        self._ports: dict[str, int] = {}
        self._found = threading.Event()

    def _ensure_browser(self) -> None:
        with self._lock:
            if self._browser is not None:
                return
            from zeroconf import ServiceBrowser, Zeroconf

            self._zc = Zeroconf()
            self._browser = ServiceBrowser(self._zc, OSCQUERY_SERVICE_TYPE, self)

    # ServiceBrowser 回调（在 zeroconf 线程里执行）
    def add_service(self, zc, type_, name) -> None:
        if not name.lower().startswith(VRCHAT_SERVICE_PREFIX):
            return
        info = zc.get_service_info(type_, name, timeout=2000)
        if info is None or not info.port:
            return
        with self._lock:
            self._ports[name] = int(info.port)
        self._found.set()

    def update_service(self, zc, type_, name) -> None:
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name) -> None:
        with self._lock:
            self._ports.pop(name, None)
            if not self._ports:
                self._found.clear()

    def forget(self, port: int) -> None:
        with self._lock:
            for name, p in list(self._ports.items()):
                if p == port:
                    self._ports.pop(name, None)
            if not self._ports:
                self._found.clear()

    def ports(self, wait: float) -> list[int]:
        self._ensure_browser()
        if wait > 0:
            self._found.wait(wait)
        with self._lock:
            return list(self._ports.values())


_locator = _VRChatOSCQueryLocator()


def query_vrchat_mute_self(wait: float = 2.0, timeout: float = 1.0) -> Optional[bool]:
    """返回 VRChat 当前的 MuteSelf（True=静音）。找不到服务或请求失败返回 None。

    第一次调用会等 mDNS 发现最多 `wait` 秒，之后用缓存的端口。
    """
    try:
        ports = _locator.ports(wait)
    except Exception:
        return None
    for port in ports:
        url = f"http://127.0.0.1:{port}/avatar/parameters/MuteSelf"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
        except Exception:
            # VRChat 重启后端口会变，丢掉旧端口等 mDNS 重新发现
            _locator.forget(port)
            continue
        value = data.get("VALUE")
        if isinstance(value, list) and value:
            return bool(value[0])
        # 没有 VALUE：VRChat 还没加载完模型，参数还不存在
        return None
    return None


def get_vrchat_pids() -> list[int]:
    """返回当前所有 VRChat.exe 进程 PID（不依赖 psutil）。"""
    pids: list[int] = []
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq VRChat.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                line = line.strip()
                if not line or "VRChat.exe" not in line:
                    continue
                # CSV: "VRChat.exe","12345","Console","1","12,345 K"
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                        if pid > 0 and pid not in pids:
                            pids.append(pid)
                    except ValueError:
                        continue
        if pids:
            return pids

        # 兜底：全量扫描
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in out.stdout.splitlines():
            if not any(kw in line for kw in VRCHAT_PATH_KEYWORDS):
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2:
                try:
                    pid = int(parts[1])
                    if pid > 0 and pid not in pids:
                        pids.append(pid)
                except ValueError:
                    continue
    except Exception:
        pass
    return pids


def get_vrchat_pid() -> Optional[int]:
    """返回主 VRChat 进程 PID（优先列表中的第一个）。"""
    pids = get_vrchat_pids()
    return pids[0] if pids else None


def _is_vrchat_running() -> bool:
    """通过 tasklist 检测 VRChat.exe 是否在运行（不依赖 psutil）"""
    return get_vrchat_pid() is not None


def _read_vrchat_config_hints() -> tuple[Optional[bool], str]:
    """读取 VRChat 的常见配置文件，尝试找到 mic / voice 相关的状态字段。
    返回 (hint, detail)。hint 为 None 表示没找到。"""
    # 候选路径（按可能性排序）
    candidates = []
    local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    roaming = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    local_low = os.path.join(os.path.expanduser("~"), "AppData", "LocalLow")

    base_dirs = [local_low, local, roaming]
    for base in base_dirs:
        candidates.append(os.path.join(base, "VRChat", "VRChat"))
        candidates.append(os.path.join(base, "VRChat"))

    # 配置文件名（按常见程度）
    file_globs = [
        "OSC\\usr_*.json",
        "OSC\\*.json",
        "LocalAvatarConfig.json",
        "settings.json",
        "config.json",
        "preferences.json",
    ]

    # 用于匹配的键
    mic_keys = ("micmuted", "mute_mic", "voicemuted", "voice_muted", "mutemicrophone", "mutemic", "mic_enabled", "voiceenabled", "push_to_talk", "ptt")

    found_hints: list[Optional[bool]] = []
    detail_lines: list[str] = []

    for base in candidates:
        if not os.path.isdir(base):
            continue
        for pattern in file_globs:
            for path in Path(base).glob(pattern):
                if not path.is_file() or path.stat().st_size > 5_000_000:
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                except Exception:
                    continue
                # 先尝试 JSON 解析
                try:
                    data = json.loads(content)
                except Exception:
                    # 退而求其次：纯文本搜索
                    data = None
                # 在 JSON 对象里查找键
                hint = _find_key_recursive(data, mic_keys) if data else None
                if hint is None and data is None:
                    # 文本搜索（处理类似 "micMuted": true 这种行）
                    for key in mic_keys:
                        m = re.search(rf'"?{re.escape(key)}"?\s*[:=]\s*(true|false|1|0)', content, re.IGNORECASE)
                        if m:
                            v = m.group(1).lower()
                            hint = v in ("true", "1")
                            break
                if hint is not None:
                    found_hints.append(bool(hint))
                    detail_lines.append(f"{path.name}: {key} = {hint}")
                if found_hints:
                    break
            if found_hints:
                break
        if found_hints:
            break

    if found_hints:
        # 如果多处 hint 冲突，取"开启"为主（保守）
        result = any(found_hints)
        return result, "; ".join(detail_lines)
    return None, ""


def _find_key_recursive(obj, keys: tuple[str, ...], depth: int = 0) -> Optional[bool]:
    """在 JSON 对象中递归查找任意一个 key，返回其 bool 值"""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(target in kl for target in keys):
                if isinstance(v, bool):
                    return v
                if isinstance(v, (int, float)):
                    return bool(v)
                if isinstance(v, str):
                    vl = v.strip().lower()
                    if vl in ("true", "1", "yes", "on"):
                        return True
                    if vl in ("false", "0", "no", "off"):
                        return False
            result = _find_key_recursive(v, keys, depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_key_recursive(item, keys, depth + 1)
            if result is not None:
                return result
    return None


def get_mic_status(user_assumed_open: Optional[bool] = None, has_user_override: bool = False,
                   pushed_mute_self: Optional[bool] = None) -> MicStatus:
    """综合判断 VRChat 麦克风状态

    user_assumed_open / has_user_override 来自配置 / 用户手动开关；
    pushed_mute_self 是 osc_manager 记下的 VRChat 最近一次推送（osc_manager.last_mute_value）。
    """
    is_running = _is_vrchat_running()
    osc_mute_self = query_vrchat_mute_self() if is_running else None
    if not is_running:
        pushed_mute_self = None
    # OSCQuery 读到了 / 有推送就不用再翻配置文件
    known = osc_mute_self is not None or pushed_mute_self is not None
    config_hint, detail = (None, "") if known else _read_vrchat_config_hints()

    if not is_running:
        running_detail = "VRChat 进程未运行"
    else:
        running_detail = "VRChat 进程正在运行"

    if osc_mute_self is not None:
        source_detail = f"OSCQuery: MuteSelf={osc_mute_self}（麦克风{'已静音' if osc_mute_self else '开着'}）"
    elif pushed_mute_self is not None:
        source_detail = f"OSC 推送: MuteSelf={pushed_mute_self}（麦克风{'已静音' if pushed_mute_self else '开着'}）"
    elif is_running:
        source_detail = "OSCQuery 未读到 MuteSelf（VRChat 没开 OSC 或模型还没加载完）"
        if config_hint is None:
            source_detail += "; 未找到 mic 配置字段"
        else:
            source_detail += f"; 配置暗示: mic={config_hint}"
    else:
        source_detail = ""

    full_detail = running_detail + (f"; {source_detail}" if source_detail else "")
    if detail:
        full_detail += f" ({detail})"

    return MicStatus(
        is_running=is_running,
        is_voice_active=is_running,  # 进程在跑即视为启用
        has_user_override=has_user_override,
        user_assumed_open=user_assumed_open,
        config_hint=config_hint,
        detail=full_detail,
        osc_mute_self=osc_mute_self,
        pushed_mute_self=pushed_mute_self,
    )
