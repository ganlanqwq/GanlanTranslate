"""
VRChat.exe 进程环回音频采集（Windows WASAPI Process Loopback via ProcTap）。

仅采集指定 PID 及其子进程的渲染音频，不采集系统默认播放设备。
需要 Windows 10 Build 20348+。

底层使用 PyPI `proc-tap`（proctap.ProcessAudioCapture），输出约 48kHz
float32 stereo；本模块转为目标采样率的 int16 单声道 PCM，供 VAD/ASR 使用。
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ProcTap 固定输出格式
_PROCTAP_RATE = 48000
_PROCTAP_CHANNELS = 2


class ProcessLoopbackCapture:
    """采集指定进程渲染音频，输出目标采样率的 int16 单声道 PCM。"""

    def __init__(self, target_sample_rate: int = 16000):
        self.target_sample_rate = int(target_sample_rate)
        self.pid: Optional[int] = None
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._error: Optional[str] = None
        self._active = False
        self._status = "idle"
        self._tap = None
        self._resampler = None
        self._pcm_logged = False
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._active

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def start(self, pid: int) -> None:
        self.stop()
        self.pid = int(pid)
        self._error = None
        self._status = "starting"
        self._pcm_logged = False

        try:
            from proctap import ProcessAudioCapture
        except ImportError as exc:
            self._status = "error"
            self._error = f"未安装 proc-tap: {exc}"
            raise RuntimeError(self._error) from exc

        from audio_resampler import AudioResampler

        if self.target_sample_rate != _PROCTAP_RATE:
            self._resampler = AudioResampler(
                _PROCTAP_RATE, self.target_sample_rate, channels=1
            )
        else:
            self._resampler = None

        try:
            self._tap = ProcessAudioCapture(pid=self.pid, on_data=self._on_data)
            self._tap.start()
        except Exception as exc:
            self._tap = None
            self._resampler = None
            self._status = "error"
            self._error = str(exc)
            raise RuntimeError(f"ProcTap 启动失败: {exc}") from exc

        self._active = True
        self._status = "capturing"
        print(f"[Others/Capture] 已开始采集 PID={self.pid} (ProcTap)")
        logger.info("[Others/Capture] 已开始采集 PID=%s (ProcTap)", self.pid)

    def stop(self) -> None:
        with self._lock:
            tap = self._tap
            self._tap = None
            self._active = False
            self._status = "stopped"
            self._resampler = None

        if tap is not None:
            try:
                tap.stop()
            except Exception as exc:
                logger.debug("[Others/Capture] ProcTap stop: %s", exc)

        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def read(self, timeout: float = 0.2) -> Optional[bytes]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _on_data(self, pcm_bytes: bytes, num_frames: int) -> None:
        """ProcTap 回调：float32 stereo → int16 mono @ target_sample_rate。"""
        if not self._active or not pcm_bytes:
            return
        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.float32)
            if samples.size < 2:
                return
            # interleaved stereo → RMS 加权单声道（弱化对打/串台词）
            if samples.size % _PROCTAP_CHANNELS == 0:
                stereo = samples.reshape(-1, _PROCTAP_CHANNELS)
                energy = np.mean(np.square(stereo), axis=0) + 1e-12
                weights = energy / energy.sum()
                mono = stereo @ weights
            else:
                mono = samples

            pcm_i16 = np.clip(mono * 32767.0, -32768, 32767).astype(np.int16)
            chunk = pcm_i16.tobytes()

            resampler = self._resampler
            if resampler is not None:
                chunk = resampler.resample(chunk)
                if not chunk:
                    return

            if not self._pcm_logged:
                self._pcm_logged = True
                print(
                    f"[Others/Capture] PCM→VAD: {len(chunk)} bytes "
                    f"@ {self.target_sample_rate}Hz int16 mono"
                )
                logger.info(
                    "[Others/Capture] first PCM chunk %s bytes → VAD",
                    len(chunk),
                )

            try:
                self._queue.put_nowait(chunk)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(chunk)
                except queue.Full:
                    pass
        except Exception as exc:
            logger.warning("[Others/Capture] on_data 处理失败: %s", exc)
