"""Fáze 0 — načtení PDF: textová vrstva, a kde chybí, OCR přes gpt5.4-mini (vision)."""
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
- If a line is a chapter heading, prefix it with '## '.
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

    pages: list[str] = []
    for i in range(total):
        engine.checkpoint_wait()
        key = f"p{i:04d}"
        if job.has_chunk("ingest", key):
            pages.append(job.load_chunk("ingest", key))
            engine.report("ingest", i + 1, total)
            continue
        page = pdf[i]
        text = page.get_textpage().get_text_bounded() or ""
        if len(text.strip()) < MIN_TEXT_CHARS:
            text = _ocr_page(api, cfg, page)
        job.save_chunk("ingest", key, text)
        pages.append(text)
        engine.report("ingest", i + 1, total)

    job.write_json("pages.json", pages)
    return pages


def _ocr_page(api, cfg, page) -> str:
    pil = page.render(scale=RENDER_SCALE).to_pil()
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    text = api.complete(cfg.model_draft, OCR_SYSTEM, "Transcribe this page.", images=[buf.getvalue()])
    return "" if text.strip() == "[EMPTY]" else text
