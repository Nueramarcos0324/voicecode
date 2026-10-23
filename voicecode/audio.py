"""Microphone capture and playback, via arecord and PipeWire/PulseAudio."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Callable

from .config import recording_path, recording_state_path
from .vad import read_utterance

RECORD_RATE = 16000


class AudioError(RuntimeError):
    pass


def _save_state(pid: int, path: Path, started: float) -> None:
    state = recording_state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"pid": pid, "wav": str(path), "started": started}))


def read_state() -> dict[str, Any] | None:
    path = recording_state_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
        return None
    return data


def clear_state() -> None:
    recording_state_path().unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pick_source(cfg: dict[str, Any]) -> str | None:
    """Return the configured source, or the first real microphone."""
    configured = str(cfg["audio"].get("input_device") or "").strip()
    if configured:
        return configured
    sources = list_sources()
    for _index, name, monitor in sources:
        if not monitor:
            return name
    return None


def _require_source(cfg: dict[str, Any]) -> str | None:
    source = _pick_source(cfg)
    if source is None:
        sources = list_sources()
        if sources and all(monitor for _index, _name, monitor in sources):
            raise AudioError(
                "no microphone found (only .monitor sources); plug one in or set audio.input_device"
            )
    return source


def list_sources() -> list[tuple[int, str, bool]]:
    """List PipeWire/Pulse sources as (index, name, is_monitor)."""
    import shutil

    if not shutil.which("pactl"):
        return []
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sources"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        name = parts[1]
        result.append((index, name, name.endswith(".monitor")))
    return result


def start_recording(cfg: dict[str, Any]) -> Path:
    audio = cfg["audio"]
    state = read_state()
    if state and _pid_alive(state["pid"]):
        raise AudioError(f"already recording (pid {state['pid']})")

    out = recording_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)

    from shutil import which

    source = _require_source(cfg)
    max_seconds = int(audio.get("max_seconds", 60))
    if which("pw-record"):
        cmd = [
            "timeout",
            "-s",
            "INT",
            str(max_seconds),
            "pw-record",
            "--rate",
            "16000",
            "--channels",
            "1",
            "--format",
            "s16",
        ]
        if source:
            cmd += ["--target", source]
        cmd.append(str(out))
    elif which("parec"):
        cmd = [
            "timeout",
            "-s",
            "INT",
            str(max_seconds),
            "parec",
            "--format=s16le",
            "--rate=16000",
            "--channels=1",
            "--file-format=wav",
        ]
        if source:
            cmd.append(f"--device={source}")
        cmd.append(str(out))
    else:
        cmd = [
            "arecord",
            "-q",
            "-f",
            "S16_LE",
            "-r",
            str(RECORD_RATE),
            "-c",
            "1",
            "-t",
            "wav",
            "-d",
            str(max_seconds),
        ]
        device = str(audio.get("input_device") or "").strip()
        if device:
            cmd += ["-D", device]
        cmd.append(str(out))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(0.25)
    if proc.poll() is not None:
        err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
        raise AudioError(f"recording failed: {err or 'unknown error'}")
    _save_state(proc.pid, out, time.time())
    return out


def stop_recording(cfg: dict[str, Any]) -> Path:
    state = read_state()
    if not state:
        raise AudioError("not recording")
    pid = state["pid"]
    wav = Path(state["wav"])
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.05)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    clear_state()
    if not wav.exists() or wav.stat().st_size < 128:
        raise AudioError("recording produced no audio")
    return wav


def is_recording() -> int | None:
    state = read_state()
    if not state:
        return None
    return state["pid"] if _pid_alive(state["pid"]) else None


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except (OSError, wave.Error, ZeroDivisionError):
        return 0.0


def trim_silence(path: Path, threshold: float = 0.008) -> Path:
    """Trim leading/trailing silence in place. No-op on failure."""
    try:
        import numpy as np

        with wave.open(str(path), "rb") as wf:
            params = wf.getparams()
            raw = wf.readframes(wf.getnframes())
        if params.sampwidth != 2 or params.nchannels != 1:
            return path
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return path
        frame = max(1, params.framerate // 33)  # ~30 ms
        count = samples.size // frame
        if count < 4:
            return path
        frames = samples[: count * frame].reshape(count, frame)
        rms = np.sqrt(np.mean(frames * frames, axis=1))
        noise = float(np.percentile(rms, 10))
        cut = max(float(threshold), noise * 2.5)
        active = np.flatnonzero(rms > cut)
        if active.size == 0:
            return path
        pad = 6  # ~180 ms of context on each side
        lo = max(0, int(active[0]) - pad) * frame
        hi = min(count, int(active[-1]) + 1 + pad) * frame
        trimmed = samples[lo:hi]
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(params.framerate)
            out.writeframes((trimmed * 32767.0).astype(np.int16).tobytes())
        return path
    except Exception:
        return path


def audio_levels(path: Path) -> tuple[float, float]:
    """Return (peak, rms) of a 16-bit mono WAV, or (0, 0)."""
    try:
        import numpy as np

        with wave.open(str(path), "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            width = wf.getsampwidth()
        if width != 2:
            return 0.0, 0.0
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if data.size == 0:
            return 0.0, 0.0
        return float(np.max(np.abs(data))), float(np.sqrt(np.mean(data * data)))
    except Exception:
        return 0.0, 0.0


def _player(cfg: dict[str, Any]) -> list[str]:
    custom = str(cfg["audio"].get("output_command") or "").strip()
    if custom:
        return shlex.split(custom)
    for name in ("paplay", "pw-play", "aplay"):
        from shutil import which

        if which(name):
            return [name]
    raise AudioError("no audio player found (install paplay, pw-play, or aplay)")


def play(path: Path, cfg: dict[str, Any]) -> None:
    cmd = _player(cfg)
    if any("{file}" in part for part in cmd):
        cmd = [part.replace("{file}", str(path)) for part in cmd]
    else:
        cmd.append(str(path))
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise AudioError(proc.stderr.decode("utf-8", "replace").strip() or "playback failed")


def _raw_recorder(cfg: dict[str, Any]) -> list[str]:
    """Command that streams raw s16le mono PCM at RECORD_RATE on stdout."""
    from shutil import which

    source = _require_source(cfg)
    if which("pw-record"):
        cmd = [
            "pw-record",
            "--container",
            "raw",
            "--rate",
            str(RECORD_RATE),
            "--channels",
            "1",
            "--format",
            "s16",
        ]
        if source:
            cmd += ["--target", source]
        cmd.append("-")
        return cmd
    if which("parec"):
        cmd = ["parec", "--format=s16le", f"--rate={RECORD_RATE}", "--channels=1", "--raw"]
        if source:
            cmd.append(f"--device={source}")
        return cmd
    if which("arecord"):
        cmd = ["arecord", "-q", "-f", "S16_LE", "-r", str(RECORD_RATE), "-c", "1", "-t", "raw"]
        device = str(cfg["audio"].get("input_device") or "").strip()
        if device:
            cmd += ["-D", device]
        return cmd
    raise AudioError("no recorder found (install pipewire, pulseaudio-utils, or alsa-utils)")


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def open_recorder(cfg: dict[str, Any]) -> subprocess.Popen:
    """Start a raw PCM recorder that stays open across conversation turns."""
    return subprocess.Popen(
        _raw_recorder(cfg),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )


def write_pcm(pcm: bytes, path: Path | None = None) -> Path:
    out = path or recording_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RECORD_RATE)
        wf.writeframes(pcm)
    return out


def drain_stream(stream: Any, seconds: float = 0.8) -> None:
    """Throw away buffered audio (assistant echo, backlog) before listening."""
    import select

    fd = stream.fileno()
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (OSError, ValueError):
            break
        if not ready:
            break
        try:
            chunk = stream.read1(65536)
        except (BlockingIOError, OSError):
            break
        if not chunk:
            break


def stop_recorder(proc: subprocess.Popen) -> None:
    _stop_process(proc)


def record_utterance(
    cfg: dict[str, Any],
    wait_seconds: float | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> Path | None:
    """Record one hands-free turn: wait for speech, stop after silence.

    Returns a WAV path, or None if nobody spoke within the wait window.
    """
    chat = cfg.get("chat", {})
    audio = cfg["audio"]
    proc = open_recorder(cfg)
    pcm: bytes | None = None
    error = ""
    try:
        pcm = read_utterance(
            proc.stdout,
            sample_rate=RECORD_RATE,
            threshold=float(audio.get("silence_threshold", 0.008)),
            silence_ms=int(chat.get("silence_ms", 800)),
            min_speech_ms=int(chat.get("min_speech_ms", 300)),
            max_seconds=float(audio.get("max_seconds", 60)),
            wait_seconds=float(wait_seconds if wait_seconds is not None else chat.get("listen_timeout", 30)),
            warmup_ms=int(chat.get("warmup_ms", 400)),
            on_phase=on_phase,
        )
    finally:
        _stop_process(proc)
        try:
            error = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            pass

    if not pcm:
        if proc.returncode not in (0, -15, -2) and error:
            raise AudioError(f"recorder failed: {error}")
        return None
    return write_pcm(pcm)
