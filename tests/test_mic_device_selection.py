"""Tests for choosing the self-mode microphone from the web page.

The page must offer the same endpoint IDs that audio_capture records from
(config.MIC_DEVICE_ID). It used to send PyAudio indices instead, which the
WASAPI backend refuses, so self mode silently recorded nothing.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='WASAPI is Windows only')

import audio_capture  # noqa: E402
import audio_devices as audio_devices_module  # noqa: E402
import config  # noqa: E402
from wasapi_microphones import PREFIX, WasapiAudioDevices  # noqa: E402

TX = SimpleNamespace(id='{0.0.1.00000000}.{tx}', name='麦克风 (TX 301)', channels=1)
PICO = SimpleNamespace(id='{0.0.1.00000000}.{pico}', name='麦克风 (PicoStreamingMicrophone)', channels=2)


class FakeSoundcard:
    def __init__(self, microphones, default):
        self.microphones = microphones
        self.default = default

    def all_microphones(self, include_loopback=False):
        return list(self.microphones)

    def default_microphone(self):
        return self.default


def fake_devices(microphones, default):
    soundcard = FakeSoundcard(microphones, default)
    return WasapiAudioDevices(backend=lambda: soundcard, apartment=nullcontext)


@pytest.fixture
def mic_config(monkeypatch):
    monkeypatch.setattr(config, 'MIC_DEVICE_ID', None)
    monkeypatch.setattr(config, 'MIC_DEVICE_INDEX', None)


def test_input_devices_endpoint_offers_recording_ids(monkeypatch, mic_config):
    from ui import app as ui_app

    monkeypatch.setattr(audio_devices_module, 'audio_devices', fake_devices([TX, PICO], TX))
    config.MIC_DEVICE_ID = PREFIX + PICO.id

    response = ui_app.app.test_client().get('/api/audio/input-devices')

    assert response.status_code == 200
    data = response.get_json()
    assert [d['id'] for d in data['devices']] == [PREFIX + TX.id, PREFIX + PICO.id]
    assert [d['name'] for d in data['devices']] == [TX.name, PICO.name]
    assert data['default_id'] == PREFIX + TX.id
    assert data['default_name'] == TX.name
    assert data['selected_id'] == PREFIX + PICO.id
    assert data['follows_default'] is True


def test_same_name_devices_get_different_suffixes(monkeypatch, mic_config):
    from ui import app as ui_app

    first = SimpleNamespace(id='{a}', name='USB Mic', channels=1)
    second = SimpleNamespace(id='{b}', name='USB Mic', channels=1)
    monkeypatch.setattr(audio_devices_module, 'audio_devices', fake_devices([first, second], first))

    devices = ui_app.app.test_client().get('/api/audio/input-devices').get_json()['devices']

    suffixes = [d['label_suffix'] for d in devices]
    assert all(suffixes) and suffixes[0] != suffixes[1]


def test_web_selection_sets_recording_device(mic_config):
    from ui import app as ui_app

    config.MIC_DEVICE_INDEX = 25
    success, _message_id, _message = ui_app.update_config(
        {'mic_control': {'mic_device_id': PREFIX + PICO.id}}
    )

    assert success is True
    assert config.MIC_DEVICE_ID == PREFIX + PICO.id
    assert config.MIC_DEVICE_INDEX is None
    mic = ui_app.app.test_client().get('/api/config').get_json()['mic_control']
    assert mic['mic_device_id'] == PREFIX + PICO.id

    ui_app.update_config({'mic_control': {'mic_device_id': None}})
    assert config.MIC_DEVICE_ID is None


def test_legacy_index_falls_back_to_system_default(mic_config, capsys):
    from ui import app as ui_app

    config.MIC_DEVICE_ID = PREFIX + PICO.id
    success, _message_id, _message = ui_app.update_config({'mic_control': {'mic_device_index': 25}})

    assert success is True
    assert config.MIC_DEVICE_ID is None
    assert config.MIC_DEVICE_INDEX is None
    assert '旧版麦克风序号' in capsys.readouterr().out


def _capture_state(capture_device_id):
    return SimpleNamespace(
        audio_executor=None,
        stream=object(),
        capture_device_id=capture_device_id,
        mic=None,
    )


def _run_maintain(monkeypatch, devices, state):
    calls = []

    async def fake_close(s):
        calls.append('close')

    async def fake_init(s):
        calls.append('init')
        s.capture_device_id = devices.resolve(config.MIC_DEVICE_ID)

    monkeypatch.setattr(audio_capture, 'audio_devices', devices)
    monkeypatch.setattr(audio_capture, 'close_audio_stream', fake_close)
    monkeypatch.setattr(audio_capture, 'init_audio_stream', fake_init)
    ready = asyncio.run(audio_capture.maintain_audio_source(state))
    return ready, calls


def test_capture_keeps_running_on_system_default(monkeypatch, mic_config):
    devices = fake_devices([TX, PICO], TX)
    state = _capture_state(PREFIX + TX.id)

    ready, calls = _run_maintain(monkeypatch, devices, state)

    assert ready is True
    assert calls == []


def test_capture_switches_to_the_selected_microphone(monkeypatch, mic_config):
    devices = fake_devices([TX, PICO], TX)
    state = _capture_state(PREFIX + TX.id)
    config.MIC_DEVICE_ID = PREFIX + PICO.id

    ready, calls = _run_maintain(monkeypatch, devices, state)

    assert ready is True
    assert calls == ['close', 'init']
    assert state.capture_device_id == PREFIX + PICO.id


def test_unavailable_microphone_is_reported_in_the_web_log(monkeypatch, mic_config, capsys):
    devices = fake_devices([TX], TX)
    state = _capture_state(PREFIX + TX.id)
    config.MIC_DEVICE_ID = PREFIX + PICO.id

    ready, _calls = _run_maintain(monkeypatch, devices, state)

    assert ready is False
    assert '[Audio] 麦克风暂不可用' in capsys.readouterr().out
