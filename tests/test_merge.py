import pytest

from booktr.engine.merge import merge_book
from booktr.errors import BookTrError


def ids(book):
    return [p["id"] for ch in book["chapters"] for p in ch["paragraphs"]]


def make_draft(book):
    return {i: f"CZ {i}" for i in ids(book)}


def test_patch_wins_over_draft(sample_book):
    draft = make_draft(sample_book)
    final = merge_book(sample_book, draft, {"ch01-p002": "Opraveno."})
    assert final["ch01-p002"] == "Opraveno."
    assert final["ch01-p001"] == "CZ ch01-p001"


def test_invented_patch_ids_are_dropped(sample_book):
    draft = make_draft(sample_book)
    final = merge_book(sample_book, draft, {"ch99-p999": "Vymyšlené."})
    assert "ch99-p999" not in final
    assert set(final) == set(ids(sample_book))


def test_empty_patch_is_ignored(sample_book):
    draft = make_draft(sample_book)
    final = merge_book(sample_book, draft, {"ch01-p001": "   "})
    assert final["ch01-p001"] == "CZ ch01-p001"


def test_missing_paragraph_raises(sample_book):
    draft = make_draft(sample_book)
    del draft["ch02-p001"]
    with pytest.raises(BookTrError):
        merge_book(sample_book, draft, {})


def test_output_preserves_book_order(sample_book):
    draft = make_draft(sample_book)
    final = merge_book(sample_book, draft, {})
    assert list(final) == ids(sample_book)
