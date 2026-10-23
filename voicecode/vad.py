"""Endpointing: cut one spoken turn out of a live PCM stream.

Pure block-based state machine so it can be tested without a microphone.
The stream is 16-bit little-endian mono at ``sample_rate``.
"""

from __future__ import annotations

from collections import deque
from typing import BinaryIO, Callable

BLOCK_MS = 30


def _rms(block: bytes) -> float:
    try:
        import numpy as np

        data = np.frombuffer(block, dtype="<i2").astype("float32") / 32768.0
        return float(np.sqrt(np.mean(data * data))) if data.size else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def read_utterance(
    stream: BinaryIO,
    *,
    sample_rate: int = 16000,
    threshold: float = 0.008,
    silence_ms: int = 800,
    min_speech_ms: int = 300,
    max_seconds: float = 60.0,
    preroll_ms: int = 300,
    wait_seconds: float = 30.0,
    start_blocks: int = 2,
    warmup_ms: int = 0,
    on_phase: Callable[[str], None] | None = None,
) -> bytes | None:
    """Read until one utterance ends. Returns raw PCM, or None on silence.

    ``on_phase`` is called with "waiting", "recording", and "done" as the
    state changes, which lets the CLI print a prompt without polling.
    """
    block_samples = max(1, sample_rate * BLOCK_MS // 1000)
    block_bytes = block_samples * 2
    block_s = BLOCK_MS / 1000.0

    preroll = deque(maxlen=max(1, int(preroll_ms / BLOCK_MS)))
    frames: list[bytes] = []
    phase = "waiting"
    if on_phase:
        on_phase(phase)

    trigger = 0
    silent = 0
    recorded = 0.0
    waited = 0.0
    warmup_blocks = max(0, int(warmup_ms / BLOCK_MS))

    while True:
        block = stream.read(block_bytes)
        if not block:
            return None

        if warmup_blocks:
            # Discard the device wake-up pop (a DC step that decays for a few
            # hundred milliseconds) instead of treating it as speech.
            warmup_blocks -= 1
            continue

        level = _rms(block)
        waited += block_s

        if phase == "waiting":
            preroll.append(block)
            trigger = trigger + 1 if level > threshold else 0
            if trigger >= start_blocks:
                frames = list(preroll)
                recorded = len(frames) * block_s
                silent = 0
                phase = "recording"
                if on_phase:
                    on_phase(phase)
            elif waited >= wait_seconds:
                return None
            continue

        frames.append(block)
        recorded += block_s
        silent = silent + 1 if level < threshold * 0.6 else 0

        if recorded >= max_seconds:
            break
        if recorded * 1000 >= min_speech_ms and silent * BLOCK_MS >= silence_ms:
            break

    if on_phase:
        on_phase("done")
    if recorded * 1000 < min_speech_ms:
        return None
    return b"".join(frames)
