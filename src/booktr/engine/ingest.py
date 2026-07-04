"""Fáze 0 — načtení PDF: textová vrstva, a kde chybí, OCR přes gpt-5.4-mini (vision)."""
from __future__ import annotations

import io
import logging

from ..errors import BookTrError

log = logging.getLogger("booktr.ingest")

MIN_TEXT_CHARS = 50  # méně znaků na stránce = skenovaná stránka bez textové vrstvy
RENDER_SCALE = 200 / 72  # ~200 DPI

OCR_SYSTEM = """[OCR] You transcribe one scanned book page verbatim.
Rules:
- Reproduce the text exactly in its original language. Do not translate.
- Join words hyphenated across line breaks.
- Drop running headers, footers and bare page numbers.
- Separate paragraphs with one blank line.
- Prefix a line with '## ' ONLY if it is clearly a chapter title (like 'CHAPTER V.'
  or a standalone centered title). Dialogue, shouted ALL-CAPS sentences and
  continuations from the previous page are NOT headings — when unsure, do not mark.
- If the page is blank or contains only an image, return exactly: [EMPTY]
Return only the transcription, no commentary."""


def run(engine):
    job, api, cfg = engine.job, engine.api, engine.config
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(job.source_pdf))
    except Exception as exc:  # noqa: BLE001
        raise BookTrError("bad_pdf", str(exc)) from exc

    total = len(pdf)
    if total == 0:
        raise BookTrError("bad_pdf", "PDF has no pages")

    # 1) sekvenčně: textová vrstva + render stránek pro OCR (pypdfium2 není thread-safe)
    items: list[tuple[str, object]] = []
    for i in range(total):
        key = f"p{i:04d}"
        if job.has_chunk("ingest", key):
            items.append((key, None))
            continue
        page = pdf[i]
        text = page.get_textpage().get_text_bounded() or ""
        if len(text.strip()) >= MIN_TEXT_CHARS:
            job.save_chunk("ingest", key, text)
            items.append((key, None))
        else:
            items.append((key, _render_png(page)))

    # 2) souběžně: OCR volání (jen API, žádné pdfium)
    def ocr(png: bytes) -> str:
        text = api.complete(cfg.model_draft, OCR_SYSTEM, "Transcribe this page.", images=[png])
        return "" if text.strip() == "[EMPTY]" else text

    results = engine.run_chunks("ingest", items, ocr)
    pages = [str(results[f"p{i:04d}"]) for i in range(total)]
    job.write_json("pages.json", pages)
    return pages


def _render_png(page) -> bytes:
    pil = page.render(scale=RENDER_SCALE).to_pil()
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()
