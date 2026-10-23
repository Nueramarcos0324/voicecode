#!/usr/bin/env bash
# Install voicecode into ~/voicecode with a local venv. No sudo, no systemd.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"
VOICE_DIR="$ROOT/voices"
VOICE="$VOICE_DIR/en_US-lessac-medium.onnx"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/voicecode/config.json"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m  !!\033[0m %s\n' "$*"; }

if [[ ! -d "$VENV" ]]; then
  say "creating $VENV"
  "$PYTHON" -m venv "$VENV"
fi

say "installing voicecode + whisper + piper"
"$VENV/bin/pip" -q install --upgrade pip
"$VENV/bin/pip" -q install -e ".[all]"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/voicecode" "$BIN_DIR/voicecode"
ok "linked $BIN_DIR/voicecode"

if [[ ! -f "$VOICE" ]]; then
  say "downloading the Piper voice (~61 MB)"
  mkdir -p "$VOICE_DIR"
  base="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
  curl -fL --progress-bar -o "$VOICE" "$base/en_US-lessac-medium.onnx"
  curl -fL --progress-bar -o "$VOICE.json" "$base/en_US-lessac-medium.onnx.json"
else
  ok "Piper voice already present"
fi

if [[ ! -f "$CONFIG" ]]; then
  say "writing $CONFIG"
  mkdir -p "$(dirname "$CONFIG")"
  "$VENV/bin/python" - "$CONFIG" "$VENV/bin/piper" "$VOICE" <<'PY'
import json, sys
path, piper, voice = sys.argv[1:4]
cfg = {
  "stt": {"engine": "auto", "voxtype_binary": "voxtype", "model": "small.en",
          "device": "cpu", "compute_type": "int8", "language": "en"},
  "tts": {"engine": "piper", "url": "http://127.0.0.1:8765", "speed": 0.95,
          "max_chars": 600, "piper_binary": piper, "piper_model": voice,
          "command": ""},
  "opencode": {"binary": "opencode", "model": "", "agent": "",
               "auto_approve": False, "timeout": 300},
  "audio": {"input_device": "", "output_command": "", "max_seconds": 60,
            "min_seconds": 0.4, "silence_threshold": 0.008},
  "chat": {"silence_ms": 800, "min_speech_ms": 300, "listen_timeout": 30,
           "max_turns": 0, "warmup_ms": 400},
  "session": {"keep_context": True},
  "ui": {"notify": True},
}
open(path, "w").write(json.dumps(cfg, indent=2) + "\n")
PY
else
  ok "config already exists, left untouched"
fi

say "checking the pipeline"
"$VENV/bin/voicecode" doctor || true

cat <<'EOF'

Bind a key. In ~/.config/hypr/bindings.lua:

  if o.cmd_present("voicecode") then
    o.bind("SUPER + F9", "Voice prompt (hold to talk)", "voicecode start")
    o.bind("SUPER + F9", "Voice prompt (release to send)", "voicecode stop", { release = true })
  end

Then: hyprctl reload

Try it without a microphone:

  voicecode say "hello from voicecode"
  voicecode listen --wav some-16k-mono.wav --transcribe-only
EOF
