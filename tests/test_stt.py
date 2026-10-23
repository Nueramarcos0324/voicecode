from voicecode.stt import _parse_voxtype


def test_parse_voxtype_strips_logs():
    output = (
        "whisper_init_state: compute buffer (decode) =   96.37 MB\n"
        "\x1b[2m2026-10-23T01:07:43.565342Z\x1b[0m \x1b[32m INFO\x1b[0m "
        'Transcription completed in 1.26s: "Hello there."\n'
        "Hello there.\n"
    )
    assert _parse_voxtype(output) == "Hello there."


def test_parse_voxtype_empty():
    assert _parse_voxtype("whisper_init_state: x\n") == ""
