"""和主程序（Flask，127.0.0.1:5001）的连接。

- 收：主程序在 VR 模式下把字幕状态用 UDP 推过来（vr_bridge.py）。
- 发：启停模式、改译文语言、切桌面/VR、触发画面翻译，走现有的 HTTP 接口。
- 每 2 秒 POST /api/vr/hello 报到，顺便知道主程序是不是开着、在不在 VR 模式。
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

APP_VERSION = '1.0.4'


class AlreadyRunning(RuntimeError):
    pass


class MainLink:
    def __init__(self, base_url: str = 'http://127.0.0.1:5001', udp_port: int = 5012):
        self.base_url = base_url.rstrip('/')
        self.udp_port = udp_port
        self.payload: dict = {}
        self.payload_at = 0.0
        self.version = 0              # 收到新内容就 +1，渲染用来判断要不要重画
        self.hello_ok_at = 0.0
        self.display_mode = 'desktop'
        self.toast = ''
        self.toast_at = 0.0
        self.busy: set = set()        # 正在执行的操作（按钮显示「…」）
        self._lock = threading.Lock()
        self._actions: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._sock = self._bind_udp()

    # ---------- 启动 ----------

    def _bind_udp(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(('127.0.0.1', self.udp_port))
        except OSError as exc:
            sock.close()
            # 端口被占 = 已经有一个附属程序在跑（单实例）
            raise AlreadyRunning(f'UDP {self.udp_port} 已被占用') from exc
        sock.settimeout(0.5)
        return sock

    def start(self) -> None:
        for target, name in ((self._recv_loop, 'vr-recv'), (self._hello_loop, 'vr-hello'),
                             (self._action_loop, 'vr-actions')):
            threading.Thread(target=target, name=name, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    # ---------- 状态 ----------

    @property
    def connected(self) -> bool:
        return time.time() - self.hello_ok_at < 6.0

    @property
    def vr_mode(self) -> bool:
        return self.connected and self.display_mode == 'vr'

    def snapshot(self) -> dict:
        with self._lock:
            fresh = time.time() - self.payload_at < 4.0
            return dict(self.payload) if fresh else {}

    def set_toast(self, text: str) -> None:
        with self._lock:
            self.toast = text
            self.toast_at = time.time()
            self.version += 1

    def current_toast(self, ttl: float = 5.0) -> str:
        with self._lock:
            return self.toast if self.toast and time.time() - self.toast_at < ttl else ''

    # ---------- 线程 ----------

    def _recv_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                # Windows 上对端端口不可达会在这里报 10054，忽略继续收
                if self._stop.is_set():
                    return
                continue
            try:
                payload = json.loads(data.decode('utf-8'))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            with self._lock:
                changed = payload != self.payload
                self.payload = payload
                self.payload_at = time.time()
                if payload.get('display_mode'):
                    self.display_mode = payload['display_mode']
                if changed:
                    self.version += 1

    def _hello_loop(self) -> None:
        while not self._stop.is_set():
            was_connected, was_mode = self.connected, self.display_mode
            ok, data = self.request('/api/vr/hello', {'version': APP_VERSION, 'udp_port': self.udp_port},
                                    timeout=2.0)
            if ok:
                self.hello_ok_at = time.time()
                self.display_mode = data.get('display_mode') or 'desktop'
            if was_connected != self.connected or was_mode != self.display_mode:
                with self._lock:
                    self.version += 1
            self._stop.wait(2.0)

    def _action_loop(self) -> None:
        while not self._stop.is_set():
            try:
                key, path, body = self._actions.get(timeout=0.5)
            except queue.Empty:
                continue
            ok, data = self.request(path, body, timeout=90.0)
            message = str(data.get('message') or data.get('error') or '')
            if not ok:
                # 主程序返回的原因原样显示；没有原因时用界面语言的通用提示
                self.set_toast(message or 'i18n:toast.failed')
            with self._lock:
                self.busy.discard(key)
                self.version += 1

    # ---------- HTTP ----------

    def request(self, path: str, body: Optional[dict] = None, timeout: float = 5.0):
        url = self.base_url + path
        data = None if body is None else json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST' if body is not None else 'GET',
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode('utf-8', 'replace')
                parsed = json.loads(text) if text else {}
                return bool(parsed.get('success', True)), parsed
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read().decode('utf-8', 'replace'))
            except Exception:
                parsed = {'message': f'HTTP {exc.code}'}
            return False, parsed
        except Exception:
            return False, {'message': 'i18n:toast.unreachable'}

    def submit(self, key: str, path: str, body: dict) -> None:
        """排队执行一个 HTTP 操作；同一个 key 正在执行时忽略重复点击。"""
        with self._lock:
            if key in self.busy:
                return
            self.busy.add(key)
            self.version += 1
        self._actions.put((key, path, body))

    # 便捷操作
    def start_modes(self, modes) -> None:
        self.submit('modes:' + ','.join(modes) + ':start', '/api/modes/start',
                    {'modes': list(modes), 'api_keys': {}})

    def stop_modes(self, modes) -> None:
        self.submit('modes:' + ','.join(modes) + ':stop', '/api/modes/stop', {'modes': list(modes)})

    def set_language(self, mode: str, lang: str) -> None:
        path = {
            'self': '/api/target-language',
            'others': '/api/others-target-language',
            'screen': '/api/screen-target-language',
        }[mode]
        self.submit(f'lang:{mode}', path, {'target_language': lang})

    def screen_translate(self) -> None:
        self.submit('screen-translate', '/api/screen-translate', {})
