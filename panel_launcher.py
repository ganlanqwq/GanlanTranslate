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
