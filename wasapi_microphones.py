"""Live Windows microphone discovery and recording by exact WASAPI endpoint ID.

Enumeration never reinitializes the audio engine or touches an active recorder.
COM enumeration scopes and recorder leases are created/closed on their owning
threads. Only plain endpoint IDs and metadata leave an enumeration scope.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import sys
import threading
import uuid
import warnings

import numpy as np

from audio_runtime_guard import MicrophoneSelectionError

PREFIX = 'wasapi:'
_backend = None
_backend_lock = threading.Lock()


@contextmanager
def com_apartment():
    ole32 = ctypes.WinDLL('ole32')
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    # S_OK/S_FALSE both own an initialization count. An existing STA is usable
    # after RPC_E_CHANGED_MODE, but we must not uninitialize someone else's COM.
    initialized = False
    try:
        result = ole32.CoInitializeEx(None, 0)
        if result >= 0:
            initialized = True
        elif result & 0xffffffff != 0x80010106:
            raise OSError(f'COM initialization failed: 0x{result & 0xffffffff:08x}')
        yield
    finally:
        if initialized:
            ole32.CoUninitialize()


def soundcard_backend():
    global _backend
    with _backend_lock:
        if _backend is None:
            def load():
                already_loaded = 'soundcard.mediafoundation' in sys.modules
                import soundcard
                if not already_loaded:
                    # SoundCard 0.4.6 initializes COM at import and incorrectly
                    # treats S_FALSE (already initialized MTA) as a failure.
                    # Import on a fresh thread, then balance its initialization
                    # there. Our scopes/leases own all subsequent COM lifetime.
                    from soundcard import mediafoundation
                    if mediafoundation._com.com_loaded:
                        mediafoundation._ole32.CoUninitialize()
                        mediafoundation._com.com_loaded = False
                return soundcard

            with ThreadPoolExecutor(max_workers=1, thread_name_prefix='wasapi-import') as pool:
                _backend = pool.submit(load).result()
            try:
                from soundcard.mediafoundation import SoundcardRuntimeWarning
                warnings.filterwarnings(
                    'ignore',
                    message='data discontinuity in recording',
                    category=SoundcardRuntimeWarning,
                )
            except Exception:
                pass
    return _backend


class _WaveFormatEx(ctypes.Structure):
    """mmreg.h WAVEFORMATEX (packed, 18 bytes)."""
    _pack_ = 1
    _fields_ = [('wFormatTag', ctypes.c_uint16), ('nChannels', ctypes.c_uint16),
                ('nSamplesPerSec', ctypes.c_uint32), ('nAvgBytesPerSec', ctypes.c_uint32),
                ('nBlockAlign', ctypes.c_uint16), ('wBitsPerSample', ctypes.c_uint16),
                ('cbSize', ctypes.c_uint16)]


class _WaveFormatExtensible(ctypes.Structure):
    """mmreg.h WAVEFORMATEXTENSIBLE (packed, 40 bytes).

    SoundCard's cdef of this struct is not packed, so its Samples and SubFormat
    fields sit 2-4 bytes off; the real layout is built here instead.
    """
    _pack_ = 1
    _fields_ = [('Format', _WaveFormatEx), ('wValidBitsPerSample', ctypes.c_uint16),
                ('dwChannelMask', ctypes.c_uint32), ('SubFormat', ctypes.c_ubyte * 16)]


_WAVE_FORMAT_EXTENSIBLE = 0xFFFE
_SUBTYPE_IEEE_FLOAT = uuid.UUID('00000003-0000-0010-8000-00aa00389b71').bytes_le
_SPEAKER_MASKS = {1: 0x4, 2: 0x3}  # front center / front left + right
# SoundCard 0.4.6's own flags: rate adjust | auto-convert PCM | better SRC | no persist
_STREAM_FLAGS = 0x00100000 | 0x80000000 | 0x08000000 | 0x00080000
_STREAM_FLAG_LOOPBACK = 0x00020000


def _float32_format(samplerate, channels):
    fmt = _WaveFormatExtensible()
    fmt.Format.wFormatTag = _WAVE_FORMAT_EXTENSIBLE
    fmt.Format.nChannels = channels
    fmt.Format.nSamplesPerSec = samplerate
    fmt.Format.nAvgBytesPerSec = samplerate * channels * 4
    fmt.Format.nBlockAlign = channels * 4
    fmt.Format.wBitsPerSample = 32
    fmt.Format.cbSize = ctypes.sizeof(_WaveFormatExtensible) - ctypes.sizeof(_WaveFormatEx)
    fmt.wValidBitsPerSample = 32
    fmt.dwChannelMask = _SPEAKER_MASKS.get(channels, 0)
    fmt.SubFormat[:] = _SUBTYPE_IEEE_FLOAT
    return fmt


def _mix_format_text(mf, client):
    """Describe the endpoint's shared-mode format for the log."""
    pp_format = mf._ffi.new('WAVEFORMATEXTENSIBLE**')
    try:
        mf._com.check_error(client[0][0].lpVtbl.GetMixFormat(client[0], pp_format))
    except Exception:
        return '格式未知'
    try:
        fmt = _WaveFormatEx.from_address(int(mf._ffi.cast('uintptr_t', pp_format[0])))
        return (f'格式 0x{fmt.wFormatTag:04X} / {fmt.nChannels}ch / '
                f'{fmt.nSamplesPerSec}Hz / {fmt.wBitsPerSample}bit')
    finally:
        mf._ole32.CoTaskMemFree(pp_format[0])


def _float32_recorder(mf, client, samplerate, channels, blocksize, isloopback):
    """A SoundCard _Recorder initialized with our own float32 format.

    Mirrors _AudioClient.__init__ of SoundCard 0.4.6 (pinned in
    requirements.txt) minus its mix-format assertions.
    """
    recorder = mf._Recorder.__new__(mf._Recorder)
    recorder._ptr = client
    recorder.channelmap = list(range(channels))
    if blocksize is None:
        blocksize = recorder.deviceperiod[0] * samplerate
    fmt = _float32_format(int(samplerate), channels)
    flags = _STREAM_FLAGS | (_STREAM_FLAG_LOOPBACK if isloopback else 0)
    hr = client[0][0].lpVtbl.Initialize(
        client[0], mf._ole32.AUDCLNT_SHAREMODE_SHARED, flags,
        int(blocksize / samplerate * 10_000_000), 0,
        mf._ffi.cast('WAVEFORMATEXTENSIBLE*', ctypes.addressof(fmt)), mf._ffi.NULL)
    mf._com.check_error(hr)
    recorder.samplerate = samplerate
    recorder._idle_start_time = None
    return recorder


def _open_recorder(microphone, samplerate, channels, blocksize):
    """microphone.recorder(), but also for endpoints SoundCard cannot parse.

    SoundCard asserts the shared-mode mix format is float32
    WAVEFORMATEXTENSIBLE before IAudioClient::Initialize. Some drivers report
    plain WAVEFORMATEX / PCM, and recorder() dies with a bare AssertionError
    (2026-10-17 on a friend's PC: self mode never started). Shared mode with
    auto-convert PCM accepts any format, so ask for float32 directly.
    """
    from soundcard import mediafoundation as mf

    channels = int(channels)
    isloopback = bool(getattr(microphone, 'isloopback', False))
    client = microphone._audio_client()
    try:
        return mf._Recorder(client, samplerate, channels, blocksize, isloopback)
    except AssertionError:
        pass  # the assertions run before Initialize, so the client is still fresh
    except BaseException:
        mf._com.release(client)
        raise
    print(f'[Audio] 麦克风「{microphone.name}」的系统{_mix_format_text(mf, client)}，'
          '改用 32 位浮点格式打开')
    try:
        return _float32_recorder(mf, client, samplerate, channels, blocksize, isloopback)
    except BaseException:
        mf._com.release(client)
        raise


class WasapiStream:
    """Expose the PCM16 read/close interface consumed by audio_capture."""

    def __init__(self, microphone, rate, channels, blocksize):
        self.context = _open_recorder(microphone, rate, channels, blocksize)
        self.recorder = self.context.__enter__()

    def read(self, frames, exception_on_overflow=False):
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message='data discontinuity in recording')
            data = self.recorder.record(numframes=frames)
        samples = np.nan_to_num(np.asarray(data), nan=0.0, posinf=1.0, neginf=-1.0)
        return np.clip(np.rint(samples * 32768.0), -32768, 32767).astype(np.int16).tobytes()

    def stop_stream(self):
        self.close()

    def close(self):
        if self.recorder is not None:
            self.recorder = None
            self.context.__exit__(None, None, None)


class WasapiSession:
    def __init__(self, microphone, apartment):
        self.microphone = microphone
        self.apartment = apartment
        self.device_id = PREFIX + str(microphone.id)
        self.info = dict(index=0, name=microphone.name, maxInputChannels=microphone.channels,
                         defaultSampleRate=48000, capture_all_channels=True)

    def get_device_info_by_index(self, index):
        if index != 0:
            raise MicrophoneSelectionError('Invalid microphone session index')
        return self.info

    def get_default_input_device_info(self):
        return self.info

    def open(self, *, rate, channels, frames_per_buffer, input_device_index=0, **kwargs):
        self.get_device_info_by_index(input_device_index)
        return WasapiStream(self.microphone, rate, channels, frames_per_buffer)

    def close(self):
        if self.apartment is not None:
            apartment, self.apartment = self.apartment, None
            apartment.__exit__(None, None, None)


class WasapiAudioDevices:
    supports_hotplug = True

    def __init__(self, backend=None, apartment=com_apartment):
        self.backend = backend or soundcard_backend
        self.apartment = apartment
        self.active_captures = 0

    def refresh(self):
        # Every list_inputs call enumerates the current OS endpoints.
        pass

    def shutdown(self):
        # Recorder leases belong to the audio executor, which closes them.
        pass

    def list_inputs(self):
        with self.apartment():
            sc = self.backend()
            microphones = sc.all_microphones(include_loopback=False)
            devices = []
            for microphone in microphones:
                endpoint = str(microphone.id)
                try:
                    name, channels = microphone.name, microphone.channels
                except RuntimeError:
                    # An endpoint can disappear between enumeration and lookup.
                    continue
                devices.append(dict(id=PREFIX + endpoint, name=name,
                                    max_input_channels=channels,
                                    label_suffix=hashlib.sha256(endpoint.encode()).hexdigest()[:8]))
            try:
                default = sc.default_microphone()
                default_id, default_name = PREFIX + str(default.id), default.name
            except RuntimeError:
                default_id, default_name = None, None
        return dict(devices=devices, default_id=default_id, default_name=default_name)

    @staticmethod
    def validate(token, legacy_index=None):
        if legacy_index is not None and not token:
            raise MicrophoneSelectionError('旧麦克风序号无法安全恢复，请重新选择麦克风。')
        if token and (not isinstance(token, str) or not token.startswith(PREFIX)):
            raise MicrophoneSelectionError('麦克风选择已失效，请重新选择麦克风。')

    def _microphone(self, sc, token, legacy_index=None):
        self.validate(token, legacy_index)
        if not token:
            return sc.default_microphone()
        # Never use SoundCard's name/fuzzy matching API for a saved ID.
        for microphone in sc.all_microphones(include_loopback=False):
            if PREFIX + str(microphone.id) == token:
                return microphone
        raise MicrophoneSelectionError('所选麦克风未连接，正在等待该设备恢复。')

    def resolve(self, token, legacy_index=None):
        with self.apartment():
            mic = self._microphone(self.backend(), token, legacy_index)
            return PREFIX + str(mic.id)

    def acquire(self, token, legacy_index=None):
        apartment = self.apartment()
        apartment.__enter__()
        try:
            microphone = self._microphone(self.backend(), token, legacy_index)
            session = WasapiSession(microphone, apartment)
        except Exception:
            apartment.__exit__(None, None, None)
            raise
        self.active_captures += 1
        return session, 0

    def release(self, session):
        try:
            session.close()
        finally:
            self.active_captures = max(0, self.active_captures - 1)
