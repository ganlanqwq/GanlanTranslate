"""
翻译他人管线：VRChat.exe 进程音频 → VAD → ASR → 说话人 A-Z → LLM → 悬浮窗。
与自我翻译并行，不发送 OSC。
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time
import uuid
from typing import Optional

import numpy as np

import config
from app_state import get_smart_selector
from speaker_id import SpeakerRegistry
from speech_recognizers.base_speech_recognizer import (
    RecognitionEvent,
    SpeechRecognitionCallback,
)
from hot_words_manager import HotWordsManager
from speech_recognizers.recognizer_factory import asr_auth_error_hint, create_recognizer, select_backend
from streaming_translation import translate_with_backend
from text_processor import (
    canonicalize_output_language,
    normalize_lang_code,
)

logger = logging.getLogger(__name__)


def _is_session_close_race(message: str) -> bool:
    text = message.lower()
    return (
        "unexpected websocket close" in text
        or "unexpectedmessagereceived" in text
        or "connection unexpected closed" in text
    )


def _refresh_dashscope_task_id(recognizer) -> None:
    """每次重开都用新 task id。复用已结束的 id 时，服务端会直接关掉 websocket。"""
    recognition = getattr(recognizer, "_recognition", None)
    if recognition is None:
        return
    recognition.last_request_id = uuid.uuid4().hex
    recognition.request_id_confirmed = False


def others_target_language() -> str:
    """他人悬浮窗译文语言：优先用独立填写项，留空则跟随自我目标语言。"""
    requested = canonicalize_output_language(
        getattr(config, "OTHERS_TARGET_LANGUAGE", None)
    )
    if requested:
        return requested
    fallback = canonicalize_output_language(
        getattr(config, "TARGET_LANGUAGE", None)
    )
    return fallback or "zh-CN"


def others_context_prefix(target_lang: str) -> str:
    return (
        "You are translating other VRChat players' speech for the local user to read. "
        f"Translate into {target_lang} only. Do not use any other output language. "
        "Preserve meaning and tone. Output ONLY the translation text with no quotes, "
        "labels, speaker tags, or explanations."
    )


def _resolve_others_asr_backend() -> str:
    requested = (getattr(config, "OTHERS_ASR_BACKEND", "") or "").strip()
    if not requested:
        requested = config.PREFERRED_ASR_BACKEND
    return select_backend(requested, config.VALID_ASR_BACKENDS)


def _load_others_asr_vocab(backend: str, state) -> tuple[Optional[str], Optional[str], Optional[list]]:
    """与自我同一套 HotWordsManager：即时 vocabulary / vocabulary_id / 语料文本。"""
    vocabulary_id = None
    corpus_text = None
    hot_word_entries = None
    if not getattr(config, "ENABLE_HOT_WORDS", True):
        print("[Others/热词] 已关闭（ENABLE_HOT_WORDS=False）")
        return vocabulary_id, corpus_text, hot_word_entries
    if backend not in {"qwen", "qwen_audio3", "dashscope", "local"}:
        print(f"[Others/热词] 后端 {backend} 不使用热词")
        return vocabulary_id, corpus_text, hot_word_entries
    try:
        manager = HotWordsManager(
            api_key=str(getattr(config, "DASHSCOPE_API_KEY", "") or "").strip()
        )
        manager.load_all_hot_words()
        entries = [e for e in manager.get_hot_words() if e.get("text")]
        words = [e.get("text") for e in entries if e.get("text")]
        if not words:
            print("[Others/热词] 未加载到热词条目")
            return vocabulary_id, corpus_text, hot_word_entries
        if backend in {"qwen", "qwen_audio3"}:
            corpus_text = "\n".join(words)
        if backend == "qwen_audio3":
            hot_word_entries = entries
            print(f"[Others/热词] Qwen-Audio-3 即时热词 {len(hot_word_entries)} 条，语料已注入上下文")
        elif backend == "qwen":
            print(f"[Others/热词] Qwen 语料 {len(words)} 条")
        elif backend == "local":
            local_engine = getattr(config, "LOCAL_ASR_ENGINE", "sensevoice")
            if local_engine == "qwen3-asr":
                corpus_text = "\n".join(words)
                print(f"[Others/热词] 本地 Qwen3-ASR 语料 {len(words)} 条")
            else:
                print(
                    f"[Others/热词] 已加载 {len(words)} 条；本地引擎 {local_engine} 不使用语料注入"
                )
        else:
            existing = getattr(state, "vocabulary_id", None)
            if existing:
                vocabulary_id = existing
                print(f"[Others/热词] 复用自我 DashScope 热词表 {vocabulary_id}")
            else:
                vocabulary_id = manager.create_vocabulary(target_model="fun-asr-realtime")
                print(f"[Others/热词] 热词表创建成功，ID: {vocabulary_id}")
    except Exception as exc:
        print(f"[Others/热词] 初始化失败，将不使用热词: {exc}")
        return None, None, None
    return vocabulary_id, corpus_text, hot_word_entries


_OTHERS_DETECTOR = None


def _others_language_detector():
    """他人可能说任何语言，用 fast-langdetect（176 种语言）；加载失败退回自我的检测器。"""
    global _OTHERS_DETECTOR
    if _OTHERS_DETECTOR is None:
        try:
            from language_detectors.fasttext_detector import FasttextDetector
            _OTHERS_DETECTOR = FasttextDetector()
        except Exception as exc:
            print(f"[Others] 多语言检测器加载失败，改用自我的检测器: {exc}")
            _OTHERS_DETECTOR = False
    return _OTHERS_DETECTOR or None


def format_others_line(speaker: str, original: str, lang: str, translated: str) -> str:
    lang_tag = (lang or "und").strip() or "und"
    return f"{speaker}：{original}[{lang_tag}]\n{translated}"


def build_others_display(lines: list[dict]) -> str:
    blocks = []
    for item in lines:
        blocks.append(
            format_others_line(
                item.get("speaker", "?"),
                item.get("original", ""),
                item.get("lang", "und"),
                item.get("translated", ""),
            )
        )
    return "\n\n".join(blocks)


class OthersRecognitionCallback(SpeechRecognitionCallback):
    """他人语音识别回调：最终结果 → 说话人 → 翻译 → 悬浮窗。"""

    def __init__(self, state, speaker_registry: SpeakerRegistry):
        self.state = state
        self.speaker_registry = speaker_registry
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._busy = False
        self.session_alive = False
        self.utterance_seen = False
        self._last_partial = ""
        self._last_partial_log_at = 0.0
        # 翻译上一句时又来的终句排队处理（只留一个会把前面的覆盖掉）
        self._pending_finals = collections.deque()
        # Key 无效（401）时别每帧都重连（2026-10-11 真机每 0.3 秒一次），等一会儿再试，越等越久
        self.backend = ""
        self.auth_blocked_until = 0.0
        self._auth_backoff = 0.0

    def on_session_started(self) -> None:
        self.session_alive = True
        print("[Others/ASR] 会话已打开")

    def on_session_stopped(self) -> None:
        self.session_alive = False
        print("[Others/ASR] 会话已关闭（将自动重开）")

    def on_error(self, error: Exception) -> None:
        self.session_alive = False
        message = str(error)
        if _is_session_close_race(message):
            print("[Others/ASR] 断句时识别连接已关闭，已忽略")
            return
        hint = asr_auth_error_hint(message, self.backend)
        if hint:
            self._auth_backoff = min(60.0, max(5.0, self._auth_backoff * 2))
            self.auth_blocked_until = time.time() + self._auth_backoff
            print(f"[Others/ASR] 识别错误: {error}")
            print(f"[Others/ASR] {hint}（{int(self._auth_backoff)} 秒后再试）")
            return
        logger.warning("[Others/ASR] 识别错误: %s", error)
        print(f"[Others/ASR] 识别错误: {error}")

    def on_result(self, event: RecognitionEvent) -> None:
        text = (event.text or "").strip()
        if not text:
            return
        self.utterance_seen = True
        self._auth_backoff = 0.0
        if not event.is_final:
            try:
                self.state.others_subtitles_state["ongoing"] = True
            except Exception:
                pass
            now = time.time()
            if text != self._last_partial and now - self._last_partial_log_at >= 1.0:
                self._last_partial = text
                self._last_partial_log_at = now
                print(f"[Others/ASR] 部分：{text}")
            return

        audio = getattr(self.state, "others_last_speech_audio", None)
        if self._busy:
            self._pending_finals.append((text, audio))
            return
        loop = self.loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(
                lambda t=text, a=audio: asyncio.ensure_future(
                    self._handle_final(t, a), loop=loop
                )
            )

    async def _handle_final(self, text: str, audio) -> None:
        if self._busy:
            self._pending_finals.append((text, audio))
            return
        self._busy = True
        t0 = time.perf_counter()
        try:
            lang = "und"
            detector = _others_language_detector() or getattr(self.state, "language_detector", None)
            if detector is not None:
                try:
                    info = detector.detect(text) or {}
                    raw = info.get("language") if isinstance(info, dict) else None
                    lang = normalize_lang_code(raw or "und")
                    if lang in ("auto", ""):
                        lang = "und"
                except Exception as exc:
                    logger.debug("[Others] 语言检测失败: %s", exc)

            try:
                if lang and lang != "und":
                    get_smart_selector().record_language(lang)
            except Exception:
                pass

            target_lang = others_target_language()
            same_lang = (
                lang not in ("und", "auto", "")
                and normalize_lang_code(lang) == normalize_lang_code(target_lang)
            )

            t_prep = time.perf_counter()
            self.state.append_others_subtitle(
                speaker="…",
                original=text,
                lang=lang,
                translated="",
            )

            loop = asyncio.get_running_loop()
            sample_rate = int(getattr(config, "SAMPLE_RATE", 16000) or 16000)

            def _assign_speaker():
                clip = audio
                if clip is not None:
                    arr = np.asarray(clip).reshape(-1)
                    max_samples = sample_rate * 3
                    if arr.size > max_samples:
                        arr = arr[-max_samples:]
                    clip = arr
                return self.speaker_registry.assign(clip)

            speaker_fut = loop.run_in_executor(self.state.executor, _assign_speaker)

            translated = text
            translator = getattr(self.state, "translator", None)
            if translator is not None and not same_lang:
                try:
                    src_lang = lang if lang != "und" else None

                    def _do_translate():
                        return translate_with_backend(
                            translator,
                            getattr(self.state, "deepl_fallback_translator", None),
                            text,
                            target_lang,
                            detected_source_language=src_lang,
                            source_language="auto",
                            context_prefix=others_context_prefix(target_lang),
                            record_history=False,
                            include_history=False,
                        )

                    translate_fut = loop.run_in_executor(self.state.executor, _do_translate)
                    speaker, translated_raw = await asyncio.gather(speaker_fut, translate_fut)
                    translated = (translated_raw or "").strip() or text
                except Exception as exc:
                    logger.warning("[Others] 翻译失败，回退原文: %s", exc)
                    print(f"[Others] 翻译失败，回退原文: {exc}")
                    speaker = await speaker_fut
                    translated = text
            else:
                speaker = await speaker_fut

            try:
                if self.state.translator is not None and hasattr(
                    self.state.translator, "add_external_speech"
                ):
                    self.state.translator.add_external_speech(text)
            except Exception:
                pass

            self.state.update_last_others_subtitle(
                speaker=speaker,
                lang=lang,
                translated=translated,
            )
            logger.info("[Others] %s：%s[%s] → %s", speaker, text, lang, translated)
            print(f"[Others] 识别：{speaker}：{text}[{lang}] → {target_lang}")
            print(f"[Others] 译文：{translated}")
            print(
                f"[耗时] 他人 语检 {int((t_prep - t0) * 1000)}ms "
                f"说话人+翻译 {int((time.perf_counter() - t_prep) * 1000)}ms"
            )
        finally:
            self._busy = False
            self.state.others_subtitles_state["ongoing"] = False
            if self._pending_finals:
                p_text, p_audio = self._pending_finals.popleft()
                await self._handle_final(p_text, p_audio)


async def others_translation_task(state) -> None:
    """他人模式主循环（由 main.start_modes 单独启动，停止时被取消）。"""
    from local_asr.model_manager import download_silero, is_silero_cached
    from local_asr.vad_processor import VADProcessor
    from process_audio_capture import ProcessLoopbackCapture
    from vrchat_detector import get_vrchat_pid

    speaker_registry = SpeakerRegistry(
        similarity_threshold=float(getattr(config, "OTHERS_SPEAKER_SIMILARITY", 0.75)),
        sample_rate=int(config.SAMPLE_RATE),
    )
    state.others_speaker_registry = speaker_registry
    state.others_capture_status = "waiting"
    state.others_capture_pid = None

    callback = OthersRecognitionCallback(state, speaker_registry)
    callback.loop = asyncio.get_running_loop()

    backend = _resolve_others_asr_backend()
    callback.backend = backend
    self_backend = select_backend(config.PREFERRED_ASR_BACKEND, config.VALID_ASR_BACKENDS)
    print(f"[Others/ASR] 后端={backend}" + ("" if backend == self_backend else f"（自我={self_backend}）"))
    vocabulary_id, corpus_text, hot_word_entries = _load_others_asr_vocab(backend, state)
    recognizer = None
    capture: Optional[ProcessLoopbackCapture] = None
    vad: Optional[VADProcessor] = None
    # 用 -1 作哨兵，避免 pid/current 同为 None 时跳过首次状态更新
    current_pid: Optional[int] = -1
    recognition_started = False

    print("[Others] 翻译他人管线已启动（等待 VRChat.exe）")

    try:
        if getattr(config, "VAD_ENABLED", True):
            try:
                if not is_silero_cached():
                    print("[Others/VAD] 正在下载 Silero ONNX...")
                    download_silero()
                others_threshold = float(getattr(config, "OTHERS_VAD_THRESHOLD", 0.45))
                others_min_speech = float(getattr(config, "OTHERS_VAD_MIN_SPEECH_DURATION", 0.6))
                others_silence = config.clamp_vad_silence_duration(
                    float(getattr(config, "OTHERS_VAD_SILENCE_DURATION", 0.9))
                )
                others_pre = float(getattr(config, "OTHERS_VAD_PRE_SPEECH_DURATION", 0.35))
                vad = VADProcessor(
                    sample_rate=config.SAMPLE_RATE,
                    threshold=others_threshold,
                    min_speech_duration=others_min_speech,
                    chunk_duration=512.0 / config.SAMPLE_RATE,
                    pre_speech_duration=others_pre,
                )
                vad.update_settings({
                    "vad_mode": getattr(config, "LOCAL_VAD_MODE", "silero"),
                    "vad_threshold": others_threshold,
                    "min_speech_duration": others_min_speech,
                    "silence_duration": others_silence,
                    "pre_speech_duration": others_pre,
                })
                print(
                    f"[Others/VAD] 已就绪（他人专用 阈值={others_threshold:.2f} "
                    f"最短={others_min_speech:.1f}s 静音={others_silence:.1f}s "
                    f"预缓冲={others_pre:.2f}s；自我仍用 LOCAL_VAD_*）"
                )
            except Exception as exc:
                print(f"[Others/VAD] 初始化失败，将直通送 ASR: {exc}")
                vad = None

        others_silence_s = config.clamp_vad_silence_duration(
            float(getattr(config, "OTHERS_VAD_SILENCE_DURATION", 0.9))
        )
        others_vad_ms = int(round(others_silence_s * 1000))
        extra_asr_kwargs = {}
        if backend == "qwen_audio3":
            # 中文热词语料当上下文会把日语等外语往中文上带；他人只给多语言提示（热词表照常）
            corpus_text = None
            extra_asr_kwargs["speaker_language"] = "multi"
        recognizer = create_recognizer(
            backend=backend,
            callback=callback,
            sample_rate=config.SAMPLE_RATE,
            audio_format=config.FORMAT_PCM,
            # SOURCE_LANGUAGE 是「我说什么语言」，别人说什么语言不一定，固定自动识别
            source_language="auto",
            vocabulary_id=vocabulary_id,
            corpus_text=corpus_text,
            hot_words=hot_word_entries,
            enable_vad=config.ENABLE_VAD,
            vad_threshold=config.VAD_THRESHOLD,
            vad_silence_duration_ms=others_vad_ms,
            keepalive_interval=config.KEEPALIVE_INTERVAL,
            **extra_asr_kwargs,
        )
        state.others_recognition_instance = recognizer

        pending = np.array([], dtype=np.float32)
        chunk_samples = 512 if config.SAMPLE_RATE == 16000 else 256
        last_pid_check = 0.0
        last_asr_send = 0.0
        last_energy_log = 0.0
        last_restart_at = 0.0
        vad_was_speaking = False
        long_cut_s = float(getattr(config, "OTHERS_LONG_SEGMENT_SECONDS", 6.0) or 0)
        long_cut_pause_s = float(getattr(config, "OTHERS_LONG_SEGMENT_PAUSE", 0.25) or 0)
        keepalive_silence = b"\x00" * (max(chunk_samples, 1600) * 2)
        loop = asyncio.get_running_loop()

        async def _stop_recognizer() -> None:
            nonlocal recognition_started
            if recognizer is None:
                return
            try:
                await loop.run_in_executor(state.asr_send_executor, recognizer.stop)
            except Exception:
                pass
            recognition_started = False
            callback.session_alive = False

        async def _start_recognizer(force: bool = False) -> bool:
            nonlocal recognition_started, last_asr_send, last_restart_at
            if recognizer is None:
                return False
            now_start = time.time()
            if now_start < callback.auth_blocked_until:
                return False
            if not force and now_start - last_restart_at < 2.0:
                return False
            last_restart_at = now_start
            callback.utterance_seen = False
            _refresh_dashscope_task_id(recognizer)
            try:
                await loop.run_in_executor(state.asr_send_executor, recognizer.start)
                recognition_started = True
                last_asr_send = time.time()
                print("[Others/ASR] 识别会话已启动")
                return True
            except Exception as exc:
                recognition_started = False
                callback.session_alive = False
                print(f"[Others/ASR] 启动失败: {exc}")
                return False

        async def _pause_for_endpoint() -> None:
            """本地 VAD 判停后立刻 pause，让 DashScope 马上出终句（与自我翻译同一策略）。"""
            nonlocal recognition_started
            if recognizer is None or not recognition_started:
                return
            # start() 在 websocket 收到 task-started 之前就返回。这时 stop
            # 会让 SDK 在握手中收到 CLOSE，并打出 UnexpectedMessageReceived。
            # 已经有识别文本就可以断句；否则最多再等一小段，让握手完成。
            deadline = time.time() + 1.2
            while not callback.utterance_seen and time.time() < deadline:
                if not recognition_started or recognizer is None:
                    return
                await asyncio.sleep(0.05)
            if recognizer is None or not recognition_started:
                return
            try:
                burst_ms = int(getattr(config, "OTHERS_VAD_END_BURST_MS", 200) or 0)
                if burst_ms > 0:
                    n_bytes = max(320, int(config.SAMPLE_RATE * burst_ms / 1000) * 2)
                    await _send_asr(b"\x00" * n_bytes)
                pause = getattr(recognizer, "pause", None)
                if pause is not None:
                    await loop.run_in_executor(state.asr_send_executor, pause)
                else:
                    await _stop_recognizer()
                    return
                recognition_started = False
                print("[Others/ASR] VAD 静音，已补尾音并结束当前会话以出终句")
            except Exception as exc:
                if _is_session_close_race(str(exc)):
                    print("[Others/ASR] 断句时识别连接已关闭，已忽略")
                else:
                    print(f"[Others/ASR] pause 失败: {exc}")
                await _stop_recognizer()

        async def _send_asr(pcm_bytes: bytes) -> None:
            nonlocal last_asr_send
            if recognizer is None or not pcm_bytes:
                return
            if not recognition_started:
                ok = await _start_recognizer(force=True)
                if not ok:
                    return
            try:
                await loop.run_in_executor(
                    state.asr_send_executor, recognizer.send_audio_frame, pcm_bytes
                )
                last_asr_send = time.time()
            except Exception as exc:
                print(f"[Others/ASR] 发送失败，将重开会话: {exc}")
                await _stop_recognizer()

        while not state.stop_event.is_set():
            now = time.time()
            if now - last_pid_check > 2.0:
                last_pid_check = now
                pid = get_vrchat_pid()
                if pid != current_pid:
                    if capture is not None:
                        try:
                            capture.stop()
                        except Exception:
                            pass
                        capture = None
                    await _stop_recognizer()

                    current_pid = pid
                    state.others_capture_pid = pid
                    if pid is None:
                        state.others_capture_status = "waiting_vrchat"
                        continue

                    try:
                        capture = ProcessLoopbackCapture(target_sample_rate=config.SAMPLE_RATE)
                        await loop.run_in_executor(None, capture.start, pid)
                        state.others_capture_status = "capturing"
                        print(f"[Others] 已挂接 VRChat.exe PID={pid}")
                        await _start_recognizer()
                    except Exception as exc:
                        state.others_capture_status = f"error: {exc}"
                        print(f"[Others] 挂接失败: {exc}")
                        capture = None
                        current_pid = -1
                        # 退避，避免挂接失败时刷屏
                        await asyncio.sleep(8.0)
                        continue

            if capture is None or not capture.active:
                await asyncio.sleep(0.2)
                continue

            # 说话中才保活；静音应 pause 出终句，不要继续灌静音把会话拖到 23s
            if vad is None and recognition_started and now - last_asr_send > 5.0:
                await _send_asr(keepalive_silence)

            pcm = await loop.run_in_executor(None, capture.read, 0.15)
            if not pcm:
                continue

            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if now - last_energy_log > 5.0:
                last_energy_log = now
                rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
                print(
                    f"[Others/Capture] rms={rms:.4f} bytes={len(pcm)} "
                    f"session={'on' if callback.session_alive else 'off'}"
                )

            if vad is None:
                await _send_asr(pcm)
                continue

            pending = np.concatenate([pending, samples])
            while pending.size >= chunk_samples:
                chunk = pending[:chunk_samples]
                pending = pending[chunk_samples:]
                segment = vad.process_chunk(chunk)

                # 长句切分：连着说很久时，不等满静音时长，在短停顿处就切开出一句
                if (
                    segment is None
                    and long_cut_s > 0
                    and vad.is_speaking
                    and vad.speech_seconds >= long_cut_s
                    and vad.silence_seconds >= long_cut_pause_s
                ):
                    print(f"[Others/VAD] 长句在停顿处切开（{vad.speech_seconds:.1f}s）")
                    segment = vad.force_flush()

                # 先存这一段的音频再 pause：终句在 pause 期间就回来了，说话人要用这一段
                if segment is not None:
                    state.others_last_speech_audio = segment.astype(np.float32, copy=False)

                if vad.is_speaking != vad_was_speaking:
                    vad_was_speaking = bool(vad.is_speaking)
                    conf = float(getattr(vad, "last_confidence", 0.0) or 0.0)
                    if vad_was_speaking:
                        print(f"[Others/VAD] SPEECH 开始 (conf={conf:.3f})")
                        if not recognition_started:
                            await _start_recognizer(force=True)
                    else:
                        print(f"[Others/VAD] SILENCE (conf={conf:.3f})")
                        await _pause_for_endpoint()

                if vad.is_speaking:
                    frame_i16 = (
                        np.clip(chunk * 32767.0, -32768, 32767).astype(np.int16).tobytes()
                    )
                    await _send_asr(frame_i16)

        if vad is not None:
            try:
                leftover = vad.flush()
                if leftover is not None:
                    state.others_last_speech_audio = leftover.astype(np.float32, copy=False)
            except Exception:
                pass

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("[Others] 管线异常: %s", exc)
        state.others_capture_status = f"error: {exc}"
    finally:
        state.others_capture_status = "stopped"
        if capture is not None:
            try:
                capture.stop()
            except Exception:
                pass
        if recognizer is not None and recognition_started:
            try:
                await asyncio.get_running_loop().run_in_executor(state.executor, recognizer.stop)
            except Exception:
                pass
        speaker_registry.clear()
        print("[Others] 翻译他人管线已停止")
