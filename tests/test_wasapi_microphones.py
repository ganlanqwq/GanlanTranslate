"""Tests for wasapi_microphones: opening endpoints whose mix format SoundCard rejects."""

from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='WASAPI is Windows only')

import wasapi_microphones as wm  # noqa: E402

FLOAT_SUBTYPE = bytes.fromhex('0300000000001000800000aa00389b71')


@pytest.fixture(scope='module')
def mf():
    wm.soundcard_backend()
    from soundcard import mediafoundation
    return mediafoundation


class FakeAudioClient:
    """Stands in for IAudioClient**: client[0][0].lpVtbl.Method(client[0], ...)."""

    def __init__(self, mf, mix_format: bytes):
        self.mf = mf
        self.mix_format = mix_format
        self.initialized = None
        self.flags = None
        self.released = 0
        self.ole32 = ctypes.WinDLL('ole32')
        self.ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
        self.ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]

    def as_pointer(self):
        return [[SimpleNamespace(lpVtbl=self)]]

    def GetMixFormat(self, this, pp_format):
        # Real GetMixFormat hands out CoTaskMemAlloc memory; SoundCard's
        # struct is 44 bytes unpacked, so leave room for it to read.
        address = self.ole32.CoTaskMemAlloc(48)
        ctypes.memset(address, 0, 48)
        ctypes.memmove(address, self.mix_format, len(self.mix_format))
        pp_format[0] = self.mf._ffi.cast('WAVEFORMATEXTENSIBLE*', address)
        return 0

    def GetDevicePeriod(self, this, p_default, p_minimum):
        p_default[0] = 100_000
        p_minimum[0] = 30_000
        return 0

    def Initialize(self, this, sharemode, flags, duration, periodicity, p_format, session):
        address = int(self.mf._ffi.cast('uintptr_t', p_format))
        self.initialized = ctypes.string_at(address, 40)
        self.flags = flags
        return 0

    def Release(self, this):
        self.released += 1
        return 0


class FakeMicrophone:
    name = 'Fake Mic'
    isloopback = False

    def __init__(self, client):
        self.client = client

    def _audio_client(self):
        return self.client.as_pointer()


def pcm16_stereo_waveformatex() -> bytes:
    fmt = wm._WaveFormatEx(wFormatTag=1, nChannels=2, nSamplesPerSec=48000,
                           nAvgBytesPerSec=48000 * 4, nBlockAlign=4, wBitsPerSample=16, cbSize=0)
    return bytes(fmt)


def float32_extensible() -> bytes:
    return bytes(wm._float32_format(48000, 2))


def test_float32_format_layout():
    assert ctypes.sizeof(wm._WaveFormatEx) == 18
    assert ctypes.sizeof(wm._WaveFormatExtensible) == 40
    data = bytes(wm._float32_format(16000, 1))
    fmt = wm._WaveFormatEx.from_buffer_copy(data[:18])
    assert (fmt.wFormatTag, fmt.nChannels, fmt.nSamplesPerSec) == (0xFFFE, 1, 16000)
    assert (fmt.nAvgBytesPerSec, fmt.nBlockAlign, fmt.wBitsPerSample, fmt.cbSize) == (64000, 4, 32, 22)
    assert int.from_bytes(data[18:20], 'little') == 32      # wValidBitsPerSample
    assert int.from_bytes(data[20:24], 'little') == 0x4     # front center
    assert data[24:40] == FLOAT_SUBTYPE


def test_plain_pcm_endpoint_falls_back_to_float32(mf, capsys):
    client = FakeAudioClient(mf, pcm16_stereo_waveformatex())
    recorder = wm._open_recorder(FakeMicrophone(client), 16000, 2, 1024)

    assert isinstance(recorder, mf._Recorder)
    assert recorder.samplerate == 16000
    assert recorder.channelmap == [0, 1]
    assert client.initialized == bytes(wm._float32_format(16000, 2))
    assert client.flags == wm._STREAM_FLAGS
    assert client.released == 0
    out = capsys.readouterr().out
    assert 'Fake Mic' in out and '0x0001 / 2ch / 48000Hz / 16bit' in out


def test_float32_endpoint_keeps_soundcard_path(mf, capsys):
    client = FakeAudioClient(mf, float32_extensible())
    recorder = wm._open_recorder(FakeMicrophone(client), 16000, 1, 1024)

    assert isinstance(recorder, mf._Recorder)
    # SoundCard edits the device format in place; only the fallback prints.
    initialized = wm._WaveFormatEx.from_buffer_copy(client.initialized[:18])
    assert (initialized.wFormatTag, initialized.nChannels, initialized.nSamplesPerSec) == (0xFFFE, 1, 16000)
    assert capsys.readouterr().out == ''


def test_failed_fallback_releases_client(mf):
    client = FakeAudioClient(mf, pcm16_stereo_waveformatex())
    client.Initialize = lambda *args: 0x88890008 - (1 << 32)  # AUDCLNT_E_UNSUPPORTED_FORMAT

    with pytest.raises(Exception):
        wm._open_recorder(FakeMicrophone(client), 16000, 1, 1024)
    assert client.released == 1
