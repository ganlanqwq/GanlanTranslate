"""进程内日志缓冲。

拦截 stdout/stderr 的 print 输出到环形缓冲，供 Web UI 查询展示。
安装版（PyInstaller 打包）以控制台模式运行时，控制台窗口会被隐藏，
用户无法看到启动日志；本模块让日志可经 /api/logs 显示在控制面板上。
"""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from typing import Deque, List, Optional

_MAX_ENTRIES = 2000
_lock = threading.Lock()
_buffer: Deque[dict] = deque(maxlen=_MAX_ENTRIES)

# 是否已把原始 stdout/stderr 替换为日志转发流
_installed = False


def _append(message: str) -> None:
    if message is None:
        return
    if isinstance(message, (bytes, bytearray)):
        try:
            message = message.decode('utf-8', errors='replace')
        except Exception:
            message = str(message)
    else:
        message = str(message)
    if not message:
        return
    ts = time.time()
    with _lock:
        for line in message.splitlines():
            if not line:
                continue
            _buffer.append({"t": ts, "line": line})


class _LogStream:
    """包装原始流，写入内容同时转发到环形缓冲。"""

    def __init__(self, original):
        self._original = original

    def write(self, message) -> int:
        if message:
            _append(message)
        try:
            # 原始流可能要求 str 或 bytes，尽量按原类型写入
            return self._original.write(message)
        except TypeError:
            try:
                if isinstance(message, (bytes, bytearray)):
                    return self._original.write(message.decode('utf-8', errors='replace'))
                return self._original.write(str(message).encode('utf-8', errors='replace'))
            except Exception:
                return len(message or "")
        except Exception:
            return len(message or b"") if isinstance(message, (bytes, bytearray)) else len(message or "")

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def fileno(self):
        try:
            return self._original.fileno()
        except Exception:
            return -1

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self._original, name)

def install() -> None:
    """把 sys.stdout/sys.stderr 替换为日志转发流（幂等）。

    若 Qt 等组件在首次 install 之后又替换了 stdout/stderr，
    会重新包装，避免缓冲永久失效。
    """
    global _installed
    try:
        if not isinstance(sys.stdout, _LogStream):
            sys.stdout = _LogStream(sys.stdout)
        if not isinstance(sys.stderr, _LogStream):
            sys.stderr = _LogStream(sys.stderr)
        _installed = True
    except Exception:
        pass


def get_logs(limit: int = 500, after_ts: Optional[float] = None) -> List[dict]:
    """返回最近日志（倒序，最新在前）。

    after_ts 使用 >= 语义，避免同一时间戳（time.time 精度）的多条日志
    被跳过；调用方自行跳过首条重复项。
    """
    with _lock:
        items = list(_buffer)
    if after_ts is not None:
        items = [it for it in items if it["t"] >= after_ts]
    items = items[-int(limit):] if limit and limit > 0 else items

    def _as_text(value) -> str:
        if isinstance(value, (bytes, bytearray)):
            try:
                return value.decode('utf-8', errors='replace')
            except Exception:
                return str(value)
        return str(value if value is not None else '')

    # 倒序：最新在前；确保 line 恒为 str，避免 JSON 序列化失败
    out = []
    for it in reversed(items):
        out.append({"t": it.get("t"), "line": _as_text(it.get("line", ""))})
    return out


def clear() -> None:
    with _lock:
        _buffer.clear()
