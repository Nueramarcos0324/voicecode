"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .audio import (
    AudioError,
    RECORD_RATE,
    audio_levels,
    drain_stream,
    is_recording,
    list_sources,
    open_recorder,
    start_recording,
    stop_recorder,
    stop_recording,
    trim_silence,
    wav_duration,
    write_pcm,
)
from .config import load_config, load_session, save_session, state_dir, write_default_config, config_path
from .doctor import check as doctor_check
from .opencode import OpencodeError, ask as ask_opencode
from .stt import STTError, preload as preload_stt, transcribe
from .text import for_speech
from .tts import TTSError, speak
from .vad import read_utterance


def _err(message: str) -> None:
    print(f"voicecode: {message}", file=sys.stderr)


def _timing(label: str, seconds: float) -> None:
    """Print a stage duration on stderr when VOICECODE_TIMINGS=1."""
    if os.environ.get("VOICECODE_TIMINGS") == "1":
        _err(f"timing {label}: {seconds * 1000:.0f} ms")


def _speak_reply(reply: str, cfg: dict[str, Any], dry_run: bool) -> None:
    tts = cfg["tts"]
    if str(tts.get("engine") or "none") == "none":
        return
    spoken = for_speech(reply, int(tts.get("max_chars") or 600))
    if not spoken:
        return
    try:
        speak(spoken, cfg, dry_run=dry_run)
    except TTSError as exc:
        _err(f"speech failed: {exc}")


def _notify(text: str, cfg: dict[str, Any]) -> None:
    if not cfg["ui"].get("notify", True) or not shutil.which("notify-send"):
        return
    body = for_speech(text, 300)
    if not body:
        return
    try:
        subprocess.run(
            ["notify-send", "-a", "voicecode", "-t", "8000", "voicecode", body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _ask(text: str, cfg: dict[str, Any], no_speak: bool) -> str:
    if not text.strip():
        raise OpencodeError("empty prompt")
    _err("thinking…")
    session = load_session()
    started = time.monotonic()
    reply = ask_opencode(text, cfg, session)
    _timing("model", time.monotonic() - started)
    if cfg["session"].get("keep_context", True):
        save_session(reply.session_id)
    else:
        save_session(None)
    print(reply.text, flush=True)
    _notify(reply.text, cfg)
    if not no_speak:
        _speak_reply(reply.text, cfg, dry_run=False)
    return reply.text


def _transcribe_file(path: Path, cfg: dict[str, Any], in_place: bool = True) -> str:
    if not path.exists():
        raise STTError(f"no such file: {path}")
    duration = wav_duration(path)
    if duration < float(cfg["audio"].get("min_seconds", 0.4)):
        raise STTError(f"recording is too short ({duration:.2f}s)")
    if in_place:
        trim_silence(path, float(cfg["audio"].get("silence_threshold", 0.008)))
    peak, rms = audio_levels(path)
    if peak < 0.01:
        raise STTError("recording is silent (check the microphone)")
    _err(f"transcribing {wav_duration(path):.1f}s (peak {peak:.2f})…")
    return transcribe(path, cfg)


def cmd_start(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    path = start_recording(cfg)
    _err(f"recording -> {path} (release the key to send)")
    return 0


def cmd_stop(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    path = stop_recording(cfg)
    text = _transcribe_file(path, cfg)
    if not text:
        _err("no speech recognized")
        return 2
    if args.transcribe_only:
        print(text)
        return 0
    _err(f"you: {text}")
    _ask(text, cfg, no_speak=args.no_speak)
    return 0


def cmd_toggle(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    if is_recording():
        return cmd_stop(args, cfg)
    return cmd_start(args, cfg)


def cmd_status(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    pid = is_recording()
    print(f"recording: {'yes (pid %d)' % pid if pid else 'no'}")
    print(f"session:   {load_session() or '(new on first ask)'}")
    print(f"stt:       {cfg['stt']['model']} on {cfg['stt']['device']}")
    print(f"tts:       {cfg['tts']['engine']}")
    print(f"config:    {config_path()}")
    print(f"state:     {state_dir()}")
    return 0


def cmd_ask(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    text = " ".join(args.text).strip() or sys.stdin.read().strip()
    if args.model:
        cfg["opencode"]["model"] = args.model
    _ask(text, cfg, no_speak=args.no_speak)
    return 0


def cmd_listen(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    if args.wav:
        path = Path(args.wav).expanduser()
        text = _transcribe_file(path, cfg, in_place=False)
    else:
        path = stop_recording(cfg)
        text = _transcribe_file(path, cfg)
    if not text:
        _err("no speech recognized")
        return 2
    if args.transcribe_only or args.no_ask:
        print(text)
        return 0
    _err(f"you: {text}")
    _ask(text, cfg, no_speak=args.no_speak)
    return 0


def cmd_transcribe(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    text = _transcribe_file(Path(args.wav).expanduser(), cfg, in_place=False)
    if not text:
        _err("no speech recognized")
        return 2
    print(text)
    return 0


_EXIT_PHRASES = {
    "stop",
    "stop listening",
    "exit",
    "quit",
    "goodbye",
    "bye",
    "that's all",
    "thats all",
    "end conversation",
    "we're done",
    "were done",
}


def _is_exit_phrase(text: str) -> bool:
    normalized = " ".join(text.lower().replace("’", "'").split()).strip(" .,!?")
    return normalized in _EXIT_PHRASES


def cmd_chat(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    max_turns = args.max_turns
    if max_turns is None:
        max_turns = int(cfg["chat"].get("max_turns", 0) or 0)
    chat = cfg["chat"]
    audio = cfg["audio"]
    model_override = getattr(args, "model", None) or chat.get("model")
    if model_override:
        cfg["opencode"]["model"] = str(model_override)
    wait_seconds = args.timeout if args.timeout is not None else float(chat.get("listen_timeout", 30))
    turns = 0
    _err("conversation mode — speak when prompted; Ctrl+C to stop")
    # Warm the local STT model while the microphone opens for the first turn.
    threading.Thread(target=preload_stt, args=(cfg,), daemon=True).start()
    proc = open_recorder(cfg)
    first = True

    try:
        while not max_turns or turns < max_turns:
            try:
                if first:
                    _err("starting microphone…")
                else:
                    _err("listening…")
                    # Flush the assistant's own playback and any backlog before
                    # asking for the next turn.
                    drain_stream(proc.stdout, 0.8)
                pcm = read_utterance(
                    proc.stdout,
                    sample_rate=RECORD_RATE,
                    threshold=float(audio.get("silence_threshold", 0.008)),
                    silence_ms=int(chat.get("silence_ms", 800)),
                    min_speech_ms=int(chat.get("min_speech_ms", 300)),
                    max_seconds=float(audio.get("max_seconds", 60)),
                    wait_seconds=wait_seconds,
                    warmup_ms=1300 if first else 150,
                    on_phase=lambda phase: _err("recording…") if phase == "recording" else None,
                )
            except KeyboardInterrupt:
                _err("stopped")
                return 130

            if pcm is None:
                if proc.poll() is not None:
                    try:
                        error = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
                    except (OSError, ValueError):
                        error = ""
                    raise AudioError(f"recorder stopped: {error or 'unknown error'}")
                _err("no speech detected; still listening (Ctrl+C to stop)")
                first = False
                continue
            first = False
            path = write_pcm(pcm)

            try:
                started = time.monotonic()
                text = _transcribe_file(path, cfg)
                _timing("stt", time.monotonic() - started)
            except STTError as exc:
                _err(str(exc))
                continue
            if not text:
                continue
            _err(f"you: {text}")

            if _is_exit_phrase(text):
                farewell = "Okay. Conversation ended."
                print(farewell, flush=True)
                if not args.no_speak:
                    _speak_reply(farewell, cfg, dry_run=False)
                break

            try:
                _ask(text, cfg, no_speak=args.no_speak)
            except OpencodeError as exc:
                _err(str(exc))
                if not args.no_speak:
                    _speak_reply("I could not get an answer. Check the terminal.", cfg, dry_run=False)
            turns += 1
            time.sleep(0.3)
    finally:
        stop_recorder(proc)

    return 0


def cmd_say(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    text = " ".join(args.text).strip() or sys.stdin.read().strip()
    if not text:
        _err("nothing to say")
        return 2
    try:
        path = speak(text, cfg, dry_run=args.dry_run, keep=args.keep)
    except TTSError as exc:
        _err(str(exc))
        return 1
    if path and args.keep:
        print(path)
    return 0


def cmd_reset(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    save_session(None)
    _err("cleared the voice session; the next question starts fresh")
    return 0


def cmd_init(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    if config_path().exists() and not args.force:
        print(config_path())
        return 0
    overrides: dict[str, Any] = {}
    url = str(cfg["tts"].get("url") or "http://127.0.0.1:8765").rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            if resp.status < 500:
                overrides = {"tts": {"engine": "http"}}
    except Exception:  # noqa: BLE001
        pass
    if overrides:
        _err(f"detected a TTS server at {url}; enabling http TTS")
    path = write_default_config(overrides=overrides)
    print(path)
    return 0


def cmd_doctor(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    failures = 0
    for ok, line in doctor_check(cfg):
        print(f"{'ok  ' if ok else 'FAIL'} {line}")
        failures += 0 if ok else 1
    return 1 if failures else 0


def cmd_devices(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    sources = list_sources()
    if not sources:
        _err("pactl not available; set audio.input_device to an ALSA device instead")
        return 1
    selected = str(cfg["audio"].get("input_device") or "").strip()
    for index, name, monitor in sources:
        marks = []
        if monitor:
            marks.append("monitor")
        if selected == name or (not selected and not monitor):
            marks.append("selected")
        print(f"{index:>3}  {name}  {', '.join(marks)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voicecode",
        description="Push-to-talk speech-to-speech for OpenCode (local STT/TTS).",
    )
    parser.add_argument("--version", action="version", version=f"voicecode {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="start recording (bind to key press)")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("stop", help="stop recording, transcribe, ask, and speak")
    p.add_argument("--transcribe-only", action="store_true")
    p.add_argument("--no-speak", action="store_true")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("toggle", help="start recording, or stop if already recording")
    p.add_argument("--transcribe-only", action="store_true")
    p.add_argument("--no-speak", action="store_true")
    p.set_defaults(func=cmd_toggle)

    p = sub.add_parser("listen", help="transcribe an existing WAV and optionally ask")
    p.add_argument("--wav", help="WAV file instead of the current recording")
    p.add_argument("--transcribe-only", action="store_true")
    p.add_argument("--no-ask", action="store_true")
    p.add_argument("--no-speak", action="store_true")
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("chat", help="hands-free conversation: listen, answer, repeat")
    p.add_argument("--max-turns", type=int, default=None, help="stop after N exchanges (0 = unlimited)")
    p.add_argument("--timeout", type=float, default=None, help="seconds to wait for speech each turn")
    p.add_argument("--model", help="provider/model for the conversation")
    p.add_argument("--no-speak", action="store_true", help="text-only conversation")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("ask", help="send text to OpenCode and speak the answer")
    p.add_argument("text", nargs="*")
    p.add_argument("--model", help="provider/model for this call")
    p.add_argument("--no-speak", action="store_true")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("say", help="speak text through the configured TTS")
    p.add_argument("text", nargs="*")
    p.add_argument("--dry-run", action="store_true", help="synthesize but do not play")
    p.add_argument("--keep", action="store_true", help="keep the generated WAV and print its path")
    p.set_defaults(func=cmd_say)

    p = sub.add_parser("transcribe", help="transcribe a WAV without asking OpenCode")
    p.add_argument("wav")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("status", help="show recording, session, and backend state")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("devices", help="list microphones and show which is used")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("reset", help="forget the persistent OpenCode session")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("init", help="write a default config (detects a local TTS server)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="check the local dependencies")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    try:
        return int(args.func(args, cfg))
    except (AudioError, STTError, OpencodeError, TTSError) as exc:
        _err(str(exc))
        return 1
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
