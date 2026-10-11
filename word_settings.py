"""「词语设置」页：读写识别热词（hot_words/*.txt）和翻译术语（terminology/*.csv）。

内置的那份在程序资源目录里，只读展示；网页上改的是用户自己的那份（hot_words_private/、
terminology_private/，在 exe 旁边），升级覆盖安装不会丢。两边加载时本来就会合并。
"""
import csv
import io
import os
import re
from typing import Dict, List

from resource_path import ensure_dir, get_resource_path, get_user_data_path

HOT_WORDS_DIR = 'hot_words'
HOT_WORDS_PRIVATE_DIR = 'hot_words_private'
TERMINOLOGY_DIR = 'terminology'
TERMINOLOGY_PRIVATE_DIR = 'terminology_private'

# 与 HotWordsManager.ENABLED_LANGUAGES 一致：只有这几种文件会被识别加载
HOT_WORD_LANGUAGES = ('zh-cn', 'en', 'ja')
HOT_WORDS_LIMIT = 500   # HotWordsManager / Soniox 都只取前 500 个

# 按译文语言分文件（文件名 = 翻译时的目标语言代码）
TERM_LANGUAGES = ('ja', 'en', 'zh-CN', 'zh-TW', 'ko', 'ru', 'es', 'fr', 'de', 'pt', 'it', 'id')

MAX_WORD_LEN = 100
MAX_INSTRUCTION_LEN = 500


def _check_lang(lang: str, allowed) -> str:
    lang = str(lang or '').strip()
    if lang not in allowed:
        raise ValueError(f'不支持的语言: {lang}')
    return lang


def _read_words(path: str) -> List[str]:
    words: List[str] = []
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                w = line.strip()
                if w and not w.startswith('#') and w not in words:
                    words.append(w)
    except FileNotFoundError:
        pass
    return words


def _hot_words_path(private: bool, lang: str) -> str:
    if private:
        return os.path.join(get_user_data_path(HOT_WORDS_PRIVATE_DIR), f'{lang}.txt')
    return os.path.join(get_resource_path(HOT_WORDS_DIR), f'{lang}.txt')


def get_hot_words() -> Dict:
    languages = {}
    total = 0
    for lang in HOT_WORD_LANGUAGES:
        builtin = _read_words(_hot_words_path(False, lang))
        custom = _read_words(_hot_words_path(True, lang))
        total += len(builtin) + len(custom)
        languages[lang] = {'builtin': builtin, 'custom': custom}
    return {'languages': languages, 'total': total, 'limit': HOT_WORDS_LIMIT}


def save_hot_words(lang: str, words) -> List[str]:
    """覆盖保存某种语言的自定义热词，返回清理后的列表（去空行、去重、去掉内置里已有的）。"""
    lang = _check_lang(lang, HOT_WORD_LANGUAGES)
    if isinstance(words, str):
        words = words.splitlines()
    builtin = set(_read_words(_hot_words_path(False, lang)))
    cleaned: List[str] = []
    for w in words or []:
        w = re.sub(r'\s+', ' ', str(w)).strip()
        if not w or w.startswith('#') or w in builtin or w in cleaned:
            continue
        cleaned.append(w[:MAX_WORD_LEN])
    path = _hot_words_path(True, lang)
    ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(cleaned) + ('\n' if cleaned else ''))
    return cleaned


def _read_terms(path: str) -> List[Dict]:
    """同 TerminologyManager._load_csv：第一行表头，# 开头是注释。"""
    entries: List[Dict] = []
    try:
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if not row or len(row) < 2 or row[0].strip().startswith('#'):
                    continue
                keywords = [k.strip() for k in row[0].split(';') if k.strip()]
                instruction = row[1].strip()
                if keywords and instruction:
                    entries.append({'keywords': keywords, 'instruction': instruction})
    except FileNotFoundError:
        pass
    return entries


def _terms_path(private: bool, lang: str) -> str:
    if private:
        return os.path.join(get_user_data_path(TERMINOLOGY_PRIVATE_DIR), f'{lang}.csv')
    return os.path.join(get_resource_path(TERMINOLOGY_DIR), f'{lang}.csv')


def get_terms() -> Dict:
    languages = {}
    for lang in TERM_LANGUAGES:
        languages[lang] = {
            'builtin': _read_terms(_terms_path(False, lang)),
            'custom': _read_terms(_terms_path(True, lang)),
        }
    return {'languages': languages}


def save_terms(lang: str, entries) -> List[Dict]:
    """覆盖保存某种译文语言的自定义术语，返回清理后的条目。"""
    lang = _check_lang(lang, TERM_LANGUAGES)
    cleaned: List[Dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        raw = entry.get('keywords', '')
        if isinstance(raw, str):
            raw = re.split(r'[;；]', raw)
        keywords = []
        for k in raw or []:
            # 开头是 # 的整行会被当成注释跳过
            k = re.sub(r'\s+', ' ', str(k)).strip().lstrip('#').strip()[:MAX_WORD_LEN]
            if k and k not in keywords:
                keywords.append(k)
        instruction = re.sub(r'\s+', ' ', str(entry.get('instruction', ''))).strip()[:MAX_INSTRUCTION_LEN]
        if keywords and instruction:
            cleaned.append({'keywords': keywords, 'instruction': instruction})
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(['keywords', 'instruction'])
    for entry in cleaned:
        writer.writerow(['; '.join(entry['keywords']), entry['instruction']])
    path = _terms_path(True, lang)
    ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(buf.getvalue())
    return cleaned


def folder_paths() -> Dict[str, str]:
    """「详情」页可以打开的文件夹（只认这几个键，不接受任意路径）。"""
    return {
        'data': get_user_data_path(),
        'hot_words': get_user_data_path(HOT_WORDS_PRIVATE_DIR),
        'terminology': get_user_data_path(TERMINOLOGY_PRIVATE_DIR),
        'vr': get_user_data_path('vr_overlay'),
    }
