from __future__ import annotations

import base64
import io
import json
import subprocess
import threading
import wave
from types import SimpleNamespace

import httpx
import pytest

from booktr.audio.chunking import split_text
from booktr.audio.providers import ElevenLabsProvider, GoogleProvider, PcmAudio
from booktr.audio.service import AudiobookService, stitch_wav
from booktr.errors import BookTrError


def wav_bytes(samples: bytes, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples)
    return buffer.getvalue()


@pytest.mark.parametrize("text", ["Žluťoučký kůň. " * 1000, "Ř" * 10000, "Slovo " * 1000])
def test_chunks_obey_utf8_and_character_limits_without_losing_text(text):
    chunks = split_text(text, max_chars=1800, max_bytes=4500)
    assert all(0 < len(chunk) <= 1800 and len(chunk.encode("utf-8")) <= 4500 for chunk in chunks)
    assert "".join("".join(chunks).split()) == "".join(text.split())


def test_chunking_prefers_sentence_boundary():
    assert split_text("První věta. Druhá věta je delší.", max_chars=23) == [
        "První věta.", "Druhá věta je delší.",
    ]


def test_google_sends_czech_chirp_and_decodes_wav():
    seen = []
    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"voices": [{"name": "cs-CZ-Chirp3-HD-Gacrux"}]})
        return httpx.Response(200, json={"audioContent": base64.b64encode(wav_bytes(b"\x01\x00" * 50)).decode()})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleProvider(SimpleNamespace(google_api_key="secret", google_cloud_project="project-id"), client=client)
        provider.validate()
        audio = provider.synthesize("Dobrý den.")
    assert audio.frames == b"\x01\x00" * 50
    body = json.loads(seen[-1].content)
    assert body["voice"] == {"languageCode": "cs-CZ", "name": "cs-CZ-Chirp3-HD-Gacrux"}
    assert body["audioConfig"]["audioEncoding"] == "LINEAR16"
    assert seen[-1].headers["x-goog-api-key"] == "secret"
    assert "x-goog-user-project" not in seen[-1].headers


def test_eleven_v4_uses_dialogue_and_neighboring_context():
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json=[{"model_id": "eleven_v4", "can_do_text_to_speech": True}])
        if request.url.path.startswith("/v1/voices/"):
            return httpx.Response(200, json={"voice_id": "czech-narrator"})
        return httpx.Response(200, content=b"\x02\x00" * 100)
    config = SimpleNamespace(elevenlabs_api_key="secret", elevenlabs_voice_id="czech-narrator")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = ElevenLabsProvider(config, client=client)
        provider.validate()
        audio = provider.synthesize("Ahoj.", previous_text="x" * 150, future_text="y" * 150)
    assert len(audio.frames) == 200
    request = seen[-1]
    assert request.url.path == "/v1/text-to-dialogue"
    assert request.url.params["output_format"] == "pcm_24000"
    body = json.loads(request.content)
    assert body["model_id"] == "eleven_v4"
    assert body["language_code"] == "cs"
    assert body["inputs"] == [{"text": "Ahoj.", "voice_id": "czech-narrator"}]
    assert len(body["previous_text"]) == len(body["future_text"]) == 100


def test_provider_retries_only_transient_http_failures():
    attempts = []
    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, content=b"\x00\x00" * 100)
    config = SimpleNamespace(elevenlabs_api_key="secret", elevenlabs_voice_id="v")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = ElevenLabsProvider(config, client=client, sleep=lambda _: None)
        provider.synthesize("Zkouška.")
    assert len(attempts) == 2


def test_stitch_wav_copies_actual_audio_in_order(tmp_path):
    sources = []
    for i in (1, 2, 3):
        path = tmp_path / f"{i}.wav"
        path.write_bytes(wav_bytes(bytes([i, 0]) * 10))
        sources.append(path)
    output = tmp_path / "book.wav"
    stitch_wav(sources, output)
    with wave.open(str(output)) as reader:
        assert reader.getnframes() == 30
        assert reader.readframes(30) == b"\x01\x00" * 10 + b"\x02\x00" * 10 + b"\x03\x00" * 10


def test_stitch_failure_preserves_existing_output(tmp_path):
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    output = tmp_path / "book.wav"
    first.write_bytes(wav_bytes(b"\x00\x00" * 10))
    second.write_bytes(wav_bytes(b"\x00\x00" * 10, rate=16000))
    output.write_bytes(b"previous output")
    with pytest.raises(ValueError, match="format"):
        stitch_wav([first, second], output)
    assert output.read_bytes() == b"previous output"


class FakeProvider:
    max_chars = 50
    max_bytes = None
    identity = {"provider": "fake", "voice": "one", "model": "v1", "rate": 24000}
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
    def validate(self):
        pass
    def close(self):
        pass
    def synthesize(self, text, **kwargs):
        with self.lock:
            self.calls.append(text)
        return PcmAudio(b"\x03\x00" * 100)


def test_audiobook_resume_voice_invalidation_and_chapter_offsets(job, tmp_path, monkeypatch):
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    config = SimpleNamespace(output_path=tmp_path, audio_workers=3, voice_mode="normal")
    book = {"title": "Test", "chapters": [
        {"id": "ch01", "heading": "První", "paragraphs": [{"id": "p1"}]},
        {"id": "ch02", "heading": "Druhá", "paragraphs": [{"id": "p2"}]},
    ]}
    final = {"p1": "První odstavec.", "p2": "Druhý odstavec."}
    provider = FakeProvider()
    output = AudiobookService(job, config, provider=provider).run(book, final)
    assert output.suffix == ".wav"
    assert job.progress()["status"] == "done"
    assert len(provider.calls) == 2
    manifest = json.loads(output.with_suffix(".chapters.json").read_text())
    assert manifest["chapters"][0]["start_ms"] == 0
    assert manifest["chapters"][1]["start_ms"] > 0
    AudiobookService(job, config, provider=provider).run(book, final)
    assert len(provider.calls) == 2
    provider.identity = {**provider.identity, "voice": "two"}
    AudiobookService(job, config, provider=provider).run(book, final)
    assert len(provider.calls) == 4
    final["p2"] = "Změněný odstavec."
    AudiobookService(job, config, provider=provider).run(book, final)
    assert len(provider.calls) == 5  # Only the changed chapter is billed again.


def test_audiobook_reads_translated_footnotes_once_without_spoken_ids(job, tmp_path, monkeypatch):
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    config = SimpleNamespace(output_path=tmp_path, audio_workers=3, voice_mode="normal")
    book = {"title": "Poznámky", "chapters": [{"id": "ch01", "paragraphs": [{"id": "p1"}, {"id": "p2"}]}],
            "footnotes": [{"id": "fn-p0001-001", "text": "Source note", "source_page": 1}]}
    final = {"p1": "Věta[[FN:fn-p0001-001]].", "p2": "Znovu[[FN:fn-p0001-001]].", "fn-p0001-001": "Česká poznámka."}
    provider = FakeProvider()
    provider.max_chars = 1800
    AudiobookService(job, config, provider=provider).run(book, final)
    spoken = " ".join(provider.calls)
    assert "[[FN:" not in spoken and "fn-p0001" not in spoken
    assert spoken.count("Česká poznámka.") == 1
    assert spoken.index("Věta.") < spoken.index("Poznámka: Česká poznámka.") < spoken.index("Znovu.")


def test_audiobook_rejects_missing_footnote_body_before_paid_requests(job, tmp_path):
    config = SimpleNamespace(output_path=tmp_path, audio_workers=3, voice_mode="normal")
    book = {"title": "Test", "chapters": [{"id": "ch01", "paragraphs": [{"id": "p"}]}],
            "footnotes": [{"id": "n", "text": "Original"}]}
    provider = FakeProvider()
    with pytest.raises(Exception, match="translated footnote"):
        AudiobookService(job, config, provider=provider).run(book, {"p": "Text[[FN:n]]."})
    assert provider.calls == []


def test_provider_auth_error_does_not_retry_or_echo_secret():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"detail": "Invalid API key: secret-to-never-log"})
    config = SimpleNamespace(elevenlabs_api_key="secret-to-never-log", elevenlabs_voice_id="v")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BookTrError) as error:
            ElevenLabsProvider(config, client=client).synthesize("Text.")
    assert error.value.code == "auth"
    assert "secret-to-never-log" not in str(error.value)
    assert len(calls) == 1


def test_paused_audio_can_be_cancelled_without_resume(job, tmp_path, monkeypatch):
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    validated, cancelled, paused = threading.Event(), threading.Event(), threading.Event()
    provider = FakeProvider()
    provider.validate = validated.set
    config = SimpleNamespace(output_path=tmp_path, audio_workers=3, voice_mode="normal")
    book = {"title": "Test", "chapters": [{"id": "ch01", "paragraphs": [{"id": "p"}]}]}
    errors = []
    def work():
        try:
            AudiobookService(job, config, pause_event=paused, cancel_event=cancelled, provider=provider).run(book, {"p": "Text."})
        except BookTrError as error:
            errors.append(error)
    thread = threading.Thread(target=work)
    thread.start()
    assert validated.wait(1)
    cancelled.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(errors) == 1 and errors[0].code == "cancelled"
    assert provider.calls == []


def test_real_m4b_export_embeds_chapters_and_decodes_audio(tmp_path):
    from booktr.audio.service import _make_m4b, find_ffmpeg
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    source = tmp_path / "book.wav"
    source.write_bytes(wav_bytes(b"\x01\x00" * 48000))
    manifest = {"title": "Test audiobook", "chapters": [
        {"title": "First", "start_ms": 0, "end_ms": 1000},
        {"title": "Second", "start_ms": 1000, "end_ms": 2000},
    ]}
    output = _make_m4b(source, manifest, ffmpeg)
    assert output is not None
    decoded = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(output), "-f", "null", "-"], capture_output=True)
    assert decoded.returncode == 0
    inspected = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(output)], capture_output=True)
    metadata = inspected.stderr.decode("utf-8")
    assert "Chapters:" in metadata and "First" in metadata and "Second" in metadata
