"""单实例锁（仅主程序生效，小面板进程不受影响）。

使用 Windows 命名互斥体（named mutex）实现：同一时刻只允许一个主程序实例
运行。第二次启动时会检测到互斥体已存在并提示后退出。

小面板（--panel-app）是独立进程，由主程序派生，必须跳过本锁。
"""
from __future__ import annotations

import sys
import threading
from typing import Optional

try:
    import ctypes
    from ctypes import wintypes
except ImportError:  # pragma: no cover
    ctypes = None

_MUTEX_NAME = "Local\\ganlan_translate_single_instance"
_mutex_handle: Optional[int] = None
_lock = threading.Lock()


def acquire() -> bool:
    """尝试获取单实例互斥体。成功返回 True（本进程是首个实例）；
    若互斥体已存在返回 False（已有实例在运行）。"""
    global _mutex_handle
    if ctypes is None:  # pragma: no cover - 非 Windows 环境不做限制
        return True
    if _mutex_handle is not None:
        return True

    with _lock:
        if _mutex_handle is not None:
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not handle:
            # ERROR_ACCESS_DENIED：互斥体已被管理员身份运行的实例创建（比如安装完直接启动的那个），
            # 普通权限打不开，说明已经有一个在运行。其他错误保守起见允许运行。
            return ctypes.get_last_error() != 5

        already_exists = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        if already_exists:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            return False
        _mutex_handle = handle
        return True


def release() -> None:
    """释放互斥体（进程退出时由资源回收兜底，这里显式释放更干净）。"""
    global _mutex_handle
    if _mutex_handle is None:
        return
    with _lock:
        handle = _mutex_handle
        _mutex_handle = None
        if handle and ctypes is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass


def is_panel_mode() -> bool:
    """当前是否是小面板进程（--panel-app）。"""
    return len(sys.argv) >= 2 and sys.argv[1] == "--panel-app"
