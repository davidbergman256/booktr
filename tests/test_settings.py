import json

import pytest

from booktr import config as settings
from booktr.config import Config


def test_font_size_is_validated():
    with pytest.raises(ValueError):
        Config(font_size=60)


def test_preferences_do_not_contain_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'app_dir', lambda: tmp_path)
    cfg = Config(openai_api_key='private-test-value', font_size=20, voice_mode='advanced')
    settings.save_preferences(cfg)
    raw = json.loads((tmp_path / 'preferences.json').read_text())
    assert raw == {'font_size': 20, 'voice_mode': 'advanced', 'page_format': 'a4'}
    assert 'private-test-value' not in (tmp_path / 'preferences.json').read_text()


def test_settings_survive_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'app_dir', lambda: tmp_path)
    monkeypatch.setenv('BOOKTR_CONFIG_FILE', str(tmp_path / 'config.json'))
    (tmp_path / 'config.json').write_text(json.dumps({'openai_api_key': 'test-key'}))
    cfg = settings.load_config()
    cfg.font_size, cfg.voice_mode = 18, 'advanced'
    settings.save_preferences(cfg)
    assert settings.load_config().font_size == 18
    assert settings.load_config().voice_mode == 'advanced'


def test_main_screen_can_load_before_provider_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'app_dir', lambda: tmp_path)
    monkeypatch.setenv('BOOKTR_CONFIG_FILE', str(tmp_path / 'config.json'))
    assert settings.load_config(validate=False).font_size == 14


def test_voice_mode_is_validated():
    with pytest.raises(ValueError):
        Config(voice_mode='unknown')


def test_legacy_custom_sol_draft_uses_supported_reasoning():
    assert Config(model_draft='gpt-6.1-sol').draft_reasoning_effort == 'low'
