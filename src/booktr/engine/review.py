"""Fáze 3 — revize (pass 2, gpt-5.5): vrací POUZE změněné odstavce.

Selhání revize nikdy nezastaví knihu — u nevalidního výstupu se po jednom
opravném dotazu chunk prostě ponechá v podobě z pass 1.
"""
from __future__ import annotations

import json
import logging

from ..api import parse_json_map
from .translate import build_chunks

log = logging.getLogger("booktr.review")

SYSTEM_TMPL = """[REVIEW] You are a senior Czech literary editor reviewing a translation.
Compare the draft against the source for accuracy, natural Czech, register and
style-sheet compliance (names, declensions, ty/vy, terminology).

STYLE SHEET:
{stylesheet}

Return ONLY a JSON object containing the paragraphs you are improving:
{{"paragraph-id": "revised Czech text"}}.
Return {{}} if the chunk needs no changes.
Never invent ids you were not given. Most paragraphs should NOT need changes —
only rewrite where there is a real error or clear improvement."""


HEADINGS_SYSTEM = """[HEADINGS] You are a senior Czech literary editor.
You receive the book title and all chapter headings of one book: the source text
and the current Czech translation. Chapter headings are translated in isolation,
so the set may be stylistically inconsistent (e.g. "PRVNÍ KAPITOLA" next to
"KAPITOLA DRUHÁ"). Unify them: one pattern for numbered chapters, consistent
capitalisation and punctuation, matching the tone of the book.

Return ONLY a JSON object mapping EVERY given id to its final Czech heading.
Keep headings that already fit. Never invent ids you were not given."""


def heading_entries(book: dict) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if book.get("title"):
        items.append(("book-title", book["title"]))
    for ch in book["chapters"]:
        if ch.get("heading"):
            items.append((f"{ch['id']}-h000", ch["heading"]))
    return items


def harmonize_headings(engine, heads: list[tuple[str, str]], current: dict[str, str]) -> dict:
    """Jeden dotaz přes všechny nadpisy najednou — konzistence napříč kapitolami.

    Vrací jen skutečné změny; při nevalidním výstupu se nadpisy prostě ponechají.
    """
    source = dict(heads)
    user = (
        f"SOURCE HEADINGS_JSON:\n{json.dumps(source, ensure_ascii=False)}\n\n"
        f"CURRENT CZECH_JSON:\n{json.dumps(current, ensure_ascii=False)}"
    )
    for attempt in range(2):
        raw = engine.api.complete(engine.config.model_review, HEADINGS_SYSTEM, user, json_mode=True)
        try:
            parsed = parse_json_map(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            return {
                k: v for k, v in parsed.items()
                if k in source and v.strip() and v != current.get(k)
            }
        log.warning("headings attempt %d: invalid JSON", attempt + 1)
        user += "\n\nReturn a valid JSON object only."
    log.warning("headings: keeping per-chunk translations (harmonization failed)")
    return {}


def review_chunk(engine, system: str, chunk: dict, draft: dict) -> dict:
    ids = {p["id"] for p in chunk["paragraphs"]}
    source = {p["id"]: p["text"] for p in chunk["paragraphs"]}
    drafted = {i: draft.get(i, "") for i in source}
    user = (
        f"SOURCE PARAGRAPHS_JSON:\n{json.dumps(source, ensure_ascii=False)}\n\n"
        f"DRAFT TRANSLATION_JSON:\n{json.dumps(drafted, ensure_ascii=False)}"
    )
    for attempt in range(2):
        raw = engine.api.complete(engine.config.model_review, system, user, json_mode=True)
        try:
            parsed = parse_json_map(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            bad = [k for k in parsed if k not in ids or not parsed[k].strip()]
            for k in bad:
                log.warning("review chunk %s: dropping invalid patch id %r", chunk["key"], k)
                parsed.pop(k)
            return parsed
        log.warning("review chunk %s attempt %d: invalid JSON", chunk["key"], attempt + 1)
        user += "\n\nReturn a valid JSON object only."
    log.warning("review chunk %s: keeping draft (review failed)", chunk["key"])
    return {}


def run(engine):
    job = engine.job
    book = job.read_json("book.json")
    draft = job.read_json("draft.json")
    stylesheet = job.read_text("stylesheet.md") if job.exists("stylesheet.md") else ""
    system = SYSTEM_TMPL.format(stylesheet=stylesheet)

    chunks = build_chunks(book)
    heads = heading_entries(book)
    total = len(chunks)
    parts = engine.run_chunks(
        "review",
        [(c["key"], c) for c in chunks],
        lambda chunk: review_chunk(engine, system, chunk, draft),
    )
    patches: dict[str, str] = {}
    for c in chunks:
        patches.update(parts[c["key"]])

    if len(heads) >= 2:  # sjednocení stylu nadpisů přes celou knihu
        engine.checkpoint_wait()
        if job.has_chunk("review", "headings"):
            part = job.load_chunk("review", "headings")
        else:
            current = {i: patches.get(i) or draft.get(i, "") for i, _ in heads}
            part = harmonize_headings(engine, heads, current)
            job.save_chunk("review", "headings", part)
        patches.update(part)
        engine.report("review", total, total)

    job.write_json("patches.json", patches)
    return patches
