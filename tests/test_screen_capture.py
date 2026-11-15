"""翻译画面的窗口采集：VRChat 最小化时临时还原（不激活、压到最底层）截一张，再最小化回去。

用假的 user32 测流程，不碰真的窗口。真窗口在开发机上用 Qt 测试窗口验证过（scratchpad 的 min_capture_test.py）。
"""
from __future__ import annotations

import pytest
from PIL import Image

import screen_translation as st


class FakeUser32:
    def __init__(self, iconic=True, foreground=999):
        self.iconic = iconic
        self.foreground = foreground
        self.calls = []
        self.placement_flags = 2          # WPF_RESTORETOMAXIMIZED：原来是最大化后最小化的

    def SetThreadDpiAwarenessContext(self, ctx):
        return None

    def IsIconic(self, hwnd):
        return self.iconic

    def GetWindowPlacement(self, hwnd, ref):
        ref._obj.flags = self.placement_flags
        ref._obj.showCmd = 2
        return 1

    def SetWindowPlacement(self, hwnd, ref):
        self.calls.append(('SetWindowPlacement', ref._obj.showCmd, ref._obj.flags))
        self.iconic = ref._obj.showCmd == st.SW_SHOWMINNOACTIVE
        return 1

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(('ShowWindow', cmd))
        if cmd == st.SW_SHOWNOACTIVATE:
            self.iconic = False
        return 1

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append(('SetWindowPos', after.value, flags & st.SWP_NOACTIVATE))
        return 1

    def GetForegroundWindow(self):
        return self.foreground


@pytest.fixture
def fake(monkeypatch):
    user32 = FakeUser32()
    monkeypatch.setattr(st, '_user32', user32)
    monkeypatch.setattr(st, '_find_vrchat_window', lambda: 42)
    monkeypatch.setattr(st, 'MINIMIZED_SETTLE_SECONDS', 0)
    monkeypatch.setattr(st, 'MINIMIZED_WAIT_SECONDS', 0.5)
    monkeypatch.setattr(st.time, 'sleep', lambda s: None)
    return user32


def test_minimized_window_restored_captured_and_minimized_again(fake, monkeypatch):
    frames = iter([Image.new('RGB', (64, 36), 'black'), Image.new('RGB', (64, 36), 'white')])
    seen_iconic = []

    def grab(hwnd):
        seen_iconic.append(fake.iconic)
        return next(frames)

    monkeypatch.setattr(st, '_grab_window', grab)
    image = st.capture_vrchat_window()
    assert image.getpixel((0, 0)) == (255, 255, 255)        # 全黑的那帧不要，等到新画面
    assert seen_iconic == [False, False]                      # 截的时候是还原着的
    assert fake.calls[0] == ('ShowWindow', st.SW_SHOWNOACTIVATE)
    assert fake.calls[1] == ('SetWindowPos', 1, st.SWP_NOACTIVATE)      # HWND_BOTTOM，不激活
    # 按原来的样子最小化回去（还保留「还原时最大化」）
    assert fake.calls[-1] == ('SetWindowPlacement', st.SW_SHOWMINNOACTIVE, 2)
    assert fake.iconic


def test_minimized_window_stays_black_reports_error_and_reminimizes(fake, monkeypatch):
    monkeypatch.setattr(st, '_grab_window', lambda hwnd: Image.new('RGB', (64, 36), 'black'))
    with pytest.raises(st.ScreenCaptureError, match='全黑'):
        st.capture_vrchat_window()
    assert fake.iconic


def test_user_switched_to_vrchat_meanwhile_not_minimized(fake, monkeypatch):
    fake.foreground = 42                                       # 截的时候用户自己切到了 VRChat
    monkeypatch.setattr(st, '_grab_window', lambda hwnd: Image.new('RGB', (64, 36), 'white'))
    st.capture_vrchat_window()
    assert not fake.iconic
    assert not any(call[0] == 'SetWindowPlacement' for call in fake.calls)


def test_normal_window_not_touched(fake, monkeypatch):
    fake.iconic = False
    monkeypatch.setattr(st, '_grab_window', lambda hwnd: Image.new('RGB', (64, 36), 'white'))
    st.capture_vrchat_window()
    assert fake.calls == []
    monkeypatch.setattr(st, '_grab_window', lambda hwnd: Image.new('RGB', (64, 36), 'black'))
    with pytest.raises(st.ScreenCaptureError, match='独占全屏'):
        st.capture_vrchat_window()
