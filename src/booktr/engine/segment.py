"""Fáze 0b — segmentace: stránky → odstavce se stabilními ID, detekce kapitol.

ID odstavců (chNN-pMMM) jsou páteří celé pipeline — po této fázi se už nikdy nemění.
"""
from __future__ import annotations

import logging
import re

from ..errors import BookTrError
from .document import SCHEMA_VERSION

log = logging.getLogger("booktr.segment")

LANG_SYSTEM = "[LANG] Identify the language of the text. Reply with a two-letter ISO 639-1 code only."

_CHAPTER_WORDS = (
    "chapter|kapitola|kapitel|chapitre|capitolo|cap[ií]tulo|глава|rozdzia[łl]|fejezet|hoofdstuk"
)
_CHAPTER_RE = re.compile(
    rf"^(?:##\s+.+|(?:{_CHAPTER_WORDS})\b[\s\.:]*\w*.{{0,60}}|[IVXLC]+\.?|\d{{1,3}}\.?)$",
    re.IGNORECASE,
)


def split_paragraphs(page_text: str) -> list[str]:
    """Rozdělí text stránky na odstavce; spojí řádky zalomené uvnitř odstavce."""
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", page_text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        joined = ""
        for ln in lines:
            if joined.endswith("-") and not joined.endswith(" -"):
                joined = joined[:-1] + ln  # slovo rozdělené spojovníkem
            elif joined:
                joined += " " + ln
            else:
                joined = ln
        paragraphs.append(joined)
    return paragraphs


_QUOTES = "„“”\"'‘’«»"


def looks_like_heading(paragraph: str) -> bool:
    p = paragraph.strip()
    if len(p) > 80:
        return False
    if p.startswith("## "):
        # OCR občas označí křičenou větu jako nadpis; věta pokračující za ! nebo ? nadpis není
        return not re.search(r"[!?]['’”\"]?\s+\S", p[3:])
    if p[:1] in _QUOTES:
        return False  # citovaná řeč — i křičená VELKÝMI PÍSMENY není nadpis
    if _CHAPTER_RE.match(p):
        return True
    # krátký řádek celý velkými písmeny (bez interpunkce na konci, ani před uvozovkou)
    core = p.strip(_QUOTES + " ")
    letters = [c for c in p if c.isalpha()]
    if 2 <= len(letters) and len(p) <= 40 and all(c.isupper() for c in letters) and not core.endswith((".", ",", "!", "?", ":", ";")):
        return True
    return False


def build_book(pages: list[str | dict], title: str, source_lang: str = "unknown") -> dict:
    """Postaví book.json: kapitoly a odstavce se stabilními ID."""
    chapters: list[dict] = []
    current: dict | None = None
    images: list[dict] = []
    footnotes: list[dict] = []
    notes_by_id: dict[str, dict] = {}
    cover = None
    warnings: list[str] = []

    def new_chapter(heading: str):
        nonlocal current
        current = {"id": f"ch{len(chapters) + 1:02d}", "heading": heading.lstrip("# ").strip(), "paragraphs": []}
        chapters.append(current)

    for page_index, page in enumerate(pages, start=1):
        if isinstance(page, dict):
            page_no = page.get("index", page_index)
            for original_note in page.get("footnotes", []):
                note = dict(original_note)
                if note["id"] in notes_by_id:
                    raise BookTrError("bad_pdf", f"Duplicate original footnote ID: {note['id']}")
                notes_by_id[note["id"]] = note
                footnotes.append(note)
            for continuation in page.get("footnote_continuations", []):
                note = notes_by_id.get(continuation["id"])
                if note is None:
                    raise BookTrError("bad_pdf", f"Missing original footnote for continuation: {continuation['id']}")
                previous = note["text"].rstrip()
                continued = continuation["text"].strip()
                if previous.endswith("-") and continued[:1].islower():
                    note["text"] = previous[:-1] + continued
                else:
                    note["text"] = (previous + " " + continued).strip()
                source_pages = note.setdefault("source_pages", [note.get("source_page", page_no)])
                continuation_page = continuation.get("source_page", page_no)
                if continuation_page not in source_pages:
                    source_pages.append(continuation_page)
            images.extend(dict(image) for image in page.get("images", []))
            cover = cover or page.get("cover")
            warnings.extend(page.get("warnings", []))
            blocks = page.get("blocks") or [
                {"type": "paragraph", "text": p} for p in split_paragraphs(page.get("text", ""))
            ]
        else:
            page_no = page_index
            blocks = [{"type": "paragraph", "text": p} for p in split_paragraphs(page)]
        first_in_page = True
        for block in blocks:
            if block.get("type") not in ("paragraph", "heading"):
                continue
            para = block.get("text", "").strip()
            if not para:
                continue
            if block.get("type") == "heading" or looks_like_heading(para):
                new_chapter(para)
                current["source_page"] = page_no
                current["bbox"] = block.get("bbox")
                first_in_page = False
                continue
            if current is None:
                new_chapter("")  # text před první kapitolou
                current["heading"] = ""
            para = para.lstrip("# ").strip()  # zamítnutý ## nadpis je běžný odstavec
            if (first_in_page and current["paragraphs"] and para[:1].islower()
                    and not re.search(r'[.!?:;][”"\u2019]?$', current["paragraphs"][-1]["text"])):
                # odstavec rozříznutý koncem stránky — pokračování malým písmenem
                current["paragraphs"][-1]["text"] += " " + para
                current["paragraphs"][-1]["source_pages"].append(page_no)
                if block.get("bbox"):
                    current["paragraphs"][-1]["source_bboxes"][str(page_no)] = block["bbox"]
            else:
                current["paragraphs"].append({"id": "", "text": para,
                    "source_page": page_no, "source_pages": [page_no], "bbox": block.get("bbox"),
                    "source_bboxes": {str(page_no): block.get("bbox")}})
            first_in_page = False

    # Part/section headings may introduce another heading without intervening prose.
    chapters = [ch for ch in chapters if ch["paragraphs"] or ch["heading"]]
    if not chapters:
        chapters = [{"id": "ch01", "heading": "", "paragraphs": []}]
    for ch in chapters:
        for n, p in enumerate(ch["paragraphs"], start=1):
            p["id"] = f"{ch['id']}-p{n:03d}"
    paragraphs = [p for ch in chapters for p in ch["paragraphs"]]
    source_units = []
    for chapter in chapters:
        if chapter["heading"]:
            source_units.append((chapter.get("source_page", 1), "before_heading", f"{chapter['id']}-h000"))
        source_units.extend((p["source_page"], "before_paragraph", p["id"]) for p in chapter["paragraphs"])
    for image in images:
        if image["source_page"] == 1 and cover:
            image["role"] = "cover_artwork"  # already appears in the exact original cover
            continue
        on_page = [p for p in paragraphs if image["source_page"] in p["source_pages"]]
        if not on_page:
            earlier = [p for p in paragraphs if p["source_page"] < image["source_page"]]
            if earlier:
                image["after_paragraph"] = earlier[-1]["id"]
            else:
                following = next((unit for unit in source_units if unit[0] >= image["source_page"]), None)
                if following:
                    _, placement, ident = following
                    image[placement] = ident
            continue
        preceding = [p for p in on_page if p.get("source_bboxes", {}).get(str(image["source_page"]))
                     and image.get("bbox") and
                     p["source_bboxes"][str(image["source_page"])][3] <= image["bbox"][1] + 2]
        if preceding:
            image["after_paragraph"] = preceding[-1]["id"]
        else:
            image["before_paragraph"] = on_page[0]["id"]
    return {"schema_version": SCHEMA_VERSION, "title": title, "source_lang": source_lang,
            "chapters": chapters, "footnotes": footnotes, "images": images,
            "cover": cover, "extraction_warnings": warnings}


def all_ids(book: dict) -> list[str]:
    """Všechna ID v pořadí knihy — včetně titulu a nadpisů kapitol (překládají se také)."""
    ids: list[str] = []
    if book.get("title"):
        ids.append("book-title")
    for ch in book["chapters"]:
        if ch.get("heading"):
            ids.append(f"{ch['id']}-h000")
        ids.extend(p["id"] for p in ch["paragraphs"])
    ids.extend(note["id"] for note in book.get("footnotes", []))
    return ids


def language_samples(pages: list[str | dict], max_chars: int = 2000) -> list[str]:
    """Sample substantial prose at the start, middle and end of the source.

    Headings and author notes do not determine whether the entire book can safely
    bypass translation. A short source uses its complete prose as one sample.
    """
    paragraphs: list[str] = []
    for page in pages:
        if isinstance(page, dict):
            blocks = page.get("blocks") or [
                {"type": "paragraph", "text": text} for text in split_paragraphs(page.get("text", ""))
            ]
            paragraphs.extend(block.get("text", "").strip() for block in blocks
                              if block.get("type") == "paragraph" and not looks_like_heading(block.get("text", "")))
        else:
            paragraphs.extend(text for text in split_paragraphs(page) if not looks_like_heading(text))
    text = "\n\n".join(paragraph for paragraph in paragraphs if paragraph)
    if not text.strip():
        return []
    if len(text) <= max_chars:
        return [text]
    starts = [0, (len(text) - max_chars) // 2, len(text) - max_chars]
    return [text[start:start + max_chars] for start in starts]


def _source_language(engine, pages: list[str | dict]) -> str:
    samples = language_samples(pages)
    if not samples:
        return "unknown"
    languages = []
    for sample in samples:
        engine.checkpoint_wait()
        try:
            answer = engine.api.complete(engine.config.model_draft, LANG_SYSTEM, sample).strip().lower()
            languages.append(answer if re.fullmatch(r"[a-z]{2}", answer) else "unknown")
        except Exception as exc:  # Metadata failure must still route the book through translation.
            if isinstance(exc, BookTrError) and exc.code == "cancelled":
                raise
            languages.append("unknown")
    return languages[0] if all(lang == languages[0] for lang in languages) else "unknown"


def run(engine):
    job = engine.job
    engine.report("segment", 0, 1)
    pages = job.read_json("pages.json")

    lang = _source_language(engine, pages)

    book = build_book(pages, title=job.title, source_lang=lang)
    job.write_json("book.json", book)
    engine.report("segment", 1, 1)
    return book
