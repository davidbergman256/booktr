"""Narrating an existing book extracts source words without translation calls."""
from __future__ import annotations

import json

import pytest
from PIL import Image

from booktr.engine import Engine, ingest
from booktr.engine.segment import build_book
from booktr.engine.typeset import compile_typ
from booktr.errors import BookTrError


class TranscriptionOnlyApi:
    """Only a real scanned body page earns an external transcription request."""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def complete(self, model, system, user, **kwargs):
        self.calls.append((system, user))
        assert system == ingest.OCR_SYSTEM, "Narration must never translate or detect language"
        page = next((number for number in self.responses
                     if f"source page {number} " in user), None)
        assert page is not None, "A native or decorative page must not call OpenAI"
        response = self.responses[page]
        if isinstance(response, Exception):
            raise response
        return json.dumps(response, ensure_ascii=False)


def _engine(job, config, api=None, *, narration_only=True):
    context = Engine(job, api or TranscriptionOnlyApi(), config)
    context.narration_only = narration_only
    return context


def _compile(job, source):
    path = job.dir / "narration-source.typ"
    path.write_text(source, encoding="utf-8")
    compile_typ(path, job.source_pdf)


def test_native_narration_keeps_chapters_notes_and_short_pages_without_openai(job, config):
    Image.new("RGB", (500, 700), "#234b36").save(job.dir / "cover.png")
    Image.new("RGB", (500, 700), "#dcc39e").save(job.dir / "illustration.png")
    _compile(job, '''#set page(paper: "a5", margin: 18mm)
#set text(size: 12pt)
#page(margin: 0pt)[#image("cover.png", width: 100%, height: 100%)]
= První kapitola
Toto je přesné znění již přeložené knihy s původní poznámkou#footnote[Úplné české znění poznámky včetně roku 1842.]. Další slova zůstávají ve stejném pořadí.

#image("illustration.png", width: 90%, height: 190pt)

Krátký odstavec pod obrázkem.
#pagebreak()
Konec.
''')
    context = _engine(job, config)
    pages = ingest.run(context)
    book = build_book(pages, "Hotová kniha")
    assert context.api.calls == []
    assert pages[0]["blocks"] == []
    assert book["chapters"][0]["heading"] == "První kapitola"
    body = "\n".join(p["text"] for ch in book["chapters"] for p in ch["paragraphs"])
    assert "původní poznámkou[[FN:fn-p0002-001]]." in body
    assert "Krátký odstavec pod obrázkem." in body
    assert "Konec." in body
    assert [note["text"] for note in book["footnotes"]] == [
        "Úplné české znění poznámky včetně roku 1842."
    ]


def test_native_single_short_page_is_spoken_without_openai(job, config):
    _compile(job, '#set page(paper: "a5", margin: 18mm)\nKonec.')
    context = _engine(job, config)
    pages = ingest.run(context)
    assert context.api.calls == []
    assert pages[0]["text"] == "Konec."


def test_native_body_over_full_page_background_never_needs_transcription(job, config):
    Image.new("RGB", (500, 700), "#f4eedf").save(job.dir / "background.png")
    _compile(job, '''#set page(paper: "a5", margin: 18mm)
#place(top + left, dx: -18mm, dy: -18mm, image("background.png", width: 148mm, height: 210mm))
Přesný český odstavec je uložen v textové vrstvě dokumentu, přestože původní stránka používá obrázek jako celostránkové pozadí.
''')
    context = _engine(job, config)
    pages = ingest.run(context)
    assert context.api.calls == []
    assert pages[0]["text"] == (
        "Přesný český odstavec je uložen v textové vrstvě dokumentu, přestože původní stránka "
        "používá obrázek jako celostránkové pozadí."
    )


def test_scanned_book_transcribes_front_page_before_omitting_empty_cover_text(job, config):
    Image.new("RGB", (500, 700), "#234b36").save(job.dir / "cover.png")
    Image.new("RGB", (500, 700), "white").save(job.dir / "scan.png")
    _compile(job, '''#set page(paper: "a5", margin: 0pt)
#page[#image("cover.png", width: 100%, height: 100%)]
#page[#image("scan.png", width: 100%, height: 100%)]
''')
    api = TranscriptionOnlyApi({1: {"blocks": [], "footnotes": [], "figures": []}, 2: {
        "blocks": [{"type": "heading", "text": "Druhá kapitola"},
                   {"type": "paragraph", "text": "Český text naskenované knihy."}],
        "footnotes": [], "figures": [],
    }})
    pages = ingest.run(_engine(job, config, api))
    assert len(api.calls) == 2
    assert pages[0]["text"] == ""
    assert pages[1]["text"] == "## Druhá kapitola\n\nČeský text naskenované knihy."


def test_scanned_first_chapter_is_not_mistaken_for_a_cover(job, config):
    Image.new("RGB", (500, 700), "white").save(job.dir / "scan.png")
    _compile(job, '''#set page(paper: "a5", margin: 0pt)
#page[#image("scan.png", width: 100%, height: 100%)]
#page[#image("scan.png", width: 100%, height: 100%)]
''')
    api = TranscriptionOnlyApi({
        1: {"blocks": [{"type": "paragraph", "text": "První skutečný odstavec knihy."}],
            "footnotes": [], "figures": []},
        2: {"blocks": [{"type": "paragraph", "text": "Druhý skutečný odstavec knihy."}],
            "footnotes": [], "figures": []},
    })
    pages = ingest.run(_engine(job, config, api))
    assert len(api.calls) == 2
    assert [page["text"] for page in pages] == [
        "První skutečný odstavec knihy.", "Druhý skutečný odstavec knihy."
    ]


def test_single_scanned_page_is_body_and_requires_transcription(job, config):
    Image.new("RGB", (500, 700), "white").save(job.dir / "scan.png")
    _compile(job, '''#set page(paper: "a5", margin: 0pt)
#image("scan.png", width: 100%, height: 100%)
''')
    api = TranscriptionOnlyApi({1: {
        "blocks": [{"text": "Celé znění jednostránkového českého dokumentu."}],
        "footnotes": [], "figures": [],
    }})
    pages = ingest.run(_engine(job, config, api))
    assert len(api.calls) == 1
    assert pages[0]["text"] == "Celé znění jednostránkového českého dokumentu."


def test_resumed_body_scan_checkpoint_still_requires_first_page_transcription(job, config):
    Image.new("RGB", (500, 700), "white").save(job.dir / "scan.png")
    _compile(job, '''#set page(paper: "a5", margin: 0pt)
#page[#image("scan.png", width: 100%, height: 100%)]
#page[#image("scan.png", width: 100%, height: 100%)]
''')
    body = {"blocks": [{"text": "Druhý skutečný odstavec knihy."}], "footnotes": [], "figures": []}
    interrupted = TranscriptionOnlyApi({1: BookTrError("bad_pdf", "Interrupted first-page OCR"), 2: body})
    with pytest.raises(BookTrError, match="Interrupted first-page OCR"):
        ingest.run(_engine(job, config, interrupted))
    assert job.has_chunk("ingest", "p0001")
    resumed = TranscriptionOnlyApi({1: {
        "blocks": [{"text": "První skutečný odstavec knihy."}], "footnotes": [], "figures": [],
    }})
    pages = ingest.run(_engine(job, config, resumed))
    assert len(resumed.calls) == 1
    assert [page["text"] for page in pages] == [
        "První skutečný odstavec knihy.", "Druhý skutečný odstavec knihy."
    ]


def test_scan_with_only_native_margin_header_still_transcribes_body(job, config):
    Image.new("RGB", (500, 700), "white").save(job.dir / "scan.png")
    _compile(job, '''#set page(paper: "a5", margin: 18mm)
Úvodní nativní stránka je dostatečně dlouhá a uchovává všechna původní česká slova.
#pagebreak()
#place(top + left, image("scan.png", width: 100%, height: 100%))
#place(top + left, dy: -45pt, text(size: 9pt)[Název knihy])
''')
    api = TranscriptionOnlyApi({2: {
        "blocks": [{"type": "paragraph", "text": "Skutečný text těla naskenované stránky."}],
        "footnotes": [], "figures": [],
    }})
    pages = ingest.run(_engine(job, config, api))
    assert len(api.calls) == 1
    assert pages[1]["text"] == "Skutečný text těla naskenované stránky."


def test_scan_with_native_chapter_title_still_transcribes_missing_body(job, config):
    Image.new("RGB", (500, 700), "white").save(job.dir / "scan.png")
    _compile(job, '''#set page(paper: "a5", margin: 18mm)
Úvodní nativní stránka je dostatečně dlouhá a uchovává všechna původní česká slova.
#pagebreak()
#place(top + left, image("scan.png", width: 100%, height: 100%))
#v(40pt)
#text(size: 20pt)[Druhá kapitola]
''')
    api = TranscriptionOnlyApi({2: {
        "blocks": [{"type": "heading", "text": "Druhá kapitola"},
                   {"type": "paragraph", "text": "Naskenované tělo knihy musí být rovněž zachováno."}],
        "footnotes": [], "figures": [],
    }})
    pages = ingest.run(_engine(job, config, api))
    assert len(api.calls) == 1
    assert pages[1]["text"] == "## Druhá kapitola\n\nNaskenované tělo knihy musí být rovněž zachováno."


def test_blank_body_page_never_requests_transcription(job, config):
    _compile(job, '''#set page(paper: "a5", margin: 18mm)
Krátký začátek.
#pagebreak()
#box(width: 0pt, height: 0pt)
#pagebreak()
Krátký konec.
''')
    context = _engine(job, config)
    pages = ingest.run(context)
    assert context.api.calls == []
    assert [page["text"] for page in pages] == ["Krátký začátek.", "", "Krátký konec."]


def test_translation_mode_keeps_existing_short_page_transcription_policy(job, config):
    _compile(job, '#set page(paper: "a5", margin: 18mm)\nKonec.')
    api = TranscriptionOnlyApi({1: {"blocks": [{"text": "Konec."}], "footnotes": [], "figures": []}})
    pages = ingest.run(_engine(job, config, api, narration_only=False))
    assert len(api.calls) == 1
    assert pages[0]["text"] == "Konec."


def test_ambiguous_native_note_fails_without_paid_anchor_repair(job, config):
    _compile(job, '''#set page(paper: "a5", margin: 18mm)
#set text(size: 12pt)
Původní český odstavec nemá žádný odkaz na poznámku, ale jeho znění musí zůstat beze změn.
#place(bottom + left, block[
  #line(length: 30%, stroke: 0.5pt)
  #text(size: 10pt)[1 Tuto poznámku nelze spolehlivě přiřadit k odstavci.]
])
''')
    context = _engine(job, config)
    with pytest.raises(BookTrError, match="native footnote"):
        ingest.run(context)
    assert context.api.calls == []
