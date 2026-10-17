"""
应用状态模块 - 集中管理所有运行时可变状态，消除全局变量
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, TYPE_CHECKING

import config

if TYPE_CHECKING:
    from streaming_translation import SmartTargetLanguageSelector


class AppState:
    """集中管理应用的全部可变运行时状态。

    替代原 main.py 中散落的 19+ 个全局变量，通过依赖注入传递给各模块。
    """

    def __init__(self):
        # ---- 音频采集 ----
        self.mic = None                   # pyaudio.PyAudio 实例
        self.stream = None                # pyaudio.Stream 实例
        self.input_sample_rate: int = config.SAMPLE_RATE
        self.input_block_size: int = config.BLOCK_SIZE
        self.capture_channels: int = config.CHANNELS
        self.resampler = None             # AudioResampler | None
        self.debug_pre_audio_recorder = None   # WaveDebugRecorder | None
        self.debug_audio_recorder = None       # WaveDebugRecorder | None
        self.audio_closing: bool = False

        # ---- 线程 / 异步 ----
        self.executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=config.MAX_WORKERS
        )
        self.audio_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ganlan-audio-io",
        )
        self.asr_send_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ganlan-asr-send",
        )
        self.audio_send_generation: int = 0
        self.stop_event: Optional[asyncio.Event] = None
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None

        # ---- 语音识别 ----
        self.recognition_active: bool = False
        self.recognition_started: bool = False
        self.recognition_instance = None      # SpeechRecognizer | None
        self.recognition_callback = None      # VRChatRecognitionCallback | None
        self.mute_delay_task = None           # asyncio.Task | None
        self.last_mute_engaged_time: Optional[float] = None  # 上次收到静音消息的时刻（用于快速双击清空）
        self.current_asr_backend: str = config.PREFERRED_ASR_BACKEND
        self.vocabulary_id: Optional[str] = None

        # ---- 翻译器实例 ----
        self.translation_api = None
        self.translator = None                # ContextAwareTranslator | None
        self.secondary_translation_api = None
        self.secondary_translator = None
        self.backwards_translation_api = None
        self.backwards_translator = None
        self.deepl_fallback_translation_api = None
        self.deepl_fallback_translator = None
        self.secondary_deepl_fallback_translation_api = None
        self.secondary_deepl_fallback_translator = None
        self.secondary_target_language: Optional[str] = None
        self.translation_api_type: Optional[str] = None
        self.target_language: Optional[str] = None

        # ---- 语言检测 ----
        self.language_detector = None

        # ---- 字幕状态（供控制面板轮询） ----
        self.subtitles_state: dict = {
            "original": "",
            "translated": "",
            "reverse_translated": "",
            "ongoing": False,
        }

        # ---- 翻译他人（进程环回） ----
        self.others_subtitles_state: dict = {
            "lines": [],
            "display": "",
            "ongoing": False,
        }
        self.others_last_speech_audio = None
        self.others_speaker_registry = None
        self.others_recognition_instance = None
        self.others_capture_status: str = "idle"
        self.others_capture_pid: Optional[int] = None

        # ---- 三个模式（自我 / 他人 / 屏幕），各自独立启停 ----
        self.mode_tasks: dict = {}   # mode -> asyncio.Task
        self.mode_status: dict = {"self": "stopped", "others": "stopped", "screen": "stopped"}

        # ---- 屏幕翻译 ----
        from screen_translation import new_screen_state
        self.screen_state: dict = new_screen_state()

        # ---- 本地 VAD 发送门控 ----
        self.vad_processor = None  # VADProcessor | None
        self.vad_enabled: bool = False
        self._vad_pending_samples = None  # np.ndarray | None, 累积未处理的音频帧
        self._vad_was_speaking: bool = False  # 上一次检测的状态（用于记录状态变化）
        self._vad_drop_count: int = 0  # 门控丢弃音频帧计数器

        # ---- VRChat 麦克风状态 ----
        self.vrchat_mic_open: bool = True   # VRChat 麦克风是否开启（决定是否翻译）
        self.vrchat_mic_detail: str = ""    # 检测详情（供 UI 展示）
        self.vrchat_mic_checked_at: Optional[float] = None  # 最近一次检测时间（time.time()）
        self.last_osc_mute_event_at: Optional[float] = None  # 最近一次收到 OSC MuteSelf 推送（time.monotonic()）

    def update_subtitles(
        self,
        original: str,
        translated: str,
        ongoing: bool,
        reverse_translated: str = "",
    ):
        self.subtitles_state["original"] = original
        self.subtitles_state["translated"] = translated
        self.subtitles_state["reverse_translated"] = reverse_translated
        self.subtitles_state["ongoing"] = ongoing

    def append_others_subtitle(
        self,
        speaker: str,
        original: str,
        lang: str,
        translated: str,
    ) -> None:
        limit = max(1, int(getattr(config, "OTHERS_HISTORY_LIMIT", 8)))
        lines = list(self.others_subtitles_state.get("lines") or [])
        lines.append({
            "speaker": speaker,
            "original": original,
            "lang": lang,
            "translated": translated,
            "ts": __import__("time").time(),
        })
        if len(lines) > limit:
            lines = lines[-limit:]
        self.others_subtitles_state["lines"] = lines
        self._refresh_others_display()

    def update_last_others_subtitle(
        self,
        *,
        speaker: Optional[str] = None,
        lang: Optional[str] = None,
        translated: Optional[str] = None,
    ) -> None:
        lines = list(self.others_subtitles_state.get("lines") or [])
        if not lines:
            return
        if speaker is not None:
            lines[-1]["speaker"] = speaker
        if lang is not None:
            lines[-1]["lang"] = lang
        if translated is not None:
            lines[-1]["translated"] = translated
        self.others_subtitles_state["lines"] = lines
        self._refresh_others_display()

    def _refresh_others_display(self) -> None:
        lines = list(self.others_subtitles_state.get("lines") or [])
        blocks = [
            f"{item.get('speaker', '?')}：{item.get('original', '')}[{item.get('lang', 'und') or 'und'}]\n{item.get('translated', '')}"
            for item in lines
        ]
        self.others_subtitles_state["display"] = "\n\n".join(blocks)
        self.others_subtitles_state["ongoing"] = False

    def clear_others_subtitles(self) -> None:
        self.others_subtitles_state = {
            "lines": [],
            "display": "",
            "ongoing": False,
        }
        self.others_last_speech_audio = None

    def ensure_executor(self):
        """如果 executor 已关闭，重新创建。"""
        if self.executor._shutdown:
            self.executor = ThreadPoolExecutor(max_workers=config.MAX_WORKERS)

    def ensure_audio_executor(self):
        """如果 audio_executor 已关闭，重新创建。"""
        if self.audio_executor._shutdown:
            self.audio_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="ganlan-audio-io",
            )

    def ensure_asr_send_executor(self):
        """如果 asr_send_executor 已关闭，重新创建。"""
        if self.asr_send_executor._shutdown:
            self.asr_send_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="ganlan-asr-send",
            )

    def bump_audio_send_generation(self) -> int:
        """让旧识别会话排队中的音频帧失效。"""
        self.audio_send_generation += 1
        return self.audio_send_generation


# ---- 模块级单例访问 ----
_current_state: Optional[AppState] = None


def get_state() -> Optional[AppState]:
    """获取当前应用状态（可能为 None，仅在服务启动后可用）。"""
    return _current_state


def set_state(state: Optional[AppState]):
    """设置当前应用状态（由 main() 在启动时调用）。"""
    global _current_state
    _current_state = state


# ---- 智能目标语言选择器单例 ----

_smart_selector_instance: Optional["SmartTargetLanguageSelector"] = None


def get_smart_selector() -> "SmartTargetLanguageSelector":
    """获取全局单例 SmartTargetLanguageSelector。"""
    global _smart_selector_instance
    if _smart_selector_instance is None:
        from streaming_translation import SmartTargetLanguageSelector, config_from_module
        _smart_selector_instance = SmartTargetLanguageSelector(config_from_module(config))
    return _smart_selector_instance
