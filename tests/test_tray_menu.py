"""托盘右键菜单（qt_ui/tray.py）和 panel_launcher 按角色开关悬浮窗。不显示窗口、不连 5001。"""
import os
import sys

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import panel_launcher  # noqa: E402
from qt_ui import tray  # noqa: E402


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


class _SyncThread:
    """后台线程改成当场跑完，测试好断言。"""

    def __init__(self, target, **_):
        self._target = target

    def start(self):
        self._target()


@pytest.fixture
def menu(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(tray, '_api', lambda path, body=None, timeout=3.0: calls.append((path, body)) or {})
    monkeypatch.setattr(tray.threading, 'Thread', _SyncThread)
    monkeypatch.setattr(tray, '_lang', lambda: 'zh-CN')
    m = tray.TrayMenu(QIcon())
    m.calls = calls
    yield m
    m.deleteLater()


def test_mode_state_mapping():
    assert tray._mode_state('running') == 'running'
    assert tray._mode_state('error: 没有 Key') == 'error'
    assert tray._mode_state('') == 'stopped'
    assert tray._mode_state('weird') == 'stopped'


def test_clicking_a_mode_starts_or_stops_it(menu):
    menu._on_status({'modes': {'self': 'running', 'others': 'stopped', 'screen': 'error: 没有填 Key'}})
    menu.calls.clear()
    menu._toggle_mode('self')
    menu._toggle_mode('others')
    menu._toggle_mode('screen')
    assert ('/modes/stop', {'modes': ['self']}) in menu.calls
    # 和 VR 附属程序一样：Key 用网页推过来的那份
    assert ('/modes/start', {'modes': ['others'], 'api_keys': {}}) in menu.calls
    assert ('/modes/start', {'modes': ['screen'], 'api_keys': {}}) in menu.calls
    assert not menu._pending


def test_render_states_vr_and_error_tooltip(menu):
    menu._on_status({'modes': {'self': 'running', 'others': 'stopped', 'screen': 'error: 没有填 Key'},
                     'display_mode': 'desktop'})
    menu._render()
    row, _, name, state = menu._mode_rows['self']
    assert (name.text(), state.text()) == ('自我翻译', '运行中')
    assert '没有填 Key' in menu._mode_rows['screen'][0].toolTip()
    assert not menu._chips_row.isHidden() and menu._vr_hint.isHidden()

    menu._on_status({'modes': {}, 'display_mode': 'vr'})
    menu._render()
    assert menu._chips_row.isHidden() and not menu._vr_hint.isHidden()


def test_panel_chips_open_and_close_one_role(menu, monkeypatch):
    opened = {'others'}
    actions = []
    monkeypatch.setattr(panel_launcher, 'running_roles', lambda: set(opened))
    monkeypatch.setattr(panel_launcher, 'close_panel', lambda role: actions.append(('close', role)))
    monkeypatch.setattr(panel_launcher, 'launch_panel_like_last', lambda role: actions.append(('open', role)))
    menu._render()
    assert menu._chips['others'].isChecked() and not menu._chips['self'].isChecked()
    menu._toggle_panel('others')
    menu._toggle_panel('self')
    assert actions == [('close', 'others'), ('open', 'self')]


class _FakeProc:
    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


def test_panel_launcher_per_role(monkeypatch):
    started = []
    monkeypatch.setattr(panel_launcher, '_panel_processes', [])
    monkeypatch.setattr(panel_launcher, '_last_options', {})
    monkeypatch.setattr(panel_launcher.subprocess, 'Popen', lambda cmd: started.append(cmd) or _FakeProc())

    # 网页打开过一次：记住置顶、快捷语言；托盘照着开
    panel_launcher.launch_panel(floating_mode=False, quick_lang_enabled=False, quick_langs=['ja', 'en', 'ko', 'fr'],
                                role='self', window_x=80, window_y=80)
    assert panel_launcher.launch_panel_like_last('screen')
    assert panel_launcher.running_roles() == {'self', 'screen'}
    cmd = ' '.join(started[-1])
    assert 'quick_lang_bar=0' in cmd and 'quick_lang=fr' in cmd and 'role=screen' in cmd
    assert 'floating-off' in cmd and cmd.endswith(' 80 520')

    assert panel_launcher.close_panel('self') is True
    assert panel_launcher.running_roles() == {'screen'}
    assert panel_launcher.close_panel('self') is False
