from io import BytesIO

import numpy as np

from voicecode.vad import BLOCK_MS, read_utterance

RATE = 16000
BLOCK_BYTES = RATE * BLOCK_MS // 1000 * 2


def pcm(seconds: float, amplitude: float = 0.0) -> bytes:
    samples = int(RATE * seconds)
    data = (np.sin(np.arange(samples) * 0.1) * amplitude * 32767).astype("<i2")
    return data.tobytes()


def blocks(seconds: float, amplitude: float) -> bytes:
    raw = pcm(seconds, amplitude)
    size = BLOCK_BYTES
    return raw[: len(raw) // size * size]


def test_detects_speech_and_stops_after_silence():
    stream = BytesIO(
        blocks(0.3, 0.0) + blocks(1.0, 0.2) + blocks(1.2, 0.0) + blocks(1.0, 0.2)
    )
    audio = read_utterance(stream, threshold=0.008, silence_ms=600)
    assert audio is not None
    duration = len(audio) / 2 / RATE
    assert 0.9 < duration < 2.0


def test_returns_none_on_silence():
    stream = BytesIO(blocks(1.0, 0.0))
    assert read_utterance(stream, wait_seconds=0.5) is None


def test_ignores_short_blip_then_catches_real_speech():
    stream = BytesIO(
        blocks(0.2, 0.0) + blocks(0.05, 0.3) + blocks(0.5, 0.0) + blocks(0.8, 0.2) + blocks(0.8, 0.0)
    )
    audio = read_utterance(stream, threshold=0.008, min_speech_ms=300, silence_ms=600)
    assert audio is not None
    assert len(audio) / 2 / RATE > 0.6


def test_max_seconds_caps_the_turn():
    stream = BytesIO(blocks(3.0, 0.2) + blocks(1.0, 0.0))
    audio = read_utterance(stream, max_seconds=1.0, silence_ms=5000)
    assert audio is not None
    assert 0.9 < len(audio) / 2 / RATE < 1.2


def test_warmup_ignores_device_pop():
    stream = BytesIO(blocks(0.4, 0.9) + blocks(1.0, 0.0))
    assert read_utterance(stream, wait_seconds=0.5, warmup_ms=400) is None


def test_speech_after_warmup_is_still_detected():
    stream = BytesIO(blocks(0.4, 0.9) + blocks(0.3, 0.0) + blocks(0.8, 0.2) + blocks(1.0, 0.0))
    audio = read_utterance(stream, warmup_ms=400, wait_seconds=5)
    assert audio is not None
    assert len(audio) / 2 / RATE > 0.5
