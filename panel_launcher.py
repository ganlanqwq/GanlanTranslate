"""迷你面板进程管理。

统一管理面板进程的启动 / 关闭 / 状态查询，供 Web UI 的 /api/open-panel
与系统托盘菜单共用。程序退出时调用 :func:`close_all_panels` 一并关闭面板。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import List, Optional
from urllib.parse import urlencode

_lock = threading.Lock()
_panel_processes: List[dict] = []
# 网页上次打开悬浮窗用的选项（置顶、快捷语言）；托盘打开时照着用
_last_options: dict = {}
# 网页 /api/open-panel 用的位置：三个窗口上下错开
PANEL_WINDOW_Y = {"self": 80, "others": 270, "screen": 520}


def _role_key(role: str) -> str:
    key = str(role).strip().lower()
    return key if key in ("others", "screen") else "self"


def _build_launch_command(
    floating_mode: bool,
    reverse_enabled: bool,
    panel_width: int,
    quick_lang_enabled: bool,
    quick_langs: List[str],
    role: str = "self",
    window_x: Optional[int] = None,
    window_y: Optional[int] = None,
) -> List[str]:
    """构造启动面板进程的命令行。"""
    python_exe = sys.executable
    panel_script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "panel_app.py",
    )
    role_key = _role_key(role)
    panel_query = [
        ("quick_lang_bar", "1" if quick_lang_enabled else "0"),
        ("role", role_key),
    ]
    panel_query.extend(("quick_lang", lang) for lang in quick_langs)
    try:
        import ui_locale
        panel_query.append(("ui_lang", ui_locale.current_ui_language()))
    except Exception:
        pass
    panel_url = f"http://127.0.0.1:5001/panel?{urlencode(panel_query)}"
    initial_mode = "reverse-on" if reverse_enabled else "reverse-off"
    floating_mode_arg = "floating-on" if floating_mode else "floating-off"
    width_arg = str(max(300, int(panel_width)))
    panel_args = [
        panel_url,
        initial_mode,
        floating_mode_arg,
        width_arg,
        "auto" if window_x is None else str(int(window_x)),
        "auto" if window_y is None else str(int(window_y)),
    ]
    if getattr(sys, "frozen", False):
        return [python_exe, "--panel-app", *panel_args]
    return [python_exe, panel_script, *panel_args]


def launch_panel(
    *,
    floating_mode: bool,
    reverse_enabled: bool = False,
    panel_width: int = 600,
    quick_lang_enabled: bool = True,
    quick_langs: Optional[List[str]] = None,
    role: str = "self",
    window_x: Optional[int] = None,
    window_y: Optional[int] = None,
) -> bool:
    """启动一个迷你面板进程。同一角色已在运行时不再重复打开。"""
    role_key = _role_key(role)
    with _lock:
        _last_options.update(floating_mode=floating_mode, panel_width=panel_width,
                             quick_lang_enabled=quick_lang_enabled, quick_langs=quick_langs)
        _prune_locked()
        if any(item.get("role") == role_key for item in _panel_processes):
            return True
    quick_langs = quick_langs or ["en", "zh-CN", "ja", "ko"]
    cmd = _build_launch_command(
        floating_mode=floating_mode,
        reverse_enabled=reverse_enabled,
        panel_width=panel_width,
        quick_lang_enabled=quick_lang_enabled,
        quick_langs=quick_langs,
        role=role,
        window_x=window_x,
        window_y=window_y,
    )
    try:
        proc = subprocess.Popen(cmd)
    except Exception as exc:
        print(f"[panel] 启动面板失败: {exc!r}")
        return False
    with _lock:
        _panel_processes.append({"proc": proc, "role": role_key})
    return True


def launch_translation_panels(
    *,
    floating_mode: bool,
    reverse_enabled: bool = False,
    panel_width: int = 600,
    quick_lang_enabled: bool = True,
    quick_langs: Optional[List[str]] = None,
) -> bool:
    """同时打开自我翻译与翻译他人两个悬浮窗。"""
    width = max(300, int(panel_width))
    self_ok = launch_panel(
        floating_mode=floating_mode,
        reverse_enabled=reverse_enabled,
        panel_width=width,
        quick_lang_enabled=quick_lang_enabled,
        quick_langs=quick_langs,
        role="self",
        window_x=80,
        window_y=80,
    )
    others_ok = launch_panel(
        floating_mode=floating_mode,
        reverse_enabled=False,
        panel_width=width,
        quick_lang_enabled=quick_lang_enabled,
        quick_langs=quick_langs,
        role="others",
        window_x=80,
        window_y=80 + 190,
    )
    return self_ok or others_ok


def launch_panel_like_last(role: str) -> bool:
    """托盘用：按网页上次打开悬浮窗的选项打开这个角色的（网页还没打开过就置顶、默认快捷语言）。"""
    import config
    role_key = _role_key(role)
    with _lock:
        options = dict(_last_options)
    return launch_panel(
        floating_mode=bool(options.get("floating_mode", True)),
        reverse_enabled=bool(getattr(config, "ENABLE_REVERSE_TRANSLATION", False)) if role_key == "self" else False,
        panel_width=int(options.get("panel_width") or getattr(config, "PANEL_WIDTH", 600)),
        quick_lang_enabled=bool(options.get("quick_lang_enabled", True)),
        quick_langs=options.get("quick_langs"),
        role=role_key,
        window_x=80,
        window_y=PANEL_WINDOW_Y[role_key],
    )


def running_roles() -> set:
    """开着的悬浮窗是哪几个（self / others / screen）。"""
    with _lock:
        _prune_locked()
        return {item.get("role") for item in _panel_processes}


def close_panel(role: str, timeout: float = 2.0) -> bool:
    """只关这一个角色的悬浮窗。没开着返回 False。"""
    role_key = _role_key(role)
    with _lock:
        _prune_locked()
        procs = [item for item in _panel_processes if item.get("role") == role_key]
        _panel_processes[:] = [item for item in _panel_processes if item.get("role") != role_key]
    for item in procs:
        proc = item.get("proc")
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return bool(procs)


def has_running_panel() -> bool:
    """是否有仍在运行的面板进程。"""
    with _lock:
        _prune_locked()
        return bool(_panel_processes)


def panel_count() -> int:
    """返回正在运行的面板进程数。"""
    with _lock:
        _prune_locked()
        return len(_panel_processes)


def close_all_panels(timeout: float = 2.0) -> int:
    """关闭全部面板进程。返回成功终止的进程数。"""
    with _lock:
        _prune_locked()
        procs = list(_panel_processes)
        _panel_processes.clear()

    closed = 0
    for item in procs:
        proc = item.get("proc")
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                closed += 1
        except Exception:
            pass
    # 等待退出，必要时强制结束
    for item in procs:
        proc = item.get("proc")
        try:
            if proc is not None and proc.poll() is None:
                proc.wait(timeout=timeout)
        except Exception:
            try:
                if proc is not None:
                    proc.kill()
            except Exception:
                pass
    return closed


def _prune_locked() -> None:
    """移除已结束的进程记录。调用方必须持有 _lock。"""
    alive: List[dict] = []
    for item in _panel_processes:
        proc = item.get("proc")
        try:
            if proc is not None and proc.poll() is None:
                alive.append(item)
        except Exception:
            continue
    _panel_processes[:] = alive
