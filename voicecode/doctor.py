"""Preflight checks for voicecode."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import urllib.request
from typing import Any

from .config import config_path, load_session


def _ok(name: str, detail: str = "") -> tuple[bool, str]:
    return True, f"{name}: {detail or 'ok'}"


def _bad(name: str, detail: str) -> tuple[bool, str]:
    return False, f"{name}: {detail}"


def check(cfg: dict[str, Any]) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    if importlib.util.find_spec("faster_whisper"):
        results.append(_ok("faster-whisper", "installed"))
    else:
        results.append(_bad("faster-whisper", "missing; run pip install faster-whisper"))

    results.append(
        _ok("recorder", shutil.which("pw-record") or shutil.which("parec") or shutil.which("arecord") or "missing")
        if (shutil.which("pw-record") or shutil.which("parec") or shutil.which("arecord"))
        else _bad("recorder", "install pipewire (pw-record) or alsa-utils (arecord)")
    )
    player = next((name for name in ("paplay", "pw-play", "aplay") if shutil.which(name)), None)
    results.append(_ok("player", player) if player else _bad("player", "no paplay/pw-play/aplay"))

    binary = str(cfg["opencode"].get("binary") or "opencode").split()[0]
    if shutil.which(binary):
        try:
            version = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            version = "unknown"
        results.append(_ok("opencode", version))
    else:
        results.append(_bad("opencode", f"{binary} not on PATH"))

    tts = cfg["tts"]
    engine = str(tts.get("engine") or "none")
    if engine == "http":
        url = str(tts.get("url") or "").rstrip("/") + "/health"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                results.append(_ok("tts", f"http {url} -> {resp.status}"))
        except Exception as exc:  # noqa: BLE001
            results.append(_bad("tts", f"http {url} unreachable: {exc}"))
    elif engine == "piper":
        model = str(tts.get("piper_model") or "")
        if not model:
            results.append(_bad("tts", "piper model not configured"))
        elif not shutil.which(str(tts.get("piper_binary") or "piper")):
            results.append(_bad("tts", "piper binary not found"))
        else:
            results.append(_ok("tts", f"piper {model}"))
    elif engine == "command":
        results.append(_ok("tts", "command") if tts.get("command") else _bad("tts", "command empty"))
    else:
        results.append(_ok("tts", "disabled (voice output off)"))

    session = load_session()
    results.append(_ok("session", session or "new session on first ask"))
    results.append(_ok("config", str(config_path()) if config_path().exists() else "defaults"))
    return results
