"""
Web UI for VRChat Translator
提供配置管理和服务控制的Web界面
"""
import asyncio
import ctypes
import json
import threading
import time
import logging
from ctypes import wintypes
from typing import List, Optional
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import sys
import os

# 添加父目录到路径以导入config和main
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from log_buffer import install as _install_log_buffer
    _install_log_buffer()
except Exception:
    pass
import config
from audio_runtime_guard import hold_portaudio
from text_processor import canonicalize_output_language, sanitize_text_fancy_style
from udp_port_check import (
    get_non_vrchat_udp_port_occupants,
    get_vrchat_udp_port_occupants,
)
from vrcx_context_bridge import (
    build_console_script,
    get_status as get_vrcx_bridge_status,
    store_payload as store_vrcx_context_payload,
)
try:
    from local_asr import (
        LOCAL_ASR_DISPLAY_NAMES,
        LOCAL_ASR_ENGINES,
        get_local_asr_features,
        is_local_asr_build_enabled,
        is_local_asr_ui_enabled,
    )
    from local_asr.model_manager import download_asr as download_local_asr_model
    from local_asr.model_manager import download_silero, get_engine_status, is_silero_cached
except ImportError:  # pragma: no cover
    LOCAL_ASR_ENGINES = ()
    LOCAL_ASR_DISPLAY_NAMES = {}

    def get_local_asr_features():
        return {
            'local_asr_build_enabled': False,
            'local_asr_ui_enabled': False,
            'engines': {},
        }

    def is_local_asr_build_enabled():
        return False

    def is_local_asr_ui_enabled():
        return False

    def download_silero():
        raise RuntimeError('Local ASR unavailable')

    def is_silero_cached():
        return False

    def download_local_asr_model(*args, **kwargs):
        raise RuntimeError('Local ASR unavailable')

    def get_engine_status(*args, **kwargs):
        raise RuntimeError('Local ASR unavailable')
from resource_path import get_resource_path

# 版本号：网页页脚和「详情」页显示，和 installer.nsi 的 APP_VERSION 保持一致
APP_VERSION = '1.1.0'

# 配置Flask使用正确的模板和静态文件路径
template_folder = get_resource_path('ui/templates')
static_folder = get_resource_path('ui/static')

app = Flask(__name__, 
            template_folder=template_folder,
            static_folder=static_folder)
CORS(app)


@app.after_request
def _no_cache_static(response):
    """静态资源禁用缓存，避免升级后浏览器使用旧 JS/CSS。"""
    try:
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
    except Exception:
        pass
    return response

VALID_LLM_TRANSLATION_FORMALITY = ('low', 'medium', 'high')
VALID_LLM_TRANSLATION_STYLE = ('standard', 'light')
DEFAULT_LLM_TRANSLATION_FORMALITY = 'medium'
DEFAULT_LLM_TRANSLATION_STYLE = 'standard'

def _sanitize_llm_translation_formality(value: Optional[str]) -> str:
    normalized = str(value or DEFAULT_LLM_TRANSLATION_FORMALITY).strip().lower()
    if normalized not in VALID_LLM_TRANSLATION_FORMALITY:
        return DEFAULT_LLM_TRANSLATION_FORMALITY
    return normalized


def _sanitize_llm_translation_style(value: Optional[str]) -> str:
    normalized = str(value or DEFAULT_LLM_TRANSLATION_STYLE).strip().lower()
    if normalized not in VALID_LLM_TRANSLATION_STYLE:
        return DEFAULT_LLM_TRANSLATION_STYLE
    return normalized


# 禁用Flask的请求日志
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# 全局状态
service_status = {
    'lifecycle': 'stopped',
    'running': False,
    'recognition_active': False,
    'backend': config.PREFERRED_ASR_BACKEND
}
service_status_lock = threading.Lock()

service_thread: Optional[threading.Thread] = None
service_loop: Optional[asyncio.AbstractEventLoop] = None
stop_event: Optional[asyncio.Event] = None
local_asr_download_state = {
    'running': False,
    'engine': None,
    'status': '',
    'error': None,
}
local_asr_download_lock = threading.Lock()


def _snapshot_service_status() -> dict:
    with service_status_lock:
        return dict(service_status)


def _set_service_status(
    *,
    lifecycle: Optional[str] = None,
    recognition_active: Optional[bool] = None,
    backend: Optional[str] = None,
) -> dict:
    with service_status_lock:
        if lifecycle is not None:
            service_status['lifecycle'] = lifecycle
            service_status['running'] = lifecycle == 'running'
            if lifecycle != 'running' and recognition_active is None:
                service_status['recognition_active'] = False
        if recognition_active is not None:
            service_status['recognition_active'] = bool(recognition_active)
        if backend is not None:
            service_status['backend'] = backend
        return dict(service_status)


def _get_service_lifecycle() -> str:
    with service_status_lock:
        return str(service_status.get('lifecycle') or 'stopped')


def _is_service_active() -> bool:
    return _get_service_lifecycle() in {'starting', 'running', 'stopping'}


RUNTIME_CREDENTIAL_FIELDS = {
    'dashscope': 'DASHSCOPE_API_KEY',
    'deepl': 'DEEPL_API_KEY',
    'llm': 'LLM_API_KEY',
    'soniox': 'SONIOX_API_KEY',
    'doubao': 'DOUBAO_API_KEY',
    'screen_ocr': 'SCREEN_OCR_API_KEY',
}


def update_runtime_credentials(api_keys: Optional[dict]) -> None:
    """Update browser-managed credentials in process memory only."""
    if not isinstance(api_keys, dict):
        return
    for field_name, config_attr in RUNTIME_CREDENTIAL_FIELDS.items():
        if field_name in api_keys:
            setattr(config, config_attr, str(api_keys[field_name] or '').strip())


def _snapshot_runtime_config() -> dict:
    """Capture mutable process configuration for transactional updates."""
    return {
        name: value
        for name, value in vars(config).items()
        if name.isupper()
    }


def _restore_runtime_config(snapshot: dict) -> None:
    for name, value in snapshot.items():
        setattr(config, name, value)
    try:
        from app_state import get_smart_selector
        from streaming_translation import config_from_module
        get_smart_selector().reload_config(config_from_module(config))
    except Exception:
        # Rollback of the authoritative config must not be masked by an
        # optional runtime consumer that has not been initialized yet.
        pass


def _get_feature_flags() -> dict:
    return get_local_asr_features()


def _local_asr_config_dict() -> dict:
    return {
        'engine': getattr(config, 'LOCAL_ASR_ENGINE', 'sensevoice'),
        'incremental_asr': getattr(config, 'LOCAL_INCREMENTAL_ASR', True),
        'interim_interval': getattr(config, 'LOCAL_INTERIM_INTERVAL', 2.0),
    }


def _vad_config_dict() -> dict:
    return {
        'enabled': bool(getattr(config, 'VAD_ENABLED', True)),
        'mode': getattr(config, 'LOCAL_VAD_MODE', 'silero'),
        'threshold': getattr(config, 'LOCAL_VAD_THRESHOLD', 0.50),
        'min_speech_duration': getattr(config, 'LOCAL_VAD_MIN_SPEECH_DURATION', 1.0),
        'max_speech_duration': getattr(config, 'LOCAL_VAD_MAX_SPEECH_DURATION', 30.0),
        'silence_duration': getattr(config, 'LOCAL_VAD_SILENCE_DURATION', 0.8),
        'pre_speech_duration': getattr(config, 'LOCAL_VAD_PRE_SPEECH_DURATION', 0.2),
    }


def _osc_udp_port() -> int:
    """与游戏接收 OSC 的端口一致，用于占用检测；优先使用 OSC_SEND_TARGET_PORT。"""
    try:
        p = int(
            getattr(config, "OSC_SEND_TARGET_PORT", None)
            or getattr(config, "OSC_SERVER_PORT", 9000)
            or 9000
        )
        return max(1, min(65535, p))
    except (TypeError, ValueError):
        return 9000


def _osc_udp_port_status_payload() -> dict:
    """本机 OSC 目标 UDP 端口占用情况（供独立检测接口与启动前校验）。"""
    port = _osc_udp_port()
    if bool(getattr(config, 'OSC_COMPAT_MODE', False)):
        return {
            "osc_udp_port": port,
            "udp_port_conflicts": [],
            "port_clear": True,
            "skipped_due_to_compat_mode": True,
            "vrchat_udp_port_occupants": [],
            "vrchat_osc_listening": True,
        }
    conflicts = get_non_vrchat_udp_port_occupants(port)
    vrchat_occupants = get_vrchat_udp_port_occupants(port)
    return {
        "osc_udp_port": port,
        "udp_port_conflicts": conflicts,
        "port_clear": len(conflicts) == 0,
        "skipped_due_to_compat_mode": False,
        "vrchat_udp_port_occupants": vrchat_occupants,
        "vrchat_osc_listening": len(vrchat_occupants) > 0,
    }


def _vrchat_osc_warning_payload(udp_status: Optional[dict] = None) -> dict:
    """VRChat 未监听 OSC 目标 UDP 端口时返回非阻断启动提示。"""
    if bool(getattr(config, 'OSC_COMPAT_MODE', False)):
        return {
            'vrchat_osc_warning_message_id': None,
            'vrchat_osc_warning_message': '',
        }
    status = udp_status if udp_status is not None else _osc_udp_port_status_payload()
    if bool(status.get('vrchat_osc_listening', False)):
        return {
            'vrchat_osc_warning_message_id': None,
            'vrchat_osc_warning_message': '',
        }
    port = status.get('osc_udp_port', _osc_udp_port())
    return {
        'vrchat_osc_warning_message_id': 'msg.vrchatOscNotListeningWarning',
        'vrchat_osc_warning_message': (
            f'未检测到 VRChat 正在监听本机 UDP {port} 端口。'
            '您可能未启动游戏，或未开启 OSC 端口。'
            '如需开启，请在游戏轮盘菜单的 Options（选项）里的 OSC 中打开开关。'
        ),
        'vrchat_osc_listening': False,
        'osc_udp_port': port,
        'vrchat_udp_port_occupants': status.get('vrchat_udp_port_occupants', []),
    }


def _find_window_titles_containing(keyword: str) -> List[str]:
    """枚举顶层窗口标题，返回包含指定关键字的窗口标题。"""
    if os.name != 'nt' or not keyword:
        return []

    matches: List[str] = []
    seen = set()

    try:
        user32 = ctypes.windll.user32
        enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @enum_windows_proc
        def _enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True

            title_length = user32.GetWindowTextLengthW(hwnd)
            if title_length <= 0:
                return True

            buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, buffer, len(buffer))
            title = buffer.value.strip()
            if title and keyword in title and title not in seen:
                seen.add(title)
                matches.append(title)
            return True

        user32.EnumWindows(_enum_proc, 0)
    except Exception as exc:
        print(f'[UI] Failed to scan window titles for "{keyword}": {exc}')

    return matches


def _accelerator_window_warning_payload() -> dict:
    """检测是否存在标题含“加速器”的窗口，并返回前端提示所需字段。"""
    matched_titles = _find_window_titles_containing('加速器')
    detected = len(matched_titles) > 0
    return {
        'accelerator_window_detected': detected,
        'accelerator_window_titles': matched_titles,
        'accelerator_warning_message_id': (
            'msg.acceleratorProcessModeWarning' if detected else None
        ),
        'accelerator_warning_message': (
            '检测到疑似有加速器正在运行，如您在使用加速器，请确保加速器当前模式不是“进程模式”。'
            if detected else ''
        ),
    }


def _sanitize_preferred_backend(value: Optional[str]) -> str:
    backend = (value or config.PREFERRED_ASR_BACKEND or 'qwen_audio3').strip() or 'qwen_audio3'
    # 已下线的后端（如 qwen3-asr-flash-realtime）自动迁移到替代后端
    backend = getattr(config, 'DEPRECATED_ASR_BACKENDS', {}).get(backend, backend)
    if backend == 'local' and not is_local_asr_ui_enabled():
        return config.PREFERRED_ASR_BACKEND
    return backend


def _sanitize_others_asr_backend(value: Optional[str]) -> str:
    backend = (value or '').strip()
    if not backend:
        return ''
    backend = getattr(config, 'DEPRECATED_ASR_BACKENDS', {}).get(backend, backend)
    if backend == 'local' and not is_local_asr_ui_enabled():
        return ''
    valid = getattr(config, 'VALID_ASR_BACKENDS', None)
    if valid and backend not in valid:
        return ''
    return backend


def _update_local_asr_download_state(**changes) -> None:
    with local_asr_download_lock:
        local_asr_download_state.update(changes)


def _snapshot_local_asr_download_state() -> dict:
    with local_asr_download_lock:
        return dict(local_asr_download_state)


def _download_local_asr_worker(engine: str) -> None:
    _update_local_asr_download_state(
        running=True,
        engine=engine,
        status=f'准备下载 {LOCAL_ASR_DISPLAY_NAMES.get(engine, engine)}',
        error=None,
    )
    try:
        if not is_silero_cached():
            _update_local_asr_download_state(status='下载 Silero VAD...')
            download_silero()
        _update_local_asr_download_state(
            status=f'下载 {LOCAL_ASR_DISPLAY_NAMES.get(engine, engine)} 模型与运行时...',
        )
        download_local_asr_model(engine)
        _update_local_asr_download_state(
            running=False,
            status='下载完成',
            error=None,
        )
    except Exception as e:
        _update_local_asr_download_state(
            running=False,
            status='下载失败',
            error=str(e),
        )


def get_config_dict():
    """获取当前配置"""
    return {
        'features': _get_feature_flags(),
        # 语音识别配置
        'asr': {
            'preferred_backend': _sanitize_preferred_backend(config.PREFERRED_ASR_BACKEND),
            'qwen_audio3_model': getattr(
                config, 'QWEN_AUDIO3_ASR_MODEL', 'qwen-audio-3.0-asr-flash-streaming'
            ),
            'keepalive_interval': config.KEEPALIVE_INTERVAL,
            'enable_hot_words': config.ENABLE_HOT_WORDS,
            'use_international_endpoint': config.USE_INTERNATIONAL_ENDPOINT,
        },
        'custom_api': {
            'base_url': getattr(config, 'CUSTOM_API_BASE_URL', ''),
            'key': getattr(config, 'CUSTOM_API_KEY', ''),
            'model': getattr(config, 'CUSTOM_API_MODEL', ''),
            'target_language': getattr(config, 'CUSTOM_API_TARGET_LANGUAGE', 'ja'),
            'timeout': getattr(config, 'CUSTOM_API_TIMEOUT', 30),
            'prompt': getattr(config, 'CUSTOM_API_PROMPT', ''),
            'audio_format': getattr(config, 'CUSTOM_API_AUDIO_FORMAT', 'wav'),
        },
        'vrchat_mic': {
            'detection_enabled': bool(getattr(config, 'VRCHAT_MIC_DETECTION_ENABLED', True)),
            'user_open': getattr(config, 'VRCHAT_MIC_USER_OPEN', None),
        },
        'vad': _vad_config_dict(),
        # 翻译配置
        'translation': {
            'enable_translation': config.ENABLE_TRANSLATION,
            'source_language': config.SOURCE_LANGUAGE,
            'target_language': config.TARGET_LANGUAGE,
            'secondary_target_language': getattr(config, 'SECONDARY_TARGET_LANGUAGE', None),
            'fallback_language': config.FALLBACK_LANGUAGE,
            'api_type': config.TRANSLATION_API_TYPE,
            'llm_template': getattr(
                config, 'LLM_TEMPLATE', config.DEFAULT_LLM_TEMPLATE
            ),
            'llm_base_url': getattr(
                config, 'LLM_BASE_URL', config.DEFAULT_LLM_BASE_URL
            ),
            'llm_model': getattr(
                config, 'LLM_MODEL', config.DEFAULT_LLM_MODEL
            ),
            'llm_translation_formality': _sanitize_llm_translation_formality(
                getattr(config, 'LLM_TRANSLATION_FORMALITY', DEFAULT_LLM_TRANSLATION_FORMALITY)
            ),
            'llm_translation_style': _sanitize_llm_translation_style(
                getattr(config, 'LLM_TRANSLATION_STYLE', DEFAULT_LLM_TRANSLATION_STYLE)
            ),
            'openai_compat_extra_body_json': getattr(
                config,
                'OPENAI_COMPAT_EXTRA_BODY_JSON',
                config.DEFAULT_LLM_EXTRA_BODY_JSON,
            ),
            'llm_parallel_fastest_mode': getattr(
                config, 'LLM_PARALLEL_FASTEST_MODE', 'off'
            ),
            'enable_llm_parallel_fastest': (
                getattr(config, 'LLM_PARALLEL_FASTEST_MODE', 'off')
                not in ('off', None, '')
            ),
            'show_partial_results': config.SHOW_PARTIAL_RESULTS,
            'enable_furigana': getattr(config, 'ENABLE_JA_FURIGANA', False),
            'enable_pinyin': getattr(config, 'ENABLE_ZH_PINYIN', False),
            'enable_arabic_reshaper': getattr(config, 'ENABLE_ARABIC_RESHAPER', True),
            'remove_trailing_period': getattr(config, 'REMOVE_TRAILING_PERIOD', False),
            'text_fancy_style': sanitize_text_fancy_style(
                getattr(config, 'TEXT_FANCY_STYLE', 'none')
            ),
            'enable_reverse_translation': config.ENABLE_REVERSE_TRANSLATION,
            'show_original_and_lang_tag': getattr(config, 'SHOW_ORIGINAL_AND_LANG_TAG', True),
        },
        # 麦克风控制配置
        'mic_control': {
            'enable_mic_control': config.ENABLE_MIC_CONTROL,
            'mute_delay_seconds': config.MUTE_DELAY_SECONDS,
            'mic_device_id': getattr(config, 'MIC_DEVICE_ID', None),
            'enable_double_mute_clear': getattr(config, 'ENABLE_DOUBLE_MUTE_CLEAR', True),
        },
        # 语言检测器配置
        'language_detector': {
            'type': config.LANGUAGE_DETECTOR_TYPE,
        },
        'smart_target_language': {
            'primary_enabled': getattr(config, 'SMART_TARGET_PRIMARY_ENABLED', False),
            'secondary_enabled': getattr(config, 'SMART_TARGET_SECONDARY_ENABLED', False),
            'strategy': getattr(config, 'SMART_TARGET_LANGUAGE_STRATEGY', 'most_common'),
            'window_size': getattr(config, 'SMART_TARGET_LANGUAGE_WINDOW_SIZE', 10),
            'exclude_self_language': getattr(config, 'SMART_TARGET_LANGUAGE_EXCLUDE_SELF_LANGUAGE', True),
            'min_samples': getattr(config, 'SMART_TARGET_LANGUAGE_MIN_SAMPLES', 3),
        },
        'others_translation': {
            'enabled': bool(getattr(config, 'OTHERS_TRANSLATION_ENABLED', False)),
            'speaker_similarity': float(getattr(config, 'OTHERS_SPEAKER_SIMILARITY', 0.75)),
            'history_limit': int(getattr(config, 'OTHERS_HISTORY_LIMIT', 8)),
            'asr_backend': _sanitize_others_asr_backend(
                getattr(config, 'OTHERS_ASR_BACKEND', '')
            ),
            'target_language': (
                canonicalize_output_language(
                    getattr(config, 'OTHERS_TARGET_LANGUAGE', '')
                )
                or canonicalize_output_language(getattr(config, 'TARGET_LANGUAGE', 'zh-CN'))
                or 'zh-CN'
            ),
        },
        'screen_translation': {
            'enabled': bool(getattr(config, 'SCREEN_TRANSLATION_ENABLED', False)),
            'target_language': (
                canonicalize_output_language(getattr(config, 'SCREEN_TARGET_LANGUAGE', ''))
                or 'zh-CN'
            ),
            'hotkey': getattr(config, 'SCREEN_HOTKEY', ''),
            'ocr_base_url': getattr(config, 'SCREEN_OCR_BASE_URL', ''),
            'ocr_model': getattr(config, 'SCREEN_OCR_MODEL', ''),
        },
        'panel': {
            'width': getattr(config, 'PANEL_WIDTH', 600),
        },
        'display': {
            'mode': _normalize_display_mode(getattr(config, 'DISPLAY_MODE', 'desktop')),
        },
        # OSC配置
        'osc': {
            'server_ip': config.OSC_SERVER_IP,
            'server_port': config.OSC_SERVER_PORT,
            'client_ip': config.OSC_CLIENT_IP,
            'client_port': config.OSC_CLIENT_PORT,
            'send_target_port': int(getattr(config, 'OSC_SEND_TARGET_PORT', 9000)),
            'compat_mode': bool(getattr(config, 'OSC_COMPAT_MODE', False)),
            'compat_listen_port': int(getattr(config, 'OSC_COMPAT_LISTEN_PORT', 9001)),
            'bypass_udp_port_check': bool(getattr(config, 'BYPASS_OSC_UDP_PORT_CHECK', False)),
            'send_error_messages': bool(getattr(config, 'OSC_SEND_ERROR_MESSAGES', False)),
        },
        'local_asr': _local_asr_config_dict() if is_local_asr_ui_enabled() else None,
        'config_applied_at_ms': int(getattr(config, 'CONFIG_APPLIED_AT_MS', 0) or 0),
        'backend_boot_ms': int(getattr(config, 'BACKEND_BOOT_MS', 0) or 0),
    }


def update_config(config_data):
    """更新配置"""
    snapshot = _snapshot_runtime_config()
    try:
        api_keys = config_data.get('api_keys') or {}
        update_runtime_credentials(api_keys)

        # 更新ASR配置
        if 'asr' in config_data:
            asr = config_data['asr']
            if 'preferred_backend' in asr:
                config.PREFERRED_ASR_BACKEND = _sanitize_preferred_backend(asr['preferred_backend'])
            if 'qwen_audio3_model' in asr:
                model_name = (asr['qwen_audio3_model'] or '').strip()
                config.QWEN_AUDIO3_ASR_MODEL = (
                    model_name or 'qwen-audio-3.0-asr-flash-streaming'
                )
            if 'enable_vad' in asr:
                config.ENABLE_VAD = asr['enable_vad']
            if 'vad_threshold' in asr:
                config.VAD_THRESHOLD = float(asr['vad_threshold'])
            if 'vad_silence_duration_ms' in asr:
                config.VAD_SILENCE_DURATION_MS = int(asr['vad_silence_duration_ms'])
            if 'keepalive_interval' in asr:
                config.KEEPALIVE_INTERVAL = int(asr['keepalive_interval'])
            if 'enable_hot_words' in asr:
                config.ENABLE_HOT_WORDS = asr['enable_hot_words']
            if 'use_international_endpoint' in asr:
                config.USE_INTERNATIONAL_ENDPOINT = bool(asr['use_international_endpoint'])

        # 自定义 API（OpenAI 兼容）
        if 'custom_api' in config_data and config_data['custom_api']:
            ca = config_data['custom_api']
            if 'base_url' in ca:
                config.CUSTOM_API_BASE_URL = (ca['base_url'] or '').strip()
            if 'key' in ca:
                config.CUSTOM_API_KEY = (ca['key'] or '').strip()
            if 'model' in ca:
                config.CUSTOM_API_MODEL = (ca['model'] or '').strip()
            if 'target_language' in ca:
                config.CUSTOM_API_TARGET_LANGUAGE = (ca['target_language'] or 'ja').strip()
            if 'timeout' in ca:
                try:
                    config.CUSTOM_API_TIMEOUT = int(ca['timeout'])
                except (TypeError, ValueError):
                    config.CUSTOM_API_TIMEOUT = 30
            if 'prompt' in ca:
                config.CUSTOM_API_PROMPT = ca['prompt'] or ''
            if 'audio_format' in ca:
                af = (ca['audio_format'] or 'wav').strip().lower()
                config.CUSTOM_API_AUDIO_FORMAT = af if af in ('wav', 'mp3', 'flac') else 'wav'

        # VRChat 麦克风检测设置
        if 'vrchat_mic' in config_data and config_data['vrchat_mic']:
            vm = config_data['vrchat_mic']
            if 'detection_enabled' in vm:
                config.VRCHAT_MIC_DETECTION_ENABLED = bool(vm['detection_enabled'])
            if 'user_open' in vm:
                v = vm['user_open']
                config.VRCHAT_MIC_USER_OPEN = True if v is True else (False if v is False else None)

        if 'vad' in config_data and config_data['vad']:
            vad = config_data['vad']
            if 'enabled' in vad:
                config.VAD_ENABLED = bool(vad['enabled'])
            if 'mode' in vad:
                mode = str(vad['mode'] or 'silero')
                config.LOCAL_VAD_MODE = mode if mode in ('silero', 'energy') else 'silero'
            if 'threshold' in vad:
                config.LOCAL_VAD_THRESHOLD = float(vad['threshold'])
            if 'min_speech_duration' in vad:
                config.LOCAL_VAD_MIN_SPEECH_DURATION = float(vad['min_speech_duration'])
            if 'max_speech_duration' in vad:
                config.LOCAL_VAD_MAX_SPEECH_DURATION = float(vad['max_speech_duration'])
            if 'silence_duration' in vad:
                config.LOCAL_VAD_SILENCE_DURATION = float(vad['silence_duration'])
            if 'pre_speech_duration' in vad:
                config.LOCAL_VAD_PRE_SPEECH_DURATION = max(0.0, float(vad['pre_speech_duration']))

        # 更新翻译配置
        if 'translation' in config_data:
            trans = config_data['translation']
            if 'enable_translation' in trans:
                config.ENABLE_TRANSLATION = trans['enable_translation']
            if 'source_language' in trans:
                config.SOURCE_LANGUAGE = trans['source_language']
            if 'target_language' in trans:
                config.TARGET_LANGUAGE = trans['target_language']
            if 'secondary_target_language' in trans:
                config.SECONDARY_TARGET_LANGUAGE = trans['secondary_target_language'] if trans['secondary_target_language'] else None
            if 'fallback_language' in trans:
                config.FALLBACK_LANGUAGE = trans['fallback_language'] if trans['fallback_language'] else None
            if 'api_type' in trans:
                config.TRANSLATION_API_TYPE = trans['api_type']
                # 前端的"流式翻译模式"开关会将 api_type 设为 'openrouter_streaming'
                # 此时启用部分结果翻译（实时翻译未完成的句子）
                config.TRANSLATE_PARTIAL_RESULTS = trans['api_type'] in (
                    'openrouter_streaming',
                    'openrouter_streaming_deepl_hybrid',
                )
            if 'llm_template' in trans:
                config.LLM_TEMPLATE = (
                    trans['llm_template'] or config.DEFAULT_LLM_TEMPLATE
                ).strip() or config.DEFAULT_LLM_TEMPLATE
            if 'llm_base_url' in trans:
                config.LLM_BASE_URL = (trans['llm_base_url'] or '').strip()
            if 'llm_model' in trans:
                config.LLM_MODEL = (trans['llm_model'] or '').strip()
            if 'llm_translation_formality' in trans:
                config.LLM_TRANSLATION_FORMALITY = _sanitize_llm_translation_formality(
                    trans['llm_translation_formality']
                )
            if 'llm_translation_style' in trans:
                config.LLM_TRANSLATION_STYLE = _sanitize_llm_translation_style(
                    trans['llm_translation_style']
                )
            if 'openai_compat_extra_body_json' in trans:
                raw_extra_body = (trans['openai_compat_extra_body_json'] or '').strip()
                if raw_extra_body:
                    parsed_extra_body = json.loads(raw_extra_body)
                    if not isinstance(parsed_extra_body, dict):
                        raise ValueError('OpenAI 兼容 extra_body 必须是 JSON 对象')
                config.OPENAI_COMPAT_EXTRA_BODY_JSON = raw_extra_body
            if 'llm_parallel_fastest_mode' in trans:
                mode = trans['llm_parallel_fastest_mode']
                if mode not in ('off', 'final_only', 'all'):
                    _restore_runtime_config(snapshot)
                    return (
                        False,
                        'msg.invalidParallelFastestMode',
                        '并行双发模式必须是 off、final_only 或 all',
                    )
                config.LLM_PARALLEL_FASTEST_MODE = mode
            elif 'enable_llm_parallel_fastest' in trans:
                config.LLM_PARALLEL_FASTEST_MODE = (
                    'final_only' if trans['enable_llm_parallel_fastest'] else 'off'
                )
            if 'show_partial_results' in trans:
                config.SHOW_PARTIAL_RESULTS = trans['show_partial_results']
            if 'enable_furigana' in trans:
                config.ENABLE_JA_FURIGANA = trans['enable_furigana']
            if 'enable_pinyin' in trans:
                config.ENABLE_ZH_PINYIN = trans['enable_pinyin']
            if 'enable_arabic_reshaper' in trans:
                config.ENABLE_ARABIC_RESHAPER = bool(trans['enable_arabic_reshaper'])
            if 'remove_trailing_period' in trans:
                config.REMOVE_TRAILING_PERIOD = bool(trans['remove_trailing_period'])
            if 'text_fancy_style' in trans:
                config.TEXT_FANCY_STYLE = sanitize_text_fancy_style(trans['text_fancy_style'])
            if 'enable_reverse_translation' in trans:
                config.ENABLE_REVERSE_TRANSLATION = trans['enable_reverse_translation']
            if 'show_original_and_lang_tag' in trans:
                config.SHOW_ORIGINAL_AND_LANG_TAG = bool(trans['show_original_and_lang_tag'])
        
        # 更新麦克风控制配置
        if 'mic_control' in config_data:
            mic = config_data['mic_control']
            if 'enable_mic_control' in mic:
                # 固定开启：VRChat 麦克风没开时不跑自我识别，忽略旧页面传来的 False
                config.ENABLE_MIC_CONTROL = True
            if 'mute_delay_seconds' in mic:
                config.MUTE_DELAY_SECONDS = float(mic['mute_delay_seconds'])
            if 'enable_double_mute_clear' in mic:
                config.ENABLE_DOUBLE_MUTE_CLEAR = bool(mic['enable_double_mute_clear'])
            if 'mic_device_id' in mic:
                value = mic['mic_device_id']
                config.MIC_DEVICE_ID = str(value) if value else None
                config.MIC_DEVICE_INDEX = None
            elif 'mic_device_index' in mic:
                # 旧页面传来的设备序号：录音按设备 ID 选麦克风，序号对不上，
                # 留着会让自我模式一直打不开麦克风，所以改用系统默认
                if mic['mic_device_index'] not in (None, ''):
                    print('[Audio] 网页传来的是旧版麦克风序号，已改用系统默认麦克风，请在网页上重新选择麦克风')
                config.MIC_DEVICE_ID = None
                config.MIC_DEVICE_INDEX = None

        # 更新语言检测器配置
        if 'language_detector' in config_data:
            ld = config_data['language_detector']
            if 'type' in ld:
                config.LANGUAGE_DETECTOR_TYPE = ld['type']

        # 更新智能目标语言配置
        if 'smart_target_language' in config_data:
            st = config_data['smart_target_language']
            if 'primary_enabled' in st:
                config.SMART_TARGET_PRIMARY_ENABLED = bool(st['primary_enabled'])
                # 同步更新废弃变量以维持旧逻辑兼容
                config.SMART_TARGET_LANGUAGE_ENABLED = config.SMART_TARGET_PRIMARY_ENABLED
            if 'secondary_enabled' in st:
                config.SMART_TARGET_SECONDARY_ENABLED = bool(st['secondary_enabled'])
                # 同步更新废弃变量以维持旧逻辑兼容
                config.SMART_TARGET_LANGUAGE_COUNT = 2 if config.SMART_TARGET_SECONDARY_ENABLED else 1
                config.SMART_TARGET_LANGUAGE_ENABLED = config.SMART_TARGET_PRIMARY_ENABLED or config.SMART_TARGET_SECONDARY_ENABLED
            if 'strategy' in st:
                config.SMART_TARGET_LANGUAGE_STRATEGY = str(st['strategy'])
            if 'window_size' in st:
                config.SMART_TARGET_LANGUAGE_WINDOW_SIZE = int(st['window_size'])
            if 'exclude_self_language' in st:
                config.SMART_TARGET_LANGUAGE_EXCLUDE_SELF_LANGUAGE = bool(st['exclude_self_language'])
            if 'min_samples' in st:
                config.SMART_TARGET_LANGUAGE_MIN_SAMPLES = int(st['min_samples'])

            from streaming_translation import config_from_module
            from app_state import get_smart_selector
            get_smart_selector().reload_config(config_from_module(config))

        if 'others_translation' in config_data and config_data['others_translation']:
            ot = config_data['others_translation']
            if 'enabled' in ot:
                config.OTHERS_TRANSLATION_ENABLED = bool(ot['enabled'])
            if 'speaker_similarity' in ot:
                try:
                    config.OTHERS_SPEAKER_SIMILARITY = float(ot['speaker_similarity'])
                except (TypeError, ValueError):
                    pass
            if 'history_limit' in ot:
                try:
                    config.OTHERS_HISTORY_LIMIT = max(1, int(ot['history_limit']))
                except (TypeError, ValueError):
                    pass
            if 'asr_backend' in ot:
                config.OTHERS_ASR_BACKEND = _sanitize_others_asr_backend(ot.get('asr_backend'))
            if 'target_language' in ot:
                lang = canonicalize_output_language(ot.get('target_language')) or ''
                config.OTHERS_TARGET_LANGUAGE = lang

        if 'screen_translation' in config_data and config_data['screen_translation']:
            sc = config_data['screen_translation']
            if 'enabled' in sc:
                config.SCREEN_TRANSLATION_ENABLED = bool(sc['enabled'])
            if 'target_language' in sc:
                config.SCREEN_TARGET_LANGUAGE = (
                    canonicalize_output_language(sc.get('target_language')) or 'zh-CN'
                )
            if 'hotkey' in sc:
                config.SCREEN_HOTKEY = str(sc.get('hotkey') or '').strip()
            if 'ocr_base_url' in sc:
                config.SCREEN_OCR_BASE_URL = (
                    str(sc.get('ocr_base_url') or '').strip()
                    or 'https://dashscope.aliyuncs.com/compatible-mode/v1'
                )
            if 'ocr_model' in sc:
                config.SCREEN_OCR_MODEL = str(sc.get('ocr_model') or '').strip() or 'qwen-vl-ocr'

        if isinstance(config_data.get('display'), dict) and 'mode' in config_data['display']:
            config.DISPLAY_MODE = _normalize_display_mode(config_data['display'].get('mode'))

        if 'panel' in config_data:
            panel = config_data['panel']
            if 'width' in panel:
                config.PANEL_WIDTH = max(300, int(panel['width']))

        if 'osc' in config_data:
            osc = config_data['osc']
            if 'compat_mode' in osc:
                config.OSC_COMPAT_MODE = bool(osc['compat_mode'])
            if 'compat_listen_port' in osc:
                try:
                    p = int(osc['compat_listen_port'])
                except (TypeError, ValueError):
                    p = 9001
                config.OSC_COMPAT_LISTEN_PORT = max(1, min(65535, p))
            if 'bypass_udp_port_check' in osc:
                config.BYPASS_OSC_UDP_PORT_CHECK = bool(osc['bypass_udp_port_check'])
            if 'send_error_messages' in osc:
                config.OSC_SEND_ERROR_MESSAGES = bool(osc['send_error_messages'])
            if 'send_target_port' in osc:
                try:
                    p = int(osc['send_target_port'])
                except (TypeError, ValueError):
                    p = 9000
                config.OSC_SEND_TARGET_PORT = max(1, min(65535, p))

        if is_local_asr_ui_enabled() and 'local_asr' in config_data and config_data['local_asr']:
            local_asr = config_data['local_asr']
            if 'engine' in local_asr:
                _eng = str(local_asr['engine'] or 'sensevoice')
                if _eng not in LOCAL_ASR_ENGINES:
                    _eng = 'sensevoice'
                config.LOCAL_ASR_ENGINE = _eng
            if 'vad_mode' in local_asr and 'vad' not in config_data:
                mode = str(local_asr['vad_mode'] or 'silero')
                config.LOCAL_VAD_MODE = mode if mode in ('silero', 'energy') else 'silero'
            if 'vad_threshold' in local_asr and 'vad' not in config_data:
                config.LOCAL_VAD_THRESHOLD = float(local_asr['vad_threshold'])
            if 'min_speech_duration' in local_asr and 'vad' not in config_data:
                config.LOCAL_VAD_MIN_SPEECH_DURATION = float(local_asr['min_speech_duration'])
            if 'max_speech_duration' in local_asr and 'vad' not in config_data:
                config.LOCAL_VAD_MAX_SPEECH_DURATION = float(local_asr['max_speech_duration'])
            if 'silence_duration' in local_asr and 'vad' not in config_data:
                config.LOCAL_VAD_SILENCE_DURATION = float(local_asr['silence_duration'])
            if 'pre_speech_duration' in local_asr and 'vad' not in config_data:
                config.LOCAL_VAD_PRE_SPEECH_DURATION = max(0.0, float(local_asr['pre_speech_duration']))
            if 'incremental_asr' in local_asr:
                config.LOCAL_INCREMENTAL_ASR = bool(local_asr['incremental_asr'])
            if 'interim_interval' in local_asr:
                config.LOCAL_INTERIM_INTERVAL = float(local_asr['interim_interval'])
        
        config.bump_config_applied_at_ms()
        return True, 'msg.configUpdated', '配置已更新'
    except json.JSONDecodeError:
        _restore_runtime_config(snapshot)
        return False, 'msg.invalidExtraBodyJson', 'OpenAI 兼容 extra_body 不是合法的 JSON 对象'
    except ValueError as e:
        _restore_runtime_config(snapshot)
        if 'extra_body' in str(e):
            return False, 'msg.invalidExtraBodyJson', str(e)
        print(f'Error updating config: {e}')
        return False, 'msg.configUpdateFailed', '配置更新失败'
    except Exception as e:
        _restore_runtime_config(snapshot)
        print(f'Error updating config: {e}')
        return False, 'msg.configUpdateFailed', '配置更新失败'


def run_service_async(initial_modes=('self',)):
    """在独立线程中运行异步服务；initial_modes 是服务起来后要启动的模式。"""
    global service_loop, stop_event
    
    # 创建新的事件循环
    service_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(service_loop)

    def _lifecycle_callback(lifecycle: str, recognition_active: Optional[bool] = None):
        _set_service_status(
            lifecycle=lifecycle,
            recognition_active=recognition_active,
        )
    
    # 导入main模块并运行
    try:
        import main
        # 在 main() 函数内部会创建新的 stop_event，所以这里不需要清除
        service_loop.run_until_complete(
            main.main(
                keep_oscquery_alive=True,
                lifecycle_callback=_lifecycle_callback,
                initial_modes=tuple(initial_modes),
            )
        )
        # 运行完成后获取 main 模块中的 stop_event 引用
        stop_event = main.stop_event
    except Exception as e:
        print(f'Service error: {e}')
    finally:
        service_loop.close()
        service_loop = None
        stop_event = None
        _set_service_status(lifecycle='stopped', recognition_active=False)


def _request_translator_context_clear():
    try:
        if service_loop is not None:
            import main as main_module
            clear_contexts = getattr(
                main_module,
                'clear_translator_contexts_compat',
                lambda: None,
            )
            service_loop.call_soon_threadsafe(clear_contexts)
    except Exception as e:
        print(f'Error requesting translator context clear: {e}')


def _vrcx_context_endpoint_from_request() -> str:
    host = request.host or '127.0.0.1:5001'
    port = request.environ.get('SERVER_PORT') or '5001'
    if ':' in host:
        maybe_port = host.rsplit(':', 1)[-1]
        if maybe_port.isdigit():
            port = maybe_port
    return f'http://127.0.0.1:{port}/vrcx/context'


@app.route('/')
def index():
    """主页面"""
    import ui_locale
    # 安装版窗口里 navigator.languages 不一定是 Windows 的显示语言，直接告诉页面
    return render_template('index.html', system_ui_language=ui_locale.system_ui_language(),
                           app_version=APP_VERSION)


_show_window_handler = None


def set_show_window_handler(handler) -> None:
    """安装版（run_ui）注册：再次打开程序时把已在运行的主窗口叫出来。"""
    global _show_window_handler
    _show_window_handler = handler


@app.route('/api/show-window', methods=['POST'])
def show_window_route():
    """只能同时运行一个：第二个实例调这个接口，让这边显示主窗口后自己退出。"""
    handler = _show_window_handler
    if handler is None:
        return jsonify({'success': False, 'message': 'no window'})
    try:
        return jsonify({'success': bool(handler())})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


_quit_handler = None


def set_quit_handler(handler) -> None:
    """安装版（run_ui）注册：和托盘「退出」一样正常退出。"""
    global _quit_handler
    _quit_handler = handler


@app.route('/api/quit', methods=['POST'])
def quit_route():
    """安装程序覆盖升级前调：让程序自己退出，网页存储（Key、设置）写完再走，不用强制结束。开发版没有登记，什么都不做。"""
    if not _same_origin_request():
        return jsonify({'success': False, 'message': 'forbidden'}), 403
    handler = _quit_handler
    if handler is None:
        return jsonify({'success': False, 'message': 'not supported'})
    try:
        handler()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/ui-language', methods=['GET', 'POST'])
def ui_language_route():
    """网页确定界面语言后报上来；悬浮窗、托盘、VR 附属程序都跟着它。"""
    import ui_locale
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        ui_locale.set_ui_language(data.get('language'))
    return jsonify({'success': True, 'language': ui_locale.current_ui_language(),
                    'system': ui_locale.system_ui_language()})


@app.route('/api/config', methods=['GET'])
def get_config():
    """获取配置"""
    return jsonify(get_config_dict())


@app.route('/api/features', methods=['GET'])
def get_features():
    """获取按构建/依赖裁剪后的前端功能开关。"""
    return jsonify(_get_feature_flags())


@app.route('/api/vrcx-bridge/script', methods=['GET'])
def get_vrcx_bridge_script():
    """Return the VRCX console bridge script with the current localhost endpoint."""
    endpoint = _vrcx_context_endpoint_from_request()
    return jsonify({
        'success': True,
        'endpoint': endpoint,
        'script': build_console_script(endpoint),
        'status': get_vrcx_bridge_status(),
    })


@app.route('/api/vrcx-bridge/status', methods=['GET'])
def get_vrcx_bridge_status_api():
    """Return the latest VRCX bridge receive status without exposing the token."""
    return jsonify(get_vrcx_bridge_status())


@app.route('/vrcx/context', methods=['POST', 'OPTIONS'])
def receive_vrcx_context():
    """Receive compact VRCX context posted by the console bridge script."""
    if request.method == 'OPTIONS':
        return ('', 204)

    token = request.args.get('token', '')
    ok, reason = store_vrcx_context_payload(token, request.get_data(cache=False))
    if not ok:
        status_code = 403 if reason == 'invalid token' else 400
        return jsonify({'success': False, 'message': reason}), status_code
    return ('', 204)


@app.route('/api/local-asr/status', methods=['GET'])
def get_local_asr_status():
    """获取本地 ASR 下载/可用状态。"""
    if not is_local_asr_build_enabled():
        return jsonify({'success': False, 'message': 'Local ASR is disabled in this build'}), 404

    engines = {}
    for engine in LOCAL_ASR_ENGINES:
        try:
            engines[engine] = get_engine_status(engine)
        except Exception as e:
            engines[engine] = {
                'engine': engine,
                'display_name': LOCAL_ASR_DISPLAY_NAMES.get(engine, engine),
                'ready': False,
                'error': str(e),
            }
    return jsonify({
        'success': True,
        'ui_enabled': is_local_asr_ui_enabled(),
        'engines': engines,
        'download': _snapshot_local_asr_download_state(),
    })


@app.route('/api/local-asr/download', methods=['POST'])
def download_local_asr():
    """后台下载本地 ASR 模型与运行时。"""
    if not is_local_asr_build_enabled():
        return jsonify({'success': False, 'message': 'Local ASR is disabled in this build'}), 404

    data = request.json or {}
    engine = str(data.get('engine') or getattr(config, 'LOCAL_ASR_ENGINE', 'sensevoice'))
    if engine not in LOCAL_ASR_ENGINES:
        return jsonify({'success': False, 'message': f'Unsupported engine: {engine}'}), 400

    snapshot = _snapshot_local_asr_download_state()
    if snapshot.get('running'):
        return jsonify({'success': False, 'message': 'Another local ASR download is already running'}), 409

    worker = threading.Thread(
        target=_download_local_asr_worker,
        args=(engine,),
        daemon=True,
    )
    worker.start()
    return jsonify({'success': True, 'message': 'download started', 'engine': engine})


@app.route('/api/local-asr/download-progress', methods=['GET'])
def get_local_asr_download_progress():
    """查询本地 ASR 下载状态。"""
    if not is_local_asr_build_enabled():
        return jsonify({'success': False, 'message': 'Local ASR is disabled in this build'}), 404
    return jsonify({'success': True, **_snapshot_local_asr_download_state()})


@app.route('/api/credentials/status', methods=['GET'])
@app.route('/api/env', methods=['GET'])
def get_credential_status():
    """获取进程内凭据状态；旧 /api/env 路径仅用于向后兼容。"""
    llm_api_key_set = bool(
        str(getattr(config, 'LLM_API_KEY', '') or '').strip()
        or str(getattr(config, 'OPENAI_API_KEY', '') or '').strip()
    )
    return jsonify({
        'llm': {
            'api_key_set': llm_api_key_set,
        },
    })


@app.route('/api/config', methods=['POST'])
def update_config_api():
    """更新配置"""
    try:
        config_data = request.json
        success, message_id, message = update_config(config_data)
        if success:
            # 如果服务正在运行，通知主服务线程热应用运行时配置（无需重启识别线程）
            try:
                if _get_service_lifecycle() == 'running' and service_loop is not None:
                    import main as main_module
                    # 在主服务的事件循环线程安全地调度热更新操作
                    service_loop.call_soon_threadsafe(getattr(main_module, 'reinitialize_translator_compat', lambda: None))
            except Exception as e:
                print(f'Error notifying service to reload translator: {e}')

            return jsonify({
                'success': True,
                'message_id': message_id,
                'message': message,
                'config_applied_at_ms': int(getattr(config, 'CONFIG_APPLIED_AT_MS', 0) or 0),
            })
        else:
            status_code = 400 if message_id == 'msg.invalidExtraBodyJson' else 500
            return jsonify({'success': False, 'message_id': message_id, 'message': message}), status_code
    except Exception as e:
        print(f'Error updating config: {e}')
        return jsonify({'success': False, 'message_id': 'msg.configUpdateFailed', 'message': '配置更新失败'}), 500


@app.route('/api/ipc_status', methods=['GET'])
def get_ipc_status():
    """获取 IPC 连接状态"""
    from osc_manager import osc_manager
    ipc_client = getattr(osc_manager, '_ipc_client', None)
    
    if ipc_client is None:
        return jsonify({"connected": False, "mode": "standalone"})
        
    return jsonify({
        "connected": ipc_client.is_connected(),
        "mode": ipc_client.get_mode()
    })

@app.route('/api/status', methods=['GET'])
def get_status():
    """获取服务状态"""
    status = _snapshot_service_status()
    status['target_language'] = config.TARGET_LANGUAGE
    status['config_applied_at_ms'] = int(getattr(config, 'CONFIG_APPLIED_AT_MS', 0) or 0)
    status['backend_boot_ms'] = int(getattr(config, 'BACKEND_BOOT_MS', 0) or 0)
    status['local_asr_ui_enabled'] = is_local_asr_ui_enabled()
    status['modes'] = _snapshot_mode_status()
    status['display_mode'] = _normalize_display_mode(getattr(config, 'DISPLAY_MODE', 'desktop'))
    try:
        import vr_bridge
        status['vr_overlay'] = vr_bridge.overlay_status()
    except Exception:
        status['vr_overlay'] = {'connected': False}
    return jsonify(status)


SERVICE_MODES = ('self', 'others', 'screen')


def _snapshot_mode_status() -> dict:
    """三个模式各自的状态：stopped / starting / running / stopping / error: ..."""
    modes = {mode: 'stopped' for mode in SERVICE_MODES}
    lifecycle = _get_service_lifecycle()
    try:
        from app_state import get_state
        app_state = get_state()
        if app_state is not None:
            for mode in SERVICE_MODES:
                value = str(app_state.mode_status.get(mode, 'stopped'))
                # 服务已停：只保留出错信息（让网页能看到为什么没启动成功）
                if lifecycle != 'stopped' or value.startswith('error'):
                    modes[mode] = value
    except Exception:
        pass
    if lifecycle == 'stopped':
        return modes
    with _pending_modes_lock:
        for mode in _pending_modes:
            if modes.get(mode) == 'stopped':
                modes[mode] = 'starting'
    return modes


# 服务还在 starting 时收到的启动请求，服务起来后补上
_pending_modes: set = set()
_pending_modes_lock = threading.Lock()


def _sanitize_modes(raw) -> list:
    if isinstance(raw, str):
        raw = [raw]
    modes = []
    for item in raw or []:
        mode = str(item).strip().lower()
        if mode in SERVICE_MODES and mode not in modes:
            modes.append(mode)
    return modes


def _wait_for_lifecycle(targets: set, timeout: float) -> str:
    deadline = time.time() + timeout
    lifecycle = _get_service_lifecycle()
    while lifecycle not in targets and time.time() < deadline:
        time.sleep(0.2)
        lifecycle = _get_service_lifecycle()
    return lifecycle


def _start_service_thread(modes) -> None:
    global service_thread
    backend = _sanitize_preferred_backend(config.PREFERRED_ASR_BACKEND)
    _set_service_status(lifecycle='starting', recognition_active=False, backend=backend)
    service_thread = threading.Thread(target=run_service_async, args=(tuple(modes),), daemon=True)
    service_thread.start()


@app.route('/api/modes/start', methods=['POST'])
def start_modes_route():
    """单独启动一个或多个模式（self / others / screen）。服务没跑会自动拉起。"""
    data = request.json or {}
    modes = _sanitize_modes(data.get('modes'))
    if not modes:
        return jsonify({'success': False, 'message': 'modes is required'}), 400
    update_runtime_credentials(data.get('api_keys') or {})

    extra = {}
    if 'self' in modes:
        bypass_udp = (
            bool(data.get('bypass_osc_udp_port_check'))
            if 'bypass_osc_udp_port_check' in data
            else bool(getattr(config, 'BYPASS_OSC_UDP_PORT_CHECK', False))
        )
        if bool(getattr(config, 'OSC_COMPAT_MODE', False)):
            bypass_udp = True
        udp_status = None if bypass_udp else _osc_udp_port_status_payload()
        extra.update(udp_status or {})
        extra.update(_accelerator_window_warning_payload())
        extra.update(_vrchat_osc_warning_payload(udp_status))

    lifecycle = _get_service_lifecycle()
    if lifecycle == 'stopping':
        lifecycle = _wait_for_lifecycle({'stopped'}, 12.0)
    if lifecycle == 'stopping':
        return jsonify({'success': False, 'message': '服务正在停止，请稍后再试', 'lifecycle': lifecycle}), 409

    if lifecycle == 'stopped':
        try:
            _start_service_thread(modes)
        except Exception as e:
            _set_service_status(lifecycle='stopped', recognition_active=False)
            return jsonify({'success': False, 'message': f'启动失败: {e}'}), 500
        return jsonify({'success': True, 'lifecycle': 'starting', 'modes': modes,
                        'message': '服务启动中', **extra})

    if lifecycle == 'starting':
        with _pending_modes_lock:
            _pending_modes.update(modes)
        lifecycle = _wait_for_lifecycle({'running', 'stopped'}, 60.0)
        with _pending_modes_lock:
            _pending_modes.difference_update(modes)
        if lifecycle != 'running':
            return jsonify({'success': False, 'message': '服务没能启动，请看运行日志', 'lifecycle': lifecycle}), 409

    try:
        import main as main_module
        results = main_module.submit_mode_action('start', modes)
    except Exception as e:
        return jsonify({'success': False, 'message': f'启动失败: {e}'}), 500
    failed = {m: msg for m, (ok, msg) in results.items() if not ok}
    return jsonify({
        'success': not failed,
        'lifecycle': _get_service_lifecycle(),
        'modes': modes,
        'results': {m: {'ok': ok, 'message': msg} for m, (ok, msg) in results.items()},
        'message': '；'.join(f'{m}: {msg}' for m, msg in failed.items()) or '已启动',
        **extra,
    })


@app.route('/api/modes/stop', methods=['POST'])
def stop_modes_route():
    """单独停止一个或多个模式。最后一个模式停掉后服务自动退出。"""
    data = request.json or {}
    modes = _sanitize_modes(data.get('modes'))
    if not modes:
        return jsonify({'success': False, 'message': 'modes is required'}), 400
    with _pending_modes_lock:
        _pending_modes.difference_update(modes)
    lifecycle = _get_service_lifecycle()
    if lifecycle == 'stopped':
        return jsonify({'success': True, 'lifecycle': 'stopped', 'message': '未在运行'})
    if lifecycle == 'starting':
        lifecycle = _wait_for_lifecycle({'running', 'stopped'}, 60.0)
        if lifecycle == 'stopped':
            return jsonify({'success': True, 'lifecycle': 'stopped', 'message': '未在运行'})
    try:
        import main as main_module
        main_module.submit_mode_action('stop', modes)
    except Exception as e:
        return jsonify({'success': False, 'message': f'停止失败: {e}'}), 500
    # 如果这是最后一个模式，服务会自己退出；等一下让状态落定
    _wait_for_lifecycle({'stopped', 'running'}, 12.0)
    return jsonify({'success': True, 'lifecycle': _get_service_lifecycle(), 'message': '已停止'})

@app.route('/api/subtitles', methods=['GET'])
def get_subtitles():
    """获取最新的一条字幕状态"""
    try:
        import sys
        if 'main' in sys.modules:
            import main as m
            if hasattr(m, 'subtitles_state'):
                state = m.subtitles_state.copy()
                snapshot = _snapshot_service_status()
                state['running'] = snapshot['running']
                state['lifecycle'] = snapshot['lifecycle']
                state['show_reverse_translation'] = bool(getattr(config, 'ENABLE_REVERSE_TRANSLATION', False))
                state['target_language'] = config.TARGET_LANGUAGE
                state['others_target_language'] = (
                    canonicalize_output_language(
                        getattr(config, 'OTHERS_TARGET_LANGUAGE', None)
                    )
                    or canonicalize_output_language(getattr(config, 'TARGET_LANGUAGE', None))
                    or 'zh-CN'
                )
                state['config_applied_at_ms'] = int(getattr(config, 'CONFIG_APPLIED_AT_MS', 0) or 0)
                state['backend_boot_ms'] = int(getattr(config, 'BACKEND_BOOT_MS', 0) or 0)
                others = getattr(m, 'others_subtitles_state', None)
                if isinstance(others, dict):
                    state['others'] = {
                        'display': others.get('display', ''),
                        'ongoing': bool(others.get('ongoing', False)),
                        'lines': list(others.get('lines') or []),
                    }
                else:
                    state['others'] = {'display': '', 'ongoing': False, 'lines': []}
                # 翻译他人运行状态
                try:
                    from app_state import get_state
                    app_state = get_state()
                    if app_state is not None:
                        registry = getattr(app_state, 'others_speaker_registry', None)
                        state['others_status'] = {
                            'enabled': _snapshot_mode_status()['others'] == 'running',
                            'capture_status': getattr(app_state, 'others_capture_status', 'idle'),
                            'capture_pid': getattr(app_state, 'others_capture_pid', None),
                            'speaker_count': int(registry.count) if registry is not None else 0,
                        }
                    else:
                        state['others_status'] = {
                            'enabled': _snapshot_mode_status()['others'] == 'running',
                            'capture_status': 'idle',
                            'capture_pid': None,
                            'speaker_count': 0,
                        }
                except Exception:
                    state['others_status'] = {
                        'enabled': _snapshot_mode_status()['others'] == 'running',
                        'capture_status': 'idle',
                        'capture_pid': None,
                        'speaker_count': 0,
                    }
                state['screen'] = _screen_payload()
                return jsonify(state)
    except Exception:
        pass

    return jsonify({
        "original": "",
        "translated": "",
        "reverse_translated": "",
        "ongoing": False,
        "show_reverse_translation": bool(getattr(config, 'ENABLE_REVERSE_TRANSLATION', False)),
        "running": _snapshot_service_status()['running'],
        "lifecycle": _snapshot_service_status()['lifecycle'],
        "target_language": config.TARGET_LANGUAGE,
        "others_target_language": (
            canonicalize_output_language(getattr(config, 'OTHERS_TARGET_LANGUAGE', None))
            or canonicalize_output_language(getattr(config, 'TARGET_LANGUAGE', None))
            or 'zh-CN'
        ),
        "config_applied_at_ms": int(getattr(config, 'CONFIG_APPLIED_AT_MS', 0) or 0),
        "backend_boot_ms": int(getattr(config, 'BACKEND_BOOT_MS', 0) or 0),
        "others": {"display": "", "ongoing": False, "lines": []},
        "others_status": {
            "enabled": _snapshot_mode_status()['others'] == 'running',
            "capture_status": "idle",
            "capture_pid": None,
            "speaker_count": 0,
        },
        "screen": _screen_payload(),
    })


def _screen_payload() -> dict:
    """屏幕悬浮窗要显示的内容。"""
    payload = {
        'enabled': _snapshot_mode_status()['screen'] == 'running',
        'target_language': (
            canonicalize_output_language(getattr(config, 'SCREEN_TARGET_LANGUAGE', None))
            or 'zh-CN'
        ),
        'status': 'idle',
        'message': '',
        'items': [],
        'busy': False,
        'updated_at': 0,
        'hotkey': '',
    }
    try:
        from app_state import get_state
        app_state = get_state()
        screen = getattr(app_state, 'screen_state', None) if app_state is not None else None
        if isinstance(screen, dict):
            payload.update({
                'status': screen.get('status', 'idle'),
                'message': screen.get('message', ''),
                'items': [dict(item) for item in (screen.get('items') or [])],
                'busy': bool(screen.get('busy', False)),
                'updated_at': screen.get('updated_at', 0),
                'hotkey': screen.get('hotkey', ''),
            })
    except Exception:
        pass
    return payload


@app.route('/api/screen-translate', methods=['POST'])
def screen_translate():
    """屏幕悬浮窗按钮：截一次 VRChat 画面并翻译。"""
    if _get_service_lifecycle() != 'running' or service_loop is None:
        return jsonify({'success': False, 'message': '屏幕模式没有启动'}), 409
    try:
        from app_state import get_state
        from screen_translation import request_screen_translation
        ok, message = request_screen_translation(get_state(), service_loop)
        return jsonify({'success': ok, 'message': message}), (200 if ok else 409)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/screen-target-language', methods=['POST'])
def set_screen_target_language():
    """屏幕悬浮窗快捷切换译文语言（不影响自我 / 他人）。"""
    try:
        data = request.json or {}
        lang = canonicalize_output_language(data.get('target_language'))
        if not lang:
            return jsonify({'success': False, 'message': 'target_language is required'}), 400
        config.SCREEN_TARGET_LANGUAGE = lang
        applied_ms = config.bump_config_applied_at_ms()
        return jsonify({
            'success': True,
            'target_language': lang,
            'config_applied_at_ms': applied_ms,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/target-language', methods=['POST'])
def set_target_language():
    """快速切换目标翻译语言（小面板快捷按钮）"""
    try:
        data = request.json or {}
        lang = (data.get('target_language') or '').strip()
        if not lang:
            return jsonify({'success': False, 'message': 'target_language is required'}), 400

        config.TARGET_LANGUAGE = lang
        applied_ms = config.bump_config_applied_at_ms()

        # 如果服务正在运行，热加载翻译器
        try:
            if _get_service_lifecycle() == 'running' and service_loop is not None:
                import main as main_module
                service_loop.call_soon_threadsafe(getattr(main_module, 'reinitialize_translator_compat', lambda: None))
        except Exception as e:
            print(f'Error notifying service to reload translator: {e}')

        return jsonify({
            'success': True,
            'target_language': lang,
            'config_applied_at_ms': applied_ms,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/others-target-language', methods=['POST'])
def set_others_target_language():
    """他人悬浮窗快捷切换译文语言（不影响自我 OSC 目标语言）。"""
    try:
        data = request.json or {}
        lang = canonicalize_output_language(data.get('target_language'))
        if not lang:
            return jsonify({'success': False, 'message': 'target_language is required'}), 400

        config.OTHERS_TARGET_LANGUAGE = lang
        applied_ms = config.bump_config_applied_at_ms()
        return jsonify({
            'success': True,
            'target_language': lang,
            'config_applied_at_ms': applied_ms,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/secondary-target-language', methods=['POST'])
def set_secondary_target_language():
    """设置第二目标翻译语言"""
    try:
        data = request.json or {}
        lang = data.get('secondary_target_language')
        if lang is not None:
            lang = str(lang).strip()
            if not lang:
                lang = None
        config.SECONDARY_TARGET_LANGUAGE = lang
        applied_ms = config.bump_config_applied_at_ms()
        try:
            if _get_service_lifecycle() == 'running' and service_loop is not None:
                import main as main_module
                service_loop.call_soon_threadsafe(getattr(main_module, 'reinitialize_translator_compat', lambda: None))
        except Exception as e:
            logging.error(f'Error notifying service to reload translator: {e}')
        return jsonify({
            'success': True,
            'secondary_target_language': lang,
            'config_applied_at_ms': applied_ms,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/smart-target-status', methods=['GET'])
def get_smart_target_status():
    """获取智能目标语言选择器状态"""
    try:
        from app_state import get_smart_selector
        selector = get_smart_selector()
        enabled_primary = bool(getattr(config, 'SMART_TARGET_PRIMARY_ENABLED', False))
        enabled_secondary = bool(getattr(config, 'SMART_TARGET_SECONDARY_ENABLED', False))
        min_samples = int(getattr(config, 'SMART_TARGET_LANGUAGE_MIN_SAMPLES', 3))
        history_count = len(selector._history)
        active = enabled_primary and history_count >= min_samples
        enabled = enabled_primary or enabled_secondary
        active_languages = selector.select_target_language()
        return jsonify({
            'enabled': enabled,
            'history_count': history_count,
            'min_samples': min_samples,
            'active': active,
            'active_languages': active_languages,
            'recent_languages': list(selector._history),
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/smart-target-reset', methods=['POST'])
def reset_smart_target():
    """重置智能目标语言选择器历史记录"""
    try:
        from app_state import get_smart_selector
        get_smart_selector().clear_history()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/panel', methods=['GET'])
def panel():
    """渲染迷你面板"""
    import ui_locale
    return render_template('panel.html', ui_language=ui_locale.current_ui_language())

@app.route('/api/open-panel', methods=['POST'])
def open_panel():
    """打开迷你面板窗口"""
    try:
        # 接收大面板传来的 API Keys，仅写入进程内运行配置
        data = request.json or {}
        api_keys = data.get('api_keys', {})
        floating_mode = bool(data.get('floating_mode', False))
        quick_language_settings = data.get('quick_language_settings') or {}
        update_runtime_credentials(api_keys)

        quick_lang_defaults = ['en', 'zh-CN', 'ja', 'ko']
        raw_quick_langs = quick_language_settings.get('languages')
        panel_quick_langs = []
        for index, fallback in enumerate(quick_lang_defaults):
            value = fallback
            if isinstance(raw_quick_langs, list) and index < len(raw_quick_langs):
                candidate = str(raw_quick_langs[index]).strip()
                if candidate:
                    value = candidate
            panel_quick_langs.append(value)

        raw_quick_lang_enabled = quick_language_settings.get('enabled', True)
        if isinstance(raw_quick_lang_enabled, str):
            quick_lang_enabled = raw_quick_lang_enabled.strip().lower() not in {'', '0', 'false', 'no', 'off'}
        else:
            quick_lang_enabled = bool(raw_quick_lang_enabled)

        from panel_launcher import PANEL_WINDOW_Y, launch_panel
        role = str(data.get("role") or "self").strip().lower()
        if role not in {"self", "others", "screen"}:
            role = "self"
        window_y = PANEL_WINDOW_Y[role]
        ok = launch_panel(
            floating_mode=floating_mode,
            reverse_enabled=bool(getattr(config, 'ENABLE_REVERSE_TRANSLATION', False)) if role == "self" else False,
            panel_width=int(getattr(config, 'PANEL_WIDTH', 600)),
            quick_lang_enabled=quick_lang_enabled,
            quick_langs=panel_quick_langs,
            role=role,
            window_x=80,
            window_y=window_y,
        )
        if not ok:
            return jsonify({"success": False, "error": "启动面板进程失败"}), 500
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/panel-status', methods=['GET'])
def panel_status_route():
    """查询迷你面板进程状态。"""
    try:
        from panel_launcher import has_running_panel, panel_count
        return jsonify({'success': True, 'running': has_running_panel(), 'count': panel_count()})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/panel-close', methods=['POST'])
def panel_close_route():
    """关闭所有迷你面板进程。"""
    try:
        from panel_launcher import close_all_panels
        closed = close_all_panels()
        return jsonify({'success': True, 'closed': closed})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/audio/input-devices', methods=['GET'])
def list_input_devices():
    """列出可选的麦克风。

    选项的值是设备 ID（Windows 上是 WASAPI 端点 ID），和自我模式录音时
    按 config.MIC_DEVICE_ID 找麦克风用的是同一套，不能用 PyAudio 的序号。
    """
    selected_id = getattr(config, 'MIC_DEVICE_ID', None)
    try:
        from audio_devices import audio_devices
        with hold_portaudio("list_input_devices"):
            data = audio_devices.list_inputs()
        listed = [d for d in data.get('devices', []) if d.get('id')]
        default_id = data.get('default_id')
        if default_id is None and data.get('default_index') is not None:
            default_id = next((d['id'] for d in listed if d.get('index') == data['default_index']), None)
        devices = [{
            'id': d['id'],
            'name': d.get('name') or '',
            'max_input_channels': d.get('max_input_channels', 0),
            'label_suffix': d.get('label_suffix') or '',
        } for d in listed]
        return jsonify({
            'devices': devices,
            'default_id': default_id,
            'default_name': data.get('default_name'),
            'selected_id': selected_id,
            # 选「系统默认」时，录音会自己跟着系统默认麦克风换，不用重启服务
            'follows_default': bool(getattr(audio_devices, 'supports_hotplug', False)),
        })
    except Exception as e:
        return jsonify({'devices': [], 'default_id': None, 'default_name': None,
                        'selected_id': selected_id, 'error': str(e)}), 500


@app.route('/api/udp-port-check', methods=['GET'])
def udp_port_check():
    """独立接口：检测本机 VRChat OSC 所用 UDP 端口是否被非 VRChat 进程占用。"""
    return jsonify({'success': True, **_osc_udp_port_status_payload()})


@app.route('/api/service/start', methods=['POST'])
def start_service():
    """启动服务"""
    global service_thread
    
    current_lifecycle = _get_service_lifecycle()
    if current_lifecycle != 'stopped':
        message_id = 'msg.serviceAlreadyRunning' if current_lifecycle == 'running' else 'msg.serviceBusy'
        return jsonify({
            'success': False,
            'message_id': message_id,
            'message': f'服务当前状态为 {current_lifecycle}，无法重复启动',
            'lifecycle': current_lifecycle,
        })
        return jsonify({'success': False, 'message_id': 'msg.serviceAlreadyRunning', 'message': '服务已在运行中'})

    data = request.json or {}
    bypass_udp = (
        bool(data.get('bypass_osc_udp_port_check'))
        if 'bypass_osc_udp_port_check' in data
        else bool(getattr(config, 'BYPASS_OSC_UDP_PORT_CHECK', False))
    )
    if bool(getattr(config, 'OSC_COMPAT_MODE', False)):
        bypass_udp = True
    udp_status = None
    if not bypass_udp:
        udp_status = _osc_udp_port_status_payload()

    try:
        # 从请求中获取 API Keys
        api_keys = data.get('api_keys', {})
        
        update_runtime_credentials(api_keys)

        accelerator_warning = _accelerator_window_warning_payload()
        vrchat_osc_warning = _vrchat_osc_warning_payload(udp_status)
        backend = _sanitize_preferred_backend(config.PREFERRED_ASR_BACKEND)
        _set_service_status(
            lifecycle='starting',
            recognition_active=False,
            backend=backend,
        )
        
        service_thread = threading.Thread(target=run_service_async, daemon=True)
        service_thread.start()
        return jsonify({
            'success': True,
            'message_id': 'msg.serviceStarting',
            'lifecycle': 'starting',
            'message': '服务已启动',
            **(udp_status or {}),
            **accelerator_warning,
            **vrchat_osc_warning,
        })
    except Exception as e:
        _set_service_status(lifecycle='stopped', recognition_active=False)
        print(f'Error starting service: {e}')
        return jsonify({'success': False, 'message_id': 'msg.startFailed', 'message': '启动失败'}), 500


@app.route('/api/service/stop', methods=['POST'])
def stop_service():
    """停止服务"""
    global stop_event
    
    current_lifecycle = _get_service_lifecycle()
    if current_lifecycle == 'stopped':
        return jsonify({'success': False, 'message_id': 'msg.serviceNotRunning', 'message': '服务未运行'})
    if current_lifecycle == 'stopping':
        return jsonify({
            'success': True,
            'message_id': 'msg.serviceStopping',
            'message': '服务正在停止',
            'lifecycle': 'stopping',
        })

    try:
        # 从 main 模块获取最新的 stop_event（starting 阶段可能尚未初始化，需容错）
        import main
        _request_translator_context_clear()
        _set_service_status(lifecycle='stopping', recognition_active=False)
        current_stop_event = getattr(main, 'stop_event', None)

        if current_stop_event is not None and service_loop:
            service_loop.call_soon_threadsafe(current_stop_event.set)

        # 等待线程结束（最多10秒）
        if service_thread:
            service_thread.join(timeout=10)

        still_alive = bool(service_thread and service_thread.is_alive())
        if still_alive:
            # 线程仍存活（可能卡在网络/初始化），强制标记为 stopped
            _set_service_status(lifecycle='stopped', recognition_active=False)
            stop_event = None
            return jsonify({
                'success': True,
                'message_id': 'msg.serviceStopped',
                'message': '服务已停止',
                'lifecycle': 'stopped',
            })

        _set_service_status(lifecycle='stopped', recognition_active=False)
        stop_event = None
        return jsonify({
            'success': True,
            'message_id': 'msg.serviceStopped',
            'message': '服务已停止',
            'lifecycle': 'stopped',
        })
    except Exception as e:
        print(f'Error stopping service: {e}')
        _set_service_status(lifecycle='stopped', recognition_active=False)
        return jsonify({'success': False, 'message_id': 'msg.stopFailed', 'message': '停止失败'}), 500


@app.route('/api/service/restart', methods=['POST'])
def restart_service():
    """重启服务"""
    global service_thread, stop_event
    
    if _get_service_lifecycle() != 'running':
        return jsonify({'success': False, 'message_id': 'msg.noRestartNeeded', 'message': '服务未运行，无需重启'})

    udp_status = None
    if (
        not bool(getattr(config, 'BYPASS_OSC_UDP_PORT_CHECK', False))
        and not bool(getattr(config, 'OSC_COMPAT_MODE', False))
    ):
        udp_status = _osc_udp_port_status_payload()

    try:
        # 从 main 模块获取最新的 stop_event
        import main
        _request_translator_context_clear()
        _set_service_status(lifecycle='stopping', recognition_active=False)
        current_stop_event = main.stop_event
        
        # 先停止
        if current_stop_event and service_loop:
            service_loop.call_soon_threadsafe(current_stop_event.set)
        
        if service_thread:
            service_thread.join(timeout=10)
        
        stop_event = None
        backend = _sanitize_preferred_backend(config.PREFERRED_ASR_BACKEND)
        _set_service_status(
            lifecycle='starting',
            recognition_active=False,
            backend=backend,
        )
        
        # 再启动
        service_thread = threading.Thread(target=run_service_async, daemon=True)
        service_thread.start()

        return jsonify({
            'success': True,
            'message_id': 'msg.serviceRestarted',
            'lifecycle': 'starting',
            'message': '服务已重启',
            **(udp_status or {}),
            **_vrchat_osc_warning_payload(udp_status),
        })
    except Exception as e:
        _set_service_status(lifecycle='stopped', recognition_active=False)
        print(f'Error restarting service: {e}')
        return jsonify({'success': False, 'message_id': 'msg.restartFailed', 'message': '重启失败'}), 500


@app.route('/api/config/defaults', methods=['GET'])
def get_defaults():
    """获取默认配置"""
    defaults = config.get_default_ui_config()
    defaults['features'] = _get_feature_flags()
    defaults['local_asr'] = (
        {
            'engine': 'sensevoice',
            'incremental_asr': True,
            'interim_interval': 2.0,
        }
        if is_local_asr_ui_enabled()
        else None
    )
    return jsonify(defaults)


@app.route('/api/check-api-key', methods=['POST'])
def check_api_key():
    """检查API Key是否有效"""
    try:
        data = request.json
        api_key = data.get('api_key', '').strip()

        if not api_key:
            return jsonify({'valid': False, 'message_id': 'msg.enterDashscopeKey', 'message': '请输入 DashScope API Key'})

        # 检查API Key格式
        if not api_key.startswith('sk-'):
            return jsonify({'valid': False, 'message_id': 'msg.invalidKeyFormat', 'message': 'API Key 格式无效（应以 sk- 开头）'})

        # 检查API Key是否是占位符
        if api_key == '<your-dashscope-api-key>':
            return jsonify({'valid': False, 'message_id': 'msg.replacePlaceholder', 'message': '请替换占位符为真实的 API Key'})

        config.DASHSCOPE_API_KEY = api_key

        return jsonify({'valid': True, 'message_id': 'msg.keyFormatValid', 'message': 'API Key 格式有效'})
    except Exception as e:
        print(f'Error checking API key: {e}')
        return jsonify({'valid': False, 'message_id': 'msg.checkFailed', 'message': '检查失败'}), 500


# ============================================================================
# 自定义 API + 预设路由
# ============================================================================
@app.route('/api/presets', methods=['GET'])
def list_presets_route():
    """列出所有已保存的预设（仅名称与时间戳，不含 values）。"""
    from presets import list_presets
    return jsonify({'success': True, 'presets': list_presets()})


@app.route('/api/presets', methods=['POST'])
def save_preset_route():
    """把当前可序列化配置保存为命名预设。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        name = (body.get('name') or '').strip()
        values = body.get('values') or {}
        if not name:
            return jsonify({'success': False, 'message': '预设名不能为空'}), 400

        from presets import save_preset, snapshot_current_to_preset_dict
        # values 是从前端直接发来的字典，按白名单裁剪
        sanitized = snapshot_current_to_preset_dict(values)
        result = save_preset(name, sanitized)
        return jsonify({'success': True, **result})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/presets/<name>', methods=['GET'])
def load_preset_route(name):
    """按名称读取预设的全部字段值。"""
    from presets import load_preset
    values = load_preset(name)
    if values is None:
        return jsonify({'success': False, 'message': '预设不存在'}), 404
    return jsonify({'success': True, 'name': name, 'values': values})


@app.route('/api/presets/<name>', methods=['DELETE'])
def delete_preset_route(name):
    """按名称删除预设。"""
    from presets import delete_preset
    deleted = delete_preset(name)
    return jsonify({'success': True, 'deleted': deleted})


@app.route('/api/presets/export', methods=['GET'])
def export_presets_route():
    """导出全部预设为 JSON 数据（含完整 values），供前端下载为文件。"""
    try:
        from presets import export_presets
        data = export_presets()
        # 文件名由前端根据当前时间生成
        return jsonify({'success': True, 'data': data, 'count': len(data.get('presets') or [])})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/presets/import', methods=['POST'])
def import_presets_route():
    """从 JSON 数据导入预设，合并进本地 presets.json（同名覆盖）。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        data = body.get('data')
        overwrite = bool(body.get('overwrite', True))
        if not isinstance(data, dict):
            return jsonify({'success': False, 'message': '预设文件内容无效'}), 400

        from presets import import_presets
        result = import_presets(data, overwrite=overwrite)
        return jsonify({'success': True, **result})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/custom-api/test', methods=['POST'])
def test_custom_api_route():
    """对自定义端点做一次连通性测试（不发真实音频）。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        base_url = (body.get('base_url') or '').strip()
        api_key = (body.get('api_key') or '').strip()
        model = (body.get('model') or '').strip()
        if not base_url or not api_key or not model:
            return jsonify({'success': False, 'message': '缺少 API 地址、Key 或模型名'}), 400

        import requests
        url = base_url.rstrip('/') + '/chat/completions'
        resp = requests.post(
            url,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': 'ping'}],
                'max_tokens': 5,
            },
            timeout=10,
        )
        return jsonify({
            'success': resp.status_code < 400,
            'status_code': resp.status_code,
            'snippet': (resp.text or '')[:200],
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


# ============================================================================
# VRChat 麦克风状态
# ============================================================================
@app.route('/api/vrchat/mic-status', methods=['GET'])
def vrchat_mic_status_route():
    """获取 VRChat 麦克风状态（检测 + 运行态）。"""
    try:
        from vrchat_detector import get_mic_status
        detection_enabled = bool(getattr(config, 'VRCHAT_MIC_DETECTION_ENABLED', True))
        user_open = getattr(config, 'VRCHAT_MIC_USER_OPEN', None)
        has_override = user_open is not None

        # 服务运行中时优先读运行态
        import main as main_module
        live_open = None
        live_detail = ''
        state = main_module.get_state()
        if state is not None:
            live_open = getattr(state, 'vrchat_mic_open', None)
            live_detail = getattr(state, 'vrchat_mic_detail', '')

        from osc_manager import osc_manager as _osc_manager
        status = get_mic_status(user_assumed_open=user_open, has_user_override=has_override,
                                pushed_mute_self=_osc_manager.last_mute_value)
        data = status.to_dict()
        if live_open is not None:
            data['live_open'] = live_open
        if live_detail:
            data['live_detail'] = live_detail

        checked_at = None
        if state is not None:
            checked_at = getattr(state, 'vrchat_mic_checked_at', None)
        if checked_at is None:
            import time as _time
            checked_at = _time.time()
        data['checked_at'] = checked_at

        data['detection_enabled'] = detection_enabled
        data['user_open'] = user_open
        data['has_user_override'] = has_override
        return jsonify(data)
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/vrchat/mic-setting', methods=['POST'])
def vrchat_mic_setting_route():
    """更新 VRChat 麦克风检测设置。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        if 'detection_enabled' in body:
            config.VRCHAT_MIC_DETECTION_ENABLED = bool(body['detection_enabled'])
        if 'user_open' in body:
            v = body['user_open']
            config.VRCHAT_MIC_USER_OPEN = True if v is True else (False if v is False else None)
        config.bump_config_applied_at_ms()
        return jsonify({
            'success': True,
            'detection_enabled': bool(config.VRCHAT_MIC_DETECTION_ENABLED),
            'user_open': config.VRCHAT_MIC_USER_OPEN,
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/logs', methods=['GET'])
def get_logs_route():
    """返回进程内日志缓冲（最新在前）。"""
    try:
        from log_buffer import get_logs, install as install_log_buffer
        install_log_buffer()
        limit = request.args.get('limit', type=int)
        after_ts = request.args.get('after_ts', type=float)
        logs = get_logs(limit=limit or 500, after_ts=after_ts)
        return jsonify({'success': True, 'logs': logs})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@app.route('/api/logs/clear', methods=['POST'])
def clear_logs_route():
    """清空进程内日志缓冲。"""
    try:
        from log_buffer import clear, install as install_log_buffer
        install_log_buffer()
        clear()
        return jsonify({'success': True})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


# ===================== VR 附属程序（SteamVR 手腕显示） =====================

def _normalize_display_mode(value) -> str:
    return 'vr' if str(value or '').strip().lower() == 'vr' else 'desktop'


def _vr_payload() -> dict:
    """推给 VR 附属程序的完整状态（vr_bridge 每 150ms 取一次）。"""
    import sys as _sys
    m = _sys.modules.get('main')
    self_state = dict(getattr(m, 'subtitles_state', None) or {}) if m else {}
    others_state = dict(getattr(m, 'others_subtitles_state', None) or {}) if m else {}
    mic_open = None
    try:
        from app_state import get_state
        app_state = get_state()
        if app_state is not None:
            mic_open = getattr(app_state, 'vrchat_mic_open', None)
    except Exception:
        pass
    others_lines = []
    for item in list(others_state.get('lines') or [])[-6:]:
        others_lines.append({
            'speaker': item.get('speaker', '?'),
            'original': item.get('original', ''),
            'lang': item.get('lang', ''),
            'translated': item.get('translated', ''),
            'ts': item.get('ts'),   # VR 附属程序靠它攒历史（手腕卡片能往回翻）
        })
    return {
        'v': 1,
        'ui_language': __import__('ui_locale').current_ui_language(),
        'display_mode': _normalize_display_mode(getattr(config, 'DISPLAY_MODE', 'desktop')),
        'lifecycle': _get_service_lifecycle(),
        'modes': _snapshot_mode_status(),
        'self': {
            'original': self_state.get('original', ''),
            'translated': self_state.get('translated', ''),
            'ongoing': bool(self_state.get('ongoing', False)),
            'target_language': config.TARGET_LANGUAGE,
            'mic_open': mic_open,
        },
        'others': {
            'lines': others_lines,
            'ongoing': bool(others_state.get('ongoing', False)),
            'target_language': (
                canonicalize_output_language(getattr(config, 'OTHERS_TARGET_LANGUAGE', None))
                or canonicalize_output_language(getattr(config, 'TARGET_LANGUAGE', None))
                or 'zh-CN'
            ),
        },
        'screen': _screen_payload(),
    }


def _vr_overlay_script() -> str:
    return os.path.join(PROJECT_ROOT_FOR_VR, 'vr_overlay', 'main.py')


PROJECT_ROOT_FOR_VR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _vr_overlay_command() -> list:
    """启动附属程序的命令。安装版用 exe 旁边的 GanlanTranslateVR.exe。"""
    if getattr(sys, 'frozen', False):
        return [os.path.join(os.path.dirname(sys.executable), 'GanlanTranslateVR.exe')]
    return [_pythonw_executable(), _vr_overlay_script()]


def _pythonw_executable() -> str:
    exe = sys.executable
    candidate = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    return candidate if os.path.exists(candidate) else exe


@app.route('/api/display-mode', methods=['POST'])
def set_display_mode_route():
    """切换桌面 / VR。切到 VR 时关掉桌面悬浮窗。"""
    data = request.json or {}
    mode = _normalize_display_mode(data.get('mode'))
    config.DISPLAY_MODE = mode
    applied_ms = config.bump_config_applied_at_ms()
    closed = 0
    if mode == 'vr' and data.get('close_panels', True):
        try:
            from panel_launcher import close_all_panels
            closed = close_all_panels()
        except Exception:
            pass
    return jsonify({'success': True, 'mode': mode, 'closed_panels': closed,
                    'config_applied_at_ms': applied_ms})


@app.route('/api/vr/hello', methods=['POST'])
def vr_hello_route():
    """VR 附属程序每 2 秒报到一次，顺便拿主程序的显示方式。"""
    import vr_bridge
    vr_bridge.mark_overlay_seen(request.get_json(silent=True) or {})
    return jsonify({
        'success': True,
        'display_mode': _normalize_display_mode(getattr(config, 'DISPLAY_MODE', 'desktop')),
        'udp_port': int(getattr(config, 'VR_OVERLAY_UDP_PORT', 5012)),
    })


@app.route('/api/vr/status', methods=['GET'])
def vr_status_route():
    import vr_bridge
    status = vr_bridge.overlay_status()
    status['display_mode'] = _normalize_display_mode(getattr(config, 'DISPLAY_MODE', 'desktop'))
    try:
        if getattr(sys, 'frozen', False):
            status['openvr_installed'] = os.path.exists(_vr_overlay_command()[0])
        else:
            import importlib.util
            status['openvr_installed'] = importlib.util.find_spec('openvr') is not None
    except Exception:
        status['openvr_installed'] = False
    return jsonify({'success': True, **status})


@app.route('/api/vr/launch', methods=['POST'])
def vr_launch_route():
    """手动启动 VR 附属程序（第一次用它把自己注册进 SteamVR，之后 SteamVR 会自动启动）。"""
    import subprocess
    import vr_bridge
    if vr_bridge.overlay_status()['connected']:
        return jsonify({'success': True, 'already_running': True, 'message': 'VR 附属程序已在运行'})
    command = _vr_overlay_command()
    if not os.path.exists(command[-1]):
        return jsonify({'success': False, 'message': f'找不到 {command[-1]}'}), 500
    cwd = os.path.dirname(command[0]) if getattr(sys, 'frozen', False) else PROJECT_ROOT_FOR_VR
    try:
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command + ['--manual'], cwd=cwd, creationflags=flags, close_fds=True)
    except Exception as e:
        return jsonify({'success': False, 'message': f'启动失败: {e}'}), 500
    return jsonify({'success': True, 'message': '已启动 VR 附属程序；SteamVR 没开时它会等 SteamVR 启动'})


# ---------- 「词语设置」页 ----------

@app.route('/api/words/hot-words', methods=['GET', 'POST'])
def hot_words_route():
    """识别热词：GET 返回内置 + 自定义；POST {lang, words} 覆盖保存自定义的那份（下次启动模式时生效）。"""
    import word_settings
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            words = word_settings.save_hot_words(data.get('lang'), data.get('words'))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400
        except OSError as e:
            return jsonify({'success': False, 'message': f'保存失败: {e}'}), 500
        print(f"[词语设置] 已保存 {data.get('lang')} 自定义热词 {len(words)} 个")
        return jsonify({'success': True, 'words': words, **word_settings.get_hot_words()})
    return jsonify(word_settings.get_hot_words())


@app.route('/api/words/terms', methods=['GET', 'POST'])
def terms_route():
    """翻译术语：GET 返回各译文语言的内置 + 自定义；POST {lang, entries} 覆盖保存自定义的那份（立即生效）。"""
    import word_settings
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            entries = word_settings.save_terms(data.get('lang'), data.get('entries'))
        except ValueError as e:
            return jsonify({'success': False, 'message': str(e)}), 400
        except OSError as e:
            return jsonify({'success': False, 'message': f'保存失败: {e}'}), 500
        try:
            from terminology_manager import get_terminology_manager
            get_terminology_manager().reload()
        except Exception as e:
            print(f'[词语设置] 刷新术语缓存失败: {e}')
        print(f"[词语设置] 已保存 {data.get('lang')} 自定义术语 {len(entries)} 条")
        return jsonify({'success': True, 'entries': entries})
    return jsonify(word_settings.get_terms())


# ---------- 「详情」页 ----------

@app.route('/api/app-info', methods=['GET'])
def app_info_route():
    import platform
    import word_settings
    frozen = bool(getattr(sys, 'frozen', False))
    folders = word_settings.folder_paths()
    return jsonify({
        'version': APP_VERSION,
        'build': 'installed' if frozen else 'dev',
        'program_dir': os.path.dirname(sys.executable) if frozen else folders['data'],
        'data_dir': folders['data'],
        'folders': folders,
        'python': platform.python_version(),
        'os': f'{platform.system()} {platform.release()} ({platform.version()})',
        'uninstaller': bool(_uninstaller_path()),
    })


def _uninstaller_path():
    """安装版 exe 旁边的 Uninstall.exe（安装程序写的）；开发版没有。"""
    if not getattr(sys, 'frozen', False):
        return None
    path = os.path.join(os.path.dirname(sys.executable), 'Uninstall.exe')
    return path if os.path.isfile(path) else None


@app.route('/api/uninstall', methods=['POST'])
def uninstall_route():
    """「软件信息」页的「卸载」：打开卸载程序。确认、要不要保留数据、关掉本程序都由卸载程序自己来。"""
    if not _same_origin_request():
        return jsonify({'success': False, 'message': 'forbidden'}), 403
    path = _uninstaller_path()
    if not path:
        return jsonify({'success': False, 'message': '没有找到卸载程序'}), 404
    import subprocess
    import tempfile
    try:
        # 当前目录不放在安装目录里，不然卸载程序删不掉它
        subprocess.Popen([path], cwd=tempfile.gettempdir(), close_fds=True)
    except OSError as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    print('[卸载] 已打开卸载程序')
    return jsonify({'success': True})


@app.route('/api/open-folder', methods=['POST'])
def open_folder_route():
    """在资源管理器里打开「详情」/「词语设置」页上列出的文件夹（只认固定的几个键）。"""
    import word_settings
    key = str((request.get_json(silent=True) or {}).get('key', ''))
    path = word_settings.folder_paths().get(key)
    if not path:
        return jsonify({'success': False, 'message': '未知的文件夹'}), 400
    try:
        os.makedirs(path, exist_ok=True)
        os.startfile(path)
    except OSError as e:
        return jsonify({'success': False, 'message': f'打开失败: {e}'}), 500
    return jsonify({'success': True, 'path': path})


# ---------- 插件 ----------

def _same_origin_request() -> bool:
    """改插件的接口只接受本页面发来的请求。

    CORS 是全开的，别的网站也能往 127.0.0.1:5001 POST 表单；插件能跑代码，
    不能让随便一个网页替用户装上或打开。浏览器跨站请求一定带 Origin。
    """
    from urllib.parse import urlsplit
    host = request.host.lower()
    # 也挡住 DNS 重绑定（别的域名解析到 127.0.0.1）：Host 只认本机地址
    if urlsplit('//' + host).hostname not in ('127.0.0.1', 'localhost'):
        return False
    origin = request.headers.get('Origin')
    if not origin:
        return True
    try:
        return urlsplit(origin).netloc.lower() == host
    except ValueError:
        return False


def _plugin_error(e, status: int = 400):
    return jsonify({'success': False, 'message': str(e)}), status


@app.route('/api/plugins', methods=['GET'])
def plugins_list_route():
    from plugin_manager import plugin_manager
    return jsonify({'success': True, **plugin_manager.list_plugins()})


@app.route('/api/plugins/refresh', methods=['POST'])
def plugins_refresh_route():
    from plugin_manager import plugin_manager
    if not _same_origin_request():
        return _plugin_error('拒绝来自其他网页的请求', 403)
    plugin_manager.refresh()
    return jsonify({'success': True, **plugin_manager.list_plugins()})


@app.route('/api/plugins/import', methods=['POST'])
def plugins_import_route():
    """导入插件包：multipart 的 file 字段是 .zip；overwrite=1 时替换已装的同一个插件。"""
    from plugin_manager import MAX_PACKAGE_BYTES, PluginError, PluginExistsError, plugin_manager
    if not _same_origin_request():
        return _plugin_error('拒绝来自其他网页的请求', 403)
    if request.content_length and request.content_length > MAX_PACKAGE_BYTES + 1024 * 1024:
        return _plugin_error(f'插件包太大（超过 {MAX_PACKAGE_BYTES // 1024 // 1024} MB）', 413)
    upload = request.files.get('file')
    if upload is None:
        return _plugin_error('没有收到插件包')
    data = upload.read(MAX_PACKAGE_BYTES + 1)
    overwrite = str(request.form.get('overwrite', '')).lower() in ('1', 'true', 'yes')
    try:
        info = plugin_manager.install(data, overwrite=overwrite)
    except PluginExistsError as e:
        return jsonify({'success': False, 'exists': True, 'message': str(e),
                        'existing': e.existing, 'incoming': e.incoming}), 409
    except PluginError as e:
        return _plugin_error(e)
    except OSError as e:
        return _plugin_error(f'保存失败: {e}', 500)
    return jsonify({'success': True, 'plugin': info})


@app.route('/api/plugins/<plugin_id>/enabled', methods=['POST'])
def plugins_enable_route(plugin_id):
    from plugin_manager import PluginError, plugin_manager
    if not _same_origin_request():
        return _plugin_error('拒绝来自其他网页的请求', 403)
    enabled = (request.get_json(silent=True) or {}).get('enabled')
    try:
        info = plugin_manager.set_enabled(plugin_id, bool(enabled))
    except PluginError as e:
        return _plugin_error(e, 404)
    except OSError as e:
        return _plugin_error(f'保存失败: {e}', 500)
    return jsonify({'success': True, 'plugin': info})


@app.route('/api/plugins/<plugin_id>/settings', methods=['POST'])
def plugins_settings_route(plugin_id):
    from plugin_manager import PluginError, plugin_manager
    if not _same_origin_request():
        return _plugin_error('拒绝来自其他网页的请求', 403)
    values = (request.get_json(silent=True) or {}).get('settings')
    try:
        info = plugin_manager.save_settings(plugin_id, values)
    except PluginError as e:
        return _plugin_error(e)
    except OSError as e:
        return _plugin_error(f'保存失败: {e}', 500)
    return jsonify({'success': True, 'plugin': info})


@app.route('/api/plugins/<plugin_id>', methods=['DELETE'])
def plugins_delete_route(plugin_id):
    from plugin_manager import PluginError, plugin_manager
    if not _same_origin_request():
        return _plugin_error('拒绝来自其他网页的请求', 403)
    try:
        plugin_manager.delete(plugin_id)
    except PluginError as e:
        return _plugin_error(e)
    return jsonify({'success': True})


@app.route('/api/plugins/<plugin_id>/icon', methods=['GET'])
def plugins_icon_route(plugin_id):
    from flask import Response
    from plugin_manager import PluginError, plugin_manager
    try:
        data, mime = plugin_manager.icon(plugin_id)
    except PluginError:
        data, mime = None, None
    if not data:
        return Response(status=404)
    return Response(data, mimetype=mime, headers={'Cache-Control': 'no-store',
                                                  'X-Content-Type-Options': 'nosniff'})


def _load_plugins() -> None:
    try:
        import plugin_manager
        plugin_manager.APP_VERSION = APP_VERSION
        plugin_manager.plugin_manager.load_all()
    except Exception as e:
        print(f'[插件] 加载插件失败: {e}')


_load_plugins()


def _ensure_vr_bridge() -> None:
    try:
        import vr_bridge
        vr_bridge.start_bridge(_vr_payload)
    except Exception as e:
        print(f'[VR] 推送线程启动失败: {e}')


_ensure_vr_bridge()


def control_port_in_use(port: int = 5001) -> bool:
    """5001 上已经有服务在听（另一个甘岚翻译）。

    Windows 上 Werkzeug 会设 SO_REUSEADDR，两个进程能同时绑同一端口，请求随机落到其中一个，
    两边都会开自我模式抢麦克风、一起往聊天框发，所以启动前要自己查一下。
    """
    import socket
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == '__main__':
    if control_port_in_use(5001):
        print('端口 5001 已被占用：已经有一个甘岚翻译在运行（开发版或安装版），本次不启动。')
        sys.exit(1)
    print('='*60)
    print('VRChat 翻译器 Web UI')
    print('访问 http://localhost:5001 打开控制面板')
    print('按 Ctrl+C 退出')
    print('='*60)
    app.run(host='0.0.0.0', port=5001, debug=False)
