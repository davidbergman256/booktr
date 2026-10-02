"""Cloud provisioning contracts, without real IAM changes or account charges."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktr.errors import BookTrError


def _load_setup_script():
    path = Path(__file__).parents[1] / "scripts" / "cloud_setup.py"
    spec = importlib.util.spec_from_file_location("booktr_cloud_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cloud_key_is_restricted_idempotent_and_saved_without_printing(monkeypatch, tmp_path, capsys):
    import google.auth
    import google.auth.transport.requests
    module = _load_setup_script()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"openai_api_key": "existing-secret"}), encoding="utf-8")
    monkeypatch.setattr(module, "config_path", lambda: path)
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (object(), "project-id"))
    calls, resources = [], {}
    resource = {"name": "projects/123/locations/global/keys/booktr-narrator",
                "restrictions": {"apiTargets": [{"service": "texttospeech.googleapis.com"}]}}
    def response(data, status=200):
        return SimpleNamespace(ok=status < 400, status_code=status, json=lambda: data)
    class Session:
        def __init__(self, credentials):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            if "cloudresourcemanager" in url:
                return response({"name": "projects/123"})
            if "serviceusage" in url:
                return response({"state": "ENABLED"})
            if url.endswith("/keyString"):
                return response({"keyString": "new-secret-not-to-print"})
            if url.endswith("/booktr-narrator"):
                return response(resources.get("key", {}), 200 if "key" in resources else 404)
            raise AssertionError(url)
        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            assert kwargs["params"] == {"keyId": "booktr-narrator"}
            assert kwargs["json"]["restrictions"] == resource["restrictions"]
            resources["key"] = resource
            return response({"done": True, "response": resource})
    monkeypatch.setattr(google.auth.transport.requests, "AuthorizedSession", Session)
    config = SimpleNamespace(google_cloud_project="project-id", google_credentials_file="", google_api_key="")
    module.provision_google(config, enable=True, create_key=True)
    module.provision_google(config, enable=True, create_key=True)
    saved = json.loads(path.read_text())
    assert saved["openai_api_key"] == "existing-secret"
    assert saved["google_api_key"] == "new-secret-not-to-print"
    assert sum(method == "POST" for method, _, _ in calls) == 1
    assert "secret" not in capsys.readouterr().out
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_cloud_setup_refuses_to_reuse_unrestricted_key(monkeypatch):
    import google.auth
    import google.auth.transport.requests
    module = _load_setup_script()
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (object(), "project-id"))
    class Session:
        def __init__(self, credentials):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            if "cloudresourcemanager" in url:
                data = {"name": "projects/123"}
            elif "serviceusage" in url:
                data = {"state": "ENABLED"}
            else:
                data = {"name": "projects/123/locations/global/keys/booktr-narrator", "restrictions": {}}
            return SimpleNamespace(ok=True, status_code=200, json=lambda: data)
    monkeypatch.setattr(google.auth.transport.requests, "AuthorizedSession", Session)
    config = SimpleNamespace(google_cloud_project="project-id", google_credentials_file="", google_api_key="")
    with pytest.raises(BookTrError, match="not restricted"):
        module.provision_google(config, enable=True, create_key=True)


def test_live_sample_export_uses_one_request_and_checks_real_m4b(tmp_path):
    from booktr.audio.providers import PcmAudio
    module = _load_setup_script()
    if module.find_ffmpeg() is None:
        pytest.skip("ffmpeg not installed")
    calls = []
    class Provider:
        def synthesize(self, text):
            calls.append(text)
            return PcmAudio(b"\x01\x00" * 24000)
    result = module._export_sample(Provider(), "test", tmp_path, verify_encoding=True)
    assert len(calls) == 1 and len(calls[0]) < 100
    assert result["pcm"] == "24 kHz, 16-bit mono"
    assert result["duration_seconds"] == 1
    assert Path(result["m4b"]).is_file() and result["decoded_frames"] > 0
