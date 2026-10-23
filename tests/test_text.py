from voicecode.text import for_speech


def test_drops_fenced_code():
    text = "Run this:\n\n```bash\nrm -rf /\n```\n\nThen restart."
    spoken = for_speech(text)
    assert "rm -rf" not in spoken
    assert "Run this" in spoken
    assert "Then restart" in spoken


def test_unwraps_inline_code_and_links():
    spoken = for_speech("Edit `config.json` and see [the docs](https://example.com/x).")
    assert "`" not in spoken
    assert "https://" not in spoken
    assert "config.json" in spoken
    assert "the docs" in spoken


def test_splits_identifiers_for_tts():
    assert "get User By Id" in for_speech("Call getUserById now.")


def test_truncates_at_sentence():
    text = ("This is a sentence. " * 80).strip()
    spoken = for_speech(text, max_chars=120)
    assert len(spoken) <= 160
    assert spoken.endswith("The rest is in the terminal.")


def test_keeps_short_text_intact():
    assert for_speech("Fixed the bug in three places.") == "Fixed the bug in three places."


def test_strips_ansi():
    assert "\x1b" not in for_speech("\x1b[31mred\x1b[0m")
