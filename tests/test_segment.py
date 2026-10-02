import pytest

from booktr.engine import segment
from booktr.engine.segment import all_ids, build_book, looks_like_heading, split_paragraphs
from booktr.errors import BookTrError


def test_split_paragraphs_joins_wrapped_lines():
    page = "First line of a para-\ngraph continues here.\n\nSecond paragraph."
    paras = split_paragraphs(page)
    assert paras == ["First line of a paragraph continues here.", "Second paragraph."]


def test_heading_detection():
    assert looks_like_heading("## Kapitola první")
    assert looks_like_heading("CHAPTER ONE")
    assert looks_like_heading("Kapitola 3")
    assert looks_like_heading("XIV.")
    assert not looks_like_heading("It was a dark and stormy night, and the rain fell in torrents.")
    # křičená přímá řeč není nadpis (chytáno na Alence: „ČISTÍ BOTY A STŘEVÍCE!“)
    assert not looks_like_heading("“HE POLISHES BOOTS AND SHOES!”")
    assert not looks_like_heading('"SOUP OF THE EVENING!"')
    # ani když ji OCR omylem označí jako ## nadpis — věta pokračuje za vykřičníkem
    assert not looks_like_heading("## IT DOES THE BOOTS AND SHOES! ' the Gryphon replied.")
    assert looks_like_heading("## CHAPTER XI. Who Stole the Tarts?")


def test_paragraph_split_by_page_break_is_rejoined():
    pages = [
        "## One\n\nFirst paragraph starts here and",
        "continues on the next page.\n\nSecond paragraph.",
    ]
    book = build_book(pages, title="T")
    paras = [p["text"] for p in book["chapters"][0]["paragraphs"]]
    assert paras == [
        "First paragraph starts here and continues on the next page.",
        "Second paragraph.",
    ]


def test_build_book_assigns_stable_ids():
    pages = [
        "## One\n\nFirst paragraph.\n\nSecond paragraph.",
        "## Two\n\nThird paragraph.",
    ]
    book = build_book(pages, title="T")
    assert [c["id"] for c in book["chapters"]] == ["ch01", "ch02"]
    # titul a nadpisy kapitol mají vlastní ID — překládají se spolu s textem
    assert all_ids(book) == [
        "book-title", "ch01-h000", "ch01-p001", "ch01-p002", "ch02-h000", "ch02-p001",
    ]
    # stejný vstup → stejná ID (stabilita je páteří pipeline)
    assert all_ids(build_book(pages, title="T")) == all_ids(book)


def test_text_before_first_heading_gets_a_chapter():
    book = build_book(["Just some intro text.\n\n## One\n\nBody."], title="T")
    assert len(book["chapters"]) == 2
    assert book["chapters"][0]["paragraphs"][0]["id"] == "ch01-p001"


def test_heading_only_sections_keep_their_source_order_and_translation_ids():
    book = build_book([
        {"index": 1, "blocks": [{"type": "heading", "text": "PART ONE", "bbox": [20, 30, 300, 60]}]},
        {"index": 2, "blocks": [
            {"type": "heading", "text": "Chapter I", "bbox": [20, 40, 300, 70]},
            {"type": "paragraph", "text": "The story begins."},
        ]},
        {"index": 3, "blocks": [{"type": "heading", "text": "Appendix"}]},
    ], "T")
    assert [ch["heading"] for ch in book["chapters"]] == ["PART ONE", "Chapter I", "Appendix"]
    assert all_ids(book) == ["book-title", "ch01-h000", "ch02-h000", "ch02-p001", "ch03-h000"]
    assert book["chapters"][0]["bbox"] == [20, 30, 300, 60]


@pytest.mark.parametrize("following", ["heading", "paragraph"])
def test_image_only_frontispiece_precedes_the_next_source_unit(following):
    blocks = [{"type": following, "text": "Chapter I" if following == "heading" else "The story begins."}]
    if following == "heading":
        blocks.append({"type": "paragraph", "text": "The story begins."})
    book = build_book([
        {"index": 1, "cover": {"path": "assets/cover.png"}, "blocks": []},
        {"index": 2, "blocks": [], "images": [{"id": "frontispiece", "path": "assets/front.png", "source_page": 2}]},
        {"index": 3, "blocks": blocks},
    ], "T")
    image = book["images"][0]
    expected = "before_heading" if following == "heading" else "before_paragraph"
    assert image[expected] == ("ch01-h000" if following == "heading" else "ch01-p001")
    assert "after_paragraph" not in image


def test_czech_preface_does_not_bypass_translation_of_foreign_body(fake_engine):
    fake_engine.job.write_json("pages.json", [
        "Česká předmluva vypráví o autorovi a původu této knihy. " * 50,
        "English story follows its characters through the city and across the river. " * 300,
        "English final chapter brings the narrative to its end. " * 100,
    ])

    class MixedLanguageApi:
        samples = []

        def complete(self, model, system, user, **kwargs):
            self.samples.append(user)
            return "cs" if "Česká předmluva" in user else "en"

    fake_engine.api = MixedLanguageApi()
    book = segment.run(fake_engine)
    assert book["source_lang"] == "unknown"
    assert len(fake_engine.api.samples) == 3
    assert "English story" in fake_engine.api.samples[1]
    assert "English final chapter" in fake_engine.api.samples[2]


@pytest.mark.parametrize("answers,expected", [
    (["cs", "cs", "cs"], "cs"),
    (["cs", "unknown", "cs"], "unknown"),
    (["cs", "Czech", "cs"], "unknown"),
    (["en", "en", "en"], "en"),
])
def test_czech_bypass_requires_every_substantial_sample_to_be_czech(fake_engine, answers, expected):
    fake_engine.job.write_json("pages.json", ["Ordinary prose has many words in each substantial sample. " * 300])

    class LanguageApi:
        replies = iter(answers)

        def complete(self, *args, **kwargs):
            return next(self.replies)

    fake_engine.api = LanguageApi()
    assert segment.run(fake_engine)["source_lang"] == expected


def test_language_detection_does_not_swallow_cancellation(fake_engine):
    fake_engine.job.write_json("pages.json", ["Substantial original prose. " * 30])

    class CancelledApi:
        def complete(self, *args, **kwargs):
            raise BookTrError("cancelled", "Closed window")

    fake_engine.api = CancelledApi()
    with pytest.raises(BookTrError, match="cancelled"):
        segment.run(fake_engine)


def test_multpage_footnote_continuations_keep_the_original_id_and_complete_body():
    book = build_book([
        {"index": 1, "blocks": [{"type": "paragraph", "text": "Original reference[[FN:note-1]]."}],
         "footnotes": [{"id": "note-1", "label": "1", "source_page": 1, "text": "Original note continues with trans-"}]},
        {"index": 2, "blocks": [{"type": "paragraph", "text": "Ordinary second-page prose."}],
         "footnote_continuations": [{"id": "note-1", "text": "lation and exact punctuation;", "source_page": 2}]},
        {"index": 3, "blocks": [],
         "footnote_continuations": [{"id": "note-1", "text": "its final sentence.", "source_page": 3}]},
    ], "T")
    assert book["footnotes"] == [{"id": "note-1", "label": "1", "source_page": 1,
                                    "source_pages": [1, 2, 3],
                                    "text": "Original note continues with translation and exact punctuation; its final sentence."}]
    assert all_ids(book).count("note-1") == 1
    assert all("lation and exact" not in p["text"] for ch in book["chapters"] for p in ch["paragraphs"])


def test_orphan_footnote_continuation_fails_instead_of_losing_original_text():
    with pytest.raises(BookTrError, match="bad_pdf"):
        build_book([{"index": 2, "blocks": [],
                     "footnote_continuations": [{"id": "missing-note", "text": "Original continued note.", "source_page": 2}]}], "T")
