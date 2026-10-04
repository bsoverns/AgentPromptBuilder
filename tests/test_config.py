"""Config helpers in the GUI module (no window is opened)."""

import json

import pytest

import AgentPromptBuilder as app


def test_save_json_writes_valid_json_without_temp_files(tmp_path):
    path = tmp_path / "config.json"
    app.save_json(path, {"a": 1})
    app.save_json(path, {"a": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_save_json_failure_keeps_old_file(tmp_path):
    path = tmp_path / "config.json"
    app.save_json(path, {"a": 1})
    with pytest.raises(TypeError):
        app.save_json(path, {"bad": object()})  # not JSON serializable, fails mid-write
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '"text"', ""])
def test_load_config_bad_file_gives_empty_dict(tmp_path, text):
    path = tmp_path / "config.json"
    path.write_text(text, encoding="utf-8")
    assert app.load_config(path) == {}


def test_load_config_missing_file(tmp_path):
    assert app.load_config(tmp_path / "config.json") == {}


def test_load_config_bad_settings_reset(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"settings": [1], "target": "chat"}', encoding="utf-8")
    assert app.load_config(path) == {"settings": {}, "target": "chat"}
