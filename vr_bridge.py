"""主程序 → SteamVR 附属程序（vr_overlay/）的数据推送。

只在 config.DISPLAY_MODE == "vr" 时发送。走本机 UDP（127.0.0.1:VR_OVERLAY_UDP_PORT），
内容变了立刻发，没变也每秒发一次心跳，附属程序晚启动也能马上拿到最新状态。

反方向（附属程序改设置、启停模式）走 Flask 的 HTTP 接口，见 ui/app.py 的 /api/vr/*。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable, Optional

import config

SEND_INTERVAL = 0.15
HEARTBEAT_INTERVAL = 1.0
MAX_DATAGRAM = 60000

# 附属程序最近一次 /api/vr/hello 的时间和信息
_overlay_seen_at = 0.0
_overlay_info: dict = {}
_lock = threading.Lock()


def mark_overlay_seen(info: Optional[dict] = None) -> None:
    global _overlay_seen_at, _overlay_info
    with _lock:
        _overlay_seen_at = time.time()
        if isinstance(info, dict):
            _overlay_info = dict(info)


def overlay_status() -> dict:
    with _lock:
        age = time.time() - _overlay_seen_at if _overlay_seen_at else None
        return {
            'connected': age is not None and age < 6.0,
            'last_seen_ago': round(age, 1) if age is not None else None,
            'info': dict(_overlay_info),
        }


def _encode(payload: dict) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(data) <= MAX_DATAGRAM:
        return data
    # 太大就逐步砍掉旧内容（UDP 单包上限）
    trimmed = dict(payload)
    for key, sub in (('screen', 'items'), ('others', 'lines')):
        part = dict(trimmed.get(key) or {})
        items = list(part.get(sub) or [])
        while items and len(data) > MAX_DATAGRAM:
            items = items[:-1] if key == 'screen' else items[1:]
            part[sub] = items
            trimmed[key] = part
            data = json.dumps(trimmed, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return data


class VRBridge:
    def __init__(self, payload_provider: Callable[[], dict]):
        self._provider = payload_provider
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name='vr-bridge', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        last_data = b''
        last_sent = 0.0
        while not self._stop.wait(SEND_INTERVAL):
            if str(getattr(config, 'DISPLAY_MODE', 'desktop')) != 'vr':
                last_data = b''
                continue
            try:
                payload = self._provider()
            except Exception as exc:
                print(f'[VR] 生成推送数据失败: {exc}')
                continue
            # 时间戳不参与「有没有变化」的比较
            body = _encode(payload)
            now = time.time()
            if body == last_data and now - last_sent < HEARTBEAT_INTERVAL:
                continue
            try:
                port = int(getattr(config, 'VR_OVERLAY_UDP_PORT', 5012))
                self._sock.sendto(body, ('127.0.0.1', port))
            except OSError:
                # 附属程序没开时 Windows 可能报 10054，忽略
                pass
            last_data = body
            last_sent = now


_bridge: Optional[VRBridge] = None


def start_bridge(payload_provider: Callable[[], dict]) -> VRBridge:
    global _bridge
    if _bridge is None:
        _bridge = VRBridge(payload_provider)
        _bridge.start()
    return _bridge
