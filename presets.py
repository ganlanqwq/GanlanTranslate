"""预设（Preset）系统：把可序列化的运行时配置保存/读取到本地 JSON。

存放位置：``presets.json``，与可执行文件同目录。安装版与便携版都用这个。

每个预设是一组键值对，对应 WebUI 上的所有可序列化配置项。调用方
负责决定保存哪些字段（推荐白名单），这里只做透明存取与原子写。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional


# 默认放置在 exe 所在目录；打包后该目录可写。
PRESETS_FILENAME = "presets.json"

# 预设存储的 schema 版本，便于将来字段迁移时做兼容
PRESETS_SCHEMA_VERSION = 1

# 推荐放入预设的字段白名单。安全起见 API Key 等敏感字段不进预设；
# 用户每次使用预设时单独填入或从当前运行值带入。
DEFAULT_PRESET_FIELDS = [
    "PREFERRED_ASR_BACKEND",
    "QWEN_AUDIO3_ASR_MODEL",
    "SOURCE_LANGUAGE",
    "TARGET_LANGUAGE",
    "SECONDARY_TARGET_LANGUAGE",
    "FALLBACK_LANGUAGE",
    "ENABLE_TRANSLATION",
    "TRANSLATION_API_TYPE",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_TRANSLATION_FORMALITY",
    "LLM_TRANSLATION_STYLE",
    "OPENAI_COMPAT_EXTRA_BODY_JSON",
    "LLM_PARALLEL_FASTEST_MODE",
    "ENABLE_JA_FURIGANA",
    "ENABLE_ZH_PINYIN",
    "TEXT_FANCY_STYLE",
    "REMOVE_TRAILING_PERIOD",
    "SHOW_ORIGINAL_AND_LANG_TAG",
    "SMART_TARGET_PRIMARY_ENABLED",
    "SMART_TARGET_SECONDARY_ENABLED",
    "SMART_TARGET_LANGUAGE_STRATEGY",
    "SMART_TARGET_LANGUAGE_WINDOW_SIZE",
    "SMART_TARGET_LANGUAGE_EXCLUDE_SELF_LANGUAGE",
    "SMART_TARGET_LANGUAGE_FALLBACK",
    "SMART_TARGET_LANGUAGE_MIN_SAMPLES",
    "OTHERS_TRANSLATION_ENABLED",
    "OTHERS_ASR_BACKEND",
    "OTHERS_TARGET_LANGUAGE",
    "SCREEN_TRANSLATION_ENABLED",
    "SCREEN_TARGET_LANGUAGE",
    "SCREEN_HOTKEY",
    "SCREEN_OCR_BASE_URL",
    "SCREEN_OCR_MODEL",
    "DISPLAY_MODE",
    "PANEL_WIDTH",
    "PANEL_FLOATING_MODE",
    "ENABLE_QUICK_LANG_BAR",
    "QUICK_LANG_1",
    "QUICK_LANG_2",
    "QUICK_LANG_3",
    "QUICK_LANG_4",
    "ENABLE_REVERSE_TRANSLATION",
    "USE_INTERNATIONAL_ENDPOINT",
    "VAD_ENABLED",
    "LOCAL_VAD_MODE",
    "LOCAL_VAD_THRESHOLD",
    "LOCAL_VAD_MIN_SPEECH_DURATION",
    "LOCAL_VAD_MAX_SPEECH_DURATION",
    "LOCAL_VAD_SILENCE_DURATION",
    "LOCAL_VAD_PRE_SPEECH_DURATION",
    "ENABLE_ARABIC_RESHAPER",
    "OSC_COMPAT_MODE",
    "OSC_SEND_TARGET_PORT",
    "CUSTOM_API_BASE_URL",
    "CUSTOM_API_MODEL",
    "CUSTOM_API_TARGET_LANGUAGE",
    "CUSTOM_API_AUDIO_FORMAT",
    "CUSTOM_API_PROMPT",
    "CUSTOM_API_TIMEOUT",
    "VRCHAT_MIC_DETECTION_ENABLED",
    "VRCHAT_MIC_USER_OPEN",
]

# 永远是覆盖式合并，但当预设里没有而当前有值时，保留当前值
MERGE_KEEP_CURRENT_IF_MISSING = True


_presets_lock = threading.Lock()


def get_presets_path() -> str:
    """返回预设文件绝对路径。与 exe 同目录。"""
    if getattr(sys_modules_marker(), "frozen", False):  # type: ignore[name-defined]
        exe_dir = os.path.dirname(sys_modules_marker().executable)  # type: ignore[name-defined]
    else:
        exe_dir = os.getcwd()
    return os.path.join(exe_dir, PRESETS_FILENAME)


def sys_modules_marker():  # pragma: no cover - tiny shim for typing
    import sys as _s
    return _s


def _read_presets_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"schema_version": PRESETS_SCHEMA_VERSION, "presets": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema_version": PRESETS_SCHEMA_VERSION, "presets": []}
    if not isinstance(data, dict):
        return {"schema_version": PRESETS_SCHEMA_VERSION, "presets": []}
    data.setdefault("schema_version", PRESETS_SCHEMA_VERSION)
    if not isinstance(data.get("presets"), list):
        data["presets"] = []
    return data


def _write_presets_file(path: str, data: Dict[str, Any]) -> None:
    """原子写：写入临时文件 + os.replace 避免中途崩溃损坏文件。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".presets-", suffix=".json", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def list_presets() -> List[Dict[str, Any]]:
    """返回所有预设的精简列表（不含 values）。"""
    path = get_presets_path()
    with _presets_lock:
        data = _read_presets_file(path)
    out: List[Dict[str, Any]] = []
    for entry in data.get("presets", []):
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "name": entry.get("name") or "未命名",
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
            }
        )
    return out


def load_preset(name: str) -> Optional[Dict[str, Any]]:
    """按名称返回单条预设的全部字段值（返回深拷贝）。"""
    if not name:
        return None
    path = get_presets_path()
    with _presets_lock:
        data = _read_presets_file(path)
    for entry in data.get("presets", []):
        if isinstance(entry, dict) and entry.get("name") == name:
            values = entry.get("values") or {}
            if not isinstance(values, dict):
                return None
            return copy.deepcopy(values)
    return None


def save_preset(name: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """按名称保存/覆盖预设。values 必须是白名单字段子集。"""
    if not name or not isinstance(name, str):
        raise ValueError("预设名无效")
    if not isinstance(values, dict):
        raise ValueError("预设内容无效")

    sanitized: Dict[str, Any] = {
        k: v for k, v in values.items() if k in DEFAULT_PRESET_FIELDS
    }

    path = get_presets_path()
    with _presets_lock:
        data = _read_presets_file(path)
        presets = data.get("presets", [])
        now = _now_iso()
        existing_idx = None
        for idx, entry in enumerate(presets):
            if isinstance(entry, dict) and entry.get("name") == name:
                existing_idx = idx
                break
        if existing_idx is None:
            presets.append(
                {
                    "name": name,
                    "created_at": now,
                    "updated_at": now,
                    "values": sanitized,
                }
            )
        else:
            presets[existing_idx] = {
                "name": name,
                "created_at": presets[existing_idx].get("created_at", now),
                "updated_at": now,
                "values": sanitized,
            }
        data["presets"] = presets
        _write_presets_file(path, data)
    return {"name": name, "fields": list(sanitized.keys())}


def delete_preset(name: str) -> bool:
    """按名称删除预设。返回是否真的删除了一条。"""
    if not name:
        return False
    path = get_presets_path()
    with _presets_lock:
        data = _read_presets_file(path)
        before = len(data.get("presets", []))
        data["presets"] = [
            entry
            for entry in data.get("presets", [])
            if not (isinstance(entry, dict) and entry.get("name") == name)
        ]
        after = len(data["presets"])
        if before != after:
            _write_presets_file(path, data)
            return True
        return False


def export_presets() -> Dict[str, Any]:
    """导出全部预设为可移植的字典（含完整 values），用于下载为 .json 文件。"""
    path = get_presets_path()
    with _presets_lock:
        data = _read_presets_file(path)
    return copy.deepcopy(data)


def import_presets(data: Any, *, overwrite: bool = True) -> Dict[str, Any]:
    """从外部字典导入预设，合并进本地 presets.json。

    Args:
        data: 支持两种结构：
            - 完整预设库：{"schema_version": 1, "presets": [{name, values, ...}, ...]}
            - 单条预设：{"name": "...", "values": {...}} 或 {"name": "...", ...values}
        overwrite: 同名预设是否覆盖。False 时同名跳过。

    Returns:
        {"imported": int, "overwritten": int, "skipped": int}
    """
    if not isinstance(data, dict):
        raise ValueError("预设文件格式无效：顶层必须是 JSON 对象")

    presets = data.get("presets") if isinstance(data.get("presets"), list) else None
    if presets is None:
        # 单条预设：{"name", "values"} 或直接 {"name", ...fields}
        if "name" in data:
            presets = [data]
        else:
            raise ValueError("预设文件格式无效：找不到 presets 数组或预设名")

    imported = 0
    overwritten = 0
    skipped = 0

    path = get_presets_path()
    with _presets_lock:
        current = _read_presets_file(path)
        existing = current.get("presets", [])
        now = _now_iso()

        for entry in presets:
            if not isinstance(entry, dict):
                skipped += 1
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            values = entry.get("values")
            if not isinstance(values, dict):
                values = {k: v for k, v in entry.items() if k not in ("name", "created_at", "updated_at")}
            sanitized = {
                k: copy.deepcopy(v)
                for k, v in values.items()
                if k in DEFAULT_PRESET_FIELDS
            }

            replaced = False
            for idx, ex in enumerate(existing):
                if isinstance(ex, dict) and ex.get("name") == name:
                    if not overwrite:
                        replaced = True
                        skipped += 1
                        break
                    existing[idx] = {
                        "name": name,
                        "created_at": ex.get("created_at", now),
                        "updated_at": now,
                        "values": sanitized,
                    }
                    replaced = True
                    overwritten += 1
                    break
            if not replaced:
                existing.append(
                    {
                        "name": name,
                        "created_at": entry.get("created_at", now),
                        "updated_at": now,
                        "values": sanitized,
                    }
                )
                imported += 1

        current["presets"] = existing
        _write_presets_file(path, current)

    return {"imported": imported, "overwritten": overwritten, "skipped": skipped}


def snapshot_current_to_preset_dict(source: Dict[str, Any]) -> Dict[str, Any]:
    """从完整运行配置字典中裁出白名单字段，供保存预设使用。"""
    return {k: copy.deepcopy(source.get(k)) for k in DEFAULT_PRESET_FIELDS if k in source}


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"