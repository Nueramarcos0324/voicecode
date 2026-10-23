# voicecode

Push-to-talk and hands-free speech-to-speech for [OpenCode](https://opencode.ai) V2.
Hold a key and talk, or run a conversation loop in a terminal: your speech is
transcribed locally, sent to the OpenCode session you already use, and the
answer is spoken back.

No cloud STT, no cloud TTS, no API keys of its own. It shells out to
`opencode run`, so it inherits your providers, model, agent, permissions, and
session history.

```
hold SUPER+F9 ──► pw-record ──► whisper (local) ──► opencode run ──► piper ──► speakers
      release ─────────────────────────────────────────┘

voicecode chat ──► VAD loop ──► same pipeline ──► speaks, listens again
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
voicecode chat                                   # hands-free conversation, Ctrl+C to stop
voicecode chat --max-turns 3                     # three exchanges
voicecode chat --model provider/model            # use a specific model for this chat
voicecode chat --no-speak                        # typed answers, spoken questions
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

## Conversation mode

`voicecode chat` keeps one microphone stream open and turns it into a
back-and-forth conversation:

1. it waits for speech (VAD, 800 ms of silence ends your turn),
2. transcribes it, sends it to the persistent OpenCode session,
3. speaks the answer, then listens again.

Say **stop**, **exit**, **quit**, **goodbye**, or **that's all** to end it, or
press `Ctrl+C`. `--max-turns N` bounds the loop.

The first turn spends 1.3 s settling the microphone: some inputs emit a
decaying pop when the stream opens, and the VAD would otherwise mistake it for
speech. Later turns reuse the open stream and only flush buffered audio before
listening.

The loop is half-duplex: it does not listen while the answer is playing, so a
normal speaker setup will not feed the assistant its own voice. Headphones are
still nicer. Model latency dominates the feel of a conversation: a local LM
Studio model on a free GPU answers these short replies in roughly 1.5–3.5 s,
while a busy cloud model can take 20 s or more. Set `chat.model` to the fast
model and keep `opencode.model` for push-to-talk; see the configuration below.
Set `VOICECODE_TIMINGS=1` to print per-stage timings (STT, model, TTS) to
stderr.

Measured end-of-speech to first audio on an i5-9600K / RTX 3090, with
`faster-whisper small.en` int8, a local Qwen3.8-27B in LM Studio, and Piper
`en_US-lessac-medium`:

| stage | first turn | later turns |
|---|---:|---:|
| VAD endpoint wait | 0.7 s | 0.6 s |
| speech to text | 1.2 s | 1.0 s |
| model answer | 2.9 s | 1.5 s |
| Piper synthesis | 2.0 s | 1.6 s |
| **end of speech → first audio** | **6.9 s** | **4.8 s** |

The first turn includes OpenCode session creation and STT model loading (the
model is preloaded in the background while the microphone opens). Piper
synthesis is the largest fixed cost; a smaller voice model is faster but less
natural.

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
    "timeout": 300,
    "prompt_prefix": "You are in a live voice conversation. Answer in at most two short sentences of plain spoken text. No markdown, lists, or code, and do not use tools."
  },
  "audio": {
    "input_device": "",
    "output_command": "",
    "max_seconds": 60,
    "min_seconds": 0.4,
    "silence_threshold": 0.008
  },
  "chat": {
    "model": "",
    "silence_ms": 800,
    "min_speech_ms": 300,
    "listen_timeout": 30,
    "max_turns": 0,
    "warmup_ms": 400
  },
  "session": { "keep_context": true },
  "ui": { "notify": true }
}
```

`chat.warmup_ms` is the one-shot recorder guard. `voicecode chat` uses a longer
first-turn settle of 1.3 s and reuses the same stream afterwards.

`opencode.prompt_prefix` is prepended to every question so spoken answers stay
short and plain. Set it to `""` if you want OpenCode's normal, longer answers.
`chat.model` overrides `opencode.model` for conversation mode only; point it at
a fast local model (for example an LM Studio model) while keeping a stronger
model for push-to-talk. `voicecode chat --model <provider/model>` overrides both
for one session.

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

**`voicecode chat` starts recording by itself** — the input is producing a
device pop or hum. Raise `chat.warmup_ms` (chat also discards 1.3 s on the first
turn). If it keeps happening, switch `audio.input_device` or plug in a different
microphone.

**`voicecode chat` hears its own answer** — keep the loop half-duplex (the
default) and do not point `audio.input_device` at a `.monitor` source. Use
headphones if the speakers are very loud.

**No text from voxtype** — `voxtype transcribe file.wav` must print a transcript
on stdout. If its model is missing, run `voxtype setup`.

**OpenCode permission prompt** — run `voicecode ask ...` in a terminal once to
approve, or set `auto_approve`.

**Local model unavailable** — OpenCode caches the LM Studio model list at
startup. Start LM Studio (`lms server start`) and load the model, then run
`opencode reload`; `voicecode` will otherwise report the provider error
("Model unavailable: ...") when the answer comes back empty.

**Slow answers** — that is the model, not the audio path. Point `chat.model`
(or `opencode.model`) at a faster provider/model; startup is only ~0.3 s.
`VOICECODE_TIMINGS=1 voicecode chat` prints STT, model, and TTS durations so
you can see which stage is slow. Piper synthesis itself costs ~2 s per short
reply on a CPU; a smaller voice model is faster but less natural.

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
