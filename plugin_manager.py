"""插件：程序目录下 plugins/ 里的每个 .zip 是一个插件（开发时也可以直接放解压后的文件夹）。

插件包里有 plugin.json 记录名字、版本、作者、设置项等信息，可选一个 Python 入口（默认 main.py）。
插件不解压，直接从 zip 里导入（zipimport），所以删除 = 删掉这个 zip，导入 = 把 zip 复制进来。

插件入口里可以定义这些函数（都是可选的）：
    setup(ctx)                       启用时调用，ctx 见 PluginContext
    teardown()                       禁用、删除、替换、退出前调用
    on_settings_changed(settings)    网页上保存了这个插件的设置
    filter_source(text)              自我翻译识别出一整句、还没翻译时，返回新原文（None = 不改，"" = 这句不要）；
                                     改过的原文会拿去翻译，所以加的内容也会跟着翻译
    filter_chatbox(text, ongoing)    发到 VRChat 聊天框之前，返回新文字（None = 不改，"" = 不发）
    on_self_result(event)            自我翻译出了一句终句
    on_others_result(event)          他人翻译出了一句
    on_screen_result(event)          翻译画面完成一次
filter_source / filter_chatbox 在识别、发送的线程里直接调用，要快；on_*_result 在插件自己的后台线程里排队调用。

启用状态和每个插件的设置存在 plugins/_config.json；插件自己要写文件用 ctx.data_dir（plugins/_data/<id>）。
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import zipfile
import zipimport
from typing import Any, Callable, Dict, List, Optional

from resource_path import get_user_data_path

PLUGINS_DIR = 'plugins'
MANIFEST_NAME = 'plugin.json'
CONFIG_NAME = '_config.json'
DATA_DIR_NAME = '_data'
DEFAULT_ENTRY = 'main.py'

MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_ICON_BYTES = 512 * 1024
ICON_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
              '.webp': 'image/webp', '.gif': 'image/gif'}

ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
FIELD_TYPES = ('text', 'textarea', 'password', 'number', 'toggle', 'select')
TEXT_LIMITS = {'text': 2000, 'password': 2000, 'textarea': 20000}
EVENTS = ('self_result', 'others_result', 'screen_result')

# 一个插件的钩子连续出错这么多次就不再打印（免得刷屏），下次启用时清零
ERROR_LOG_LIMIT = 5
SLOW_FILTER_SECONDS = 0.2


class PluginError(Exception):
    """插件包或操作有问题，消息直接显示在网页上。"""


class PluginExistsError(PluginError):
    def __init__(self, message: str, existing: dict, incoming: dict):
        super().__init__(message)
        self.existing = existing
        self.incoming = incoming


# ---------------------------------------------------------------------------
# 版本号、plugin.json 校验
# ---------------------------------------------------------------------------

def _version_tuple(value: str) -> tuple:
    parts = []
    for piece in re.split(r'[.\-+]', str(value or '').strip()):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts) or (0,)


# 由 ui/app.py 启动时填进来（这里不能反过来 import ui.app，会把整个网页后端再加载一遍）
APP_VERSION = '0'


def _app_version() -> str:
    return APP_VERSION


def _text(value, limit: int = 500) -> Any:
    """名字、说明这类文字：字符串，或 {语言代码: 字符串}。"""
    if isinstance(value, str):
        return value.strip()[:limit]
    if isinstance(value, dict):
        out = {}
        for lang, text in value.items():
            if isinstance(lang, str) and isinstance(text, str) and text.strip():
                out[lang.strip()[:16]] = text.strip()[:limit]
        return out
    return ''


def _has_text(value) -> bool:
    return bool(value) and (isinstance(value, str) or any(value.values()))


def _clean_field(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise PluginError(f'settings 第 {index + 1} 项不是对象')
    key = str(raw.get('key') or '')
    if not KEY_RE.match(key):
        raise PluginError(f'settings 第 {index + 1} 项的 key 只能用字母、数字、下划线：{key!r}')
    ftype = str(raw.get('type') or 'text')
    if ftype not in FIELD_TYPES:
        raise PluginError(f'设置项 {key} 的 type 不支持：{ftype}（可用 {", ".join(FIELD_TYPES)}）')
    field = {
        'key': key,
        'type': ftype,
        'label': _text(raw.get('label')) or key,
        'description': _text(raw.get('description'), 1000),
    }
    if ftype == 'number':
        for name in ('min', 'max', 'step'):
            if isinstance(raw.get(name), (int, float)) and not isinstance(raw.get(name), bool):
                field[name] = raw[name]
    elif ftype == 'select':
        options = []
        for opt in raw.get('options') or []:
            if isinstance(opt, dict) and 'value' in opt:
                options.append({'value': str(opt['value']), 'label': _text(opt.get('label')) or str(opt['value'])})
            elif isinstance(opt, (str, int, float)) and not isinstance(opt, bool):
                options.append({'value': str(opt), 'label': str(opt)})
        if not options:
            raise PluginError(f'设置项 {key} 是 select，但没有 options')
        field['options'] = options
    elif ftype in TEXT_LIMITS:
        limit = raw.get('max_length')
        field['max_length'] = int(limit) if isinstance(limit, int) and 0 < limit <= TEXT_LIMITS[ftype] else TEXT_LIMITS[ftype]
    field['default'] = coerce_value(field, raw.get('default'), strict=False)
    return field


def coerce_value(field: dict, value, strict: bool = True):
    """把一个设置值变成字段要的类型；strict=False 时不合法就用默认值（读旧配置、读 default 用）。"""
    ftype = field['type']
    fallback = field.get('default')

    def bad(message):
        if strict:
            raise PluginError(message)
        return fallback if fallback is not None else _empty(ftype, field)

    if value is None:
        return bad(f'「{field["key"]}」没有填') if strict else (fallback if fallback is not None else _empty(ftype, field))
    if ftype == 'toggle':
        if isinstance(value, bool):
            return value
        return bad(f'「{field["key"]}」应该是开或关')
    if ftype == 'number':
        if isinstance(value, bool):
            return bad(f'「{field["key"]}」应该是数字')
        try:
            number = float(value)
        except (TypeError, ValueError):
            return bad(f'「{field["key"]}」应该是数字')
        if number != number or number in (float('inf'), float('-inf')):
            return bad(f'「{field["key"]}」应该是数字')
        if 'min' in field and number < field['min']:
            return bad(f'「{field["key"]}」不能小于 {field["min"]}')
        if 'max' in field and number > field['max']:
            return bad(f'「{field["key"]}」不能大于 {field["max"]}')
        return int(number) if number.is_integer() else number
    if ftype == 'select':
        value = str(value)
        if value not in [o['value'] for o in field['options']]:
            return bad(f'「{field["key"]}」不是可选的值：{value}')
        return value
    if not isinstance(value, str):
        return bad(f'「{field["key"]}」应该是文字')
    if len(value) > field.get('max_length', TEXT_LIMITS.get(ftype, 2000)):
        return bad(f'「{field["key"]}」太长了（最多 {field.get("max_length")} 字）')
    return value


def _empty(ftype: str, field: dict):
    if ftype == 'toggle':
        return False
    if ftype == 'number':
        return field.get('min', 0)
    if ftype == 'select':
        return field['options'][0]['value']
    return ''


def parse_manifest(raw_bytes: bytes) -> dict:
    """读 plugin.json，返回整理过的信息；不合格就抛 PluginError。"""
    try:
        data = json.loads(raw_bytes.decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PluginError(f'plugin.json 不是有效的 JSON：{exc}') from exc
    if not isinstance(data, dict):
        raise PluginError('plugin.json 最外层应该是一个对象')

    plugin_id = str(data.get('id') or '').strip()
    if not ID_RE.match(plugin_id):
        raise PluginError('plugin.json 的 id 只能用字母、数字和 . _ -，且以字母或数字开头（最多 64 个字符）')
    name = _text(data.get('name'), 100)
    if not _has_text(name):
        raise PluginError('plugin.json 缺少 name')

    entry = data.get('entry', None)
    if entry is not None:
        entry = str(entry).replace('\\', '/').strip('/')
        if not entry.endswith('.py') or '..' in entry.split('/') or entry.startswith('.'):
            raise PluginError('plugin.json 的 entry 应该是插件里的一个 .py 文件')

    homepage = str(data.get('homepage') or '').strip()
    if homepage and not re.match(r'^https?://', homepage, re.I):
        homepage = ''

    icon = str(data.get('icon') or '').replace('\\', '/').strip('/')
    if icon and (os.path.splitext(icon)[1].lower() not in ICON_TYPES or '..' in icon.split('/')):
        icon = ''

    fields = []
    seen = set()
    for index, raw in enumerate(data.get('settings') or []):
        field = _clean_field(raw, index)
        if field['key'] in seen:
            raise PluginError(f'设置项 key 重复：{field["key"]}')
        seen.add(field['key'])
        fields.append(field)

    return {
        'id': plugin_id,
        'name': name,
        'version': str(data.get('version') or '0.0.0').strip()[:32],
        'author': _text(data.get('author'), 100),
        'description': _text(data.get('description'), 2000),
        'homepage': homepage[:300],
        'entry': entry,
        'icon': icon,
        'min_app_version': str(data.get('min_app_version') or '').strip()[:32],
        'settings': fields,
    }


def display_text(value, lang: str = 'zh-CN') -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value:
        for key in (lang, lang.split('-')[0], 'zh-CN', 'zh', 'en'):
            if value.get(key):
                return value[key]
        return next(iter(value.values()))
    return ''


# ---------------------------------------------------------------------------
# 插件包（zip 或文件夹）
# ---------------------------------------------------------------------------

class Package:
    """一个插件在磁盘上的样子：zip 文件，或者开发时直接放的文件夹。"""

    def __init__(self, path: str):
        self.path = path
        self.is_zip = os.path.isfile(path)
        self.prefix = ''  # zip 里 plugin.json 所在的子目录（打包时多包了一层文件夹）

    def _zip(self) -> zipfile.ZipFile:
        return zipfile.ZipFile(self.path)

    def read_manifest(self) -> dict:
        if self.is_zip:
            with self._zip() as zf:
                self.prefix = find_manifest_prefix(zf)
                return parse_manifest(zf.read(self.prefix + MANIFEST_NAME))
        with open(os.path.join(self.path, MANIFEST_NAME), 'rb') as f:
            return parse_manifest(f.read())

    def has_file(self, rel: str) -> bool:
        if self.is_zip:
            with self._zip() as zf:
                return (self.prefix + rel) in zf.NameToInfo
        return os.path.isfile(os.path.join(self.path, *rel.split('/')))

    def read_file(self, rel: str, limit: int) -> Optional[bytes]:
        try:
            if self.is_zip:
                with self._zip() as zf:
                    info = zf.getinfo(self.prefix + rel)
                    if info.file_size > limit:
                        return None
                    return zf.read(info)
            full = os.path.join(self.path, *rel.split('/'))
            if os.path.getsize(full) > limit:
                return None
            with open(full, 'rb') as f:
                return f.read()
        except (KeyError, OSError, zipfile.BadZipFile):
            return None

    @property
    def import_root(self) -> str:
        """导入插件代码时用的路径：zip 的话是「zip 路径\\子目录」，zipimport 认这种写法。"""
        if self.is_zip:
            return os.path.join(self.path, *self.prefix.rstrip('/').split('/')) if self.prefix else self.path
        return self.path


def find_manifest_prefix(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if not n.startswith('__MACOSX/')]
    if MANIFEST_NAME in names:
        return ''
    # 打包时多包了一层文件夹：只认唯一的那一层
    candidates = [n[:-len(MANIFEST_NAME)] for n in names
                  if n.endswith('/' + MANIFEST_NAME) and n.count('/') == 1]
    if len(candidates) == 1:
        return candidates[0]
    raise PluginError('压缩包里没有 plugin.json（应该放在压缩包根目录）')


def inspect_zip(data: bytes) -> dict:
    """检查上传的插件包，返回 manifest。"""
    if len(data) > MAX_PACKAGE_BYTES:
        raise PluginError(f'插件包太大（超过 {MAX_PACKAGE_BYTES // 1024 // 1024} MB）')
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise PluginError('不是有效的 zip 压缩包') from exc
    with zf:
        if any(info.flag_bits & 0x1 for info in zf.infolist()):
            raise PluginError('压缩包加了密码，插件包不能加密')
        prefix = find_manifest_prefix(zf)
        manifest = parse_manifest(zf.read(prefix + MANIFEST_NAME))
        entry = manifest['entry'] or (DEFAULT_ENTRY if (prefix + DEFAULT_ENTRY) in zf.NameToInfo else None)
        if manifest['entry'] and (prefix + manifest['entry']) not in zf.NameToInfo:
            raise PluginError(f'plugin.json 里写的入口 {manifest["entry"]} 不在压缩包里')
        if entry:
            try:
                compile(zf.read(prefix + entry).decode('utf-8-sig'), entry, 'exec')
            except (SyntaxError, UnicodeDecodeError) as exc:
                raise PluginError(f'入口 {entry} 有语法错误：{exc}') from exc
    return manifest


# ---------------------------------------------------------------------------
# 给插件用的上下文
# ---------------------------------------------------------------------------

class PluginContext:
    """传给插件 setup(ctx) 的对象。"""

    def __init__(self, manager: 'PluginManager', plugin: 'Plugin'):
        self._manager = manager
        self._plugin = plugin
        self.id = plugin.id
        self.app_version = _app_version()

    @property
    def name(self) -> str:
        return display_text(self._plugin.manifest['name'])

    @property
    def settings(self) -> dict:
        """当前设置（副本）。"""
        return dict(self._plugin.settings)

    @property
    def data_dir(self) -> str:
        """插件自己写文件的文件夹（plugins/_data/<id>），第一次用时创建；删除插件时一起删。"""
        path = os.path.join(self._manager.root, DATA_DIR_NAME, self.id)
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def ui_language(self) -> str:
        try:
            import ui_locale
            return ui_locale.current_ui_language()
        except Exception:
            return 'zh-CN'

    def log(self, *parts) -> None:
        """写进网页「运行日志」。"""
        print(f'[插件:{self.name}] ' + ' '.join(str(p) for p in parts))

    def send_chatbox(self, text: str) -> bool:
        """往 VRChat 聊天框发一句（不经过各插件的 filter_chatbox）。"""
        text = str(text or '').strip()
        if not text:
            return False
        try:
            from osc_manager import osc_manager
            osc_manager.send_text_sync(text, False, apply_plugins=False)
            return True
        except Exception as exc:
            self.log(f'发送聊天框失败：{exc}')
            return False


# ---------------------------------------------------------------------------
# 管理
# ---------------------------------------------------------------------------

class Plugin:
    def __init__(self, package: Package, manifest: dict):
        self.package = package
        self.manifest = manifest
        self.id = manifest['id']
        self.enabled = True
        self.settings: Dict[str, Any] = {}
        self.module = None
        self.module_name = ''
        self.error = ''
        self.hook_errors = 0
        self.entry = manifest['entry'] or (DEFAULT_ENTRY if package.has_file(DEFAULT_ENTRY) else None)

    @property
    def running(self) -> bool:
        return self.module is not None

    def hook(self, name: str) -> Optional[Callable]:
        fn = getattr(self.module, name, None) if self.module is not None else None
        return fn if callable(fn) else None


class PluginManager:
    def __init__(self, root: Optional[str] = None):
        self.root = root or get_user_data_path(PLUGINS_DIR)
        self._lock = threading.RLock()
        self._plugins: Dict[str, Plugin] = {}
        self._broken: List[dict] = []   # 读不出 plugin.json 的包，网页上也要显示出来
        self._config: Dict[str, dict] = {}
        self._events: 'queue.Queue' = queue.Queue(maxsize=500)
        self._worker: Optional[threading.Thread] = None
        self._loaded = False
        self._seq = 0

    # ---- 配置文件 ----

    @property
    def config_path(self) -> str:
        return os.path.join(self.root, CONFIG_NAME)

    def _read_config(self) -> None:
        try:
            with open(self.config_path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            plugins = data.get('plugins') if isinstance(data, dict) else None
            self._config = {k: v for k, v in (plugins or {}).items() if isinstance(v, dict)}
        except FileNotFoundError:
            self._config = {}
        except (OSError, ValueError) as exc:
            print(f'[插件] 读取 {CONFIG_NAME} 失败，按默认设置处理：{exc}')
            self._config = {}

    def _write_config(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        tmp = self.config_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'plugins': self._config}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.config_path)

    def _settings_from_config(self, plugin: Plugin) -> Dict[str, Any]:
        saved = (self._config.get(plugin.id) or {}).get('settings') or {}
        values = {}
        for field in plugin.manifest['settings']:
            values[field['key']] = coerce_value(field, saved.get(field['key'], field['default']), strict=False)
        return values

    # ---- 扫描 ----

    def _scan(self) -> Dict[str, Plugin]:
        os.makedirs(self.root, exist_ok=True)
        found: Dict[str, Plugin] = {}
        self._broken = []
        for name in sorted(os.listdir(self.root), key=str.lower):
            path = os.path.join(self.root, name)
            if name.startswith(('.', '_')):
                continue
            if os.path.isdir(path):
                if not os.path.isfile(os.path.join(path, MANIFEST_NAME)):
                    continue
            elif not name.lower().endswith('.zip'):
                continue
            package = Package(path)
            try:
                manifest = package.read_manifest()
            except (PluginError, OSError, zipfile.BadZipFile, KeyError) as exc:
                self._broken.append({'file': name, 'error': str(exc)})
                continue
            if manifest['id'] in found:
                other = os.path.basename(found[manifest['id']].package.path)
                self._broken.append({'file': name, 'error': f'和 {other} 的 id 重复（{manifest["id"]}），没有加载'})
                continue
            found[manifest['id']] = Plugin(package, manifest)
        return found

    def load_all(self) -> None:
        """启动时调用：扫描 plugins/，加载启用的插件。"""
        with self._lock:
            self._read_config()
            self._clean_leftovers()
            self._plugins = self._scan()
            self._loaded = True
            for plugin in self._plugins.values():
                self._apply_config(plugin)
                if plugin.enabled:
                    self._start(plugin)
            count = sum(1 for p in self._plugins.values() if p.running)
            if self._plugins:
                print(f'[插件] 找到 {len(self._plugins)} 个插件，已启用 {count} 个')

    def refresh(self) -> None:
        """重新扫描文件夹（手动往里放了 zip / 删掉了 zip 之后）。没变的插件不重新加载。"""
        with self._lock:
            self._read_config()
            fresh = self._scan()
            for plugin_id, old in list(self._plugins.items()):
                new = fresh.get(plugin_id)
                if new is None or not _same_package(old, new):
                    self._stop(old)
                    self._plugins.pop(plugin_id, None)
            for plugin_id, new in fresh.items():
                if plugin_id in self._plugins:
                    continue
                self._plugins[plugin_id] = new
                self._apply_config(new)
                if new.enabled:
                    self._start(new)

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load_all()

    def _apply_config(self, plugin: Plugin) -> None:
        entry = self._config.get(plugin.id) or {}
        plugin.enabled = bool(entry.get('enabled', True))
        plugin.settings = self._settings_from_config(plugin)

    def _clean_leftovers(self) -> None:
        try:
            for name in os.listdir(self.root):
                if name.startswith('.') and name.endswith('.part'):
                    os.remove(os.path.join(self.root, name))
        except OSError:
            pass

    # ---- 加载 / 卸载代码 ----

    def _start(self, plugin: Plugin) -> None:
        plugin.error = ''
        plugin.hook_errors = 0
        if not plugin.entry:
            return
        need = plugin.manifest['min_app_version']
        if need and _version_tuple(_app_version()) < _version_tuple(need):
            plugin.error = f'需要甘岚翻译 {need} 或更新的版本'
            return
        self._seq += 1
        safe = re.sub(r'[^0-9A-Za-z_]', '_', plugin.id)
        package_name = f'ganlan_plugin_{safe}_{self._seq}'
        root = plugin.package.import_root
        _forget_import_caches(plugin.package.path)
        try:
            # 先建一个空的包，__path__ 指向插件根目录，入口按「包.入口」导入：
            # 入口里可以 from . import 别的文件，不同插件的同名文件也不会撞
            spec = importlib.util.spec_from_loader(package_name, loader=None, is_package=True)
            package = importlib.util.module_from_spec(spec)
            package.__path__ = [root]
            sys.modules[package_name] = package
            module_path = plugin.entry[:-3].replace('/', '.')
            module = importlib.import_module(f'{package_name}.{module_path}')
            plugin.module_name = package_name
            plugin.module = module
            setup = plugin.hook('setup')
            if setup:
                setup(PluginContext(self, plugin))
        except BaseException as exc:  # 插件里 sys.exit() 之类也不能把程序带走
            plugin.error = _short_error(exc)
            print(f'[插件] {display_text(plugin.manifest["name"])} 加载失败：{plugin.error}')
            print(traceback.format_exc().rstrip())
            self._drop_module(plugin)
            return
        self._ensure_worker()
        print(f'[插件] 已启用 {display_text(plugin.manifest["name"])} v{plugin.manifest["version"]}')

    def _stop(self, plugin: Plugin) -> None:
        if plugin.module is None:
            return
        teardown = plugin.hook('teardown')
        if teardown:
            try:
                teardown()
            except BaseException as exc:
                print(f'[插件] {display_text(plugin.manifest["name"])} 停止时出错：{_short_error(exc)}')
        self._drop_module(plugin)

    def _drop_module(self, plugin: Plugin) -> None:
        plugin.module = None
        name = plugin.module_name
        if name:
            for key in [k for k in sys.modules if k == name or k.startswith(name + '.')]:
                sys.modules.pop(key, None)
        plugin.module_name = ''
        _forget_import_caches(plugin.package.path)

    def shutdown(self) -> None:
        with self._lock:
            for plugin in self._plugins.values():
                self._stop(plugin)

    # ---- 网页操作 ----

    def list_plugins(self) -> dict:
        with self._lock:
            self.ensure_loaded()
            return {
                'dir': self.root,
                'plugins': [self._info(p) for p in sorted(
                    self._plugins.values(), key=lambda p: display_text(p.manifest['name']).lower())],
                'broken': list(self._broken),
            }

    def _info(self, plugin: Plugin) -> dict:
        m = plugin.manifest
        if not plugin.enabled:
            status = 'disabled'
        elif plugin.error:
            status = 'error'
        elif plugin.entry:
            status = 'running'
        else:
            status = 'enabled'
        return {
            'id': plugin.id,
            'name': m['name'],
            'version': m['version'],
            'author': m['author'],
            'description': m['description'],
            'homepage': m['homepage'],
            'min_app_version': m['min_app_version'],
            'has_icon': bool(m['icon']) and plugin.package.has_file(m['icon']),
            'has_code': bool(plugin.entry),
            'file': os.path.basename(plugin.package.path),
            'format': 'zip' if plugin.package.is_zip else 'folder',
            'enabled': plugin.enabled,
            'status': status,
            'error': plugin.error,
            'settings_schema': m['settings'],
            'settings': dict(plugin.settings),
        }

    def _get(self, plugin_id: str) -> Plugin:
        self.ensure_loaded()
        plugin = self._plugins.get(str(plugin_id or ''))
        if plugin is None:
            raise PluginError('没有找到这个插件，可能已经被删除了')
        return plugin

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict:
        with self._lock:
            plugin = self._get(plugin_id)
            enabled = bool(enabled)
            if enabled and not plugin.enabled:
                plugin.enabled = True
                self._start(plugin)
            elif not enabled:
                plugin.enabled = False
                self._stop(plugin)
                plugin.error = ''
            elif enabled and plugin.error:
                self._start(plugin)   # 出错的插件再点一次启用 = 重新加载
            self._config.setdefault(plugin.id, {})['enabled'] = plugin.enabled
            self._write_config()
            name = display_text(plugin.manifest['name'])
            if not plugin.enabled:
                print(f'[插件] 已禁用 {name}')
            return self._info(plugin)

    def save_settings(self, plugin_id: str, values: dict) -> dict:
        if not isinstance(values, dict):
            raise PluginError('设置格式不对')
        with self._lock:
            plugin = self._get(plugin_id)
            cleaned = {}
            for field in plugin.manifest['settings']:
                key = field['key']
                cleaned[key] = coerce_value(field, values.get(key, plugin.settings.get(key)))
            plugin.settings = cleaned
            self._config.setdefault(plugin.id, {})['settings'] = cleaned
            self._config[plugin.id].setdefault('enabled', plugin.enabled)
            self._write_config()
            callback = plugin.hook('on_settings_changed')
        if callback:
            try:
                callback(dict(cleaned))
            except BaseException as exc:
                print(f'[插件] {display_text(plugin.manifest["name"])} 应用设置时出错：{_short_error(exc)}')
        return self._info(plugin)

    def delete(self, plugin_id: str) -> None:
        with self._lock:
            plugin = self._get(plugin_id)
            self._stop(plugin)
            path = plugin.package.path
            try:
                if plugin.package.is_zip:
                    os.remove(path)
                else:
                    shutil.rmtree(path)
            except OSError as exc:
                raise PluginError(f'删除失败：{exc}') from exc
            shutil.rmtree(os.path.join(self.root, DATA_DIR_NAME, plugin.id), ignore_errors=True)
            self._plugins.pop(plugin.id, None)
            self._config.pop(plugin.id, None)
            self._write_config()
            print(f'[插件] 已删除 {display_text(plugin.manifest["name"])}')

    def install(self, data: bytes, overwrite: bool = False) -> dict:
        """导入一个插件包：检查后原样复制成 plugins/<id>.zip。已有同 id 的插件要 overwrite 才替换。"""
        manifest = inspect_zip(data)
        with self._lock:
            self.ensure_loaded()
            old = self._plugins.get(manifest['id'])
            if old is not None and not overwrite:
                raise PluginExistsError(
                    '已经装了同一个插件', existing=self._info(old),
                    incoming={'id': manifest['id'], 'name': manifest['name'], 'version': manifest['version']})
            if old is not None and not old.package.is_zip:
                raise PluginError(f'同 id 的插件是文件夹形式（{os.path.basename(old.package.path)}），'
                                  '请先在插件文件夹里删掉或改名，再导入')
            os.makedirs(self.root, exist_ok=True)
            target = os.path.join(self.root, manifest['id'] + '.zip')
            part = os.path.join(self.root, f'.{manifest["id"]}.part')
            with open(part, 'wb') as f:
                f.write(data)
            if old is not None:
                self._stop(old)
            try:
                os.replace(part, target)
                if old is not None and os.path.normcase(old.package.path) != os.path.normcase(target):
                    os.remove(old.package.path)
            except OSError as exc:
                try:
                    os.remove(part)
                except OSError:
                    pass
                if old is not None and old.enabled:
                    self._start(old)
                raise PluginError(f'保存插件失败：{exc}') from exc

            package = Package(target)
            plugin = Plugin(package, package.read_manifest())
            self._plugins[plugin.id] = plugin
            self._apply_config(plugin)
            if old is None:
                plugin.enabled = True
            self._config.setdefault(plugin.id, {})['enabled'] = plugin.enabled
            self._write_config()
            name = display_text(plugin.manifest['name'])
            verb = '已更新' if old is not None else '已导入'
            print(f'[插件] {verb} {name} v{plugin.manifest["version"]}')
            if plugin.enabled:
                self._start(plugin)
            return self._info(plugin)

    def icon(self, plugin_id: str):
        with self._lock:
            plugin = self._get(plugin_id)
            rel = plugin.manifest['icon']
            if not rel:
                return None, None
            data = plugin.package.read_file(rel, MAX_ICON_BYTES)
            return data, ICON_TYPES.get(os.path.splitext(rel)[1].lower())

    def summary(self) -> List[dict]:
        """「软件信息」页用的简表。"""
        return [{k: info[k] for k in ('id', 'name', 'version', 'status', 'enabled', 'error')}
                for info in self.list_plugins()['plugins']]

    # ---- 钩子 ----

    def _running(self, hook_name: str) -> List[tuple]:
        with self._lock:
            plugins = sorted(self._plugins.values(), key=lambda p: display_text(p.manifest['name']).lower())
            return [(p, p.hook(hook_name)) for p in plugins if p.hook(hook_name)]

    def _report(self, plugin: Plugin, hook_name: str, exc: BaseException) -> None:
        plugin.hook_errors += 1
        if plugin.hook_errors <= ERROR_LOG_LIMIT:
            more = '（之后的同类错误不再显示）' if plugin.hook_errors == ERROR_LOG_LIMIT else ''
            print(f'[插件] {display_text(plugin.manifest["name"])} 的 {hook_name} 出错：{_short_error(exc)}{more}')

    def _apply_filters(self, hook: str, text: str, extra: tuple, warn_slow: bool, slow_hint: str) -> str:
        """filter_source / filter_chatbox：按插件名字的顺序依次交给每个插件，None 不改，字符串就换掉。"""
        for plugin, fn in self._running(hook):
            started = time.perf_counter()
            try:
                result = fn(text, *extra)
            except BaseException as exc:
                self._report(plugin, hook, exc)
                continue
            elapsed = time.perf_counter() - started
            if elapsed > SLOW_FILTER_SECONDS and warn_slow:
                print(f'[插件] {display_text(plugin.manifest["name"])} 的 {hook} 用了 {int(elapsed * 1000)}ms，{slow_hint}')
            if result is None:
                continue
            if isinstance(result, str):
                text = result
            else:
                self._report(plugin, hook, TypeError('应该返回文字或 None'))
        return text

    def filter_source(self, text: str) -> str:
        return self._apply_filters('filter_source', text, (), True, '会拖慢翻译')

    def filter_chatbox(self, text: str, ongoing: bool) -> str:
        return self._apply_filters('filter_chatbox', text, (ongoing,), not ongoing, '会拖慢聊天框')

    def emit(self, event: str, payload: dict) -> None:
        if event not in EVENTS or not self._plugins:
            return
        if not any(p.hook('on_' + event) for p in list(self._plugins.values())):
            return
        try:
            self._events.put_nowait((event, dict(payload, time=time.time())))
        except queue.Full:
            pass

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run_events, name='ganlan-plugin-events', daemon=True)
        self._worker.start()

    def _run_events(self) -> None:
        while True:
            event, payload = self._events.get()
            for plugin, fn in self._running('on_' + event):
                try:
                    fn(dict(payload))
                except BaseException as exc:
                    self._report(plugin, 'on_' + event, exc)


def _same_package(a: Plugin, b: Plugin) -> bool:
    try:
        sa, sb = os.stat(a.package.path), os.stat(b.package.path)
    except OSError:
        return False
    return (os.path.normcase(a.package.path) == os.path.normcase(b.package.path)
            and sa.st_mtime_ns == sb.st_mtime_ns and sa.st_size == sb.st_size)


def _forget_import_caches(path: str) -> None:
    """zip 被替换或删除后，zipimport 缓存的目录表会过期，要清掉。"""
    norm = os.path.normcase(os.path.abspath(path))
    for key in list(sys.path_importer_cache):
        if isinstance(key, str) and os.path.normcase(os.path.abspath(key)).startswith(norm):
            sys.path_importer_cache.pop(key, None)
    cache = getattr(zipimport, '_zip_directory_cache', None)
    if isinstance(cache, dict):
        for key in list(cache):
            if os.path.normcase(os.path.abspath(key)) == norm:
                cache.pop(key, None)
    importlib.invalidate_caches()


def _short_error(exc: BaseException) -> str:
    text = str(exc).strip()
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__


plugin_manager = PluginManager()


def filter_source(text: str) -> str:
    """给 recognition_handler 用（自我翻译的整句，翻译之前）：插件都没装时直接返回原文。"""
    if not plugin_manager._plugins:
        return text
    return plugin_manager.filter_source(text)


def filter_chatbox(text: str, ongoing: bool) -> str:
    """给 osc_manager 用：插件都没装时直接返回原文。"""
    if not plugin_manager._plugins:
        return text
    return plugin_manager.filter_chatbox(text, ongoing)


def emit(event: str, payload: dict) -> None:
    plugin_manager.emit(event, payload)
