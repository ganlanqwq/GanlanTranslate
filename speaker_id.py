"""他人语音的轻量说话人区分。

环回音频是混音，做不到真正的声纹识别。这里用响度最高片段的
MFCC 加上基频：基频差明显时直接当成不同的人，避免所有句子都标成 A。
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_filterbank(
    sample_rate: int, n_fft: int, n_mels: int = 26
) -> np.ndarray:
    low = _hz_to_mel(np.array([0.0]))[0]
    high = _hz_to_mel(np.array([sample_rate / 2.0]))[0]
    points = np.linspace(low, high, n_mels + 2)
    hz = 700.0 * (10 ** (points / 2595.0) - 1.0)
    bins = np.floor((n_fft + 1) * hz / sample_rate).astype(int)
    filters = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for index in range(1, n_mels + 1):
        left, center, right = bins[index - 1], bins[index], bins[index + 1]
        if center <= left or right <= center:
            continue
        for bin_index in range(left, center):
            filters[index - 1, bin_index] = (bin_index - left) / (center - left)
        for bin_index in range(center, right):
            filters[index - 1, bin_index] = (right - bin_index) / (right - center)
    return filters


def _dct_matrix(n_ceps: int, n_mels: int) -> np.ndarray:
    rows = np.arange(n_ceps, dtype=np.float32)[:, None]
    cols = np.arange(n_mels, dtype=np.float32)[None, :]
    return np.cos(np.pi * rows * (cols + 0.5) / n_mels).astype(np.float32)


def _loudest_slice(audio: np.ndarray, sample_rate: int, seconds: float = 1.2) -> np.ndarray:
    window = int(sample_rate * seconds)
    if audio.size <= window:
        return audio
    hop = max(1, int(sample_rate * 0.1))
    best_index = 0
    best_energy = -1.0
    limit = audio.size - window + 1
    for index in range(0, limit, hop):
        chunk = audio[index : index + window]
        energy = float(np.dot(chunk, chunk))
        if energy > best_energy:
            best_energy = energy
            best_index = index
    return audio[best_index : best_index + window]


def _frame_f0(frame: np.ndarray, sample_rate: int) -> float:
    min_lag = max(1, int(sample_rate / 400))
    max_lag = int(sample_rate / 70)
    if frame.size <= max_lag + 2:
        return 0.0
    centered = frame - float(np.mean(frame))
    energy = float(np.dot(centered, centered))
    if energy < 1e-4:
        return 0.0
    corr = np.correlate(centered, centered, mode="full")
    corr = corr[corr.size // 2 :]
    region = corr[min_lag:max_lag]
    if region.size == 0:
        return 0.0
    lag = int(np.argmax(region)) + min_lag
    if float(corr[lag]) / energy < 0.35:
        return 0.0
    return float(sample_rate) / float(lag)


def extract_voice(audio: np.ndarray, sample_rate: int = 16000) -> tuple[np.ndarray, float]:
    """返回 (音色向量, 基频 Hz)。向量已做 L2 归一化。"""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size < 64:
        empty = np.zeros(28, dtype=np.float32)
        empty[0] = 1.0
        return empty, 0.0
    peak = float(np.max(np.abs(samples))) or 1.0
    samples = samples / peak
    samples = _loudest_slice(samples, sample_rate)

    frame = int(sample_rate * 0.025)
    hop = int(sample_rate * 0.010)
    if samples.size < frame:
        empty = np.zeros(28, dtype=np.float32)
        empty[0] = 1.0
        return empty, 0.0

    window = np.hanning(frame).astype(np.float32)
    n_fft = 512
    filters = _mel_filterbank(sample_rate, n_fft, 26)
    dct = _dct_matrix(13, 26)
    mfccs: List[np.ndarray] = []
    pitches: List[float] = []
    energies: List[float] = []
    for start in range(0, samples.size - frame + 1, hop):
        chunk = samples[start : start + frame]
        energy = float(np.dot(chunk, chunk))
        energies.append(energy)
        spec = np.abs(np.fft.rfft(chunk * window, n=n_fft)).astype(np.float32)
        mel = np.log(filters @ (spec**2) + 1e-6)
        mfccs.append(dct @ mel)
        pitches.append(_frame_f0(chunk, sample_rate))

    mfcc_arr = np.stack(mfccs, axis=0)
    energy_arr = np.asarray(energies, dtype=np.float32)
    voiced = energy_arr >= float(np.median(energy_arr))
    if not np.any(voiced):
        voiced = np.ones(mfcc_arr.shape[0], dtype=bool)
    used = mfcc_arr[voiced]
    mean = used.mean(axis=0)
    std = used.std(axis=0)
    pitch_arr = np.asarray(pitches, dtype=np.float32)
    voiced_pitch = pitch_arr[(pitch_arr > 0) & voiced[: pitch_arr.size]]
    f0 = float(np.median(voiced_pitch)) if voiced_pitch.size else 0.0
    f0_spread = float(np.std(voiced_pitch)) if voiced_pitch.size else 0.0
    feature = np.concatenate(
        [mean, std, np.array([f0 / 300.0, f0_spread / 80.0], dtype=np.float32)]
    ).astype(np.float32)
    norm = float(np.linalg.norm(feature)) or 1.0
    return feature / norm, f0


def extract_embedding(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    vector, _pitch = extract_voice(audio, sample_rate)
    return vector


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom <= 1e-8:
        return 0.0
    return float(np.dot(left, right) / denom)


@dataclass
class SpeakerRegistry:
    similarity_threshold: float = 0.88
    sample_rate: int = 16000
    pitch_split_hz: float = 85.0
    _speakers: Dict[str, Dict[str, object]] = field(default_factory=dict)
    _order: List[str] = field(default_factory=list)
    _last_label: Optional[str] = None

    def assign(self, audio: Optional[np.ndarray], sample_rate: Optional[int] = None) -> str:
        rate = int(sample_rate or self.sample_rate or 16000)
        if audio is None or len(audio) < 64:
            return self._last_label or "A"
        embedding, pitch = extract_voice(audio, rate)
        best_label = None
        best_score = -1.0
        for label in self._order:
            item = self._speakers[label]
            known_pitch = float(item["pitch"])
            score = _cosine(embedding, item["embedding"])  # type: ignore[arg-type]
            if pitch > 0 and known_pitch > 0:
                delta = abs(pitch - known_pitch)
                if delta >= self.pitch_split_hz:
                    continue
                score -= 0.08 * (delta / self.pitch_split_hz)
            if score > best_score:
                best_score = score
                best_label = label
        threshold = max(float(self.similarity_threshold), 0.84)
        if best_label is not None and best_score >= threshold:
            item = self._speakers[best_label]
            item["embedding"] = 0.8 * item["embedding"] + 0.2 * embedding  # type: ignore[operator]
            item["embedding"] = item["embedding"] / (
                float(np.linalg.norm(item["embedding"])) or 1.0
            )
            if pitch > 0:
                known = float(item["pitch"])
                item["pitch"] = known * 0.7 + pitch * 0.3 if known > 0 else pitch
            self._last_label = best_label
            print(
                f"[Others/Speaker] {best_label} 基频 {pitch:.0f}Hz 相似度 {best_score:.2f}"
            )
            return best_label

        label = self._next_label()
        self._speakers[label] = {"embedding": embedding, "pitch": pitch}
        self._order.append(label)
        self._last_label = label
        print(f"[Others/Speaker] 新说话人 {label} 基频 {pitch:.0f}Hz")
        return label

    def reset(self) -> None:
        self._speakers.clear()
        self._order.clear()
        self._last_label = None

    clear = reset

    @property
    def count(self) -> int:
        return len(self._order)

    def _next_label(self) -> str:
        used = set(self._order)
        for char in string.ascii_uppercase:
            if char not in used:
                return char
        return f"S{len(self._order) + 1}"
