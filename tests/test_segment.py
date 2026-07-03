from booktr.engine.segment import all_ids, build_book, looks_like_heading, split_paragraphs


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
