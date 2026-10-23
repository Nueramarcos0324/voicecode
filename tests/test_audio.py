import math
import struct
import wave

import numpy as np

from voicecode.audio import trim_silence, wav_duration


def _write(path, samples, rate=16000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def _tone(seconds, rate=16000, freq=220.0, amp=0.3):
    return [int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(int(seconds * rate))]


def test_trim_removes_leading_and_trailing_silence(tmp_path):
    path = tmp_path / "clip.wav"
    samples = [0] * 8000 + _tone(0.6) + [0] * 8000
    _write(path, samples)
    before = wav_duration(path)
    trim_silence(path, threshold=0.008)
    after = wav_duration(path)
    assert after < before - 0.5
    assert 0.5 < after < 1.1


def test_trim_leaves_silent_file_alone(tmp_path):
    path = tmp_path / "silent.wav"
    _write(path, [0] * 8000)
    before = wav_duration(path)
    trim_silence(path, threshold=0.008)
    assert abs(wav_duration(path) - before) < 0.01
