"""插件：导入 zip、启用 / 禁用 / 删除、单独设置、钩子，以及网页接口。"""

from __future__ import annotations

import io
import json
import os
import threading
import zipfile

import pytest

import plugin_manager as pm
from plugin_manager import PluginError, PluginExistsError, PluginManager

EXAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'plugin-example')

MAIN = '''
from . import helper

ctx = None
calls = []


def setup(context):
    global ctx
    ctx = context
    calls.append('setup')


def teardown():
    calls.append('teardown')


def on_settings_changed(settings):
    calls.append(('settings', settings['suffix']))


def filter_chatbox(text, ongoing):
    if ongoing:
        return None
    if ctx.settings['mute']:
        return ''
    return helper.decorate(text, ctx.settings['suffix'])


def on_self_result(event):
    with open(ctx.data_dir + '/events.txt', 'a', encoding='utf-8') as f:
        f.write(event['source'] + '\\n')
    ctx.event_seen.set()
'''

HELPER = '''
def decorate(text, suffix):
    return text + suffix
'''


def manifest(**extra):
    data = {
        'id': 'demo',
        'name': {'zh-CN': '演示', 'en': 'Demo'},
        'version': '1.0.0',
        'settings': [
            {'key': 'suffix', 'type': 'text', 'label': '后缀', 'default': '!'},
            {'key': 'mute', 'type': 'toggle', 'label': '不发', 'default': False},
            {'key': 'level', 'type': 'number', 'label': '等级', 'min': 0, 'max': 10, 'default': 3},
            {'key': 'mode', 'type': 'select', 'label': '模式', 'options': ['a', 'b'], 'default': 'a'},
        ],
    }
    data.update(extra)
    return data


def make_zip(files: dict, prefix: str = '') -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            if isinstance(content, dict):
                content = json.dumps(content, ensure_ascii=False)
            zf.writestr(prefix + name, content)
    return buf.getvalue()


def demo_zip(prefix: str = '', **extra) -> bytes:
    return make_zip({'plugin.json': manifest(**extra), 'main.py': MAIN, 'helper.py': HELPER}, prefix)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, 'APP_VERSION', '1.1.0')
    m = PluginManager(str(tmp_path / 'plugins'))
    m.load_all()
    yield m
    m.shutdown()


def module_of(manager, plugin_id='demo'):
    return manager._plugins[plugin_id].module


def test_install_loads_and_filters_chatbox(manager):
    info = manager.install(demo_zip())

    assert info['status'] == 'running'
    assert info['settings'] == {'suffix': '!', 'mute': False, 'level': 3, 'mode': 'a'}
    assert os.path.isfile(os.path.join(manager.root, 'demo.zip'))
    assert module_of(manager).calls == ['setup']
    assert manager.filter_chatbox('你好', False) == '你好!'
    assert manager.filter_chatbox('你', True) == '你'


def test_wrapped_folder_in_zip_is_accepted(manager):
    info = manager.install(demo_zip(prefix='demo-1.0/'))
    assert info['status'] == 'running'
    assert manager.filter_chatbox('a', False) == 'a!'


def test_settings_are_validated_saved_and_pushed(manager):
    manager.install(demo_zip())

    info = manager.save_settings('demo', {'suffix': '~', 'mute': False, 'level': 7, 'mode': 'b'})

    assert info['settings']['suffix'] == '~'
    assert module_of(manager).calls[-1] == ('settings', '~')
    assert manager.filter_chatbox('a', False) == 'a~'
    saved = json.load(open(manager.config_path, encoding='utf-8'))
    assert saved['plugins']['demo']['settings']['level'] == 7

    for bad in ({'level': 11}, {'level': 'x'}, {'mode': 'c'}, {'mute': 'yes'}, {'suffix': 5}):
        with pytest.raises(PluginError):
            manager.save_settings('demo', bad)
    assert manager.list_plugins()['plugins'][0]['settings']['level'] == 7


def test_empty_result_blocks_the_message(manager):
    manager.install(demo_zip())
    manager.save_settings('demo', {'mute': True})
    assert manager.filter_chatbox('a', False) == ''


def test_disable_and_enable(manager):
    manager.install(demo_zip())
    module = module_of(manager)

    info = manager.set_enabled('demo', False)

    assert info['status'] == 'disabled'
    assert module.calls[-1] == 'teardown'
    assert manager.filter_chatbox('a', False) == 'a'

    # 重开程序后仍然是禁用的
    again = PluginManager(manager.root)
    again.load_all()
    assert again.list_plugins()['plugins'][0]['enabled'] is False
    assert again.filter_chatbox('a', False) == 'a'

    assert manager.set_enabled('demo', True)['status'] == 'running'
    assert manager.filter_chatbox('a', False) == 'a!'


def test_reinstall_needs_overwrite_and_keeps_settings(manager):
    manager.install(demo_zip())
    manager.save_settings('demo', {'suffix': '?'})

    with pytest.raises(PluginExistsError) as exc:
        manager.install(demo_zip(version='2.0.0'))
    assert exc.value.existing['version'] == '1.0.0'
    assert exc.value.incoming['version'] == '2.0.0'

    info = manager.install(demo_zip(version='2.0.0'), overwrite=True)
    assert info['version'] == '2.0.0'
    assert info['settings']['suffix'] == '?'
    assert manager.filter_chatbox('a', False) == 'a?'


def test_delete_removes_package_data_and_settings(manager):
    manager.install(demo_zip())
    data_dir = os.path.join(manager.root, '_data', 'demo')
    os.makedirs(data_dir)

    manager.delete('demo')

    assert not os.path.exists(os.path.join(manager.root, 'demo.zip'))
    assert not os.path.exists(data_dir)
    assert manager.list_plugins()['plugins'] == []
    assert 'demo' not in json.load(open(manager.config_path, encoding='utf-8'))['plugins']


@pytest.mark.parametrize('files, message', [
    ({'main.py': 'x = 1'}, 'plugin.json'),
    ({'plugin.json': '{bad'}, 'JSON'),
    ({'plugin.json': {'id': '../x', 'name': 'x'}}, 'id'),
    ({'plugin.json': {'id': 'x'}}, 'name'),
    ({'plugin.json': {'id': 'x', 'name': 'x', 'entry': 'run.py'}}, 'run.py'),
    ({'plugin.json': {'id': 'x', 'name': 'x'}, 'main.py': 'def ('}, '语法错误'),
    ({'plugin.json': {'id': 'x', 'name': 'x', 'settings': [{'key': 'a b'}]}}, 'key'),
    ({'plugin.json': {'id': 'x', 'name': 'x', 'settings': [{'key': 'a', 'type': 'select'}]}}, 'options'),
])
def test_bad_packages_are_rejected(manager, files, message):
    with pytest.raises(PluginError, match=message):
        manager.install(make_zip(files))
    assert [n for n in os.listdir(manager.root) if n.endswith('.zip')] == []


def test_not_a_zip(manager):
    with pytest.raises(PluginError, match='zip'):
        manager.install(b'hello')


def test_broken_setup_is_reported_not_raised(manager):
    files = {'plugin.json': manifest(), 'main.py': 'def setup(ctx):\n    raise SystemExit(1)\n'}
    info = manager.install(make_zip(files))
    assert info['status'] == 'error'
    assert 'SystemExit' in info['error']
    assert manager.filter_chatbox('a', False) == 'a'


def test_hook_errors_do_not_break_sending(manager, capsys):
    files = {'plugin.json': manifest(), 'main.py': 'def filter_chatbox(text, ongoing):\n    return 1 / 0\n'}
    manager.install(make_zip(files))
    for _ in range(8):
        assert manager.filter_chatbox('a', False) == 'a'
    out = capsys.readouterr().out
    assert out.count('ZeroDivisionError') == pm.ERROR_LOG_LIMIT


def test_self_sentence_goes_through_filter_source_before_translation(manager, monkeypatch):
    """自我翻译的整句先过 filter_source，改过的原文再去识别语言、翻译、上字幕；说话中的半句不过。"""
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import MagicMock

    import recognition_handler as rh
    from speech_recognizers.base_speech_recognizer import RecognitionEvent

    files = {'plugin.json': manifest(), 'main.py': (
        'def filter_source(text):\n'
        '    return "" if text == "丢掉" else "<" + text + ">"\n')}
    manager.install(make_zip(files))
    monkeypatch.setattr(pm, 'plugin_manager', manager)
    monkeypatch.setattr(rh.config, 'ENABLE_TRANSLATION', False)
    osc = MagicMock()
    monkeypatch.setattr(rh, 'osc_manager', osc)

    state = MagicMock()
    state.executor = ThreadPoolExecutor(max_workers=1)
    state.language_detector.detect.return_value = {'language': 'zh-cn'}
    state.subtitles_state = {'original': '', 'translated': '', 'reverse_translated': '', 'ongoing': False}
    callback = rh.VRChatRecognitionCallback(state)
    callback.loop = MagicMock()
    try:
        callback.on_result(RecognitionEvent(text='你', is_final=False, raw={}))
        assert state.update_subtitles.call_args.args[0] == '你'
        callback.on_result(RecognitionEvent(text='你好', is_final=True, raw={}))
        state.language_detector.detect.assert_called_with('<你好>')
        assert state.update_subtitles.call_args.args[0] == '<你好>'

        state.language_detector.detect.reset_mock()
        callback.on_result(RecognitionEvent(text='丢掉', is_final=True, raw={}))
        state.language_detector.detect.assert_not_called()
        osc.set_typing_sync.assert_called_with(False)
    finally:
        state.executor.shutdown(wait=True)


def test_min_app_version(manager):
    info = manager.install(demo_zip(min_app_version='9.0'))
    assert info['status'] == 'error'
    assert '9.0' in info['error']


def test_refresh_finds_copied_zip_and_folder_plugins(manager):
    with open(os.path.join(manager.root, 'copied.zip'), 'wb') as f:
        f.write(demo_zip())
    folder = os.path.join(manager.root, 'dev-plugin')
    os.makedirs(folder)
    with open(os.path.join(folder, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump({'id': 'dev', 'name': 'Dev'}, f)
    with open(os.path.join(folder, 'main.py'), 'w', encoding='utf-8') as f:
        f.write('def filter_chatbox(text, ongoing):\n    return text + "#"\n')
    with open(os.path.join(manager.root, 'broken.zip'), 'wb') as f:
        f.write(b'not a zip')

    manager.refresh()

    listed = manager.list_plugins()
    assert {p['id']: p['format'] for p in listed['plugins']} == {'demo': 'zip', 'dev': 'folder'}
    assert [b['file'] for b in listed['broken']] == ['broken.zip']
    assert manager.filter_chatbox('a', False) in ('a!#', 'a#!')

    os.remove(os.path.join(manager.root, 'copied.zip'))
    manager.refresh()
    assert [p['id'] for p in manager.list_plugins()['plugins']] == ['dev']


def test_events_reach_plugins(manager):
    manager.install(demo_zip())
    plugin = manager._plugins['demo']
    seen = threading.Event()
    # 测试插件在收到事件后会 set 这个
    module_of(manager).ctx.event_seen = seen

    manager.emit('self_result', {'source': '你好', 'translation': 'hello'})

    assert seen.wait(3)
    with open(os.path.join(manager.root, '_data', plugin.id, 'events.txt'), encoding='utf-8') as f:
        assert f.read() == '你好\n'


def test_example_plugin_in_docs(manager):
    files = {name: open(os.path.join(EXAMPLE_DIR, name), 'rb').read() for name in os.listdir(EXAMPLE_DIR)
             if not name.startswith('__')}
    info = manager.install(make_zip(files))
    assert info['status'] == 'running', info['error']
    assert info['has_icon'] is True
    # 整句在翻译前加前后缀（跟着一起翻译），和正文之间不留空格；聊天框那一步不再加第二次
    assert manager.filter_source(' 你好 ') == '你好♪'
    assert manager.filter_chatbox('[zh→ja] こんにちは♪ (你好♪)', False) == '[zh→ja] こんにちは♪ (你好♪)'
    manager.save_settings('chatbox-decorator', {'prefix': ' 【 ', 'suffix': ' 】 '})
    assert manager.filter_source('你好') == '【你好】'
    assert manager.filter_chatbox('你', True) == '你'
    manager.save_settings('chatbox-decorator', {'prefix': '【', 'suffix': '】', 'partial': True})
    assert manager.filter_chatbox('你', True) == '【你】'
    data, mime = manager.icon('chatbox-decorator')
    assert mime == 'image/png' and data.startswith(b'\x89PNG')


# ---------- 网页接口 ----------

@pytest.fixture
def client(manager, monkeypatch):
    from ui import app as ui_app
    monkeypatch.setattr(pm, 'plugin_manager', manager)
    return ui_app.app.test_client()


def upload(client, data: bytes, overwrite=False, headers=None):
    form = {'file': (io.BytesIO(data), 'demo.zip')}
    if overwrite:
        form['overwrite'] = '1'
    return client.post('/api/plugins/import', data=form, content_type='multipart/form-data', headers=headers or {})


def test_api_flow(client, manager):
    assert client.get('/api/plugins').get_json()['plugins'] == []

    response = upload(client, demo_zip())
    assert response.status_code == 200
    assert response.get_json()['plugin']['status'] == 'running'

    again = upload(client, demo_zip(version='1.1.0'))
    assert again.status_code == 409
    assert again.get_json()['existing']['version'] == '1.0.0'
    assert upload(client, demo_zip(version='1.1.0'), overwrite=True).status_code == 200

    response = client.post('/api/plugins/demo/settings', json={'settings': {'suffix': '~'}})
    assert response.get_json()['plugin']['settings']['suffix'] == '~'
    assert client.post('/api/plugins/demo/settings', json={'settings': {'level': 99}}).status_code == 400

    assert client.post('/api/plugins/demo/enabled', json={'enabled': False}).get_json()['plugin']['status'] == 'disabled'
    assert client.post('/api/plugins/nope/enabled', json={'enabled': False}).status_code == 404

    assert client.get('/api/plugins/demo/icon').status_code == 404
    assert client.delete('/api/plugins/demo').status_code == 200
    assert client.get('/api/plugins').get_json()['plugins'] == []


def test_api_rejects_other_websites(client, manager):
    evil = {'Origin': 'https://example.com'}
    assert upload(client, demo_zip(), headers=evil).status_code == 403
    assert manager.list_plugins()['plugins'] == []
    assert upload(client, demo_zip(), headers={'Origin': 'http://localhost'}).status_code == 200
    assert client.post('/api/plugins/demo/enabled', json={'enabled': False}, headers=evil).status_code == 403
    assert client.delete('/api/plugins/demo', headers=evil).status_code == 403
    # 别的域名解析到本机（DNS 重绑定）
    rebind = {'Host': 'evil.test:5001', 'Origin': 'http://evil.test:5001'}
    assert client.delete('/api/plugins/demo', headers=rebind).status_code == 403
    assert manager.list_plugins()['plugins'][0]['enabled'] is True


def test_osc_manager_applies_plugins(manager, monkeypatch):
    from osc_manager import OSCManager
    monkeypatch.setattr(pm, 'plugin_manager', manager)
    manager.install(demo_zip())
    sent = []
    osc = OSCManager()
    monkeypatch.setattr(osc, '_send_message_immediately', lambda text, ongoing: sent.append(text))
    monkeypatch.setattr(osc, 'set_typing_sync', lambda typing: sent.append(('typing', typing)))

    osc.send_text_sync('你好', False)
    manager.save_settings('demo', {'mute': True})
    osc.send_text_sync('不发', False)
    osc.send_text_sync('插件自己发', False, apply_plugins=False)

    assert sent == ['你好!', ('typing', False), '插件自己发']
