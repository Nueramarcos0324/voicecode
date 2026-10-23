# voicecode

Push-to-talk, speech-to-speech for [OpenCode](https://opencode.ai) V2.
Hold a key, talk, release: your speech is transcribed locally, sent to the
OpenCode session you already use, and the answer is spoken back.

No cloud STT, no cloud TTS, no API keys of its own. It shells out to
`opencode run`, so it inherits your providers, model, agent, permissions, and
session history.

```
hold SUPER+F9 ──► pw-record ──► whisper (local) ──► opencode run ──► piper ──► speakers
      release ─────────────────────────────────────────┘
```

## Why this exists

The existing OpenCode voice projects were written for OpenCode V1 and do not
work on V2:

| Project | What it uses | Status on V2 |
|---|---|---|
| `renjfk/opencode-voice` | V1 TUI plugin API (`tui.json`) | plugin API is breaking in V2 |
| `Juancoll/voice-opencode` | `POST /session`, `POST /session/{id}/message` | those routes are gone |
| `AudelDiaz/voxlyn-ai` | `GET /global/health`, V1 session routes | same |
| `ondrejch/opencode-voice-kokoro` | V1 `session.idle` plugin hook | same |

V2 exposes `/api/session`, `/api/session/{id}/prompt`, `/api/event`, and a
completely new plugin API. `voicecode` avoids that moving target by using the
`opencode run --format json` CLI, which is stable and works with both the
shared background service and `--standalone`.

V2 also has an experimental native voice endpoint
(`POST /api/experimental/voice/session`) that brokers OpenAI Realtime or xAI
Grok Voice. It is not used here: it needs an OpenAI/xAI key, sends audio to a
cloud provider, and the TUI has no client for it.

## Requirements

- Linux with PipeWire or PulseAudio (`pw-record` or `parec`; `arecord` fallback)
- `opencode` V2 on `PATH`
- Python 3.10+
- A microphone and speakers
- Optional: `voxtype` for STT, `notify-send` for desktop notifications

## Install

```bash
git clone https://github.com/Nueramarcos0324/voicecode ~/voicecode
cd ~/voicecode
./install.sh
```

The installer creates `.venv`, installs `faster-whisper` + `piper-tts`,
downloads a Piper voice, writes `~/.config/voicecode/config.json`, and links
`~/.local/bin/voicecode`. No sudo.

Manual install:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
ln -s "$PWD/.venv/bin/voicecode" ~/.local/bin/voicecode
voicecode init
```

## Bind a key

Omarchy / Hyprland, in `~/.config/hypr/bindings.lua`:

```lua
if o.cmd_present("voicecode") then
  o.bind("SUPER + F9", "Voice prompt (hold to talk)", "voicecode start")
  o.bind("SUPER + F9", "Voice prompt (release to send)", "voicecode stop", { release = true })
end
```

Then `hyprctl reload` and check `hyprctl configerrors`.

`SUPER+F9` is deliberately not `F9`: Omarchy already binds plain `F9` to
push-to-talk dictation in `voxtype`. Both can coexist.

Any compositor works; the contract is two commands:

| Event | Command |
|---|---|
| key press | `voicecode start` |
| key release | `voicecode stop` |

## Use

```bash
voicecode say "hello from the speakers"          # TTS only
voicecode ask "what does this repo do?"          # text in, speech out
voicecode listen --wav question.wav              # full loop from a file
voicecode listen --wav question.wav --transcribe-only
voicecode transcribe recording.wav
voicecode devices                                # list microphones
voicecode status
voicecode reset                                  # forget the OpenCode session
voicecode doctor
```

`start` spawns the recorder and returns immediately. `stop` finalises the WAV,
trims silence, transcribes, calls OpenCode, prints the answer, shows a
notification, and speaks it. The transcript and status lines go to stderr; the
answer goes to stdout.

## Configuration

`~/.config/voicecode/config.json` (all keys optional; defaults are merged):

```json
{
  "stt": {
    "engine": "auto",
    "model": "small.en",
    "device": "cpu",
    "compute_type": "int8",
    "language": "en",
    "voxtype_binary": "voxtype"
  },
  "tts": {
    "engine": "piper",
    "piper_binary": "/home/you/voicecode/.venv/bin/piper",
    "piper_model": "/home/you/voicecode/voices/en_US-lessac-medium.onnx",
    "url": "http://127.0.0.1:8765",
    "speed": 0.95,
    "max_chars": 600,
    "command": ""
  },
  "opencode": {
    "binary": "opencode",
    "model": "",
    "agent": "",
    "auto_approve": false,
    "timeout": 300
  },
  "audio": {
    "input_device": "",
    "output_command": "",
    "max_seconds": 60,
    "min_seconds": 0.4,
    "silence_threshold": 0.008
  },
  "session": { "keep_context": true },
  "ui": { "notify": true }
}
```

### STT engines

- `auto` — `faster-whisper` if installed, else `voxtype`.
- `faster-whisper` — in-process, `small.en` int8 by default (~1 s per 5 s clip).
- `voxtype` — reuses the already-installed dictation daemon:
  `voxtype transcribe <wav>`. Its default `base.en` model is less accurate than
  `small.en`; change `[whisper] model` in `~/.config/voxtype/config.toml` if you
  want one model for both.

### TTS engines

- `piper` — local, CPU, ~1 s for a sentence. Voice files come from
  [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).
- `http` — POST `{"text": "...", "speed": 0.95}` to `<url>/speak` and expect a
  WAV back. This is compatible with the CosyVoice2 server in
  `~/kiwi-voice/pipeline/run_tts_server.sh` (`http://127.0.0.1:8765`), so the
  cloned voice can read coding answers too.
- `command` — any executable that reads text on stdin and writes a WAV to
  `{out}`, e.g. `"my-tts --out {out}"`.
- `none` — silent.

### Audio devices

With PipeWire, `voicecode devices` lists sources and marks the one it will use.
It picks the first non-`.monitor` source. Set `audio.input_device` to a source
name to pin it. `audio.output_command` can be any player; `{file}` is
substituted, otherwise the path is appended.

## Security

`opencode.auto_approve` is **false** by default. Voice prompts that trigger a
tool needing permission will stop and ask in the terminal, and the voice turn
can time out. Set it to `true` only if you accept that anything you say can run
tools without a confirmation. The voice session is a normal OpenCode session;
`voicecode reset` starts a fresh one.

## Troubleshooting

**"recording is silent"** — run `voicecode devices`; make sure a real source
(not a `.monitor`) is selected. Check `pactl get-source-mute <name>` and that a
mic is actually plugged in: `amixer -c 0 contents | grep -i jack` should not say
all mic jacks are `off`.

**No text from voxtype** — `voxtype transcribe file.wav` must print a transcript
on stdout. If its model is missing, run `voxtype setup`.

**OpenCode permission prompt** — run `voicecode ask ...` in a terminal once to
approve, or set `auto_approve`.

**Slow answers** — that is the model, not the audio path. Point
`opencode.model` at a faster provider/model; startup is only ~0.3 s.

**Piper sounds robotic** — try `en_US-amy-medium` or `en_GB-alba-medium`; keep
the `.onnx` and `.onnx.json` pair together.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The suite covers JSONL parsing, markdown-to-speech cleanup, config merging,
voxtype log parsing, and silence trimming. It needs no microphone, GPU, or
network.

## License

MIT
