"""Local speech-to-text.

Two backends:

* ``voxtype`` — reuse the push-to-talk daemon's whisper model via
  ``voxtype transcribe <file>``. No second model download, and it is the
  backend that already works on an Omarchy desktop.
* ``faster-whisper`` — self-contained fallback, loaded in-process.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from .text import strip_ansi

_MODEL = None
_MODEL_KEY: tuple[str, str, str] | None = None
_LOCK = threading.Lock()
_LOG_LINE = re.compile(r"^\s*\[?\d{4}-\d\d-\d\dT")


class STTError(RuntimeError):
    pass


def _parse_voxtype(output: str) -> str:
    lines = []
    for raw in strip_ansi(output).splitlines():
        line = raw.strip()
        if not line:
            continue
        if _LOG_LINE.match(line):
            continue
        if line.startswith(("INFO", "WARN", "DEBUG", "ERROR", "Transcription completed")):
            continue
        if "whisper_init" in line:
            continue
        lines.append(line)
    return lines[-1] if lines else ""


def _voxtype(path: Path, cfg: dict[str, Any]) -> str:
    binary = str(cfg["stt"].get("voxtype_binary") or "voxtype")
    if not shutil.which(binary):
        raise STTError(f"{binary} is not on PATH")
    try:
        proc = subprocess.run(
            [binary, "transcribe", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise STTError("voxtype transcribe timed out") from None
    text = _parse_voxtype(proc.stdout)
    if not text and proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise STTError("voxtype failed: " + (" | ".join(tail) or f"exit {proc.returncode}"))
    return text


def _load(cfg: dict[str, Any]):
    global _MODEL, _MODEL_KEY
    stt = cfg["stt"]
    key = (str(stt["model"]), str(stt["device"]), str(stt["compute_type"]))
    with _LOCK:
        if _MODEL is not None and _MODEL_KEY == key:
            return _MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on install
            raise STTError(
                "faster-whisper is not installed; install voxtype or run: pip install faster-whisper"
            ) from exc
        try:
            _MODEL = WhisperModel(key[0], device=key[1], compute_type=key[2])
        except Exception as exc:  # noqa: BLE001 - ctranslate2 raises many types
            raise STTError(f"cannot load whisper model {key[0]!r}: {exc}") from exc
        _MODEL_KEY = key
        return _MODEL


def preload(cfg: dict[str, Any]) -> None:
    """Load the local STT model ahead of the first utterance (best effort)."""
    engine = str(cfg["stt"].get("engine") or "auto").lower()
    if engine not in ("auto", "faster-whisper"):
        return
    try:
        import importlib.util

        if importlib.util.find_spec("faster_whisper"):
            _load(cfg)
    except Exception:  # noqa: BLE001 - a failed preload must not stop chat
        pass


def _faster_whisper(path: Path, cfg: dict[str, Any]) -> str:
    model = _load(cfg)
    language = str(cfg["stt"].get("language") or "").strip() or None
    try:
        segments, _info = model.transcribe(
            str(path),
            language=language,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:  # noqa: BLE001
        raise STTError(f"transcription failed: {exc}") from exc


def transcribe(path: Path, cfg: dict[str, Any]) -> str:
    engine = str(cfg["stt"].get("engine") or "auto").lower()
    if engine == "voxtype":
        return _voxtype(path, cfg)
    if engine == "faster-whisper":
        return _faster_whisper(path, cfg)
    if engine == "auto":
        try:
            import importlib.util

            if importlib.util.find_spec("faster_whisper"):
                return _faster_whisper(path, cfg)
        except Exception:  # noqa: BLE001
            pass
        return _voxtype(path, cfg)
    raise STTError(f"unknown stt.engine {engine!r}")
