"""Configuration and XDG paths for voicecode."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

APP = "voicecode"

DEFAULTS: dict[str, Any] = {
    "stt": {
        "engine": "auto",
        "voxtype_binary": "voxtype",
        "model": "small.en",
        "device": "cpu",
        "compute_type": "int8",
        "language": "en",
    },
    "tts": {
        # http | piper | command | none
        "engine": "none",
        "url": "http://127.0.0.1:8765",
        "speed": 0.95,
        "max_chars": 600,
        "piper_binary": "piper",
        "piper_model": "",
        "command": "",
    },
    "opencode": {
        "binary": "opencode",
        "model": "",
        "agent": "",
        "auto_approve": False,
        "timeout": 300,
        # Prepended to every question. Answers are spoken, so the default asks
        # for short plain text and no tool calls. Set to "" to disable.
        "prompt_prefix": (
            "You are in a live voice conversation. Answer in at most two short "
            "sentences of plain spoken text. No markdown, lists, or code, and do "
            "not use tools."
        ),
    },
    "audio": {
        "input_device": "",
        "output_command": "",
        "max_seconds": 60,
        "min_seconds": 0.4,
        "silence_threshold": 0.008,
        "sample_rate": 16000,
    },
    "session": {
        "keep_context": True,
    },
    "chat": {
        "model": "",
        "silence_ms": 800,
        "min_speech_ms": 300,
        "listen_timeout": 30,
        "max_turns": 0,
        "warmup_ms": 400,
    },
    "ui": {
        "notify": True,
    },
}


def _base_dir(env: str, fallback: Path) -> Path:
    raw = os.environ.get(env)
    return Path(raw).expanduser() if raw else fallback


def config_dir() -> Path:
    return _base_dir("XDG_CONFIG_HOME", Path.home() / ".config") / APP


def state_dir() -> Path:
    return _base_dir("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP


def config_path() -> Path:
    return config_dir() / "config.json"


def session_path() -> Path:
    return state_dir() / "session.json"


def recording_path() -> Path:
    return state_dir() / "recording.wav"


def recording_state_path() -> Path:
    return state_dir() / "recording.json"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"voicecode: cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"voicecode: {path} must contain a JSON object")
    return deep_merge(DEFAULTS, raw)


def write_default_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = deep_merge(DEFAULTS, overrides or {})
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def save_session(session_id: str | None) -> None:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if session_id:
        path.write_text(json.dumps({"id": session_id}) + "\n")
    else:
        path.unlink(missing_ok=True)


def load_session() -> str | None:
    path = session_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("id"), str):
        return data["id"]
    return None
