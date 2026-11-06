"""语音控制插件（docs/plugin-voice-control）和它用到的宿主功能：
半句钩子、ctx.call_api / ctx.llm_chat、VR 手腕命令。"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import threading
import time
import zipfile

import pytest

import plugin_manager as pm
from plugin_manager import PluginManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'docs', 'plugin-voice-control')
FILES = ('plugin.json', 'main.py', 'commands.py', 'intent.py', 'icon.png')


def _commands():
    spec = importlib.util.spec_from_file_location('vc_commands_test', os.path.join(SRC, 'commands.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name in FILES:
            zf.write(os.path.join(SRC, name), name)
    return buf.getvalue()


def test_manifest_matches_source():
    manifest = pm.parse_manifest(open(os.path.join(SRC, 'plugin.json'), 'rb').read())
    assert manifest['id'] == 'voice-control'
    assert manifest['min_app_version'] == '1.1.2'
    phrases = next(f for f in manifest['settings'] if f['key'] == 'phrases')
    assert phrases['default'] == _commands().DEFAULT_TABLE      # 口令表默认值和代码里的一样


@pytest.mark.parametrize('text, expected', [
    ('启动自我翻译', {'action': 'start', 'modes': ['self']}),
    ('起动自我翻译。', {'action': 'start', 'modes': ['self']}),          # 同音字、标点
    ('关闭所有功能', {'action': 'stop', 'modes': ['self', 'others', 'screen']}),
    ('翻译一下画面', {'action': 'screen_translate'}),
    ('主语言改成日语', {'action': 'language', 'target': 'main', 'language': 'ja'}),
    ('关闭第二语言', {'action': 'language', 'target': 'secondary', 'language': ''}),
    ('右手手腕显示画面', {'action': 'wrist', 'side': 'right', 'content': 'screen'}),
    ('Start translating others.', {'action': 'start', 'modes': ['others']}),
    ('日本語に翻訳して', {'action': 'language', 'target': 'main', 'language': 'ja'}),
    ('我想启动自我翻译', None),
    ('左手边那个人是谁', None),
    ('翻译成火星语', None),
])
def test_phrase_matching(text, expected):
    c = _commands()
    found = c.Matcher('', '甘岚').match_key(c.key(text))
    if expected is None:
        assert found is None
    else:
        found.pop('phrase')
        assert found == expected


def test_partial_prefixes():
    c = _commands()
    m = c.Matcher('', '甘岚')
    assert m.could_be_command(c.key('启动自我'))
    assert m.could_be_command(c.key('主语言改成日'))
    assert not m.could_be_command(c.key('打开窗'))
    assert not m.could_be_command(c.key('左手边'))
    assert m.could_be_wake(c.key('甘')) and m.could_be_wake(c.key('甘蓝启动'))


def test_custom_table_and_bad_lines():
    c = _commands()
    m = c.Matcher('start_others = 开工\n乱写的一行\n# 说明', '')
    assert m.problems == ['乱写的一行']
    assert m.match_key(c.key('开工'))['modes'] == ['others']
    assert m.match_key(c.key('启动翻译他人')) is None          # 自己写了表就只用自己的


def test_intent_parse():
    spec = importlib.util.spec_from_file_location('vc_intent_test', os.path.join(SRC, 'intent.py'))
    intent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(intent)
    reply = '```json\n{"commands": [{"action": "stop", "modes": ["others", "x"]}, ' \
            '{"action": "language", "target": "others", "language": "ja"}, {"action": "explode"}, ' \
            '{"action": "wrist", "side": "left", "content": "screen"}]}\n```'
    assert intent.parse(reply) == [
        {'action': 'stop', 'modes': ['others']},
        {'action': 'language', 'target': 'others', 'language': 'ja'},
        {'action': 'wrist', 'side': 'left', 'content': 'screen'},
    ]
    assert intent.parse('不是命令') == []
    assert intent.parse('{"commands": [{"action": "language", "target": "main", "language": "日语"}]}') == []


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    """装上语音控制插件，宿主的 call_api / llm_chat 换成记录调用的假函数。"""
    monkeypatch.setattr(pm, 'APP_VERSION', '1.1.2')
    calls = []
    got = threading.Event()
    llm_replies = []

    def call_api(method, path, payload):
        calls.append((method, path, payload))
        got.set()
        if path == '/api/status':
            return 200, {'modes': {'screen': 'running'}, 'target_language': 'ja'}
        if path == '/api/vr/wrist':
            return 200, {'success': True, 'overlay_connected': True}
        return 200, {'success': True}

    def llm_chat(messages, **kwargs):
        llm_replies.append(messages)
        return '{"commands": [{"action": "language", "target": "others", "language": "ja"}]}'

    monkeypatch.setattr(pm, '_HOST', {'call_api': call_api, 'llm_chat': llm_chat})
    manager = PluginManager(str(tmp_path / 'plugins'))
    manager.load_all()
    manager.install(_zip())
    monkeypatch.setattr(pm, 'plugin_manager', manager)

    def wait_calls(n=1):
        deadline = time.time() + 5
        while len(calls) < n and time.time() < deadline:
            time.sleep(0.02)
        return calls

    yield manager, calls, wait_calls, llm_replies
    manager.shutdown()


def test_fixed_phrase_runs_without_translation(plugin):
    manager, calls, wait_calls, _ = plugin
    assert pm.filter_source('启动翻译他人。') == ''                 # 口令：不翻译、不发
    assert pm.filter_source('今天天气不错') == '今天天气不错'          # 平常的话照常
    assert pm.filter_partial_source('启动翻') == ''                 # 说到一半也不显示
    assert pm.filter_partial_source('今天天') == '今天天'
    assert wait_calls(1)[0] == ('POST', '/api/modes/start', {'modes': ['others']})

    calls.clear()
    assert pm.filter_source('左手显示画面') == ''
    assert wait_calls(1)[0] == ('POST', '/api/vr/wrist', {'side': 'left', 'content': 'screen'})

    calls.clear()
    assert pm.filter_source('第二语言改成韩语') == ''
    assert wait_calls(1)[0] == ('POST', '/api/secondary-target-language', {'secondary_target_language': 'ko'})


def test_wake_word_required_when_asked(plugin):
    manager, calls, wait_calls, _ = plugin
    manager.save_settings('voice-control', {'fixed_need_wake': True})
    assert pm.filter_source('启动翻译他人') == '启动翻译他人'
    assert pm.filter_partial_source('启动翻') == '启动翻'
    assert pm.filter_partial_source('甘') == ''
    assert pm.filter_source('甘蓝，启动翻译他人') == ''              # 同音字的唤醒词也算
    assert wait_calls(1)[0][1] == '/api/modes/start'


def test_natural_language_asks_llm(plugin):
    manager, calls, wait_calls, llm_replies = plugin
    manager.save_settings('voice-control', {'mode': 'natural'})
    assert pm.filter_source('帮我把他人翻译改成日语') == '帮我把他人翻译改成日语'   # 没说唤醒词：照常翻译
    assert pm.filter_source('甘岚，帮我把他人翻译改成日语') == ''
    found = wait_calls(2)
    assert ('POST', '/api/others-target-language', {'target_language': 'ja'}) in found
    assert llm_replies and '帮我把他人翻译改成日语' in llm_replies[0][-1]['content']
    # 唤醒词后面正好是口令：直接执行，不问大模型
    calls.clear()
    llm_replies.clear()
    assert pm.filter_source('甘岚，关闭所有功能') == ''
    assert wait_calls(1)[0] == ('POST', '/api/modes/stop', {'modes': ['self', 'others', 'screen']})
    assert not llm_replies


# ---------------------------------------------------------------- 宿主

def test_partial_filter_skips_partial_in_recognition(monkeypatch):
    from unittest.mock import MagicMock
    import recognition_handler
    cb = recognition_handler.VRChatRecognitionCallback(MagicMock())
    monkeypatch.setattr(pm, 'filter_partial_source', lambda text: '')
    event = MagicMock(text='启动翻', is_final=False)
    cb.on_result(event)
    cb.state.update_subtitles.assert_not_called()


def test_host_call_api_and_wrist_commands():
    from ui import app as ui_app
    status, data = ui_app._plugin_call_api('GET', '/api/status', None)
    assert status == 200 and 'modes' in data and 'secondary_target_language' in data
    status, data = ui_app._plugin_call_api('POST', '/api/vr/wrist', {'side': 'both', 'content': 'screen'})
    assert status == 200 and data['success']
    status, _ = ui_app._plugin_call_api('POST', '/api/vr/wrist', {'side': 'up', 'content': 'screen'})
    assert status == 400
    commands = ui_app._vr_payload()['commands']
    assert commands[-1]['kind'] == 'wrist' and commands[-1]['side'] == 'both'


def test_overlay_applies_wrist_command(monkeypatch):
    from vr_overlay import link as vr_link
    link = vr_link.MainLink.__new__(vr_link.MainLink)
    link._lock = threading.Lock()
    link._commands = []
    link._command_seq = 0
    link._take_commands([{'kind': 'wrist', 'side': 'both', 'content': 'screen', 'seq': 5}])
    link._take_commands([{'kind': 'wrist', 'side': 'both', 'content': 'screen', 'seq': 5}])   # 同一条只做一次
    assert len(link._commands) == 1

    import vr_overlay.main as vr_main

    class FakeSettings:
        def __init__(self):
            self.data = {}

        def set(self, key, value):
            self.data[key] = value

    app = vr_main.OverlayApp.__new__(vr_main.OverlayApp)
    app.link = link
    app.settings = FakeSettings()
    app.wrist_rendered = {'left': 'x', 'right': 'x'}
    app.apply_link_commands()
    assert app.settings.data == {'left_wrist': 'screen', 'right_wrist': 'screen'}
    assert app.wrist_rendered == {'left': None, 'right': None}
    assert link.pop_commands() == []


def test_llm_chat_needs_config(monkeypatch):
    from ui import app as ui_app
    monkeypatch.setattr(ui_app.config, 'LLM_API_KEY', '', raising=False)
    monkeypatch.setattr(ui_app.config, 'OPENAI_API_KEY', '', raising=False)
    with pytest.raises(RuntimeError):
        ui_app._plugin_llm_chat([{'role': 'user', 'content': 'hi'}])


def test_textarea_rows_kept():
    manifest = pm.parse_manifest(open(os.path.join(SRC, 'plugin.json'), 'rb').read())
    phrases = next(f for f in manifest['settings'] if f['key'] == 'phrases')
    assert phrases['rows'] == 16
