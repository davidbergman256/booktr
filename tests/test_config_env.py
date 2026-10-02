"""Private dotenv credentials never enter global environment or reader settings."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from booktr import config as settings
from booktr.errors import BookTrError


_ENV_NAMES = (
    "OPENAI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS",
    "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
)


@pytest.fixture
def private_config(tmp_path, monkeypatch):
    for name in (*_ENV_NAMES, "BOOKTR_ENV_FILE", "BOOKTR_CONFIG_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "app_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "openai_api_key": "json-openai-test", "google_api_key": "json-google-test",
        "elevenlabs_api_key": "json-eleven-test", "model_draft": "custom-draft",
        "font_size": 16, "voice_mode": "normal", "custom_setting": "keep",
    }), encoding="utf-8")
    return tmp_path


def test_default_private_dotenv_reads_only_approved_credentials_without_side_effects(private_config, capsys, caplog):
    private_config.joinpath(".env").write_text(
        'OPENAI_API_KEY="dotenv-openai-test"\nGOOGLE_API_KEY=dotenv-google-test\n'
        'GOOGLE_CLOUD_PROJECT=booktr-project\nGOOGLE_APPLICATION_CREDENTIALS="C:/Private keys/voice.json"\n'
        'ELEVENLABS_API_KEY=dotenv-eleven-test\nELEVENLABS_VOICE_ID=czech-narrator\n'
        'MODEL_DRAFT=unapproved-model\nFONT_SIZE=24\nVOICE_MODE=advanced\n'
        'BOOKTR_CONFIG_FILE=unapproved-config.json\nARBITRARY_VALUE=should-be-ignored\n', encoding="utf-8")
    before = dict(os.environ)
    cfg = settings.load_config()
    assert cfg.openai_api_key == "dotenv-openai-test"
    assert cfg.google_api_key == "dotenv-google-test"
    assert cfg.google_cloud_project == "booktr-project"
    assert cfg.google_credentials_file == "C:/Private keys/voice.json"
    assert cfg.elevenlabs_api_key == "dotenv-eleven-test"
    assert cfg.elevenlabs_voice_id == "czech-narrator"
    assert cfg.model_draft == "custom-draft" and cfg.font_size == 16 and cfg.voice_mode == "normal"
    assert cfg.extra == {"custom_setting": "keep"}
    assert dict(os.environ) == before
    captured = capsys.readouterr()
    visible = repr(cfg) + captured.out + captured.err + caplog.text
    for secret in ("dotenv-openai-test", "dotenv-google-test", "dotenv-eleven-test"):
        assert secret not in visible


def test_process_environment_overrides_dotenv_without_interpolation(private_config, monkeypatch):
    private_config.joinpath(".env").write_text(
        'OPENAI_API_KEY="${UNAPPROVED_SECRET}-literal"\nGOOGLE_API_KEY=dotenv-google-test\n', encoding="utf-8")
    monkeypatch.setenv("UNAPPROVED_SECRET", "must-not-expand")
    monkeypatch.setenv("GOOGLE_API_KEY", "process-google-test")
    cfg = settings.load_config()
    assert cfg.openai_api_key == "${UNAPPROVED_SECRET}-literal"
    assert cfg.google_api_key == "process-google-test"


def test_explicit_dotenv_path_preserves_explicit_json_and_reader_preferences(private_config, tmp_path, monkeypatch):
    private_config.joinpath(".env").write_text("OPENAI_API_KEY=default-not-selected\n")
    selected = tmp_path / "private-account.env"
    selected.write_text("OPENAI_API_KEY=selected-dotenv-test\nELEVENLABS_API_KEY=selected-eleven-test\n")
    explicit = tmp_path / "custom.json"
    explicit.write_text(json.dumps({"openai_api_key": "custom-json-test", "model_draft": "chosen-model", "font_size": 14}))
    private_config.joinpath("preferences.json").write_text(json.dumps({"font_size": 22, "voice_mode": "advanced"}))
    monkeypatch.setenv("BOOKTR_CONFIG_FILE", str(explicit))
    monkeypatch.setenv("BOOKTR_ENV_FILE", str(selected))
    cfg = settings.load_config()
    assert cfg.openai_api_key == "selected-dotenv-test"
    assert cfg.elevenlabs_api_key == "selected-eleven-test"
    assert cfg.model_draft == "chosen-model"
    assert cfg.font_size == 22 and cfg.voice_mode == "advanced"
    settings.save_preferences(cfg)
    assert "selected-dotenv-test" not in private_config.joinpath("preferences.json").read_text()
    assert "selected-eleven-test" not in private_config.joinpath("preferences.json").read_text()


@pytest.mark.parametrize("explicit", [False, True])
def test_missing_dotenv_preserves_json_without_creating_env_file(private_config, monkeypatch, explicit):
    missing = private_config / "missing.env"
    if explicit:
        monkeypatch.setenv("BOOKTR_ENV_FILE", str(missing))
    cfg = settings.load_config()
    assert cfg.openai_api_key == "json-openai-test"
    assert cfg.google_api_key == "json-google-test"
    assert not missing.exists() and not private_config.joinpath(".env").exists()


def test_blank_process_value_clears_dotenv_credential(private_config, monkeypatch):
    private_config.joinpath(".env").write_text("OPENAI_API_KEY=dotenv-openai-test\n")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert settings.load_config(validate=False).openai_api_key == ""
    with pytest.raises(BookTrError, match="openai_api_key missing"):
        settings.load_config()


def test_valueless_dotenv_entry_preserves_lower_priority_json(private_config):
    private_config.joinpath(".env").write_text("OPENAI_API_KEY\nGOOGLE_API_KEY=\n")
    cfg = settings.load_config()
    assert cfg.openai_api_key == "json-openai-test"
    assert cfg.google_api_key == ""


def test_unreadable_private_dotenv_does_not_echo_its_contents(private_config):
    private_config.joinpath(".env").write_bytes(b"OPENAI_API_KEY=must-never-appear\xff")
    with pytest.raises(BookTrError) as error:
        settings.load_config()
    assert error.value.code == "config"
    assert "must-never-appear" not in str(error.value)


def test_cloud_setup_automatically_uses_private_dotenv(private_config, monkeypatch, capsys):
    private_config.joinpath(".env").write_text("GOOGLE_API_KEY=dotenv-google-test\nELEVENLABS_API_KEY=dotenv-eleven-test\n")
    script = Path(__file__).parents[1] / "scripts" / "cloud_setup.py"
    spec = importlib.util.spec_from_file_location("booktr_dotenv_setup_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []

    class Provider:
        def __init__(self, cfg):
            seen.append((cfg.google_api_key, cfg.elevenlabs_api_key))

        def validate(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(module, "GoogleProvider", Provider)
    monkeypatch.setattr(module, "ElevenLabsProvider", Provider)
    assert module.main(["--provider", "both"]) == 0
    assert seen == [("dotenv-google-test", "dotenv-eleven-test")] * 2
    output = capsys.readouterr()
    assert "dotenv-google-test" not in output.out + output.err
    assert "dotenv-eleven-test" not in output.out + output.err


def test_config_does_not_search_working_directory_for_credentials(private_config, monkeypatch):
    checkout = private_config / "checkout"
    checkout.mkdir()
    checkout.joinpath(".env").write_text("OPENAI_API_KEY=checkout-must-not-load\n")
    monkeypatch.chdir(checkout)
    assert settings.load_config().openai_api_key == "json-openai-test"


def test_private_dotenv_accepts_windows_utf8_bom(private_config):
    private_config.joinpath(".env").write_text("OPENAI_API_KEY=bom-dotenv-test\n", encoding="utf-8-sig")
    assert settings.load_config().openai_api_key == "bom-dotenv-test"
