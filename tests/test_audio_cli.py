from __future__ import annotations

import sys
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktr import __main__ as cli
from booktr.config import Config


@pytest.fixture
def cli_services(monkeypatch, tmp_path):
    """An audio-only entry point must never instantiate the translation stack."""
    import booktr.api
    import booktr.audio
    import booktr.config
    import booktr.engine
    import booktr.state

    def forbidden(*args, **kwargs):
        pytest.fail("Audio-only CLI invoked translation or an OpenAI client")

    calls = []
    config = Config(openai_api_key="", voice_mode="normal")
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(booktr.api, "ApiClient", forbidden)
    monkeypatch.setattr(booktr.engine, "Engine", forbidden)
    monkeypatch.setattr(booktr.config, "load_config", lambda validate=True: (
        calls.append(("config", validate)) or config
    ))
    monkeypatch.setattr(booktr.state, "jobs_base_dir", lambda: tmp_path / "jobs")

    class FakeJob:
        def __init__(self, directory):
            self.dir = Path(directory)
            self.fields = {}
            calls.append(("saved", self.dir))

        @classmethod
        def from_pdf(cls, source, base_dir=None):
            calls.append(("pdf", Path(source), base_dir))
            return SimpleNamespace(set_progress=lambda **fields: calls.append(("progress", fields)))

        @classmethod
        def from_text(cls, source, base_dir=None):
            calls.append(("text", Path(source), base_dir))
            return SimpleNamespace(set_progress=lambda **fields: calls.append(("progress", fields)))

        def set_progress(self, **fields):
            self.fields.update(fields)

    monkeypatch.setattr(booktr.state, "Job", FakeJob)

    def run_source(job, config, **kwargs):
        calls.append(("run_source", config.voice_mode, kwargs))
        return tmp_path / "Česká kniha.m4b"

    def run_saved(job, config, **kwargs):
        calls.append(("run_saved", config.voice_mode, kwargs))
        return tmp_path / "Česká kniha.m4b"

    monkeypatch.setattr(booktr.audio, "run_source", run_source, raising=False)
    monkeypatch.setattr(booktr.audio, "run_saved", run_saved, raising=False)
    return calls


@pytest.mark.parametrize("extension, factory, source_kind", [
    (".pdf", "pdf", "czech_pdf"),
    (".txt", "text", "czech_text"),
])
def test_audio_only_imports_czech_source_without_openai_key(
        tmp_path, monkeypatch, capsys, cli_services, extension, factory, source_kind):
    source = tmp_path / f"Česká kniha{extension}"
    source.write_bytes(b"existing Czech text")
    monkeypatch.setattr(sys, "argv", ["booktr", "--audiobook-only", str(source), "--voice-mode", "advanced"])

    assert cli.main() == 0

    assert ("config", False) in cli_services
    assert (factory, source, tmp_path / "jobs" / "narration") in cli_services
    assert ("progress", {"output_mode": "audio_only", "audio_source_kind": source_kind}) in cli_services
    assert any(call[:2] == ("run_source", "advanced") for call in cli_services)
    assert not any(call[0] == "run_saved" for call in cli_services)
    assert "Audiokniha:" in capsys.readouterr().out


def test_audio_only_saved_job_uses_translation_directly(tmp_path, monkeypatch, cli_services):
    saved = tmp_path / "saved-translation"
    saved.mkdir()
    monkeypatch.setattr(sys, "argv", ["booktr", "--audiobook-only", str(saved)])

    assert cli.main() == 0

    assert ("config", False) in cli_services
    assert ("saved", saved) in cli_services
    assert any(call[0] == "run_saved" for call in cli_services)
    assert not any(call[0] in ("pdf", "text", "run_source") for call in cli_services)


@pytest.mark.parametrize("arguments", [
    ["--headless", "original.pdf", "--audiobook-only", "translated.pdf"],
    ["--audiobook-only", "translated.pdf", "--audiobook"],
    ["--audiobook-only", "translated.pdf", "--font-size", "18"],
    ["--audiobook"],
    ["--voice-mode", "advanced"],
])
def test_audio_only_rejects_conflicting_modes_before_loading_config(arguments, monkeypatch, cli_services):
    monkeypatch.setattr(sys, "argv", ["booktr", *arguments])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    assert cli_services == []


@pytest.mark.parametrize("name, exists", [("missing.pdf", False), ("unsupported.docx", True)])
def test_audio_only_rejects_missing_or_unsupported_source(tmp_path, monkeypatch, cli_services, name, exists):
    source = tmp_path / name
    if exists:
        source.write_text("Česká kniha", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["booktr", "--audiobook-only", str(source)])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    assert cli_services == []


def test_self_test_still_dispatches_without_configuration(monkeypatch, cli_services):
    import booktr.selftest

    calls = []
    monkeypatch.setattr(booktr.selftest, "run", lambda: calls.append("self-test"))
    monkeypatch.setattr(sys, "argv", ["booktr", "--self-test"])

    assert cli.main() == 0
    assert calls == ["self-test"]
    assert cli_services == []


@pytest.mark.parametrize("speech_fails", [False, True])
def test_headless_audiobook_keeps_translate_then_narrate_flow(
        monkeypatch, tmp_path, cli_services, speech_fails):
    import booktr.api
    import booktr.audio
    import booktr.audio.providers
    import booktr.engine

    pipeline = []
    engines = []
    provider = SimpleNamespace(
        validate=lambda: pipeline.append("validate voice"),
        close=lambda: pipeline.append("close provider"),
    )
    monkeypatch.setattr(booktr.audio.providers, "make_provider", lambda config: provider)
    monkeypatch.setattr(booktr.api, "ApiClient", lambda *args, **kwargs: "translation client")

    class FakeEngine:
        def __init__(self, job, api, config, **kwargs):
            assert api == "translation client"
            self.job = job
            self.config = config
            self.report = kwargs["progress_cb"]
            self.pause_event = threading.Event()
            self.cancel_event = threading.Event()
            engines.append(self)

        def run(self):
            pipeline.append("translate")
            return tmp_path / "translated.pdf"

    def make_audio(job, config, *, progress_cb, pause_event, cancel_event):
        [engine] = engines
        assert job is engine.job and config is engine.config
        assert progress_cb is engine.report
        assert pause_event is engine.pause_event and cancel_event is engine.cancel_event
        assert ("progress", {"output_mode": "audio_saved", "status": "running"}) in cli_services
        pipeline.append("narrate translation")
        if speech_fails:
            raise RuntimeError("speech unavailable")
        return tmp_path / "translated.m4b"

    monkeypatch.setattr(booktr.engine, "Engine", FakeEngine)
    monkeypatch.setattr(booktr.audio, "run", lambda *a, **kw: pytest.fail("Legacy audio phase kept translation resume mode"))
    monkeypatch.setattr(booktr.audio, "run_saved", make_audio)
    monkeypatch.setattr(sys, "argv", ["booktr", "--headless", str(tmp_path / "source.pdf"), "--audiobook"])

    if speech_fails:
        with pytest.raises(RuntimeError, match="speech unavailable"):
            cli.main()
    else:
        assert cli.main() == 0
    assert ("config", True) in cli_services
    assert pipeline == ["validate voice", "close provider", "translate", "narrate translation"]


def test_audio_only_cli_generates_and_resumes_real_text_job_without_translation(tmp_path, monkeypatch):
    import booktr.api
    import booktr.audio.service
    import booktr.config
    import booktr.engine
    import booktr.state
    from booktr.audio.providers import PcmAudio

    def forbidden(*args, **kwargs):
        pytest.fail("Existing Czech text reached the translation stack")

    class Narrator:
        max_chars = 1800
        max_bytes = None
        identity = {"provider": "test", "voice": "one", "model": "v1", "rate": 24000}

        def __init__(self):
            self.spoken = []

        def validate(self):
            pass

        def close(self):
            pass

        def synthesize(self, text, **kwargs):
            self.spoken.append(text)
            return PcmAudio(b"\x01\x00" * 100)

    narrator = Narrator()
    config = Config(openai_api_key="", output_dir=str(tmp_path / "outputs"))
    monkeypatch.setattr(cli, "setup_logging", lambda: None)
    monkeypatch.setattr(booktr.config, "load_config", lambda validate=True: config)
    monkeypatch.setattr(booktr.state, "jobs_base_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(booktr.api, "ApiClient", forbidden)
    monkeypatch.setattr(booktr.engine.Engine, "__init__", forbidden)
    monkeypatch.setattr(booktr.engine.Engine, "run", forbidden)
    monkeypatch.setattr(booktr.audio.service, "make_provider", lambda config, **kwargs: narrator)
    monkeypatch.setattr(booktr.audio.service, "find_ffmpeg", lambda: None)
    source = tmp_path / "Česká kniha.txt"
    source.write_text("První český odstavec.\n\nDruhý český odstavec.", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["booktr", "--audiobook-only", str(source)])

    assert cli.main() == 0
    completed_calls = len(narrator.spoken)
    assert completed_calls > 0
    spoken = " ".join(narrator.spoken)
    assert "První český odstavec." in spoken
    assert "Druhý český odstavec." in spoken
    [saved_dir] = (tmp_path / "jobs" / "narration").iterdir()
    job = booktr.state.Job(saved_dir)
    assert job.progress()["status"] == "done"
    assert job.progress()["output_mode"] == "audio_only"
    output = Path(job.progress()["audio_output"])
    with wave.open(str(output)) as audio:
        assert audio.getnframes() == 100 * completed_calls
    original_map = job.read_text("final.json")

    assert cli.main() == 0
    assert len(narrator.spoken) == completed_calls
    assert job.read_text("final.json") == original_map
