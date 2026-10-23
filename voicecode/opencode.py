"""OpenCode V2 client.

`opencode run --format json` is deliberately used instead of the HTTP API:
it is stable across OpenCode releases, inherits the user's provider auth and
model defaults, and cannot leave a half-open SSE stream behind.  Each call is
one process, so a voice turn costs the process startup (~0.3 s) on top of the
model latency.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from typing import Any


class OpencodeError(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    session_id: str | None
    events: list[dict[str, Any]]


def parse_events(stdout: str) -> tuple[str, str | None]:
    """Return (last assistant message text, session id) from JSONL output."""
    by_message: dict[str, list[str]] = {}
    order: list[str] = []
    session_id: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        sid = event.get("sessionID")
        if isinstance(sid, str) and sid.startswith("ses"):
            session_id = sid
        part = event.get("part")
        if event.get("type") != "text" or not isinstance(part, dict):
            continue
        if part.get("type") != "text":
            continue
        text = part.get("text")
        message_id = part.get("messageID") or event.get("messageID")
        if not isinstance(text, str) or not text.strip() or not isinstance(message_id, str):
            continue
        if message_id not in by_message:
            by_message[message_id] = []
            order.append(message_id)
        # A part can be re-sent as it grows. Keep the newest full text for the
        # part id instead of appending duplicates.
        part_id = part.get("id")
        entry = (str(part_id), text)
        bucket = by_message[message_id]
        for index, (existing_id, _old) in enumerate(bucket):
            if existing_id == entry[0]:
                bucket[index] = entry
                break
        else:
            bucket.append(entry)

    if not order:
        return "", session_id
    last = order[-1]
    text = "\n".join(item[1].strip() for item in by_message[last] if item[1].strip())
    return text.strip(), session_id


def build_prompt(prompt: str, cfg: dict[str, Any]) -> str:
    """Apply the configured voice-style prefix to a question."""
    prefix = str(cfg["opencode"].get("prompt_prefix") or "").strip()
    if not prefix:
        return prompt
    return f"{prefix}\n\n{prompt}"


def parse_error(stdout: str) -> str | None:
    """Return the message from the first error event in JSONL output."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "error":
            continue
        error = event.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if message:
                return str(message)
        elif error:
            return str(error)
    return None


def ask(prompt: str, cfg: dict[str, Any], session_id: str | None = None) -> Reply:
    oc = cfg["opencode"]
    prompt = build_prompt(prompt, cfg)
    binary = shlex.split(str(oc.get("binary") or "opencode"))
    cmd = [*binary, "run", "--format", "json"]
    if session_id:
        cmd += ["--session", session_id]
    if oc.get("model"):
        cmd += ["--model", str(oc["model"])]
    if oc.get("agent"):
        cmd += ["--agent", str(oc["agent"])]
    if oc.get("auto_approve"):
        cmd += ["--auto"]
    cmd.append(prompt)

    timeout = float(oc.get("timeout") or 300)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
        raise OpencodeError(f"OpenCode did not answer within {timeout:.0f}s") from None
    if proc.returncode != 0 and not stdout.strip():
        tail = (stderr or "").strip().splitlines()[-8:]
        raise OpencodeError("OpenCode failed: " + (" | ".join(tail) or f"exit {proc.returncode}"))
    text, sid = parse_events(stdout)
    if not text:
        message = parse_error(stdout)
        if message:
            raise OpencodeError(f"OpenCode error: {message}")
        tail = (stderr or "").strip().splitlines()[-8:]
        raise OpencodeError("OpenCode returned no text" + (": " + " | ".join(tail) if tail else ""))
    return Reply(text=text, session_id=sid or session_id, events=[])
