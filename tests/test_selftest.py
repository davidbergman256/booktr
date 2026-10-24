"""Distribution checks exercise dotenv without using account credentials."""
from __future__ import annotations

import os

import pytest

from booktr import config, selftest
from booktr.config import Config


def test_configuration_smoke_uses_real_dotenv_and_restores_process_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "outer-process-test-value")
    monkeypatch.setenv("BOOKTR_CONFIG_FILE", str(tmp_path / "must-not-read-config.json"))
    monkeypatch.setenv("BOOKTR_ENV_FILE", str(tmp_path / "must-not-read.env"))
    before = dict(os.environ)
    original_app_dir = config.app_dir
    selftest._configuration_self_test(tmp_path)
    assert dict(os.environ) == before
    assert config.app_dir is original_app_dir
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_configuration_smoke_fails_if_loader_loses_dotenv_support(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: Config(openai_api_key="json-selftest-value"))
    with pytest.raises(AssertionError, match="dotenv"):
        selftest._configuration_self_test(tmp_path)
