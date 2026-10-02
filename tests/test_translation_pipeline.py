"""Translation invariants and scheduling, without paid model requests."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from booktr.engine import Engine, stylesheet, translate
from booktr.errors import BookTrError
from mock_api import MockApi, _paragraphs_from


def test_chunks_translate_footnotes_once_and_supply_neighbor_context(sample_book):
    book = copy.deepcopy(sample_book)
    book["chapters"][0]["paragraphs"][0]["text"] += " [[FN:note-1]]"
    book["footnotes"] = [{"id": "note-1", "text": "Original note.", "source_page": 1, "label": "1"}]
    chunks = translate.build_chunks(book, max_words=8)
    seen = [p["id"] for chunk in chunks for p in chunk["paragraphs"]]
    assert seen.count("note-1") == 1
    assert chunks[1]["context_before"]
    assert chunks[0]["context_after"]
    assert chunks[-1]["kind"] == "footnotes"


@pytest.mark.parametrize("corrupted", [
    {"p": "Překlad bez poznámky."},
    {"p": "Překlad [[FN:other]]."},
    {"p": "Překlad [[FN:note-1]] [[FN:note-1]]."},
    {"p": "Překlad [[FN:note-1]].", "invented": "Navíc."},
    {"p": {"nested": "not text"}},
])
def test_translation_rejects_damaged_notes_and_invented_ids(fake_engine, corrupted):
    class InvalidApi:
        def complete(self, *args, **kwargs):
            return json.dumps(corrupted)

    fake_engine.api = InvalidApi()
    chunk = {"key": "test", "chapter": "ch01", "paragraphs": [{"id": "p", "text": "Source [[FN:note-1]]."}]}
    book = {"chapters": [{"id": "ch01", "paragraphs": []}]}
    with pytest.raises(BookTrError):
        translate.translate_chunk(fake_engine, "[TRANSLATE]", chunk, {}, book)


def test_checkpoint_changes_when_source_or_model_changes(fake_engine, sample_book):
    fake_engine.job.write_json("book.json", sample_book)
    fake_engine.job.write_json("synopses.json", {})
    fake_engine.job.write_text("stylesheet.md", "Glossary")
    translate.run(fake_engine)
    count = fake_engine.api.call_count("TRANSLATE")
    changed = copy.deepcopy(sample_book)
    changed["chapters"][0]["paragraphs"][0]["text"] = "New source."
    fake_engine.job.write_json("book.json", changed)
    translate.run(fake_engine)
    assert fake_engine.job.read_json("draft.json")["ch01-p001"] == "CZ: New source."
    assert fake_engine.api.call_count("TRANSLATE") > count
    count = fake_engine.api.call_count("TRANSLATE")
    fake_engine.config.model_draft = "another-model"
    translate.run(fake_engine)
    assert fake_engine.api.call_count("TRANSLATE") > count


def test_stylesheet_reads_end_of_every_chapter_and_reduces_consistently(fake_engine, sample_book):
    book = copy.deepcopy(sample_book)
    book["chapters"][0]["paragraphs"] = [
        {"id": "ch01-p001", "text": "word " * 5000},
        {"id": "ch01-p002", "text": "END_CHARACTER_ZOE"},
    ]
    fake_engine.job.write_json("book.json", book)

    class ProfileApi(MockApi):
        def __init__(self):
            super().__init__()
            self.inputs = []

        def complete(self, model, system, user, **kwargs):
            self.inputs.append((system, user))
            return super().complete(model, system, user, **kwargs)

    fake_engine.api = ProfileApi()
    stylesheet.run(fake_engine)
    profiles = [user for system, user in fake_engine.api.inputs if system.startswith("[STYLESHEET]")]
    assert any("END_CHARACTER_ZOE" in text for text in profiles)
    assert fake_engine.api.call_count("STYLE_REDUCE") == 1
    assert "Alice" in fake_engine.job.read_text("stylesheet.md")


def test_pipeline_reviews_early_chunks_before_last_draft_finishes(fake_engine):
    from booktr.engine import pipeline

    book = {"title": "", "source_lang": "en", "chapters": [{
        "id": "ch01", "heading": "", "paragraphs": [
            {"id": f"p{i}", "text": f"source {i}"} for i in range(6)
        ],
    }]}
    fake_engine.config = SimpleNamespace(model_draft="draft", model_review="review", chunk_words=2, translation_workers=3)
    fake_engine.job.write_json("book.json", book)
    fake_engine.job.write_json("synopses.json", {})
    fake_engine.job.write_text("stylesheet.md", "Glossary")
    first_review = threading.Event()
    lock = threading.Lock()
    initial_wave = threading.Barrier(3)

    class ControlledApi(MockApi):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.peak = 0
            self.events = []

        def complete(self, model, system, user, **kwargs):
            with lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                if system.startswith("[TRANSLATE]"):
                    source = _paragraphs_from(user, "PARAGRAPHS_JSON:")
                    if int(list(source)[0][1:]) < 3:
                        initial_wave.wait(timeout=3)
                    if "p0" not in source:
                        assert first_review.wait(3), "whole-stage barrier blocked review"
                    with lock:
                        self.events.append(("draft", list(source)[0]))
                elif system.startswith("[REVIEW]"):
                    first_review.set()
                    with lock:
                        self.events.append(("review", None))
                return super().complete(model, system, user, **kwargs)
            finally:
                with lock:
                    self.active -= 1

    fake_engine.api = ControlledApi()
    pipeline.run(fake_engine)
    assert len(fake_engine.job.read_json("draft.json")) == 6
    assert fake_engine.api.peak == 3
    assert next(i for i, event in enumerate(fake_engine.api.events) if event[0] == "review") < max(
        i for i, event in enumerate(fake_engine.api.events) if event[0] == "draft"
    )
    calls = len(fake_engine.api.calls)
    pipeline.run(fake_engine)
    assert len(fake_engine.api.calls) == calls


def test_czech_book_needs_no_translation_requests(fake_engine, sample_book):
    from booktr.engine import pipeline

    book = copy.deepcopy(sample_book)
    book["source_lang"] = "cs"
    fake_engine.job.write_json("book.json", book)
    fake_engine.job.write_json("synopses.json", {})
    fake_engine.job.write_text("stylesheet.md", "")
    pipeline.run(fake_engine)
    assert fake_engine.api.calls == []
    assert fake_engine.job.read_json("draft.json")["ch01-p001"] == "Alice went to the woods."


def test_invalid_review_cannot_be_marked_complete(fake_engine, sample_book):
    from booktr.engine import review

    class BrokenReviewApi:
        def complete(self, *args, **kwargs):
            return "invalid JSON"

    fake_engine.api = BrokenReviewApi()
    chunk = translate.build_chunks(sample_book)[0]
    draft = {p["id"]: "Překlad" for p in chunk["paragraphs"]}
    with pytest.raises(BookTrError):
        review.review_chunk(fake_engine, "[REVIEW]", chunk, draft)


def test_review_failure_resume_keeps_accepted_draft(fake_engine, sample_book):
    from booktr.engine import pipeline

    class FailReviewApi(MockApi):
        fail = True

        def complete(self, model, system, user, **kwargs):
            if self.fail and system.startswith("[REVIEW]"):
                return "broken review"
            return super().complete(model, system, user, **kwargs)

    fake_engine.api = FailReviewApi()
    fake_engine.job.write_json("book.json", sample_book)
    fake_engine.job.write_json("synopses.json", {})
    fake_engine.job.write_text("stylesheet.md", "")
    with pytest.raises(BookTrError):
        pipeline.run(fake_engine)
    assert not fake_engine.job.exists("patches.json")
    drafts = fake_engine.api.call_count("TRANSLATE")
    fake_engine.api.fail = False
    pipeline.run(fake_engine)
    assert fake_engine.api.call_count("TRANSLATE") == drafts
    assert fake_engine.job.exists("patches.json")


def test_cancel_while_paused_prevents_any_api_request(job, config):
    paused = threading.Event()
    cancelled = threading.Event()
    cancelled.set()
    api = MockApi()
    engine = Engine(job, api, config, pause_event=paused, cancel_event=cancelled)
    with pytest.raises(BookTrError) as raised:
        engine.run_chunks("translate", [("one", "source")], lambda value: api.complete("model", "[TRANSLATE]", value))
    assert raised.value.detail == "Book processing cancelled"
    assert api.calls == []


def test_json_contract_rejects_duplicate_ids():
    from booktr.engine.validation import parse_text_map

    with pytest.raises(ValueError, match="duplicate"):
        parse_text_map('{"p":"first", "p":"second"}')


def test_corrupt_pipeline_checkpoint_is_recomputed(fake_engine, sample_book):
    from booktr.engine import pipeline

    fake_engine.job.write_json("book.json", sample_book)
    fake_engine.job.write_json("synopses.json", {})
    fake_engine.job.write_text("stylesheet.md", "")
    pipeline.run(fake_engine)
    # The aggregate cache may be parseable JSON while violating the contract.
    for path in (fake_engine.job.dir / "chunks").glob("translate-*.json"):
        payload = json.loads(path.read_text())
        if "draft" in payload:
            path.write_text('{"draft": {}, "patches": {}}')
    pipeline.run(fake_engine)
    assert fake_engine.job.read_json("draft.json")["ch01-p001"].startswith("CZ:")


def test_canonical_glossary_rejects_conflicting_duplicate_choices():
    with pytest.raises(ValueError, match="duplicate canonical"):
        stylesheet._validate_profile({
            "glossary": [{"term": "Alice", "czech": "Alice"}, {"term": "Alice", "czech": "Alenka"}],
            "register": [], "style": "",
        }, synopsis=False)


def test_engine_repairs_missing_cover_and_then_resumes_without_calls(job, config):
    from PIL import Image

    config.output_dir = str(job.dir)
    Image.new("RGB", (200, 300), "#2c4a35").save(job.source_pdf, format="PDF")
    api = MockApi()
    Engine(job, api, config).run()
    cover = job.dir / job.read_json("book.json")["cover"]["path"]
    assert cover.is_file()
    calls = len(api.calls)
    cover.unlink()
    Engine(job, api, config).run()
    assert cover.is_file()
    assert len(api.calls) > calls
    calls = len(api.calls)
    Engine(job, api, config).run()
    assert len(api.calls) == calls


def test_profile_preserves_changing_relationship_register():
    data = {"synopsis": "Relationships change.", "glossary": [], "register": [
        {"pair": "Alice -> Bob", "form": "contextual", "note": "Uses vy before the wedding, then ty."},
    ]}
    assert stylesheet._validate_profile(data) == data
    assert "contextual" in stylesheet._render([], data["register"])


def test_profile_repair_includes_bad_response_and_exact_contract(fake_engine):
    broken = {"synopsis": "Summary.", "glossary": [], "register": [
        {"pair": "Alice -> Bob", "form": "ty/vy", "note": "Changes after the wedding."},
    ]}

    class RepairApi:
        def __init__(self):
            self.inputs = []

        def complete(self, model, system, user, **kwargs):
            self.inputs.append(user)
            if len(self.inputs) == 1:
                return json.dumps(broken)
            assert '"form": "ty/vy"' in user
            assert "contextual" in user
            repaired = copy.deepcopy(broken)
            repaired["register"][0]["form"] = "contextual"
            return json.dumps(repaired)

    fake_engine.api = RepairApi()
    result = stylesheet._complete_profile(fake_engine, stylesheet.SYSTEM, "Source excerpt")
    assert result["register"][0]["form"] == "contextual"
    diagnostics = list((fake_engine.job.dir / "chunks").glob("diagnostic-profile-invalid-*.json"))
    assert len(diagnostics) == 1
    assert json.loads(diagnostics[0].read_text())["raw_response"] == json.dumps(broken)


def test_punctuation_and_marker_only_source_entries_survive_models_that_drop_them(fake_engine):
    from booktr.engine import review

    chunk = {"key": "punctuation", "chapter": "ch01", "paragraphs": [
        {"id": "prose", "text": "Original prose."},
        {"id": "closing", "text": "]"},
        {"id": "date", "text": "1842."},
        {"id": "anchor", "text": "[[FN:fn-1]]"},
    ]}
    book = {"source_lang": "en", "chapters": [{"id": "ch01", "paragraphs": []}]}

    class ProseOnlyApi:
        calls = []

        def complete(self, model, system, user, **kwargs):
            self.calls.append(system)
            marker = "SOURCE PARAGRAPHS_JSON:" if system.startswith("[REVIEW]") else "PARAGRAPHS_JSON:"
            requested = _paragraphs_from(user, marker)
            # Models must never get the punctuation-only IDs as editable entries.
            assert requested == {"prose": "Original prose."}
            return json.dumps({"prose": "Upravený český text." if system.startswith("[REVIEW]") else "Český text."})

    fake_engine.api = ProseOnlyApi()
    draft = translate.translate_chunk(fake_engine, "[TRANSLATE]", chunk, {}, book)
    assert draft == {"prose": "Český text.", "closing": "]", "date": "1842.", "anchor": "[[FN:fn-1]]"}
    patches = review.review_chunk(fake_engine, "[REVIEW]", chunk, draft)
    assert patches == {"prose": "Upravený český text."}
    assert len(fake_engine.api.calls) == 2


def test_nonlinguistic_chunk_requires_no_translation_or_editor_request(fake_engine):
    from booktr.engine import review

    chunk = {"key": "punctuation", "chapter": "ch01", "paragraphs": [
        {"id": "closing", "text": "]"}, {"id": "anchor", "text": "[[FN:fn-1]] 1842."},
    ]}
    book = {"chapters": [{"id": "ch01", "paragraphs": []}]}
    draft = translate.translate_chunk(fake_engine, "[TRANSLATE]", chunk, {}, book)
    assert draft == {"closing": "]", "anchor": "[[FN:fn-1]] 1842."}
    assert review.review_chunk(fake_engine, "[REVIEW]", chunk, draft) == {}
    assert fake_engine.api.calls == []


def test_editor_still_reviews_complete_author_note_text(fake_engine):
    from booktr.engine import review

    chunk = {"key": "footnotes", "chapter": "footnotes", "kind": "footnotes", "paragraphs": [
        {"id": "fn-1", "text": "The original author explains the historical event of 1842."},
    ]}
    book = {"chapters": []}
    draft = translate.translate_chunk(fake_engine, "[TRANSLATE]", chunk, {}, book)
    assert draft["fn-1"].endswith(chunk["paragraphs"][0]["text"])
    review.review_chunk(fake_engine, "[REVIEW]", chunk, draft)
    assert fake_engine.api.call_count("TRANSLATE") == 1
    assert fake_engine.api.call_count("REVIEW") == 1


def test_nonlinguistic_source_changes_in_cached_output_are_rejected():
    from booktr.engine.validation import validate_text_map

    with pytest.raises(ValueError, match="nonlinguistic source"):
        validate_text_map({"punct": "]"}, {"punct": """Omitted bracket replaced with words."""})


def test_numeric_book_title_is_preserved_during_heading_harmonization(fake_engine):
    from booktr.engine import review

    assert review.harmonize_headings(fake_engine, [("book-title", "1984"), ("h", "1")],
                                    {"book-title": "1984", "h": "1"}) == {}
    assert fake_engine.api.calls == []


def test_cached_nonlinguistic_migration_preserves_paid_prose_and_rejects_unknown_ids():
    from booktr.engine.validation import normalize_cached_text_map

    source = {"prose": "Source words.", "punct": "]"}
    migrated = normalize_cached_text_map(source, {"prose": "Přijatý český překlad.", "punct": "] "})
    assert migrated == {"prose": "Přijatý český překlad.", "punct": "]"}
    with pytest.raises(ValueError, match="ID mismatch"):
        normalize_cached_text_map(source, {**migrated, "unknown": "Extra"})
    with pytest.raises(ValueError, match="ID mismatch"):
        normalize_cached_text_map(source, {"punct": "] "})


def test_cached_pipeline_migrates_only_constants_without_repeating_paid_calls(fake_engine):
    from booktr.engine import pipeline

    book = {"source_lang": "en", "title": "", "chapters": [{"id": "ch01", "heading": "", "paragraphs": [
        {"id": "prose", "text": "Original prose."}, {"id": "punct", "text": "]"},
    ]}]}
    fake_engine.job.write_json("book.json", book)
    fake_engine.job.write_json("synopses.json", {})
    fake_engine.job.write_text("stylesheet.md", "Guide")
    pipeline.run(fake_engine)
    calls = len(fake_engine.api.calls)
    aggregates = []
    for path in (fake_engine.job.dir / "chunks").glob("translate-*.json"):
        data = json.loads(path.read_text())
        if "draft" in data:
            data["draft"]["punct"] = "] "
            path.write_text(json.dumps(data))
            aggregates.append(path)
    assert aggregates
    pipeline.run(fake_engine)
    assert len(fake_engine.api.calls) == calls
    assert fake_engine.job.read_json("draft.json")["punct"] == "]"
    assert all(json.loads(path.read_text())["draft"]["punct"] == "]" for path in aggregates)


def test_profile_cache_includes_language_hint_even_when_source_prose_is_identical(fake_engine, sample_book):
    fake_engine.job.write_json("book.json", sample_book)
    stylesheet.run(fake_engine)
    before = fake_engine.api.call_count("STYLESHEET")
    changed = copy.deepcopy(sample_book)
    changed["source_lang"] = "fr"
    fake_engine.job.write_json("book.json", changed)
    stylesheet.run(fake_engine)
    assert fake_engine.api.call_count("STYLESHEET") == before * 2


def test_extraction_epoch_reuses_unchanged_paid_maps_but_changed_source_does_not(fake_engine, monkeypatch, tmp_path):
    from booktr.engine import ingest
    from booktr.engine.typeset import compile_typ

    source = fake_engine.job.dir / "original.typ"

    def original(text):
        source.write_text('#set page(paper: "a5", margin: 18mm)\n#set text(size: 12pt)\n' + text)
        compile_typ(source, fake_engine.job.source_pdf)

    original("The original distinctive source sentence has enough words for native text extraction.")
    fake_engine.config.output_dir = str(tmp_path / "published")
    engine = Engine(fake_engine.job, fake_engine.api, fake_engine.config)
    engine.run()
    paid_tags = ("STYLESHEET", "STYLE_GUIDE", "STYLE_REDUCE", "TRANSLATE", "REVIEW", "HEADINGS")
    first_counts = {tag: fake_engine.api.call_count(tag) for tag in paid_tags}
    first_book = fake_engine.job.read_json("book.json")
    monkeypatch.setattr(ingest, "EXTRACTION_VERSION", ingest.EXTRACTION_VERSION + 1)
    engine.run()
    assert fake_engine.job.read_json("book.json") == first_book
    assert {tag: fake_engine.api.call_count(tag) for tag in paid_tags} == first_counts
    original("The updated distinctive source sentence has enough words for native text extraction.")
    engine.run()
    assert fake_engine.api.call_count("STYLESHEET") > first_counts["STYLESHEET"]
    assert fake_engine.api.call_count("TRANSLATE") > first_counts["TRANSLATE"]
    assert any("updated distinctive source" in text for text in fake_engine.job.read_json("final.json").values())
    assert not any("original distinctive source" in text for text in fake_engine.job.read_json("final.json").values())
