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
from typing import Any

from .config import recording_path, recording_state_path

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

    source = _pick_source(cfg)
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
