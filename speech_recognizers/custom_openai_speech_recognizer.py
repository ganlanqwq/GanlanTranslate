"""自定义 OpenAI 兼容 ASR 后端。

提供两个实现：
- CustomAudioRecognizer：走 /chat/completions，把整段音频作为 input_audio
  直接送入多模态模型，让模型"听 + 翻译"。模型直接产出目标语言文本。
  适用于 Qwen-Omni、GPT-4o-audio、Gemini 等支持 audio modality 的模型。

- CustomSttRecognizer：走 /audio/transcriptions（Whisper 风格），只产出
  源语言转写文本，再走项目内的翻译管线。
  适用于 OpenAI Whisper、本地兼容 Whisper 协议的端点等。

两个后端都遵循``SpeechRecognizer``接口，方便 recognizer_factory 注入。
"""
from __future__ import annotations

import base64
import io
import json
import threading
import wave
from typing import Any, Dict, Optional

import requests
from proxy_detector import refresh_system_proxy_env

from .base_speech_recognizer import (
    RecognitionEvent,
    SpeechRecognitionCallback,
    SpeechRecognizer,
)


def _wrap_pcm16le_to_wav(pcm_data: bytes, sample_rate: int, channels: int = 1) -> bytes:
    """将裸 PCM s16le 数据包装为 WAV 字节流（用于 /audio/transcriptions 上传）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


class CustomAudioRecognizer(SpeechRecognizer):
    """语音模态后端：把整段音频作为 input_audio 发到 OpenAI 兼容 /chat/completions。

    多模态 LLM（GPT-4o-audio、Qwen-Omni、Gemini 等）会直接返回
    翻译到目标语言的文本。这里每个 voice segment 一请求，不做流式增量，
    因为多数多模态 API 不支持增量 partial 返回。
    """

    def __init__(
        self,
        callback: SpeechRecognitionCallback,
        *,
        base_url: str,
        api_key: str,
        model: str,
        target_language: str,
        sample_rate: int = 16000,
        channels: int = 1,
        timeout: int = 30,
        extra_body_json: str = "",
        source_language: str = "auto",
    ) -> None:
        self._callback = callback
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._model = model or ""
        self._target_language = target_language or "ja"
        self._sample_rate = int(sample_rate or 16000)
        self._channels = max(1, int(channels or 1))
        self._timeout = int(timeout or 30)
        self._extra_body: Dict[str, Any] = {}
        if extra_body_json:
            try:
                self._extra_body = json.loads(extra_body_json)
            except (ValueError, TypeError):
                self._extra_body = {}
        self._source_language = source_language or "auto"

        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._started = False
        self._paused = False
        self._session_active = False
        self._session_lock = threading.Lock()
        self._request_seq = 0

    def set_callback(self, callback: SpeechRecognitionCallback) -> None:
        self._callback = callback

    def _ensure_started_locked(self) -> None:
        if not self._session_active:
            self._session_active = True
            try:
                self._callback.on_session_started()
            except Exception:
                pass

    def _notify_stopped_locked(self) -> None:
        if self._session_active:
            self._session_active = False
            try:
                self._callback.on_session_stopped()
            except Exception:
                pass

    def start(self) -> None:
        with self._session_lock:
            self._started = True
            self._paused = False

    def stop(self) -> None:
        with self._session_lock:
            self._started = False
            self._paused = False
            self._flush_locked()
            self._notify_stopped_locked()

    def pause(self) -> None:
        with self._session_lock:
            self._paused = True
            self._flush_locked()

    def resume(self) -> None:
        with self._session_lock:
            self._paused = False
            self._started = True

    def send_audio_frame(self, data: bytes) -> None:
        if not data:
            return
        with self._buffer_lock:
            self._buffer.extend(data)

    def _flush_locked(self) -> None:
        """把已缓存的音频作为一段送入模型。"""
        with self._buffer_lock:
            if not self._buffer:
                return
            pcm_bytes = bytes(self._buffer)
            self._buffer.clear()

        if not self._base_url or not self._api_key or not self._model:
            try:
                self._callback.on_error(
                    RuntimeError(
                        "custom_audio 后端未配置：需要 API 地址、Key 和模型名"
                    )
                )
            except Exception:
                pass
            return

        try:
            refresh_system_proxy_env()
            wav_bytes = _wrap_pcm16le_to_wav(
                pcm_bytes, self._sample_rate, self._channels
            )
            audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
            url = f"{self._base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            system_prompt = (
                "你是一名实时语音翻译助手。"
                f"请将听到的语音转写并翻译为 {self._target_language}。"
                "只输出译文本身，不要加引号、不要加解释、不要加原语言对照。"
            )
            payload: Dict[str, Any] = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": audio_b64,
                                    "format": "wav",
                                },
                            }
                        ],
                    },
                ],
                "temperature": 0.2,
            }
            if self._extra_body:
                payload.update(self._extra_body)

            self._request_seq += 1
            resp = requests.post(
                url, headers=headers, json=payload, timeout=self._timeout
            )
            if resp.status_code >= 400:
                snippet = (resp.text or "")[:300]
                raise RuntimeError(
                    f"custom_audio HTTP {resp.status_code}: {snippet}"
                )

            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return
            message = choices[0].get("message") or {}
            text = (message.get("content") or "").strip()
            if not text:
                return
            event = RecognitionEvent(
                text=text,
                is_final=True,
                confidence=None,
                raw=data,
            )
            try:
                self._callback.on_result(event)
            except Exception:
                pass
        except Exception as exc:
            try:
                self._callback.on_error(exc)
            except Exception:
                pass

    def get_last_request_id(self) -> Optional[str]:
        return f"custom-audio-{self._request_seq}" if self._request_seq else None

    def get_first_package_delay(self) -> Optional[int]:
        return None

    def get_last_package_delay(self) -> Optional[int]:
        return None


class CustomSttRecognizer(SpeechRecognizer):
    """语音转文字（STT）后端：把整段音频以 multipart 上传到 /audio/transcriptions。

    兼容 OpenAI Whisper 协议（包括本地 faster-whisper server、Azure OpenAI
    等同规格端点）。仅产出源语言文本，由项目内翻译管线再处理。
    """

    def __init__(
        self,
        callback: SpeechRecognitionCallback,
        *,
        base_url: str,
        api_key: str,
        model: str,
        sample_rate: int = 16000,
        channels: int = 1,
        timeout: int = 30,
        audio_format: str = "wav",
        prompt: str = "",
        source_language: str = "auto",
    ) -> None:
        self._callback = callback
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key or ""
        self._model = model or ""
        self._sample_rate = int(sample_rate or 16000)
        self._channels = max(1, int(channels or 1))
        self._timeout = int(timeout or 30)
        self._audio_format = (audio_format or "wav").lower()
        self._prompt = prompt or ""
        self._source_language = source_language or "auto"

        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._session_active = False
        self._request_seq = 0

    def set_callback(self, callback: SpeechRecognitionCallback) -> None:
        self._callback = callback

    def start(self) -> None:
        with self._session_lock:
            self._session_active = True
            try:
                self._callback.on_session_started()
            except Exception:
                pass

    def stop(self) -> None:
        with self._session_lock:
            self._flush_locked()
            if self._session_active:
                self._session_active = False
                try:
                    self._callback.on_session_stopped()
                except Exception:
                    pass

    def pause(self) -> None:
        with self._session_lock:
            self._flush_locked()

    def resume(self) -> None:
        pass

    def send_audio_frame(self, data: bytes) -> None:
        if not data:
            return
        with self._buffer_lock:
            self._buffer.extend(data)

    def _flush_locked(self) -> None:
        with self._buffer_lock:
            if not self._buffer:
                return
            pcm_bytes = bytes(self._buffer)
            self._buffer.clear()

        if not self._base_url or not self._api_key or not self._model:
            try:
                self._callback.on_error(
                    RuntimeError(
                        "custom_stt 后端未配置：需要 API 地址、Key 和模型名"
                    )
                )
            except Exception:
                pass
            return

        try:
            refresh_system_proxy_env()
            wav_bytes = _wrap_pcm16le_to_wav(
                pcm_bytes, self._sample_rate, self._channels
            )
            url = f"{self._base_url}/audio/transcriptions"
            headers = {"Authorization": f"Bearer {self._api_key}"}

            form_files = {
                "file": (
                    f"audio.{self._audio_format}",
                    wav_bytes,
                    "audio/wav",
                )
            }
            form_data: Dict[str, str] = {"model": self._model}
            if self._prompt:
                form_data["prompt"] = self._prompt
            if self._source_language and self._source_language.lower() not in (
                "auto",
                "auto-detect",
                "",
            ):
                form_data["language"] = self._source_language

            self._request_seq += 1
            resp = requests.post(
                url,
                headers=headers,
                files=form_files,
                data=form_data,
                timeout=self._timeout,
            )
            if resp.status_code >= 400:
                snippet = (resp.text or "")[:300]
                raise RuntimeError(
                    f"custom_stt HTTP {resp.status_code}: {snippet}"
                )

            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                payload = resp.json()
                text = (payload.get("text") or "").strip()
            else:
                text = (resp.text or "").strip()

            if not text:
                return
            event = RecognitionEvent(
                text=text,
                is_final=True,
                confidence=None,
                raw=None,
            )
            try:
                self._callback.on_result(event)
            except Exception:
                pass
        except Exception as exc:
            try:
                self._callback.on_error(exc)
            except Exception:
                pass

    def get_last_request_id(self) -> Optional[str]:
        return f"custom-stt-{self._request_seq}" if self._request_seq else None

    def get_first_package_delay(self) -> Optional[int]:
        return None

    def get_last_package_delay(self) -> Optional[int]:
        return None