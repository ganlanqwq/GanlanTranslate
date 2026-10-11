"""界面语言：默认跟 Windows 显示语言；网页里选了语言就跟网页。

网页（i18n.js）每次确定语言后会 POST /api/ui-language 报给后端，悬浮窗、托盘、提示框、
VR 附属程序都从这里取。只支持 中文 / English / 日本語 / 한국어，其他系统语言用英语。
"""

from __future__ import annotations

import locale
from typing import Dict, Optional

SUPPORTED = ('zh-CN', 'en', 'ja', 'ko')
FALLBACK = 'en'

# Windows LANGID 的主语言号 → 界面语言（繁体中文也用简体界面）
_PRIMARY_LANG = {0x04: 'zh-CN', 0x09: 'en', 0x11: 'ja', 0x12: 'ko'}

_selected: Optional[str] = None


def normalize(lang: Optional[str]) -> Optional[str]:
    s = (lang or '').strip().replace('_', '-').lower()
    if not s:
        return None
    if s.startswith('zh'):
        return 'zh-CN'
    prefix = s.split('-')[0]
    return prefix if prefix in ('en', 'ja', 'ko') else None


def system_ui_language() -> str:
    """Windows 显示语言（不是区域格式）。"""
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        lang = _PRIMARY_LANG.get(langid & 0x3FF)
        if lang:
            return lang
        return FALLBACK
    except Exception:
        pass
    try:
        return normalize(locale.getlocale()[0]) or FALLBACK
    except Exception:
        return FALLBACK


def current_ui_language() -> str:
    return _selected or system_ui_language()


def set_ui_language(lang: Optional[str]) -> str:
    """网页报上来的语言；传空或不支持的值就恢复成跟系统。"""
    global _selected
    _selected = normalize(lang)
    return current_ui_language()


def pick(texts: Dict[str, str], lang: Optional[str] = None) -> str:
    """从 {语言: 文本} 里按当前界面语言取一条，缺的用英文、再用中文。"""
    lang = lang or current_ui_language()
    return texts.get(lang) or texts.get('en') or texts.get('zh-CN') or next(iter(texts.values()), '')
