import json

from voicecode.config import DEFAULTS
from voicecode.opencode import build_prompt, parse_error, parse_events


def _text_event(message_id, part_id, text, session="ses_abc"):
    return json.dumps(
        {
            "type": "text",
            "sessionID": session,
            "part": {
                "id": part_id,
                "messageID": message_id,
                "type": "text",
                "text": text,
            },
        }
    )


def test_parse_single_reply():
    out = "\n".join(
        [
            _text_event("msg_1", "prt_1", "hello"),
            json.dumps({"type": "step_finish", "sessionID": "ses_abc"}),
        ]
    )
    text, session = parse_events(out)
    assert text == "hello"
    assert session == "ses_abc"


def test_parse_keeps_only_last_message():
    out = "\n".join(
        [
            _text_event("msg_1", "prt_1", "checking the file"),
            _text_event("msg_1", "prt_2", "done checking"),
            _text_event("msg_2", "prt_3", "the answer is 42"),
        ]
    )
    text, _ = parse_events(out)
    assert text == "the answer is 42"


def test_parse_partial_part_is_not_duplicated():
    out = "\n".join(
        [
            _text_event("msg_1", "prt_1", "hel"),
            _text_event("msg_1", "prt_1", "hello there"),
        ]
    )
    text, _ = parse_events(out)
    assert text == "hello there"


def test_parse_ignores_noise_and_bad_json():
    out = "\n".join(
        [
            "not json",
            "{broken",
            _text_event("msg_1", "prt_1", "ok"),
        ]
    )
    text, _ = parse_events(out)
    assert text == "ok"


def test_parse_empty():
    assert parse_events("") == ("", None)


def test_build_prompt_applies_voice_prefix():
    prompt = build_prompt("what is 17 times 3?", DEFAULTS)
    assert prompt.startswith(DEFAULTS["opencode"]["prompt_prefix"])
    assert prompt.endswith("what is 17 times 3?")


def test_build_prompt_can_be_disabled():
    cfg = {"opencode": {"prompt_prefix": ""}}
    assert build_prompt("tell me more", cfg) == "tell me more"


def test_parse_error_event():
    out = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "provider.no-route",
                "message": "Model unavailable: lmstudio/qwen/qwen3.8-27b",
            },
        }
    )
    assert parse_error(out) == "Model unavailable: lmstudio/qwen/qwen3.8-27b"


def test_parse_error_ignores_normal_events():
    out = "\n".join([_text_event("msg_1", "prt_1", "hello"), "{broken"])
    assert parse_error(out) is None
