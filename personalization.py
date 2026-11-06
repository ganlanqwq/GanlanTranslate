"""个性化（1.1.2，岚：可以自定义配色、首页图片和文字，个性化配置单独存储、单独导出）。

存在数据目录（安装版 = exe 旁边）：
    personalization.json        配色、首页文字、遮罩浓度
    personalization\\hero.jpg    裁好的首页图片（网页显示的就是它）
    personalization\\hero-original.<扩展名>  上传的原图，「重新裁剪」用

和预设、浏览器里的设置都分开，升级不会被覆盖（不在安装清单里）。
导出是一个 .json：设置 + 两张图（base64），导入时原样放回来。
"""
from __future__ import annotations

import base64
import copy
import datetime as _dt
import json
import os
import re
import tempfile
import threading
from typing import Dict, Optional, Tuple

from resource_path import get_user_data_path

SETTINGS_FILE = 'personalization.json'
IMAGE_DIR = 'personalization'
HERO_FILE = 'hero.jpg'
ORIGINAL_STEM = 'hero-original'
EXPORT_KIND = 'ganlan-personalization'
SCHEMA_VERSION = 1

MAX_IMAGE_BYTES = 15 * 1024 * 1024
TEXT_LIMITS = {'eyebrow': 60, 'title1': 40, 'title2': 40, 'lede': 240}
THEME_KEYS = ('accent', 'background', 'surface', 'text', 'hero_accent', 'hero_shade')
OVERLAY_RANGE = (0, 160)    # 首页遮罩浓度，百分比；100 = 原来的样子

_HEX = re.compile(r'^#[0-9A-Fa-f]{6}$')
_PRESET = re.compile(r'^[a-z0-9-]{1,32}$')
_IMAGE_TYPES = {
    'jpg': ('image/jpeg', (b'\xff\xd8\xff',)),
    'png': ('image/png', (b'\x89PNG\r\n\x1a\n',)),
    'webp': ('image/webp', (b'RIFF',)),
    'gif': ('image/gif', (b'GIF87a', b'GIF89a')),
    'bmp': ('image/bmp', (b'BM',)),
}

DEFAULTS: Dict = {
    'version': SCHEMA_VERSION,
    'theme': {'preset': 'default', **{key: '' for key in THEME_KEYS}},
    'hero': {'eyebrow': '', 'title1': '', 'title2': '', 'lede': '', 'overlay': 100},
    'image': {'custom': False, 'rev': 0, 'original': ''},
}

_lock = threading.Lock()


class PersonalizationError(ValueError):
    pass


# ---------- 路径 ----------

def settings_path() -> str:
    return get_user_data_path(SETTINGS_FILE)


def image_dir() -> str:
    return get_user_data_path(IMAGE_DIR)


def hero_path() -> str:
    return os.path.join(image_dir(), HERO_FILE)


def original_path(settings: Optional[Dict] = None) -> Optional[str]:
    ext = ((settings or load()).get('image') or {}).get('original') or ''
    if ext not in _IMAGE_TYPES:
        return None
    path = os.path.join(image_dir(), f'{ORIGINAL_STEM}.{ext}')
    return path if os.path.isfile(path) else None


def image_mimetype(ext: str) -> str:
    return _IMAGE_TYPES.get(ext, ('application/octet-stream', ()))[0]


# ---------- 读写设置 ----------

def _clean_hex(value) -> str:
    text = str(value or '').strip()
    if text and not text.startswith('#'):
        text = '#' + text
    return text.upper() if _HEX.match(text) else ''


def sanitize(data) -> Dict:
    """只留认识的字段，颜色只认 #RRGGBB，文字截短。"""
    out = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        return out
    theme = data.get('theme') if isinstance(data.get('theme'), dict) else {}
    for key in THEME_KEYS:
        out['theme'][key] = _clean_hex(theme.get(key))
    preset = str(theme.get('preset') or '').strip()
    out['theme']['preset'] = preset if _PRESET.match(preset) else ('custom' if out['theme']['accent'] else 'default')

    hero = data.get('hero') if isinstance(data.get('hero'), dict) else {}
    for key, limit in TEXT_LIMITS.items():
        text = str(hero.get(key) or '').replace('\r', '')
        if key != 'lede':
            text = text.replace('\n', ' ')
        out['hero'][key] = text.strip()[:limit]
    try:
        overlay = int(round(float(hero.get('overlay', 100))))
    except (TypeError, ValueError):
        overlay = 100
    out['hero']['overlay'] = max(OVERLAY_RANGE[0], min(OVERLAY_RANGE[1], overlay))

    image = data.get('image') if isinstance(data.get('image'), dict) else {}
    out['image']['custom'] = bool(image.get('custom'))
    try:
        out['image']['rev'] = max(0, int(image.get('rev') or 0))
    except (TypeError, ValueError):
        out['image']['rev'] = 0
    ext = str(image.get('original') or '')
    out['image']['original'] = ext if ext in _IMAGE_TYPES else ''
    return out


def load() -> Dict:
    path = settings_path()
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    settings = sanitize(data)
    # 文件说有图但图没了（被手动删掉）：当成没有
    if settings['image']['custom'] and not os.path.isfile(hero_path()):
        settings['image']['custom'] = False
    return settings


def _write(settings: Dict) -> None:
    path = settings_path()
    folder = os.path.dirname(path) or '.'
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.personalization-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(settings, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save(data) -> Dict:
    """保存网页传来的配色和文字（图片状态不归网页改）。"""
    with _lock:
        current = load()
        incoming = sanitize(data)
        current['theme'] = incoming['theme']
        current['hero'] = incoming['hero']
        _write(current)
        return current


def reset() -> Dict:
    """全部恢复默认：删掉图片，配色、文字回到默认。"""
    with _lock:
        _remove_images()
        settings = copy.deepcopy(DEFAULTS)
        _write(settings)
        return settings


# ---------- 图片 ----------

def detect_image(data: bytes) -> Optional[str]:
    for ext, (_mime, magics) in _IMAGE_TYPES.items():
        if any(data.startswith(magic) for magic in magics):
            if ext == 'webp' and data[8:12] != b'WEBP':
                continue
            return ext
    return None


def _check_image(data: bytes, allowed: Tuple[str, ...]) -> str:
    if not data:
        raise PersonalizationError('图片是空的')
    if len(data) > MAX_IMAGE_BYTES:
        raise PersonalizationError('图片太大了（最大 15 MB）')
    ext = detect_image(data)
    if ext not in allowed:
        raise PersonalizationError('不支持这种图片格式')
    return ext


def _write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, path)


def _remove_originals() -> None:
    for ext in _IMAGE_TYPES:
        try:
            os.remove(os.path.join(image_dir(), f'{ORIGINAL_STEM}.{ext}'))
        except OSError:
            pass


def _remove_images() -> None:
    try:
        os.remove(hero_path())
    except OSError:
        pass
    _remove_originals()


def save_hero_image(cropped: bytes, original: Optional[bytes] = None) -> Dict:
    """裁好的图（网页用 canvas 出的 JPEG）+ 可选的原图。不给原图时保留之前的原图（重新裁剪的情况）。"""
    _check_image(cropped, ('jpg', 'png', 'webp'))
    original_ext = _check_image(original, tuple(_IMAGE_TYPES)) if original else None
    with _lock:
        settings = load()
        _write_bytes(hero_path(), cropped)
        if original_ext:
            _remove_originals()
            _write_bytes(os.path.join(image_dir(), f'{ORIGINAL_STEM}.{original_ext}'), original)
            settings['image']['original'] = original_ext
        elif not original_path(settings):
            settings['image']['original'] = ''
        settings['image']['custom'] = True
        settings['image']['rev'] = int(settings['image'].get('rev') or 0) + 1
        _write(settings)
        return settings


def remove_hero_image() -> Dict:
    with _lock:
        settings = load()
        _remove_images()
        settings['image'] = {'custom': False, 'rev': int(settings['image'].get('rev') or 0) + 1, 'original': ''}
        _write(settings)
        return settings


# ---------- 导出 / 导入 ----------

def _data_url(path: Optional[str], ext: str) -> str:
    if not path or not os.path.isfile(path):
        return ''
    with open(path, 'rb') as fh:
        encoded = base64.b64encode(fh.read()).decode('ascii')
    return f'data:{image_mimetype(ext)};base64,{encoded}'


def export_data(app_version: str = '') -> Dict:
    settings = load()
    images = {'hero': '', 'original': ''}
    if settings['image']['custom']:
        images['hero'] = _data_url(hero_path(), detect_image(_read_head(hero_path())) or 'jpg')
        ext = settings['image']['original']
        images['original'] = _data_url(original_path(settings), ext) if ext else ''
    return {
        'kind': EXPORT_KIND,
        'version': SCHEMA_VERSION,
        'app_version': app_version,
        'exported_at': _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'theme': settings['theme'],
        'hero': settings['hero'],
        'images': images,
    }


def _read_head(path: str) -> bytes:
    try:
        with open(path, 'rb') as fh:
            return fh.read(16)
    except OSError:
        return b''


def _decode_data_url(value) -> Optional[bytes]:
    text = str(value or '').strip()
    if not text:
        return None
    match = re.match(r'^data:[\w/+.-]+;base64,(.*)$', text, re.S)
    if not match:
        raise PersonalizationError('文件里的图片格式不对')
    try:
        data = base64.b64decode(match.group(1), validate=False)
    except (ValueError, base64.binascii.Error):
        raise PersonalizationError('文件里的图片坏了')
    return data


def import_data(data) -> Dict:
    if not isinstance(data, dict) or data.get('kind') != EXPORT_KIND:
        raise PersonalizationError('这不是甘岚翻译的个性化配置文件')
    images = data.get('images') if isinstance(data.get('images'), dict) else {}
    hero = _decode_data_url(images.get('hero'))
    original = _decode_data_url(images.get('original'))
    if hero:
        _check_image(hero, ('jpg', 'png', 'webp'))
    if original:
        _check_image(original, tuple(_IMAGE_TYPES))
    with _lock:
        incoming = sanitize({'theme': data.get('theme'), 'hero': data.get('hero')})
        settings = load()
        settings['theme'] = incoming['theme']
        settings['hero'] = incoming['hero']
        rev = int(settings['image'].get('rev') or 0) + 1
        _remove_images()
        settings['image'] = {'custom': False, 'rev': rev, 'original': ''}
        if hero:
            _write_bytes(hero_path(), hero)
            settings['image']['custom'] = True
            if original:
                ext = detect_image(original)
                _write_bytes(os.path.join(image_dir(), f'{ORIGINAL_STEM}.{ext}'), original)
                settings['image']['original'] = ext
        _write(settings)
        return settings


def export_filename() -> str:
    return 'GanlanTranslate-personalization.json'
