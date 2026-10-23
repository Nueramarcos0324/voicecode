"""Text-to-speech backends."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .audio import AudioError, play


class TTSError(RuntimeError):
    pass


def _timing(label: str, seconds: float) -> None:
    if os.environ.get("VOICECODE_TIMINGS") == "1":
        print(f"voicecode: timing {label}: {seconds * 1000:.0f} ms", file=sys.stderr)


def _http_speak(text: str, cfg: dict[str, Any]) -> Path:
    tts = cfg["tts"]
    url = str(tts["url"]).rstrip("/")
    if not url.endswith("/speak"):
        url += "/speak"
    body = json.dumps({"text": text, "speed": float(tts.get("speed", 1.0))}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TTSError(f"TTS server {url} failed: {exc}") from exc
    if not data:
        raise TTSError("TTS server returned no audio")
    if "wav" not in ctype and not data.startswith(b"RIFF"):
        raise TTSError(f"TTS server returned {ctype or 'unknown data'}, expected WAV")
    handle = tempfile.NamedTemporaryFile(prefix="voicecode-tts-", suffix=".wav", delete=False)
    handle.write(data)
    handle.close()
    return Path(handle.name)


def _piper_speak(text: str, cfg: dict[str, Any]) -> Path:
    tts = cfg["tts"]
    model = str(tts.get("piper_model") or "").strip()
    if not model:
        raise TTSError("tts.piper_model is not configured")
    fd, name = tempfile.mkstemp(prefix="voicecode-tts-", suffix=".wav")
    os.close(fd)
    out = Path(name)
    cmd = [str(tts.get("piper_binary") or "piper"), "--model", model, "--output_file", str(out)]
    proc = subprocess.run(cmd, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        raise TTSError(proc.stderr.decode("utf-8", "replace").strip() or "piper failed")
    return out


def _command_speak(text: str, cfg: dict[str, Any]) -> Path:
    template = str(cfg["tts"].get("command") or "").strip()
    if not template:
        raise TTSError("tts.command is not configured")
    fd, name = tempfile.mkstemp(prefix="voicecode-tts-", suffix=".wav")
    os.close(fd)
    out = Path(name)
    parts = [part.replace("{out}", str(out)) for part in shlex.split(template)]
    proc = subprocess.run(parts, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        raise TTSError(proc.stderr.decode("utf-8", "replace").strip() or "TTS command failed")
    return out


def synthesize(text: str, cfg: dict[str, Any]) -> Path | None:
    engine = str(cfg["tts"].get("engine") or "none").lower()
    if engine == "none" or not text.strip():
        return None
    if engine == "http":
        return _http_speak(text, cfg)
    if engine == "piper":
        return _piper_speak(text, cfg)
    if engine == "command":
        return _command_speak(text, cfg)
    raise TTSError(f"unknown tts.engine {engine!r}")


def speak(text: str, cfg: dict[str, Any], dry_run: bool = False, keep: bool = False) -> Path | None:
    started = time.monotonic()
    path = synthesize(text, cfg)
    _timing("tts_synth", time.monotonic() - started)
    if path is None:
        return None
    try:
        if not dry_run:
            play(path, cfg)
        return path
    except AudioError as exc:
        raise TTSError(str(exc)) from exc
    finally:
        if not dry_run and not keep:
            path.unlink(missing_ok=True)
