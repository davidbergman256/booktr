"""Fáze 0b — segmentace: stránky → odstavce se stabilními ID, detekce kapitol.

ID odstavců (chNN-pMMM) jsou páteří celé pipeline — po této fázi se už nikdy nemění.
"""
from __future__ import annotations

import logging
import re

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


def build_book(pages: list[str], title: str, source_lang: str = "unknown") -> dict:
    """Postaví book.json: kapitoly a odstavce se stabilními ID."""
    chapters: list[dict] = []
    current: dict | None = None

    def new_chapter(heading: str):
        nonlocal current
        current = {"id": f"ch{len(chapters) + 1:02d}", "heading": heading.lstrip("# ").strip(), "paragraphs": []}
        chapters.append(current)

    for page in pages:
        first_in_page = True
        for para in split_paragraphs(page):
            if looks_like_heading(para):
                new_chapter(para)
                first_in_page = False
                continue
            if current is None:
                new_chapter("")  # text před první kapitolou
                current["heading"] = ""
            para = para.lstrip("# ").strip()  # zamítnutý ## nadpis je běžný odstavec
            if first_in_page and current["paragraphs"] and para[:1].islower():
                # odstavec rozříznutý koncem stránky — pokračování malým písmenem
                current["paragraphs"][-1]["text"] += " " + para
            else:
                current["paragraphs"].append({"id": "", "text": para})
            first_in_page = False

    chapters = [ch for ch in chapters if ch["paragraphs"]]
    if not chapters:
        chapters = [{"id": "ch01", "heading": "", "paragraphs": []}]
    for ch in chapters:
        for n, p in enumerate(ch["paragraphs"], start=1):
            p["id"] = f"{ch['id']}-p{n:03d}"
    return {"title": title, "source_lang": source_lang, "chapters": chapters}


def all_ids(book: dict) -> list[str]:
    """Všechna ID v pořadí knihy — včetně titulu a nadpisů kapitol (překládají se také)."""
    ids: list[str] = []
    if book.get("title"):
        ids.append("book-title")
    for ch in book["chapters"]:
        if ch.get("heading"):
            ids.append(f"{ch['id']}-h000")
        ids.extend(p["id"] for p in ch["paragraphs"])
    return ids


def run(engine):
    job, api, cfg = engine.job, engine.api, engine.config
    engine.report("segment", 0, 1)
    pages = job.read_json("pages.json")

    sample = "\n".join(pages)[:2000]
    try:
        lang = api.complete(cfg.model_draft, LANG_SYSTEM, sample).strip().lower()[:5]
    except Exception:  # noqa: BLE001 — jazyk je jen metadata, nesmí shodit běh
        lang = "unknown"

    book = build_book(pages, title=job.title, source_lang=lang)
    job.write_json("book.json", book)
    engine.report("segment", 1, 1)
    return book
