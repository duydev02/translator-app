import json

import translator
from translator_app import config
from translator_app.ui.app import (
    _STARTUP_IN_PROGRESS_KEY,
    _clear_startup_recovery,
    _prepare_startup_recovery,
)


def test_main_returns_error_when_schema_is_missing(monkeypatch, tmp_path):
    missing = tmp_path / "missing_schema.json"
    calls = {"app": 0, "dialog": 0}

    class FakeRoot:
        def withdraw(self):
            pass

    monkeypatch.setattr(translator, "JSON_FILE", str(missing))
    monkeypatch.setattr(translator.tk, "Tk", lambda: FakeRoot())
    monkeypatch.setattr(
        translator.messagebox,
        "showerror",
        lambda *args, **kwargs: calls.__setitem__("dialog", calls["dialog"] + 1),
    )
    monkeypatch.setattr(
        translator,
        "TranslatorApp",
        lambda *_args, **_kwargs: calls.__setitem__("app", calls["app"] + 1),
    )

    assert translator.main() == 1
    assert calls["dialog"] == 1
    assert calls["app"] == 0


def test_main_launches_app_when_schema_exists(monkeypatch, tmp_path):
    schema = tmp_path / "db_schema_output.json"
    schema.write_text("{}", encoding="utf-8")
    calls = {"init_path": None, "mainloop": 0}

    class FakeApp:
        def __init__(self, json_path):
            calls["init_path"] = json_path

        def mainloop(self):
            calls["mainloop"] += 1

    monkeypatch.setattr(translator, "JSON_FILE", str(schema))
    monkeypatch.setattr(translator, "TranslatorApp", FakeApp)
    monkeypatch.setattr(translator, "_DND_AVAILABLE", True)

    assert translator.main() == 0
    assert calls == {"init_path": str(schema), "mainloop": 1}


def test_load_settings_returns_empty_dict_for_corrupt_json(monkeypatch, tmp_path):
    settings_file = tmp_path / "translator_settings.json"
    settings_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))

    assert config.load_settings() == {}


def test_save_and_load_settings_round_trip_unicode(monkeypatch, tmp_path):
    settings_file = tmp_path / "translator_settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", str(settings_file))
    payload = {"theme": "dark", "doc_tabs": [{"title": "日本語", "input": "SELECT 1"}]}

    config.save_settings(payload)

    assert json.loads(settings_file.read_text(encoding="utf-8")) == payload
    assert config.load_settings() == payload


def test_prepare_startup_recovery_marks_clean_launch_in_progress():
    settings = {}

    assert _prepare_startup_recovery(settings) is False
    assert settings[_STARTUP_IN_PROGRESS_KEY] is True


def test_prepare_startup_recovery_detects_previous_incomplete_launch():
    settings = {_STARTUP_IN_PROGRESS_KEY: True, "mode": "logsql"}

    assert _prepare_startup_recovery(settings) is True
    assert settings[_STARTUP_IN_PROGRESS_KEY] is True


def test_clear_startup_recovery_marks_launch_complete():
    settings = {_STARTUP_IN_PROGRESS_KEY: True}

    _clear_startup_recovery(settings)

    assert settings[_STARTUP_IN_PROGRESS_KEY] is False
