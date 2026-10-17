"""
甘岚翻译 主入口 - 仅负责服务编排和生命周期管理

所有业务逻辑已拆分至：
- app_state.py          : 集中管理运行时状态
- text_processor.py     : 假名/拼音标注、双语裁剪、显示格式化
- translation_pipeline.py : 翻译器初始化、API 注册表、翻译执行
- audio_capture.py      : PyAudio 初始化、音频流管理、音频捕获
- recognition_handler.py : 语音识别回调（VRChatRecognitionCallback）
"""
import os
import time
import logging
import signal
import asyncio
from typing import Callable, Optional

# Allow PyTorch and DirectML/ONNX stacks to coexist in one process.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')

# 旧版纯命令行入口继续完全依靠 .env。WebUI 会以 ``import main`` 的方式
# 启动服务，此时绝不加载 .env，也不应用任何用户配置环境变量。
if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()

# 必须先完成 CLI 的 .env 引导，再导入任何会读取 config 的业务模块。
import config

if __name__ == '__main__':
    config.apply_cli_env()

from hot_words_manager import HotWordsManager
from proxy_detector import refresh_system_proxy_env, print_proxy_info
from speech_recognizers.recognizer_factory import (
    init_dashscope_api_key,
    create_recognizer,
    select_backend,
)

from osc_manager import osc_manager
from ipc_client import IPCClient
from vrchat_detector import get_mic_status as _get_vrchat_mic_status

# ---- 新模块 ----
from app_state import AppState, get_state, set_state
from streaming_translation import (
    clear_translation_contexts,
    config_from_module,
    reinitialize_translator,
    update_secondary_translator,
)
from streaming_translation.pipeline import (
    _is_primary_config_changed as _is_primary_translator_config_changed,
)
from audio_capture import init_audio_stream, close_audio_stream, audio_capture_task
from recognition_handler import (
    VRChatRecognitionCallback,
    PAUSE_RESUME_BACKENDS,
    is_effective_mic_control_enabled,
)
from others_pipeline import others_translation_task
from screen_translation import screen_translation_task

# 配置日志
logging.basicConfig(level=getattr(logging, config.LOG_LEVEL))
logger = logging.getLogger(__name__)

# ---- 语言检测器工厂 ----
def _create_language_detector():
    """根据当前配置创建语言检测器实例。"""
    if config.LANGUAGE_DETECTOR_TYPE == 'fasttext':
        from language_detectors.fasttext_detector import FasttextDetector
        return FasttextDetector()
    elif config.LANGUAGE_DETECTOR_TYPE == 'enzh':
        from language_detectors.enzh_detector import EnZhDetector
        return EnZhDetector()
    else:  # 默认使用 cjke
        from language_detectors.cjke_detector import CJKEDetector
        return CJKEDetector()


# ============ 向后兼容：供 ui/app.py 等外部模块访问 ============
# ui/app.py 通过 `import main; main.subtitles_state` 访问字幕状态
# 以及 `main.reinitialize_translator` 触发翻译器热重载
# 以下属性保持向后兼容

@property
def _compat_subtitles_state():
    state = get_state()
    if state:
        return state.subtitles_state
    return {"original": "", "translated": "", "reverse_translated": "", "ongoing": False}

# 使用模块级变量做兼容桥接
subtitles_state = {"original": "", "translated": "", "reverse_translated": "", "ongoing": False}
others_subtitles_state = {"lines": [], "display": "", "ongoing": False}
stop_event = None  # 由 main() 设置


def _sync_subtitles_to_module():
    """将 AppState 中的字幕状态同步到模块级变量（供 ui/app.py 轮询读取）。"""
    state = get_state()
    if state:
        subtitles_state.update(state.subtitles_state)
        others_subtitles_state.clear()
        others_subtitles_state.update(state.others_subtitles_state)


def update_subtitles(original: str, translated: str, ongoing: bool, reverse_translated: str = ""):
    """兼容旧接口的 update_subtitles。"""
    state = get_state()
    if state:
        state.update_subtitles(original, translated, ongoing, reverse_translated)
        _sync_subtitles_to_module()
    else:
        subtitles_state["original"] = original
        subtitles_state["translated"] = translated
        subtitles_state["reverse_translated"] = reverse_translated
        subtitles_state["ongoing"] = ongoing


def reinitialize_translator_compat():
    state = get_state()
    if state:
        # 翻译关闭时跳过（重）构建翻译器，避免未配置对应 API Key 时报错。
        # 运行时重新开启翻译会再次调用本函数并正常构建。
        needs_translator = (
            getattr(config, 'ENABLE_TRANSLATION', True)
            or 'others' in state.mode_tasks
            or 'screen' in state.mode_tasks
        )
        if state.translator is None and not needs_translator:
            return
        cfg = config_from_module(config)
        if _is_primary_translator_config_changed(state, cfg):
            reinitialize_translator(state, cfg)
        else:
            update_secondary_translator(state, cfg)
        _refresh_ipc_translator_reference(state)
        loop = state.main_loop
        if loop is not None and loop.is_running():
            loop.create_task(osc_manager.apply_runtime_config(app_name="甘岚翻译"))


def clear_translator_contexts_compat():
    state = get_state()
    if state:
        cleared = clear_translation_contexts(state)
        if cleared:
            logger.info('[Translator] Cleared %s context buffer(s)', cleared)


def _refresh_ipc_translator_reference(state):
    ipc_client = getattr(osc_manager, '_ipc_client', None)
    if ipc_client is None:
        return
    setter = getattr(ipc_client, 'set_translator', None)
    if callable(setter):
        setter(state.translator)


# ============ 识别控制 ============

async def stop_recognition_async(state):
    """异步暂停或停止识别服务"""
    if not state.recognition_active:
        return

    loop = asyncio.get_event_loop()
    state.recognition_active = False

    try:
        await loop.run_in_executor(state.executor, state.recognition_instance.pause)
    except Exception:
        pass

    # 闭麦时重置本地 VAD 状态与缓存，确保下一次开麦时有干净的历史。
    if state.vad_processor is not None:
        try:
            state.vad_processor.reset()
        except Exception:
            pass
    state._vad_was_speaking = False
    import numpy as np
    state._vad_pending_samples = np.array([], dtype=np.float32)

    state.bump_audio_send_generation()


async def start_recognition_async(state):
    """异步开始或恢复识别服务"""
    if state.recognition_active:
        print('Recognition already active.')
        return

    loop = asyncio.get_event_loop()

    try:
        if state.current_asr_backend in PAUSE_RESUME_BACKENDS and state.recognition_started:
            await loop.run_in_executor(state.executor, state.recognition_instance.resume)
        else:
            await loop.run_in_executor(state.executor, state.recognition_instance.start)
            state.recognition_started = True
    except Exception as e:
        state.recognition_active = False
        print(f'[ASR] 启动识别失败: {e}')
        raise

    state.bump_audio_send_generation()
    state.recognition_active = True

    # 识别重新开始/恢复，解除"撤回作废"的结果丢弃状态
    if state.recognition_callback is not None:
        state.recognition_callback.resume_outputs()


async def handle_mute_change(state, is_muted):
    """处理静音状态变化的回调函数"""
    # 快速开关麦克风以清空消息框：短时间内连续两次收到静音消息则清空聊天框。
    # 该逻辑独立于麦克风控制开关，因此放在最前面处理。
    if is_muted and getattr(config, 'ENABLE_DOUBLE_MUTE_CLEAR', True):
        now = time.monotonic()
        window = getattr(config, 'DOUBLE_MUTE_CLEAR_WINDOW_SECONDS', 0.8)
        last = state.last_mute_engaged_time
        if last is not None and (now - last) <= window:
            print('[OSC] 检测到快速开关麦克风，清空聊天框并撤回之前的内容')
            await osc_manager.clear_chatbox()
            # 撤回作废：丢弃之前已发出的识别/翻译请求迟到返回的结果
            if state.recognition_callback is not None:
                state.recognition_callback.discard_pending_outputs()
            state.last_mute_engaged_time = None  # 重置，避免连续误触发
        else:
            state.last_mute_engaged_time = now

    await _apply_mic_gate(state, is_muted)


async def _apply_mic_gate(state, is_muted):
    """按 VRChat 麦克风状态启停自我识别（不含「快速开关清空聊天框」逻辑）。"""
    if not is_effective_mic_control_enabled(state.current_asr_backend):
        return

    if state.recognition_instance is None:
        print('[ASR] 识别实例未初始化')
        return

    stop_word = '暂停' if state.current_asr_backend in PAUSE_RESUME_BACKENDS else '停止'
    start_word = (
        '恢复'
        if state.current_asr_backend in PAUSE_RESUME_BACKENDS and state.recognition_started
        else '开始'
    )

    if is_muted:
        if state.recognition_active:
            if state.recognition_callback is not None:
                state.recognition_callback.mark_mute_finalization_requested()
            if state.mute_delay_task and not state.mute_delay_task.done():
                state.mute_delay_task.cancel()

            if config.MUTE_DELAY_SECONDS > 0:
                print(f'[ASR] 检测到静音，将在 {config.MUTE_DELAY_SECONDS} 秒后{stop_word}语音识别...')

                async def delayed_stop():
                    try:
                        await asyncio.sleep(config.MUTE_DELAY_SECONDS)
                        if state.recognition_active:
                            print(f'[ASR] 延迟时间到，{stop_word}语音识别')
                            await stop_recognition_async(state)
                            logger.info('[ASR] 语音识别已%s', stop_word)
                    except asyncio.CancelledError:
                        print('[ASR] 停止识别已取消（取消静音）')

                state.mute_delay_task = asyncio.create_task(delayed_stop())
            else:
                print(f'[ASR] 检测到静音，立即{stop_word}语音识别...')
                await stop_recognition_async(state)
                logger.info('[ASR] 语音识别已%s', stop_word)
    else:
        if state.recognition_callback is not None:
            state.recognition_callback.clear_mute_finalization_requested()
        if state.mute_delay_task and not state.mute_delay_task.done():
            state.mute_delay_task.cancel()
            print('[ASR] 检测到取消静音，已取消延迟停止任务')

        if not state.recognition_active:
            print(f'[ASR] 检测到取消静音，{start_word}语音识别...')
            await start_recognition_async(state)
            logger.info('[ASR] 语音识别已%s', start_word)


# 收到 OSC 静音推送后这么久之内，轮询不按 OSCQuery 结果改识别状态
OSC_MUTE_EVENT_GRACE_SECONDS = 2.0


async def _refresh_vrchat_mic_state(state, gate: bool) -> None:
    """检测 VRChat 麦克风，更新网页显示；gate=True 时顺便按状态启停自我识别。

    麦克风关着 / VRChat 没开 → 停识别；开着 → 开识别。
    VRChat 开着但读不到 MuteSelf（没开 OSC）时 should_translate 为 True，照旧识别。
    """
    try:
        # 每次重读配置，便于 UI 改设置后无需重启服务
        detection_enabled = bool(getattr(config, 'VRCHAT_MIC_DETECTION_ENABLED', True))
        user_override = getattr(config, 'VRCHAT_MIC_USER_OPEN', None)
        has_override = user_override is not None

        if detection_enabled:
            # tasklist + OSCQuery HTTP 都是阻塞调用，放到线程里
            status = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _get_vrchat_mic_status(
                    user_assumed_open=user_override,
                    has_user_override=has_override,
                    pushed_mute_self=osc_manager.last_mute_value,
                ),
            )
            mic_open = status.should_translate
            state.vrchat_mic_detail = status.detail
        else:
            # 用户关闭了自动检测，按用户偏好
            mic_open = bool(user_override) if user_override is not None else True
            state.vrchat_mic_detail = "已关闭自动检测"
        state.vrchat_mic_open = mic_open
        state.vrchat_mic_checked_at = time.time()
    except Exception as exc:
        state.vrchat_mic_open = True
        state.vrchat_mic_detail = f"检测异常: {exc}"
        state.vrchat_mic_checked_at = time.time()
        return

    if not gate or not is_effective_mic_control_enabled(state.current_asr_backend):
        return
    last_push = getattr(state, 'last_osc_mute_event_at', None)
    if last_push is not None and time.monotonic() - last_push < OSC_MUTE_EVENT_GRACE_SECONDS:
        return
    # 静音延迟停止还在倒计时，交给它处理
    if state.mute_delay_task and not state.mute_delay_task.done():
        return
    if mic_open and not state.recognition_active:
        print(f'[VRChat 麦克风] 已开启（{state.vrchat_mic_detail}），启动自我识别')
        await _apply_mic_gate(state, False)
    elif not mic_open and state.recognition_active:
        print(f'[VRChat 麦克风] 未开启（{state.vrchat_mic_detail}），停止自我识别以节省消耗')
        await _apply_mic_gate(state, True)


def _make_mute_callback(state):
    """创建同步桥接回调：将 OSC 线程中的静音事件安全投递到主事件循环。"""
    def handle_mute_change_sync(is_muted):
        # OSC 推过来的 MuteSelf 比 3 秒轮询快，先同步给网页显示
        if (
            getattr(config, 'VRCHAT_MIC_DETECTION_ENABLED', True)
            and getattr(config, 'VRCHAT_MIC_USER_OPEN', None) is None
        ):
            state.vrchat_mic_open = not bool(is_muted)
            state.vrchat_mic_detail = f"OSC: MuteSelf={bool(is_muted)}（麦克风{'已静音' if is_muted else '开着'}）"
            state.vrchat_mic_checked_at = time.time()
        # 推送刚到时，轮询那边先别按 OSCQuery 结果改识别状态，免得来回跳
        state.last_osc_mute_event_at = time.monotonic()
        loop = state.main_loop
        if loop is None or not loop.is_running():
            logger.warning('[ASR] 主事件循环不可用，忽略静音状态变化')
            return
        try:
            asyncio.run_coroutine_threadsafe(handle_mute_change(state, is_muted), loop)
        except Exception as e:
            logger.error('[ASR] 投递静音状态变化失败: %s', e)
    return handle_mute_change_sync


def signal_handler(sig, frame):
    print('Ctrl+C pressed, stop recognition ...')
    state = get_state()
    if state and state.stop_event is not None:
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(state.stop_event.set)
        except Exception:
            state.stop_event.set()


# ============ 主入口 ============

async def _cleanup_on_startup_failure(state, osc_mgr):
    """启动早期失败时释放已分配的资源（executor / OSC）。"""
    try:
        osc_mgr.clear_mute_callback()
        osc_mgr.reset_runtime_state()
    except Exception:
        pass
    try:
        if not getattr(osc_mgr, '_running', False):
            pass
        else:
            await osc_mgr.stop_server()
    except Exception:
        pass
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, state.audio_executor.shutdown, True)
    except Exception:
        pass
    try:
        await asyncio.get_event_loop().run_in_executor(None, state.asr_send_executor.shutdown, True)
    except Exception:
        pass
    try:
        state.executor.shutdown(wait=False)
    except Exception:
        pass
    print('[启动失败] 已释放资源，服务停止。')


# ============ 三个模式：自我 / 他人 / 屏幕 ============
# 服务（main）只负责公共部分：翻译器、OSC、IPC、字幕同步。
# 三个模式是服务里各自独立的任务，可以单独启停，也可以同时运行。
# 最后一个模式停掉后服务自动退出。

MODES = ('self', 'others', 'screen')


def _ensure_translator(state) -> None:
    """按需构建翻译器（他人 / 屏幕 / 开了翻译的自我都要用）。失败时抛异常。"""
    if state.translator is not None:
        return
    reinitialize_translator(state, config_from_module(config))
    _refresh_ipc_translator_reference(state)


def _load_self_hot_words(state, backend):
    """自我识别的热词：返回 (corpus_text, hot_word_entries)，并可能设置 state.vocabulary_id。"""
    corpus_text: Optional[str] = None
    hot_word_entries: Optional[list] = None
    if not (config.ENABLE_HOT_WORDS and backend in {'qwen', 'qwen_audio3', 'dashscope', 'local'}):
        return corpus_text, hot_word_entries
    print('\n[热词] 初始化热词资源...')
    try:
        hot_words_manager = HotWordsManager(
            api_key=str(getattr(config, 'DASHSCOPE_API_KEY', '') or '').strip()
        )
        hot_words_manager.load_all_hot_words()
        entries = [entry for entry in hot_words_manager.get_hot_words() if entry.get('text')]
        words = [entry.get('text') for entry in entries]
        if backend == 'qwen':
            if words:
                corpus_text = "\n".join(words)
                print(f'[热词] 已生成 Qwen 语料文本，共 {len(words)} 条\n')
            else:
                print('[热词] 未加载到热词条目，跳过 Qwen 语料配置\n')
        elif backend == 'qwen_audio3':
            # Qwen-Audio-3.0 支持即时热词，直接下发词条与权重，无需创建热词表
            if entries:
                hot_word_entries = entries
                print(f'[热词] 已准备 Qwen-Audio-3.0 即时热词，共 {len(entries)} 条\n')
            else:
                print('[热词] 未加载到热词条目，跳过即时热词配置\n')
        elif backend == 'local':
            local_engine = getattr(config, 'LOCAL_ASR_ENGINE', 'sensevoice')
            if words and local_engine == 'qwen3-asr':
                corpus_text = "\n".join(words)
                print(f'[热词] 已生成本地 Qwen3-ASR 语料文本，共 {len(words)} 条\n')
            elif words:
                print(
                    f'[热词] 已加载 {len(words)} 条热词；当前本地引擎为 {local_engine}，'
                    '仅 qwen3-asr 会使用语料注入\n'
                )
            else:
                print('[热词] 未加载到热词条目，跳过本地语料配置\n')
        else:
            state.vocabulary_id = hot_words_manager.create_vocabulary(
                target_model='fun-asr-realtime',
            )
            print(f'[热词] 热词表创建成功，ID: {state.vocabulary_id}\n')
    except Exception as e:
        print(f'[热词] 热词初始化失败: {e}')
        print('[热词] 将继续运行但不使用热词\n')
        state.vocabulary_id = None
        return None, None
    return corpus_text, hot_word_entries


def _setup_self_vad(state, backend) -> None:
    """统一 VAD：在线 API 发送门控（本地 ASR 走识别器内部 VAD，此处不处理）。"""
    state.vad_processor = None
    state.vad_enabled = False
    if backend == 'local':
        print('[VAD] — 本地 ASR 后端：门控由识别器内部 VAD 负责，采集侧不做门控')
        return
    if not config.VAD_ENABLED:
        print('[VAD] — VAD 未启用（VAD_ENABLED=False）')
        return
    try:
        from local_asr.model_manager import is_silero_cached, download_silero
        from local_asr.vad_processor import VADProcessor

        if not is_silero_cached():
            print('[VAD] Silero ONNX 模型未下载，正在自动下载...')
            download_silero()

        print('[VAD] Silero ONNX 模型就绪，正在初始化...')
        state.vad_processor = VADProcessor(
            sample_rate=config.SAMPLE_RATE,
            threshold=config.LOCAL_VAD_THRESHOLD,
            min_speech_duration=config.LOCAL_VAD_MIN_SPEECH_DURATION,
            chunk_duration=512.0 / config.SAMPLE_RATE,
            pre_speech_duration=config.LOCAL_VAD_PRE_SPEECH_DURATION,
        )
        state.vad_processor.update_settings({
            'vad_mode': 'silero',
            'vad_threshold': config.LOCAL_VAD_THRESHOLD,
            'min_speech_duration': config.LOCAL_VAD_MIN_SPEECH_DURATION,
            'silence_duration': config.LOCAL_VAD_SILENCE_DURATION,
            'pre_speech_duration': config.LOCAL_VAD_PRE_SPEECH_DURATION,
        })
        state.vad_enabled = True
        import numpy as np
        state._vad_pending_samples = np.array([], dtype=np.float32)
        state._vad_was_speaking = False
        print('[VAD] ✓ 在线 API VAD 发送门控已启用')
        print(f'[VAD]   threshold={state.vad_processor.threshold:.2f} '
              f'min_speech={config.LOCAL_VAD_MIN_SPEECH_DURATION:.1f}s '
              f'silence={config.LOCAL_VAD_SILENCE_DURATION:.1f}s '
              f'mode={state.vad_processor.mode}')
    except Exception as e:
        import traceback
        print(f'[VAD] ✗ 初始化失败，VAD 门控未启用: {e}')
        traceback.print_exc()
        state.vad_enabled = False


async def _setup_self_mode(state) -> None:
    """自我模式的准备工作：识别器、VAD、热词、麦克风。失败时抛异常。"""
    backend = select_backend(config.PREFERRED_ASR_BACKEND, config.VALID_ASR_BACKENDS)
    if backend != config.PREFERRED_ASR_BACKEND:
        print(f'[ASR] 已切换语音识别后端为 {backend}')
    else:
        print(f'[ASR] 目标识别后端: {backend}')
    state.current_asr_backend = backend
    state.recognition_active = False
    state.recognition_started = False

    if config.ENABLE_TRANSLATION:
        _ensure_translator(state)

    _setup_self_vad(state, backend)
    corpus_text, hot_word_entries = await asyncio.get_running_loop().run_in_executor(
        None, _load_self_hot_words, state, backend
    )

    # 设置静音状态回调
    osc_manager.set_mute_callback(_make_mute_callback(state))
    print('[OSC] 已设置静音状态回调')

    callback = VRChatRecognitionCallback(state)
    callback.loop = asyncio.get_running_loop()
    state.recognition_callback = callback

    state.recognition_instance = create_recognizer(
        backend=backend,
        callback=callback,
        sample_rate=config.SAMPLE_RATE,
        audio_format=config.FORMAT_PCM,
        source_language=config.SOURCE_LANGUAGE,
        vocabulary_id=state.vocabulary_id,
        corpus_text=corpus_text,
        hot_words=hot_word_entries,
        enable_vad=config.ENABLE_VAD,
        vad_threshold=config.VAD_THRESHOLD,
        vad_silence_duration_ms=config.VAD_SILENCE_DURATION_MS,
        keepalive_interval=config.KEEPALIVE_INTERVAL,
    )
    if state.vocabulary_id and backend == 'dashscope':
        print(f'[ASR] 使用热词表: {state.vocabulary_id}')
    if backend == 'qwen':
        vad_status = '启用' if config.ENABLE_VAD else '禁用'
        print(f'[ASR] VAD状态: {vad_status}')
        if config.ENABLE_VAD:
            print(f'[ASR] VAD配置: 阈值={config.VAD_THRESHOLD}, 静音时长={config.VAD_SILENCE_DURATION_MS}ms')
        if config.KEEPALIVE_INTERVAL > 0:
            print(f'[ASR] WebSocket心跳已启用: 间隔={config.KEEPALIVE_INTERVAL}秒')
        else:
            print('[ASR] WebSocket心跳已禁用')
    print('[ASR] 识别实例已创建')

    state.ensure_audio_executor()
    state.ensure_asr_send_executor()
    await init_audio_stream(state)

    stop_hint = '暂停' if backend in PAUSE_RESUME_BACKENDS else '停止'
    resume_hint = '恢复' if backend in PAUSE_RESUME_BACKENDS else '开始'
    print("=" * 60)
    print("[自我] 只在 VRChat 麦克风开着时识别")
    print(f"取消静音(MuteSelf=False)将{resume_hint}语音识别")
    print(f"启用静音(MuteSelf=True)将{stop_hint}语音识别")
    print("=" * 60)
    # VRChat 只在静音状态变化时推 MuteSelf；启动前就开着麦的话会一直等不到。
    # 这里先主动查一次（OSCQuery），之后由轮询兜底。
    await _refresh_vrchat_mic_state(state, gate=True)


async def _self_mode_run(state) -> None:
    """自我模式主体：音频采集 + 麦克风轮询。被取消时收尾。"""
    capture_task = asyncio.create_task(audio_capture_task(state, state.recognition_instance))

    async def _vrchat_mic_poll_loop():
        """每 3 秒检查一次 VRChat 麦克风状态，并按状态启停自我识别。"""
        while True:
            await asyncio.sleep(3.0)
            await _refresh_vrchat_mic_state(state, gate=True)

    poll_task = asyncio.create_task(_vrchat_mic_poll_loop())
    try:
        await asyncio.gather(capture_task, poll_task)
    finally:
        for task in (capture_task, poll_task):
            task.cancel()
        for task in (capture_task, poll_task):
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        await _teardown_self_mode(state)


async def _teardown_self_mode(state) -> None:
    osc_manager.clear_mute_callback()
    if state.mute_delay_task and not state.mute_delay_task.done():
        state.mute_delay_task.cancel()
    loop = asyncio.get_running_loop()
    if state.recognition_active:
        try:
            await stop_recognition_async(state)
        except Exception:
            pass
    if state.recognition_instance is not None:
        try:
            request_id = await loop.run_in_executor(
                state.executor, state.recognition_instance.get_last_request_id,
            )
            first_delay = await loop.run_in_executor(
                state.executor, state.recognition_instance.get_first_package_delay,
            )
            last_delay = await loop.run_in_executor(
                state.executor, state.recognition_instance.get_last_package_delay,
            )
            print(
                '[Metric] requestId: {}, first package delay ms: {}, last package delay ms: {}'
                .format(request_id, first_delay, last_delay)
            )
        except Exception:
            pass
        try:
            await loop.run_in_executor(state.executor, state.recognition_instance.stop)
        except Exception:
            pass
    state.recognition_instance = None
    state.recognition_callback = None
    state.recognition_started = False
    state.recognition_active = False
    try:
        await close_audio_stream(state)
    except Exception as exc:
        print(f'[自我] 关闭音频流失败: {exc}')
    state.update_subtitles("", "", False)
    print('[自我] 已停止')


def _mode_label(mode: str) -> str:
    return {'self': '自我', 'others': '他人', 'screen': '屏幕'}.get(mode, mode)


def _on_mode_task_done(state, mode: str, task: asyncio.Task) -> None:
    if state.mode_tasks.get(mode) is task:
        state.mode_tasks.pop(mode, None)
    error = None
    if not task.cancelled():
        error = task.exception()
    if error is not None:
        state.mode_status[mode] = f'error: {error}'
        print(f'[{_mode_label(mode)}] 异常退出: {error}')
    else:
        state.mode_status[mode] = 'stopped'
    # 最后一个模式停了，服务也退出
    if not state.mode_tasks and state.stop_event is not None and not state.stop_event.is_set():
        print('[服务] 所有模式都已停止，服务退出')
        state.stop_event.set()


async def start_modes(state, modes) -> dict:
    """启动若干模式，返回 {mode: (ok, message)}。已在运行的直接算成功。"""
    results = {}
    for mode in modes:
        if mode not in MODES:
            results[mode] = (False, '未知模式')
            continue
        existing = state.mode_tasks.get(mode)
        if existing is not None and not existing.done():
            results[mode] = (True, '已在运行')
            continue
        state.mode_status[mode] = 'starting'
        try:
            if mode == 'self':
                await _setup_self_mode(state)
                task = asyncio.create_task(_self_mode_run(state))
            elif mode == 'others':
                _ensure_translator(state)
                task = asyncio.create_task(others_translation_task(state))
            else:
                _ensure_translator(state)
                task = asyncio.create_task(screen_translation_task(state))
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f'[{_mode_label(mode)}] 启动失败: {exc}')
            if mode == 'self':
                await _teardown_self_mode(state)
            state.mode_status[mode] = f'error: {exc}'
            results[mode] = (False, str(exc))
            continue
        state.mode_tasks[mode] = task
        state.mode_status[mode] = 'running'
        task.add_done_callback(lambda t, m=mode: _on_mode_task_done(state, m, t))
        print(f'[{_mode_label(mode)}] 已启动')
        results[mode] = (True, '已启动')
    return results


async def stop_modes(state, modes) -> dict:
    results = {}
    for mode in modes:
        task = state.mode_tasks.get(mode)
        if task is None or task.done():
            state.mode_status[mode] = 'stopped'
            results[mode] = (True, '未在运行')
            continue
        state.mode_status[mode] = 'stopping'
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
        results[mode] = (True, '已停止')
    return results


def submit_mode_action(action: str, modes, timeout: float = 60.0) -> dict:
    """供 Flask 线程调用：在服务事件循环里启动 / 停止模式并等结果。"""
    state = get_state()
    loop = state.main_loop if state is not None else None
    if state is None or loop is None or not loop.is_running():
        raise RuntimeError('服务未运行')
    coro = start_modes(state, modes) if action == 'start' else stop_modes(state, modes)
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)


async def main(
    keep_oscquery_alive: bool = False,
    lifecycle_callback: Optional[Callable[[str, Optional[bool]], None]] = None,
    initial_modes=('self',),
):
    """服务主函数：准备公共部分，启动 initial_modes，等到所有模式都停了再收尾。"""
    global stop_event

    def emit_lifecycle(lifecycle: str, recognition_active: Optional[bool] = None):
        if lifecycle_callback is not None:
            lifecycle_callback(lifecycle, recognition_active)

    # 创建并注册 AppState
    state = AppState()
    set_state(state)
    emit_lifecycle('starting', False)

    state.update_subtitles("", "", False)
    state.clear_others_subtitles()
    _sync_subtitles_to_module()

    state.main_loop = asyncio.get_running_loop()
    state.stop_event = asyncio.Event()
    stop_event = state.stop_event  # 向后兼容

    state.ensure_executor()
    state.ensure_audio_executor()

    # 检测并应用系统代理设置
    system_proxies = refresh_system_proxy_env()
    print_proxy_info(system_proxies)

    # 初始化 DashScope API Key
    init_dashscope_api_key()
    print('Initializing ...')

    state.current_asr_backend = select_backend(config.PREFERRED_ASR_BACKEND, config.VALID_ASR_BACKENDS)
    state.language_detector = _create_language_detector()

    ipc_client = None
    if getattr(config, 'IPC_ENABLED', True):
        ipc_client = IPCClient(translator=state.translator)
        osc_manager.set_ipc_client(ipc_client)
        asyncio.create_task(ipc_client.start())
    else:
        print('[IPC] IPC is disabled in config, using standalone mode')

    # 启动 OSC 服务器（自我发聊天框、收 MuteSelf 都靠它）
    print('[OSC] 启动OSC服务器...')
    await osc_manager.start_server(app_name="甘岚翻译")

    # 只在主线程中设置信号处理器
    try:
        signal.signal(signal.SIGINT, signal_handler)
    except ValueError:
        pass

    # ---- 字幕状态同步任务 ----
    async def _subtitles_sync_loop():
        """定期将 AppState.subtitles_state 同步到模块级变量。"""
        try:
            while not state.stop_event.is_set():
                _sync_subtitles_to_module()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass

    sync_task = asyncio.create_task(_subtitles_sync_loop())

    try:
        await start_modes(state, list(initial_modes or ()))
        if not state.mode_tasks:
            print('[启动失败] 没有任何模式启动成功，服务停止。')
            state.stop_event.set()
        else:
            emit_lifecycle('running', state.recognition_active)
        await state.stop_event.wait()
        emit_lifecycle('stopping', state.recognition_active)
        await stop_modes(state, list(state.mode_tasks.keys()))
    finally:
        emit_lifecycle('stopping', state.recognition_active)
        sync_task.cancel()
        # 万一还有没停干净的模式
        for task in list(state.mode_tasks.values()):
            task.cancel()
        clear_translator_contexts_compat()
        osc_manager.clear_mute_callback()
        osc_manager.reset_runtime_state()
        if ipc_client is not None:
            await ipc_client.stop()
        osc_manager.clear_ipc_client()

        loop = asyncio.get_event_loop()
        if not keep_oscquery_alive:
            await osc_manager.stop_server()

        await loop.run_in_executor(None, state.audio_executor.shutdown, True)
        await loop.run_in_executor(None, state.asr_send_executor.shutdown, True)
        await loop.run_in_executor(None, state.executor.shutdown, False)
        emit_lifecycle('stopped', False)


# main function
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nProgram terminated by user.')
    finally:
        print('Cleanup completed.')
