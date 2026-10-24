from __future__ import annotations

import pytest

from booktr.audio import prepare_source, run_saved, run_source
from booktr.config import Config
from booktr.errors import BookTrError


class Narrator:
    max_chars = 1800
    max_bytes = None
    identity = {"provider": "fake", "voice": "one", "model": "v1", "rate": 24000}

    def __init__(self):
        self.calls = []

    def validate(self):
        pass

    def synthesize(self, text, **kwargs):
        from booktr.audio.providers import PcmAudio
        self.calls.append(text)
        return PcmAudio(b"\x03\x00" * 100)


def translated_source(job):
    book = {"title": "The book", "source_lang": "en", "chapters": [
        {"id": "ch01", "heading": "One", "paragraphs": [
            {"id": "ch01-p001", "text": "The river[[FN:fn01]] flowed."},
            {"id": "ch01-p002", "text": "The river was blue."},
        ]},
    ], "footnotes": [{"id": "fn01", "text": "The source note."}]}
    final = {"book-title": "Kniha", "ch01-h000": "První kapitola",
             "ch01-p001": "Řeka[[FN:fn01]] tekla.", "ch01-p002": "Řeka byla modrá.",
             "fn01": "Původní poznámka."}
    job.write_json("book.json", book)
    job.write_json("final.json", final)
    job.write_json("fingerprints.json", {"translation": "unchanged"})
    job.set_progress(status="done", output=str(job.dir / "Kniha.pdf"))
    return book, final


def test_saved_translation_narrates_exact_final_without_any_translation(job, tmp_path, monkeypatch):
    from booktr.api import ApiClient
    from booktr.engine import Engine
    monkeypatch.setattr(ApiClient, "complete", lambda *args, **kwargs: pytest.fail("OpenAI was called"))
    monkeypatch.setattr(Engine, "run", lambda *args: pytest.fail("Translation engine was called"))
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    _, final = translated_source(job)
    original = {name: (job.dir / name).read_bytes() for name in ("book.json", "final.json", "fingerprints.json")}
    narrator = Narrator()
    config = Config(output_dir=str(tmp_path), openai_api_key="")
    output = run_saved(job, config, provider=narrator)
    assert output.is_file()
    assert narrator.calls == ["První kapitola Řeka tekla. Poznámka: Původní poznámka. Řeka byla modrá."]
    assert original == {name: (job.dir / name).read_bytes() for name in original}
    assert job.progress()["audio_source_kind"] == "translation"
    assert job.progress()["output"].endswith("Kniha.pdf")
    run_saved(job, config, provider=narrator)
    assert len(narrator.calls) == 1  # Reuse completed narration chunks.
    assert final == job.read_json("final.json")


@pytest.mark.parametrize("change", ["heading", "paragraph", "note", "extra", "anchor"])
def test_saved_translation_rejects_incomplete_or_changed_source_map_before_narration(job, tmp_path, change):
    _, final = translated_source(job)
    if change == "heading":
        del final["ch01-h000"]
    elif change == "paragraph":
        del final["ch01-p002"]
    elif change == "note":
        del final["fn01"]
    elif change == "extra":
        final["unexpected"] = "Nemá se číst."
    else:
        final["ch01-p001"] = "Řeka tekla."
    job.write_json("final.json", final)
    narrator = Narrator()
    with pytest.raises(BookTrError, match="complete"):
        run_saved(job, Config(output_dir=str(tmp_path)), provider=narrator)
    assert narrator.calls == []


def test_saved_translation_rejects_duplicate_json_ids(job, tmp_path):
    translated_source(job)
    final_path = job.dir / "final.json"
    text = final_path.read_text()
    final_path.write_text(text[:-1] + ', "ch01-p002": "Vyměněný text."}')
    with pytest.raises(BookTrError, match="complete"):
        run_saved(job, Config(output_dir=str(tmp_path)), provider=Narrator())


def test_czech_text_import_is_verbatim_without_openai_and_resumes(job, tmp_path, monkeypatch):
    from booktr.api import ApiClient
    monkeypatch.setattr(ApiClient, "complete", lambda *args, **kwargs: pytest.fail("OpenAI was called"))
    monkeypatch.setattr("booktr.engine.segment._source_language", lambda *args: pytest.fail("Language detection was called"))
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    job.set_progress(audio_source_kind="czech_text", title="Česká kniha")
    source = "Kapitola 1\n\nŽluťoučký kůň běžel přes louku.\n\nKapitola 2\n\nŘeka klidně tekla."
    (job.dir / "source.txt").write_text(source, encoding="utf-8")
    config = Config(output_dir=str(tmp_path), openai_api_key="")
    book, final = prepare_source(job, config)
    assert book["source_lang"] == "cs"
    assert final["ch01-p001"] == "Žluťoučký kůň běžel přes louku."
    assert final["ch02-p001"] == "Řeka klidně tekla."
    narrator = Narrator()
    run_source(job, config, provider=narrator)
    before = list(narrator.calls)
    run_source(job, config, provider=narrator)
    assert narrator.calls == before
    assert len(before) == 2
    assert job.read_json("audio-source.json")["kind"] == "czech_text"


def test_changed_import_rebuilds_source_and_only_changed_audio_chapter(job, tmp_path, monkeypatch):
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    job.set_progress(audio_source_kind="czech_text", title="Česká kniha")
    source_path = job.dir / "source.txt"
    source_path.write_text("Kapitola 1\n\nPrvní věta.\n\nKapitola 2\n\nDruhá věta.")
    narrator = Narrator()
    config = Config(output_dir=str(tmp_path))
    run_source(job, config, provider=narrator)
    source_path.write_text("Kapitola 1\n\nPrvní věta.\n\nKapitola 2\n\nZměněná druhá věta.")
    run_source(job, config, provider=narrator)
    assert len(narrator.calls) == 3
    assert job.read_json("final.json")["ch02-p001"] == "Změněná druhá věta."


def test_import_cannot_overwrite_original_translation(job, tmp_path):
    translated_source(job)
    before = (job.dir / "final.json").read_bytes()
    with pytest.raises(BookTrError, match="explicit"):
        prepare_source(job, Config(output_dir=str(tmp_path)))
    assert before == (job.dir / "final.json").read_bytes()


def test_empty_text_is_rejected_before_narration(job, tmp_path):
    job.set_progress(audio_source_kind="czech_text")
    (job.dir / "source.txt").write_text("  \n\n")
    narrator = Narrator()
    with pytest.raises(BookTrError, match="No text"):
        run_source(job, Config(output_dir=str(tmp_path)), provider=narrator)
    assert narrator.calls == []


def test_text_reading_accepts_utf8_bom_and_utf16_bom(job, tmp_path):
    job.set_progress(audio_source_kind="czech_text")
    path = job.dir / "source.txt"
    config = Config(output_dir=str(tmp_path))
    for encoding in ("utf-8-sig", "utf-16"):
        path.write_bytes("Žluťoučký kůň.\n\nČeská řeka.".encode(encoding))
        _, final = prepare_source(job, config)
        assert final["ch01-p001"] == "Žluťoučký kůň."


def test_native_czech_pdf_needs_no_openai_and_preserves_footnotes(job, tmp_path, monkeypatch):
    from booktr.api import ApiClient
    import typst
    source = '#set text(font: "Arial")\n#set page(width: 148mm,height: 210mm)\n= Kapitola 1\nČeská řeka #footnote[Úplná česká poznámka.] teče klidně. ' + "Žluťoučký kůň běží po louce. " * 10
    template = tmp_path / "native.typ"
    template.write_text(source)
    job.source_pdf.write_bytes(typst.compile(str(template)))
    job.set_progress(audio_source_kind="czech_pdf", title="Česká kniha")
    monkeypatch.setattr(ApiClient, "complete", lambda *args, **kwargs: pytest.fail("OpenAI was called"))
    monkeypatch.setattr("booktr.engine.segment._source_language", lambda *args: pytest.fail("Language detection was called"))
    book, final = prepare_source(job, Config(output_dir=str(tmp_path), openai_api_key=""))
    assert book["source_lang"] == "cs"
    assert len(book["footnotes"]) == 1
    assert final[book["footnotes"][0]["id"]] == "Úplná česká poznámka."
    assert "teče klidně." in final["ch01-p001"]


def test_explicit_import_kind_alone_cannot_replace_an_existing_translation(job, tmp_path):
    translated_source(job)
    original = (job.dir / "final.json").read_bytes()
    job.set_progress(audio_source_kind="czech_text")
    (job.dir / "source.txt").write_text("Jiná kniha.")
    with pytest.raises(BookTrError, match="own narration job"):
        prepare_source(job, Config(output_dir=str(tmp_path)))
    assert (job.dir / "final.json").read_bytes() == original


def test_import_resume_repairs_changed_derived_text_from_original_without_translation(job, tmp_path):
    job.set_progress(audio_source_kind="czech_text", title="Česká kniha")
    (job.dir / "source.txt").write_text("Řeka klidně tekla.")
    config = Config(output_dir=str(tmp_path))
    prepare_source(job, config)
    book = job.read_json("book.json")
    final = job.read_json("final.json")
    book["chapters"][0]["paragraphs"][0]["text"] = "Tato věta nepatří do původní knihy."
    final["ch01-p001"] = "Tato věta nepatří do původní knihy."
    job.write_json("book.json", book)
    job.write_json("final.json", final)
    _, repaired = prepare_source(job, config)
    assert repaired["ch01-p001"] == "Řeka klidně tekla."


def test_scanned_import_validates_narrator_before_any_paid_transcription(job, tmp_path, monkeypatch):
    from booktr.engine import ingest
    job.set_progress(audio_source_kind="czech_pdf")
    job.source_pdf.write_bytes(b"dummy source for stubbed extraction")
    events = []

    class BadNarrator(Narrator):
        def validate(self):
            events.append("speech validation")
            raise BookTrError("auth", "Speech credentials unavailable")

    class Transcription:
        def complete(self, *args, **kwargs):
            events.append("paid transcription")
            return "{}"

    monkeypatch.setattr(ingest, "run", lambda context: context.api.complete(
        "model", "transcribe", "page", images=[b"image"]))
    with pytest.raises(BookTrError, match="Speech credentials"):
        run_source(job, Config(output_dir=str(tmp_path)), api=Transcription(), provider=BadNarrator())
    assert events == ["speech validation"]


def test_scanned_import_reuses_preflight_provider_and_closes_owned_instance(job, tmp_path, monkeypatch):
    from booktr.engine import ingest
    job.set_progress(audio_source_kind="czech_pdf", title="Česká kniha")
    job.source_pdf.write_bytes(b"dummy source for stubbed extraction")
    events = []

    class OwnedNarrator(Narrator):
        def validate(self):
            events.append("speech validation")

        def synthesize(self, text, **kwargs):
            events.append("speech generation")
            return super().synthesize(text, **kwargs)

        def close(self):
            events.append("close")

    class Transcription:
        def complete(self, *args, **kwargs):
            events.append("paid transcription")
            return "{}"

    narrator = OwnedNarrator()
    monkeypatch.setattr("booktr.audio.service.make_provider", lambda *args, **kwargs: narrator)
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)

    def extraction(context):
        context.api.complete("model", "transcribe", "page", images=[b"image"])
        context.api.complete("model", "transcribe", "page", images=[b"image"])
        return ["Přesný český odstavec."]

    monkeypatch.setattr(ingest, "run", extraction)
    run_source(job, Config(output_dir=str(tmp_path)), api=Transcription())
    assert events == ["speech validation", "paid transcription", "paid transcription", "speech generation", "close"]


def test_failed_scanned_import_closes_owned_speech_provider(job, tmp_path, monkeypatch):
    from booktr.engine import ingest
    job.set_progress(audio_source_kind="czech_pdf")
    job.source_pdf.write_bytes(b"dummy source for stubbed extraction")
    events = []

    class OwnedNarrator(Narrator):
        def validate(self):
            raise BookTrError("auth", "Speech credentials unavailable")

        def close(self):
            events.append("close")

    narrator = OwnedNarrator()
    monkeypatch.setattr("booktr.audio.service.make_provider", lambda *args, **kwargs: narrator)
    monkeypatch.setattr(ingest, "run", lambda context: context.api.complete(
        "model", "transcribe", "page", images=[b"image"]))
    with pytest.raises(BookTrError, match="Speech credentials"):
        run_source(job, Config(output_dir=str(tmp_path)))
    assert events == ["close"]


@pytest.mark.parametrize("kind", ["czech_pdf", "czech_text"])
def test_saved_narration_retains_imported_source_kind_and_original_media(job, tmp_path, monkeypatch, kind):
    import typst
    from booktr.api import ApiClient

    monkeypatch.setattr(ApiClient, "complete", lambda *args, **kwargs: pytest.fail("OpenAI was called"))
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    job.set_progress(audio_source_kind=kind, title="Česká kniha")
    if kind == "czech_pdf":
        template = tmp_path / "saved-native.typ"
        template.write_text("= Kapitola 1\nPůvodní český odstavec zůstává stejný v audioknize i v PDF.")
        job.source_pdf.write_bytes(typst.compile(str(template)))
        source_path = job.source_pdf
    else:
        source_path = job.dir / "source.txt"
        source_path.write_text("Kapitola 1\n\nPůvodní český odstavec zůstává stejný v audioknize i v textu.")
    config = Config(output_dir=str(tmp_path), openai_api_key="")
    prepare_source(job, config)
    source_before = source_path.read_bytes()
    metadata_before = (job.dir / "audio-source.json").read_bytes()
    fingerprints_before = (job.dir / "fingerprints.json").read_bytes()
    # Recover the authoritative import origin even if an older saved-audio run
    # replaced the display-level progress kind with "translation".
    job.set_progress(audio_source_kind="translation")
    narrator = Narrator()
    for _ in range(2):
        run_saved(job, config, provider=narrator)
        assert job.progress()["audio_source_kind"] == kind
        assert job.progress()["output_mode"] == "audio_saved"
        assert source_path.read_bytes() == source_before
        assert (job.dir / "audio-source.json").read_bytes() == metadata_before
        assert (job.dir / "fingerprints.json").read_bytes() == fingerprints_before
    assert len(narrator.calls) == 1


def test_saved_translation_does_not_trust_import_kind_without_canonical_metadata(job, tmp_path, monkeypatch):
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    translated_source(job)
    job.set_progress(audio_source_kind="czech_pdf")
    job.write_json("audio-source.json", {"kind": "czech_pdf", "version": 2, "fingerprint": "forged"})
    run_saved(job, Config(output_dir=str(tmp_path)), provider=Narrator())
    assert job.progress()["audio_source_kind"] == "translation"


def test_saved_import_rejects_consistently_altered_text_before_narration(job, tmp_path, monkeypatch):
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    job.set_progress(audio_source_kind="czech_text", title="Česká kniha")
    (job.dir / "source.txt").write_text("Původní český odstavec.")
    config = Config(output_dir=str(tmp_path))
    prepare_source(job, config)
    book = job.read_json("book.json")
    final = job.read_json("final.json")
    book["chapters"][0]["paragraphs"][0]["text"] = "Změněný český odstavec."
    final["ch01-p001"] = "Změněný český odstavec."
    job.write_json("book.json", book)
    job.write_json("final.json", final)
    narrator = Narrator()
    with pytest.raises(BookTrError, match="original Czech source"):
        run_saved(job, config, provider=narrator)
    assert narrator.calls == []
    assert (job.dir / "source.txt").read_text() == "Původní český odstavec."


def test_saved_import_with_missing_provenance_cannot_fall_back_to_translation(job, tmp_path):
    job.set_progress(audio_source_kind="czech_text", title="Česká kniha")
    (job.dir / "source.txt").write_text("Původní český odstavec.")
    config = Config(output_dir=str(tmp_path))
    prepare_source(job, config)
    (job.dir / "audio-source.json").unlink()
    narrator = Narrator()
    with pytest.raises(BookTrError, match="original Czech source"):
        run_saved(job, config, provider=narrator)
    assert narrator.calls == []


def test_regular_saved_translation_can_narrate_corrected_czech_prose(job, tmp_path, monkeypatch):
    monkeypatch.setattr("booktr.audio.service.find_ffmpeg", lambda: None)
    _, final = translated_source(job)
    final["ch01-p002"] = "Řeka byla tmavě modrá."
    job.write_json("final.json", final)
    narrator = Narrator()
    run_saved(job, Config(output_dir=str(tmp_path)), provider=narrator)
    assert "Řeka byla tmavě modrá." in " ".join(narrator.calls)
    assert job.progress()["audio_source_kind"] == "translation"
