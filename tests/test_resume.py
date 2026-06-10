"""Obnova po pádu: hotové chunky se nikdy nepřekládají znovu."""
import json

from booktr.engine import review, translate


def prime(job, sample_book):
    job.write_json("book.json", sample_book)
    job.write_json("synopses.json", {"ch01": "Synopse 1.", "ch02": "Synopse 2."})
    job.write_text("stylesheet.md", "# Stylesheet\n")


def test_translate_resumes_without_repeating_chunks(fake_engine, sample_book):
    prime(fake_engine.job, sample_book)

    translate.run(fake_engine)
    first_calls = fake_engine.api.call_count("TRANSLATE")
    assert first_calls > 0
    draft = fake_engine.job.read_json("draft.json")
    assert draft["ch01-p001"].startswith("CZ: ")

    # simulace pádu po překladu: artefakt smažeme, chunky zůstanou
    (fake_engine.job.dir / "draft.json").unlink()
    translate.run(fake_engine)
    assert fake_engine.api.call_count("TRANSLATE") == first_calls  # žádné nové volání
    assert fake_engine.job.read_json("draft.json") == draft


def test_review_outputs_only_changed_paragraphs(fake_engine, sample_book):
    prime(fake_engine.job, sample_book)
    fake_engine.api.review_changes = {"ch01-p002": "Lepší překlad."}

    translate.run(fake_engine)
    review.run(fake_engine)

    patches = fake_engine.job.read_json("patches.json")
    assert patches == {"ch01-p002": "Lepší překlad."}


def test_full_pipeline_merge(fake_engine, sample_book):
    from booktr.engine import merge

    prime(fake_engine.job, sample_book)
    fake_engine.api.review_changes = {"ch02-p001": "Druhá kapitola, opraveno."}
    translate.run(fake_engine)
    review.run(fake_engine)
    merge.run(fake_engine)

    final = fake_engine.job.read_json("final.json")
    assert final["ch02-p001"] == "Druhá kapitola, opraveno."
    assert final["ch01-p001"] == "CZ: Alice went to the woods."
    assert list(final) == ["ch01-p001", "ch01-p002", "ch02-p001"]


def test_chunking_respects_word_limit(sample_book):
    big = {
        "title": "T", "source_lang": "en",
        "chapters": [{
            "id": "ch01", "heading": "One",
            "paragraphs": [{"id": f"ch01-p{n:03d}", "text": "word " * 100} for n in range(1, 81)],
        }],
    }
    chunks = translate.build_chunks(big, max_words=3000)
    assert len(chunks) > 1
    for c in chunks:
        words = sum(len(p["text"].split()) for p in c["paragraphs"])
        assert words <= 3000 or len(c["paragraphs"]) == 1
    # všechny odstavce přesně jednou
    seen = [p["id"] for c in chunks for p in c["paragraphs"]]
    assert seen == [f"ch01-p{n:03d}" for n in range(1, 81)]
