"""Real PDF fixtures exercise extraction and note placement after reflow."""
from __future__ import annotations

import json
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from booktr.engine import ingest
from booktr.engine.document import footnote_id
from booktr.engine.merge import merge_book
from booktr.engine.segment import all_ids, build_book
from booktr.engine.typeset import compile_typ, render_typ
from booktr.errors import BookTrError


def _page_texts(pdf_path: Path) -> list[str]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    texts = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            text = page.get_textpage()
            try:
                texts.append(text.get_text_bounded())
            finally:
                text.close()
                page.close()
    finally:
        pdf.close()
    return texts


def test_digital_pdf_preserves_cover_illustration_and_footnote(fake_engine):
    directory = fake_engine.job.dir
    Image.new("RGB", (600, 800), "#233d57").save(directory / "front.png")
    Image.new("RGB", (240, 140), "#eabb57").save(directory / "figure.png")
    source = directory / "original.typ"
    source.write_text('''#set page(paper: "a5", margin: 18mm)
#set text(size: 12pt)
#page(margin: 0pt)[#image("front.png", width: 100%, height: 100%)]
= Chapter one
This is the original prose and has an important reference#footnote[The complete original note is faithfully retained, including 1842 and punctuation.]. It continues with several ordinary words.

#image("figure.png", width: 55%)

Another paragraph follows the original illustration and should remain in reading order.
''', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    pages = ingest.run(fake_engine)
    book = build_book(pages, "Sample")
    assert book["cover"]["path"] == "assets/cover.png"
    assert (directory / book["cover"]["path"]).is_file()
    notes = book["footnotes"]
    assert len(notes) == 1
    assert notes[0]["text"] == "The complete original note is faithfully retained, including 1842 and punctuation."
    body = "\n".join(p["text"] for ch in book["chapters"] for p in ch["paragraphs"])
    assert f"reference[[FN:{notes[0]['id']}]]" in body
    assert notes[0]["text"] not in body
    figure = next(img for img in book["images"] if img["source_page"] == 2)
    assert figure["role"] == "illustration"
    assert figure.get("after_paragraph")
    assert (directory / figure["path"]).is_file()
    assert notes[0]["id"] in all_ids(book)


def test_structured_scan_crops_art_and_keeps_note_anchors(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "ocr-pages").mkdir()
    image = Image.new("RGB", (1000, 1500), "white")
    # The actual figure occupies this region; crop output must match it.
    image.paste("#da4428", (100, 600, 700, 1050))
    image.save(tmp_path / "ocr-pages/p0002.png")
    metadata = {"index": 3, "width": 400, "height": 600, "images": [],
                "ocr_image": "ocr-pages/p0002.png"}
    result = ingest._normalize_ocr(json.dumps({
        "blocks": [{"type": "paragraph", "text": "Exact text[[FN:1]] here."}],
        "footnotes": [{"label": "1", "text": "The exact original note."}],
        "figures": [{"bbox": [100, 400, 700, 700]}],
    }), metadata, tmp_path)
    assert result["text"] == f"Exact text[[FN:{footnote_id(3, 1)}]] here."
    assert result["footnotes"][0]["text"] == "The exact original note."
    with Image.open(tmp_path / result["images"][0]["path"]) as crop:
        assert crop.size == (600, 450)
        assert crop.getpixel((50, 50)) == (218, 68, 40)
    with pytest.raises(BookTrError, match="figure coordinates"):
        ingest._normalize_ocr('{"blocks": [], "figures": [{"bbox": [-1,0,3,4]}]}', metadata, tmp_path)


def test_missing_or_duplicate_footnote_anchors_cannot_publish():
    ident = footnote_id(1, 1)
    book = build_book([{"index": 1, "blocks": [{"type": "paragraph", "text": f"Source[[FN:{ident}]]."}],
                        "footnotes": [{"id": ident, "text": "Note", "label": "1", "source_page": 1}]}], "T")
    draft = {"book-title": "T", "ch01-p001": f"Překlad[[FN:{ident}]].", ident: "Poznámka"}
    assert merge_book(book, draft, {})[ident] == "Poznámka"
    for bad in ["Překlad.", f"Překlad[[FN:{ident}]][[FN:{ident}]]."]:
        with pytest.raises(BookTrError, match="Footnote reference mismatch"):
            merge_book(book, {**draft, "ch01-p001": bad}, {})


def test_native_footnote_moves_with_its_anchor_after_font_size_change(tmp_path):
    ident = footnote_id(1, 1)
    book = build_book([{"index": 1, "blocks": [
        {"type": "paragraph", "text": "ordinary words " * 600},
        {"type": "paragraph", "text": f"UniqueAnchor[[FN:{ident}]] ends here."},
        {"type": "paragraph", "text": "later words " * 100},
    ], "footnotes": [{"id": ident, "text": "UniqueNoteBody contains the exact original note.", "label": "1", "source_page": 1}]}], "Book")
    final = {"book-title": "Book", ident: book["footnotes"][0]["text"]}
    final.update({p["id"]: p["text"] for ch in book["chapters"] for p in ch["paragraphs"]})
    anchor_pages = []
    for size in [12, 24]:
        source = tmp_path / f"book-{size}.typ"
        preamble = f'#set page(paper: "a5", margin: 18mm)\n#set text(size: {size}pt)'
        source.write_text(render_typ(book, final, preamble), encoding="utf-8")
        output = tmp_path / f"book-{size}.pdf"
        compile_typ(source, output)
        texts = _page_texts(output)
        anchor_page = next(n for n, text in enumerate(texts) if "UniqueAnchor" in text)
        note_page = next(n for n, text in enumerate(texts) if "UniqueNoteBody" in text)
        assert note_page == anchor_page
        assert sum("UniqueNoteBody" in text for text in texts) == 1
        anchor_pages.append(anchor_page)
    assert anchor_pages[1] > anchor_pages[0]


def test_visual_anchor_repair_keeps_original_prose_exact():
    ident = footnote_id(4, 1)
    source = {
        "index": 4, "blocks": [{"type": "paragraph", "text": "The printed reference1. Continues unchanged."}],
        "footnotes": [{"id": ident, "label": "1", "text": "Original note"}],
    }
    valid = json.dumps({"blocks": {"0": f"The printed reference[[FN:{ident}]]. Continues unchanged."}})
    result = ingest._repair_anchors(valid, source)
    assert result["warnings"] == []
    assert result["blocks"][0]["text"] == f"The printed reference[[FN:{ident}]]. Continues unchanged."
    for bad in [valid.replace("unchanged", "rewritten"), json.dumps({"blocks": {"0": source["blocks"][0]["text"]}})]:
        with pytest.raises(BookTrError, match="Cannot confidently locate"):
            ingest._repair_anchors(bad, source)


@pytest.mark.parametrize("coverage", [100, 65])
def test_hybrid_scan_with_text_layer_preserves_inset_illustration(fake_engine, coverage):
    directory = fake_engine.job.dir
    scan = Image.new("RGB", (1000, 1500), "white")
    scan.paste("#379a72", (150, 500, 800, 1000))
    scan.save(directory / "scan.png")
    source = directory / "hybrid.typ"
    source.write_text(f'''#set page(paper: "a5", margin: 0pt)
#page(margin: 18mm)[Digital title and copyright information with enough original text to use the real digital extraction branch.]
#page[#place(top + left, image("scan.png", width: 100%, height: {coverage}%))
#text(fill: rgb("#00000000"))[This is a searchable OCR text layer that covers the scan and contains many words but cannot identify the inset illustration correctly.]]
''', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)

    class StructuredScanApi:
        calls = 0

        def complete(self, model, system, user, images=None, json_mode=False):
            assert system.startswith("[OCR]")
            assert "searchable OCR text layer" in user
            self.calls += 1
            return json.dumps({"blocks": [{"type": "paragraph", "text": "Verbatim scanned prose.", "bbox": [100, 100, 900, 200]}],
                               "footnotes": [], "figures": [{"bbox": [150, 333, 800, 667]}]})

    fake_engine.api = StructuredScanApi()
    pages = ingest.run(fake_engine)
    assert fake_engine.api.calls == 1
    book = build_book(pages, "Hybrid")
    assert any(i["source_page"] == 2 and i["id"].startswith("figure-") and i["role"] == "illustration"
               for i in book["images"])
    assert ingest.artifacts_valid(fake_engine.job)
    asset = next(i for i in book["images"] if i["id"].startswith("figure-"))
    (directory / asset["path"]).write_bytes(b"broken image")
    assert not ingest.artifacts_valid(fake_engine.job)


def test_each_original_image_occurrence_survives_rendering(fake_engine):
    directory = fake_engine.job.dir
    Image.new("RGB", (280, 160), "#836cd4").save(directory / "repeat.png")
    source = directory / "occurrences.typ"
    source.write_text('''#set page(paper: "a5", margin: 18mm)
#page[Original title information and a short introduction with enough digital prose to avoid scanned transcription.]
First prose paragraph introduces an illustration that occurs several times in the original book.

#image("repeat.png", width: 50%)

The prose between illustrations should appear between them in the translated book as well.

#image("repeat.png", width: 50%)

Final prose after the second occurrence is retained in reading order.
''', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    book = build_book(ingest.run(fake_engine), "Repeated images")
    figures = [i for i in book["images"] if i["source_page"] == 2]
    assert len(figures) == 2 and len({i["id"] for i in figures}) == 2
    assert figures[0]["after_paragraph"] != figures[1]["after_paragraph"]
    final = {"book-title": "Repeated images"}
    final.update({p["id"]: p["text"] for ch in book["chapters"] for p in ch["paragraphs"]})
    translated = directory / "translated.typ"
    translated.write_text(render_typ(book, final, '#set page(paper: "a5", margin: 18mm)'), encoding="utf-8")
    output = directory / "translated.pdf"
    compile_typ(translated, output)
    pdf = pdfium.PdfDocument(str(output))
    count = 0
    try:
        import pypdfium2.raw as raw
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                count += len(list(page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=15)))
            finally:
                page.close()
    finally:
        pdf.close()
    assert count == 3  # exact original cover + both repeated figure occurrences


def test_untrusted_typography_cannot_hide_source_prose(tmp_path):
    literal = 'Literal #hide[content] /* keep */ // retained. URL https://example.test/a and = equals.'
    book = build_book([literal], "Literal")
    source = tmp_path / "literal.typ"
    source.write_text(render_typ(book, {"book-title": "Literal", "ch01-p001": literal},
                                  '#set page(paper: "a4")'), encoding="utf-8")
    compile_typ(source, tmp_path / "literal.pdf")
    texts = " ".join(_page_texts(tmp_path / "literal.pdf"))
    for preserved in ["#hide[content]", "/* keep */", "// retained.", "https://example.test/a", "= equals."]:
        assert preserved in texts


def test_long_book_repeated_margin_headers_and_reset_notes(fake_engine):
    directory = fake_engine.job.dir
    source = directory / "long.typ"
    parts = ['#set page(paper: "a5", margin: 18mm, header: align(center, text(size: 9pt)[RUNNING BOOK TITLE]))',
             '#set text(size: 12pt)']
    for n in range(1, 13):
        parts.append(f'''#counter(footnote).update(0)
#page[= Chapter {n}
Source section {n} begins with original prose and a footnote#footnote[Original note for section {n}, with the exact date 1842 and full punctuation.]. The same note is referenced again#footnote(<original-{n}>).

{"This is a complete sentence about the journey and its people. " * 14}
]'''.replace(f'full punctuation.].', f'full punctuation.] <original-{n}>.'))
    source.write_text("\n".join(parts), encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    pages = ingest.run(fake_engine)
    book = build_book(pages, "Long")
    assert len(pages) >= 12
    assert "RUNNING BOOK TITLE" not in "\n".join(p["text"] for p in pages)
    assert len(book["footnotes"]) == 12
    assert len({n["id"] for n in book["footnotes"]}) == 12
    assert {note["label"] for note in book["footnotes"]} == {"1"}
    for note in book["footnotes"]:
        assert sum(p["text"].count(f"[[FN:{note['id']}]]") for ch in book["chapters"] for p in ch["paragraphs"]) == 2
    assert ingest.artifacts_valid(fake_engine.job)


def test_long_original_footnote_continuation_stays_one_note(fake_engine):
    directory = fake_engine.job.dir
    note = "Detailed original citation and historical context. " * 180
    source = directory / "continued.typ"
    source.write_text('#set page(paper: "a5", margin: 18mm)\n#set text(size: 12pt)\n'
                      'A substantial source paragraph introduces a very long original footnote and retains its exact reference'
                      f'#footnote[{note}]. The original body continues normally after its reference.', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    pages = ingest.run(fake_engine)
    book = build_book(pages, "Continuation")
    assert len(pages) > 1
    assert len(book["footnotes"]) == 1
    assert book["footnotes"][0]["text"] == note.strip()
    assert note.split()[0] not in " ".join(p["text"] for ch in book["chapters"] for p in ch["paragraphs"])


@pytest.mark.parametrize("words", [30, 80, 180, 260, 700])
def test_note_region_survives_when_note_font_dominates_page(fake_engine, words):
    directory = fake_engine.job.dir
    note = " ".join(f"context{n}" for n in range(words)) + "."
    source = directory / "dominant-note.typ"
    source.write_text('#set page(paper: "a5", margin: 18mm)\n#set text(size: 12pt)\n'
                      'Short original body has a precise reference'
                      f'#footnote[{note}]. Another original sentence completes this paragraph.', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    book = build_book(ingest.run(fake_engine), "Dominant note")
    assert len(book["footnotes"]) == 1
    assert book["footnotes"][0]["text"] == note
    assert "context0" not in " ".join(p["text"] for ch in book["chapters"] for p in ch["paragraphs"])


def test_repeated_printed_note_label_same_page_uses_internal_destinations(fake_engine):
    directory = fake_engine.job.dir
    source = directory / "restarted.typ"
    source.write_text('''#set page(paper: "a4", margin: 18mm)
#set text(size: 12pt)
The first source section contains its own reference#footnote[First original note body, date 1842.] and additional prose.

#counter(footnote).update(0)
The second source section restarts note numbering with a separate reference#footnote[Second original note body, date 1901.] and additional prose.
''', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    book = build_book(ingest.run(fake_engine), "Restarted")
    assert len(book["footnotes"]) == 2
    assert [n["label"] for n in book["footnotes"]] == ["1", "1"]
    paragraphs = [p["text"] for ch in book["chapters"] for p in ch["paragraphs"]]
    assert f"[[FN:{book['footnotes'][0]['id']}]]" in next(p for p in paragraphs if "first source" in p)
    assert f"[[FN:{book['footnotes'][1]['id']}]]" in next(p for p in paragraphs if "second source" in p)


def test_scanned_repeated_labels_have_distinct_keys(tmp_path):
    metadata = {"index": 5, "width": 400, "height": 600, "images": []}
    response = json.dumps({
        "blocks": [{"type": "paragraph", "text": "First[[FN:note-a]]."},
                   {"type": "paragraph", "text": "Second[[FN:note-b]]."}],
        "footnotes": [{"key": "note-a", "label": "1", "text": "First note."},
                      {"key": "note-b", "label": "1", "text": "Second note."}],
    })
    page = ingest._normalize_ocr(response, metadata, tmp_path)
    assert page["blocks"][0]["text"] == f"First[[FN:{footnote_id(5, 1)}]]."
    assert page["blocks"][1]["text"] == f"Second[[FN:{footnote_id(5, 2)}]]."


def test_narrow_original_word_spaces_survive_digital_extraction(fake_engine):
    directory = fake_engine.job.dir
    original = "The first source sentence ends here. Another sentence preserves narrow spaces and every original word correctly."
    source = directory / "narrow-spaces.typ"
    source.write_text('#set page(paper: "a5", margin: 18mm)\n#set text(size: 12pt, spacing: 60%)\n' + original,
                      encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    book = build_book(ingest.run(fake_engine), "Narrow spaces")
    assert " ".join(p["text"] for ch in book["chapters"] for p in ch["paragraphs"]) == original


@pytest.mark.parametrize("numbering", ["a", "i", "I"])
def test_linked_alphabetic_and_roman_notes_keep_distinct_original_bodies(fake_engine, numbering):
    source = fake_engine.job.dir / "letter-notes.typ"
    source.write_text('#set page(paper: "a4", margin: 20mm)\n#set text(size: 12pt)\n'
                      f'#set footnote(numbering: "{numbering}")\n'
                      'The first original paragraph attaches a precise reference'
                      '#footnote[FirstLetterNoteBody retains the complete citation.]. '
                      'Enough ordinary original words establish the body font and exact reading order.\n\n'
                      'A second original paragraph has another precise reference'
                      '#footnote[SecondLetterNoteBody retains another complete citation.]. '
                      'Further original body words remain unchanged after this reference.', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    book = build_book(ingest.run(fake_engine), "Letter notes")
    assert len(book["footnotes"]) == 2
    assert [note["text"] for note in book["footnotes"]] == [
        "FirstLetterNoteBody retains the complete citation.",
        "SecondLetterNoteBody retains another complete citation.",
    ]
    paragraphs = [p["text"] for ch in book["chapters"] for p in ch["paragraphs"]]
    assert f"[[FN:{book['footnotes'][0]['id']}]]" in paragraphs[0]
    assert f"[[FN:{book['footnotes'][1]['id']}]]" in paragraphs[1]
    assert "LetterNoteBody" not in " ".join(paragraphs)


def test_unlinked_letter_note_requires_raised_marker_in_body_and_note(fake_engine):
    source = fake_engine.job.dir / "raised-letter-note.typ"
    source.write_text('''#set page(paper: "a4", margin: 20mm)
#set text(size: 12pt)
#set super(typographic: false)
This original paragraph has a precise raised reference#super[a]. Ordinary words establish the regular body font and the intended paragraph order.
#place(bottom + left, block[
  #line(length: 30%, stroke: 0.5pt)
  #text(size: 10pt)[#super[a] RaisedLetterNoteBody contains the original citation.]
])
''', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    book = build_book(ingest.run(fake_engine), "Raised note")
    assert len(book["footnotes"]) == 1
    assert book["footnotes"][0]["text"] == "RaisedLetterNoteBody contains the original citation."
    assert f"[[FN:{book['footnotes'][0]['id']}]]" in book["chapters"][0]["paragraphs"][0]["text"]


def test_ordinary_bottom_prose_is_not_an_alphabetic_note(fake_engine):
    source = fake_engine.job.dir / "ordinary-letter-prose.typ"
    source.write_text('''#set page(paper: "a4", margin: 20mm)
#set text(size: 12pt)
#set super(typographic: false)
This original paragraph uses a raised mathematical variable#super[a]. Ordinary words establish the regular body font and the intended paragraph order.
#place(bottom + left, block[
  #line(length: 30%, stroke: 0.5pt)
  #text(size: 10pt)[a little ordinary footer sentence retains its complete original meaning.]
])
''', encoding="utf-8")
    compile_typ(source, fake_engine.job.source_pdf)
    book = build_book(ingest.run(fake_engine), "Ordinary prose")
    assert book["footnotes"] == []
    body = " ".join(p["text"] for ch in book["chapters"] for p in ch["paragraphs"])
    assert "a little ordinary footer sentence retains its complete original meaning." in body
