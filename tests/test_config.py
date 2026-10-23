import json

from voicecode import config


def test_deep_merge_keeps_siblings():
    merged = config.deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}})
    assert merged == {"a": {"x": 1, "y": 3}}


def test_config_and_session_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    path = config.write_default_config()
    assert path.exists()
    loaded = config.load_config()
    assert loaded["stt"]["model"] == config.DEFAULTS["stt"]["model"]

    raw = json.loads(path.read_text())
    raw["tts"]["engine"] = "http"
    path.write_text(json.dumps(raw))
    assert config.load_config()["tts"]["engine"] == "http"

    config.save_session("ses_test")
    assert config.load_session() == "ses_test"
    config.save_session(None)
    assert config.load_session() is None
