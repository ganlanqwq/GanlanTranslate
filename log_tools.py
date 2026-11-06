"""日志导出和「分析错误」（1.1.2，岚：在软件信息处可以导出日志；运行日志处可以快速分析错误）。

- export_text()：一份可以直接发给别人的纯文本日志：开头是版本、系统、模式状态，后面是全部日志
  （旧的在前），VR 附属程序的日志只带最后一段。所有 Key 都换成 ***。
- analyze_rules()：不联网，按常见报错的样子找问题，返回问题编号（文字在网页 i18n.js 的 analyze.rule.*）。
- analyze_with_llm()：把去掉 Key 的日志发给网页上填的翻译模型，让它用界面语言写结论。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import re
import sys
from typing import Dict, Iterable, List, Optional

import config

# 日志里可能出现的 Key：sk- 开头的、Bearer 后面的、api_key=xxx 这种
_SECRET_PATTERNS = [
    (re.compile(r'\bsk-[A-Za-z0-9_\-]{6,}'), 'sk-***'),
    (re.compile(r'(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}'), r'\1 ***'),
    (re.compile(r'(?i)\b(api[_-]?key|apikey|access[_-]?token|authorization)(["\']?\s*[:=]\s*["\']?)[A-Za-z0-9._\-]{8,}'), r'\1\2***'),
]

_KEY_ATTRS = ('DASHSCOPE_API_KEY', 'LLM_API_KEY', 'CUSTOM_API_KEY', 'SCREEN_OCR_API_KEY')

# 发给模型分析的日志最多这么多字（太长又贵又慢）
LLM_LOG_CHARS = 12000
VR_LOG_TAIL_LINES = 150


def _known_secrets(extra: Iterable[str] = ()) -> List[str]:
    values = []
    for attr in _KEY_ATTRS:
        value = str(getattr(config, attr, '') or '').strip()
        values.extend(part.strip() for part in value.split(','))
    values.extend(str(v or '').strip() for v in extra)
    # 太短的不当 Key（免得把普通单词换掉）；长的先换
    return sorted({v for v in values if len(v) >= 8}, key=len, reverse=True)


def redact(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """把日志里的 Key 换成 ***。"""
    text = str(text or '')
    for secret in _known_secrets(extra_secrets):
        text = text.replace(secret, '***')
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _log_entries() -> List[dict]:
    from log_buffer import get_logs, install
    install()
    entries = get_logs(limit=0)
    entries.reverse()   # get_logs 是新的在前，导出要旧的在前
    return entries


def _format_entry(entry: dict) -> str:
    try:
        stamp = _dt.datetime.fromtimestamp(float(entry.get('t') or 0)).strftime('%H:%M:%S')
    except (TypeError, ValueError, OSError):
        stamp = '--:--:--'
    return f'[{stamp}] {entry.get("line", "")}'


def log_lines() -> List[str]:
    return [_format_entry(entry) for entry in _log_entries()]


def _vr_log_tail(path: Optional[str]) -> List[str]:
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    return lines[-VR_LOG_TAIL_LINES:]


def export_text(app_version: str, status: Optional[Dict] = None, vr_log_path: Optional[str] = None,
                api_mode: str = '', extra_secrets: Iterable[str] = ()) -> str:
    """「导出日志」的内容。status 是 /api/status 里那份（模式状态），可以不给。"""
    now = _dt.datetime.now()
    build = 'installed' if getattr(sys, 'frozen', False) else 'dev'
    head = [
        'Ganlan Translate log',
        f'Exported: {now.strftime("%Y-%m-%d %H:%M:%S")}',
        f'Version: {app_version} ({build})',
        f'OS: {platform.system()} {platform.release()} ({platform.version()})',
        f'Python: {platform.python_version()}',
    ]
    try:
        import ui_locale
        head.append(f'UI language: {ui_locale.current_ui_language()}')
    except Exception:
        pass
    head.append(f'ASR backend: {getattr(config, "PREFERRED_ASR_BACKEND", "")}'
                f' / others: {getattr(config, "OTHERS_ASR_BACKEND", "") or "follow"}')
    head.append(f'Translation: {"on" if getattr(config, "ENABLE_TRANSLATION", True) else "off"}'
                f', model: {getattr(config, "LLM_MODEL", "") or "-"}')
    if api_mode:
        head.append(f'API mode: {api_mode}')
    head.append(f'International endpoint: {bool(getattr(config, "USE_INTERNATIONAL_ENDPOINT", False))}')
    head.append(f'Display mode: {getattr(config, "DISPLAY_MODE", "desktop")}')
    if status:
        modes = status.get('modes') or {}
        if modes:
            head.append('Modes: ' + ', '.join(f'{name}={value}' for name, value in modes.items()))
        head.append(f'Service: {status.get("lifecycle") or ("running" if status.get("running") else "stopped")}')
    try:
        from plugin_manager import plugin_manager
        plugins = plugin_manager.summary()
        enabled = [p for p in plugins if p.get('enabled')]
        head.append(f'Plugins: {len(plugins)} ({len(enabled)} enabled)'
                    + (': ' + ', '.join(str(p.get('id')) for p in enabled) if enabled else ''))
    except Exception:
        pass

    body = ['', '=' * 60, 'Log', '=' * 60]
    lines = log_lines()
    body.extend(lines or ['(empty)'])
    vr_tail = _vr_log_tail(vr_log_path)
    if vr_tail:
        body.extend(['', '=' * 60, f'VR overlay log (last {len(vr_tail)} lines)', '=' * 60])
        body.extend(vr_tail)
    return redact('\r\n'.join(head + body) + '\r\n', extra_secrets)


def export_filename(now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now()
    return f'GanlanTranslate-log-{now.strftime("%Y%m%d-%H%M%S")}.txt'


# ---------- 不联网的规则 ----------

# (编号, 正则)。按顺序找，同一个编号只报一次。文字在 i18n.js 的 analyze.rule.<编号>.*
_RULES = [
    ('auth', re.compile(r'\b401\b|api[- ]?key is invalid|invalid api[- ]?key|incorrect api key|invalidapikey|'
                        r'unauthorized|authentication', re.I)),
    ('model', re.compile(r'model[_ ]not[_ ]found|model not exist|does not exist|模型不存在|no such model|'
                         r'invalid model|unknown model', re.I)),
    ('quota', re.compile(r'arrearage|insufficient[_ ]quota|quota|\b429\b|rate limit|throttl|余额|欠费|'
                         r'too many requests', re.I)),
    ('network', re.compile(r'connectionerror|connection (?:refused|reset|aborted)|max retries exceeded|'
                           r'timed out|timeout|getaddrinfo|name resolution|sslerror|ssl:|proxyerror|'
                           r'无法连接|连接超时|网络', re.I)),
    ('mic', re.compile(r'麦克风暂不可用|\[自我\] 启动失败|soundcard|mediafoundation|'
                       r'no default (?:input|microphone)|麦克风.*失败', re.I)),
    ('osc', re.compile(r'osc.*(?:未监听|没有在监听|not listening)|udp.*(?:占用|in use)|端口.*占用|'
                       r'address already in use|10048', re.I)),
    ('vrchat', re.compile(r'等待 VRChat\.exe|VRChat\.exe.*(?:未运行|没有运行|not running)|未找到 VRChat', re.I)),
    ('ocr', re.compile(r'\[Screen\] (?:识别失败|采集失败)', re.I)),
    ('plugin', re.compile(r'\[插件\].*(?:失败|出错)', re.I)),
    ('crash', re.compile(r'Traceback \(most recent call last\)|异常退出|\bException\b|\bError\b:', re.I)),
]

# 只是在说明、不是报错的行（例如「断句时识别连接已关闭，已忽略」）
_IGNORE = re.compile(r'已忽略|ignored|不算识别失败', re.I)
_ATTACHED = re.compile(r'已挂接 VRChat\.exe')


def analyze_rules(lines: List[str], keys_filled: Optional[Dict[str, bool]] = None,
                  needs: Optional[Iterable[str]] = None) -> List[dict]:
    """返回 [{"id", "line"}]，line 是第一条命中的日志（已去掉 Key）。

    keys_filled：网页上哪些 Key 填了（{"asr": bool, "llm": bool, "ocr": bool}），needs 是现在开着的功能用到哪几个。
    """
    findings: List[dict] = []
    seen = set()
    keys_filled = keys_filled or {}
    missing = [name for name in (needs or ('asr', 'llm')) if keys_filled.get(name) is False]
    if missing:
        findings.append({'id': 'no_key', 'line': '', 'missing': missing})
        seen.add('no_key')
    attached_after = False   # 「等待 VRChat.exe」之后又「已挂接 VRChat.exe」了，就不是问题
    for line in reversed(lines):   # 从新的往旧的找，报最近一次
        if _ATTACHED.search(line):
            attached_after = True
        if _IGNORE.search(line):
            continue
        for rule_id, pattern in _RULES:
            if not pattern.search(line):
                continue
            # 一行只算一个原因（排在前面的更具体），比如「[Screen] 识别失败: Model not exist」只报模型名不对
            if rule_id in seen:
                break
            if rule_id == 'vrchat' and attached_after:
                break
            if rule_id == 'crash' and len(seen - {'no_key'}) > 0:
                # 已经找到具体原因时，笼统的「出错了」就不报了
                break
            seen.add(rule_id)
            findings.append({'id': rule_id, 'line': redact(line)[-300:]})
            break
    order = {rule_id: index for index, (rule_id, _p) in enumerate(_RULES)}
    findings.sort(key=lambda item: -1 if item['id'] == 'no_key' else order.get(item['id'], 99))
    return findings


def error_excerpt(lines: List[str], limit: int = LLM_LOG_CHARS) -> str:
    """给模型看的日志：最近的日志为主，出错的行前后多带几行。"""
    if not lines:
        return ''
    marks = set()
    for index, line in enumerate(lines):
        if _IGNORE.search(line):
            continue
        if any(pattern.search(line) for _id, pattern in _RULES):
            for near in range(max(0, index - 3), min(len(lines), index + 3)):
                marks.add(near)
    tail_start = max(0, len(lines) - 80)
    chosen = sorted(marks | set(range(tail_start, len(lines))))
    out: List[str] = []
    last = -2
    for index in chosen:
        if index != last + 1 and out:
            out.append('...')
        out.append(lines[index])
        last = index
    text = redact('\n'.join(out))
    if len(text) > limit:
        text = text[-limit:]
        text = text[text.find('\n') + 1:]
    return text


# ---------- 交给模型分析 ----------

_LANGUAGE_NAMES = {'zh-CN': 'Simplified Chinese', 'en': 'English', 'ja': 'Japanese', 'ko': 'Korean'}

_SYSTEM_PROMPT = (
    "You are the troubleshooting assistant built into Ganlan Translate (甘岚翻译), a Windows desktop app "
    "for VRChat. It has three modes: Self (recognises the user's microphone with a cloud speech model and "
    "sends the translation to the VRChat chatbox over OSC 127.0.0.1:9000), Others (captures VRChat.exe "
    "audio and shows translations in an overlay) and Screen (captures the VRChat window, reads text with a "
    "vision/OCR model and translates it). API settings are on the Settings > Core page: either Bailian "
    "mode (one Alibaba Cloud DashScope API key; mainland keys must NOT tick 'International', international "
    "keys must) or OpenAI-compatible mode (base URL, key and model for speech, translation and image "
    "recognition separately). Common causes: wrong or missing key (401), wrong model name (404), no balance "
    "or rate limit, proxy/network problems, VRChat OSC turned off, microphone driver formats, VRChat not "
    "running for Others mode.\n"
    "Read the log excerpt, find the real problem(s) and answer in {language}. Be short and practical:\n"
    "1. One-line conclusion.\n2. Cause (cite the log line briefly).\n3. Steps to fix, naming the page/setting.\n"
    "If the log shows no error, say so in one sentence. Do not invent settings. Plain text with simple '-' "
    "bullets; no tables, no code blocks."
)


def analyze_with_llm(lines: List[str], findings: List[dict], *, base_url: str, api_key: str, model: str,
                     language: str, extra_body: Optional[dict] = None, timeout: float = 60.0) -> str:
    """调用 OpenAI 兼容接口分析日志，返回模型写的文字。出错抛异常。"""
    import requests
    try:
        from proxy_detector import refresh_system_proxy_env
        refresh_system_proxy_env()
    except Exception:
        pass
    excerpt = error_excerpt(lines) or '(the log is empty)'
    hints = ', '.join(item['id'] for item in findings) or 'none'
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': _SYSTEM_PROMPT.format(language=_LANGUAGE_NAMES.get(language, 'English'))},
            {'role': 'user', 'content': f'Patterns detected by simple rules: {hints}\n\nLog excerpt:\n{excerpt}'},
        ],
        'temperature': 0.2,
        'max_tokens': 900,
    }
    if isinstance(extra_body, dict):
        for key, value in extra_body.items():
            payload.setdefault(key, value)
    url = base_url.rstrip('/') + '/chat/completions'
    resp = requests.post(
        url,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        timeout=timeout,
    )
    if resp.status_code >= 400:
        snippet = redact((resp.text or '')[:300], [api_key])
        raise RuntimeError(f'HTTP {resp.status_code}: {snippet}')
    data = resp.json()
    try:
        text = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise RuntimeError('模型没有返回内容')
    text = re.sub(r'<think>.*?</think>', '', str(text or ''), flags=re.S).strip()
    return redact(text, [api_key])
