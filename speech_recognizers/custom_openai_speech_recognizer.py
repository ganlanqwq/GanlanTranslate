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
import re
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


def whisper_language(code: Optional[str]) -> str:
    """Whisper 风格接口的 language 只认 ISO-639-1（zh、ja、en），带地区的 zh-CN 会被拒（HTTP 400）。

    返回 "" 表示不传 language（自动识别）。
    """
    s = str(code or '').strip().lower().replace('_', '-')
    if not s or s in ('auto', 'auto-detect', 'und', 'multi'):
        return ''
    primary = s.split('-')[0]
    return primary if 2 <= len(primary) <= 3 and primary.isalpha() else ''


def is_dashscope_url(url: str) -> bool:
    return 'dashscope' in str(url or '').lower() and 'aliyuncs.com' in str(url or '').lower()


def normalize_dashscope_url(url: str) -> str:
    """百炼地址漏了 /compatible-mode（比如填成 https://dashscope.aliyuncs.com/v1）：换成兼容模式的地址。"""
    from urllib.parse import urlsplit
    text = str(url or '').strip()
    if not is_dashscope_url(text) or '/compatible-mode' in text:
        return text
    parts = urlsplit(text)
    return f'{parts.scheme or "https"}://{parts.netloc}/compatible-mode/v1'


def transcribe_via_chat(base_url: str, api_key: str, model: str, wav_bytes: bytes,
                        language: str = '', timeout: float = 30.0):
    """没有 /audio/transcriptions 的接口（百炼兼容模式的 qwen3-asr-flash 等）：把音频放进
    /chat/completions 的 input_audio 里，回复内容就是识别出的文字。返回 requests 的 Response 和文字。"""
    b64 = base64.b64encode(wav_bytes).decode('ascii')
    payload: Dict[str, object] = {'model': model}
    if is_dashscope_url(base_url):
        # 百炼：data 用 data URI，不要额外的文字指令（文字会被当成上下文）；语言放 asr_options
        payload['messages'] = [{'role': 'user', 'content': [
            {'type': 'input_audio', 'input_audio': {'data': f'data:audio/wav;base64,{b64}'}},
        ]}]
        payload['asr_options'] = {'enable_itn': False, **({'language': language} if language else {})}
    else:
        hint = f' The language is {language}.' if language else ''
        payload['messages'] = [{'role': 'user', 'content': [
            {'type': 'input_audio', 'input_audio': {'data': b64, 'format': 'wav'}},
            {'type': 'text', 'text': 'Transcribe this audio verbatim. Output only the transcript.' + hint},
        ]}]
    resp = requests.post(
        base_url.rstrip('/') + '/chat/completions',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=timeout,
    )
    text = ''
    if resp.status_code < 400:
        try:
            content = (resp.json().get('choices') or [{}])[0].get('message', {}).get('content') or ''
            if isinstance(content, list):
                content = ''.join(str(p.get('text') or '') for p in content if isinstance(p, dict))
            text = re.sub(r'<think>.*?</think>', '', str(content), flags=re.S).strip()
        except ValueError:
            text = ''
    return resp, text


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
        self._base_url = normalize_dashscope_url(base_url or "").rstrip("/")
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
        # 百炼兼容模式没有 /audio/transcriptions（404），直接走 /chat/completions 的音频输入；
        # 别家遇到 404 也换过去，之后一直用它
        self._use_chat = is_dashscope_url(self._base_url)

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

            # 传上去的始终是 WAV（以前文件名跟着 audio_format 写成 .mp3 / .flac，有的服务按扩展名解码会失败）
            form_files = {
                "file": (
                    "audio.wav",
                    wav_bytes,
                    "audio/wav",
                )
            }
            form_data: Dict[str, str] = {"model": self._model}
            if self._prompt:
                form_data["prompt"] = self._prompt
            language = whisper_language(self._source_language)
            if language:
                form_data["language"] = language

            self._request_seq += 1
            text = ""
            if not self._use_chat:
                resp = requests.post(
                    url,
                    headers=headers,
                    files=form_files,
                    data=form_data,
                    timeout=self._timeout,
                )
                if resp.status_code == 404:
                    print(f"[ASR] {self._base_url} 没有 /audio/transcriptions（404），改用 /chat/completions 的音频输入")
                    self._use_chat = True
                elif resp.status_code >= 400:
                    snippet = (resp.text or "")[:300]
                    raise RuntimeError(
                        f"custom_stt HTTP {resp.status_code}: {snippet}"
                    )
                else:
                    ctype = resp.headers.get("Content-Type", "")
                    if "application/json" in ctype:
                        payload = resp.json()
                        text = (payload.get("text") or "").strip()
                    else:
                        text = (resp.text or "").strip()
            if self._use_chat:
                resp, text = transcribe_via_chat(
                    self._base_url, self._api_key, self._model, wav_bytes, language, self._timeout,
                )
                if resp.status_code >= 400:
                    snippet = (resp.text or "")[:300]
                    hint = ""
                    if "stream" in self._model.lower() or "realtime" in self._model.lower():
                        hint = "（这是实时流式模型，只能在百炼模式里用；兼容模式请换成 qwen-audio-3.1-asr-flash 或 qwen3-asr-flash）"
                    raise RuntimeError(
                        f"custom_stt HTTP {resp.status_code}{hint}: {snippet}"
                    )

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