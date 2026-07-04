"""Fáze 2 — překlad (pass 1, gpt-5.4-mini).

Práce po chuncích ~3000 slov; každý chunk se ukládá hned po ověření,
takže pád uprostřed knihy ztratí nejvýš jeden chunk.
Kontrakt s modelem: JSON {id: český text} pokrývající VŠECHNA zadaná ID.
"""
from __future__ import annotations

import json
import logging

from ..api import parse_json_map
from ..errors import BookTrError

log = logging.getLogger("booktr.translate")

MAX_CHUNK_WORDS = 3000

SYSTEM_TMPL = """[TRANSLATE] You are an experienced literary translator into Czech.
Translate faithfully but naturally — idiomatic, fluent Czech prose that reads as if
originally written in Czech. Preserve tone, register and era. Follow the style sheet
strictly (names, declensions, ty/vy decisions, terminology).

STYLE SHEET:
{stylesheet}

You receive paragraphs as a JSON object mapping paragraph id -> source text.
Return ONLY a JSON object mapping EVERY given paragraph id to its Czech translation.
Do not add, drop or merge paragraphs. No commentary."""


def build_chunks(book: dict, max_words: int = MAX_CHUNK_WORDS) -> list[dict]:
    """Souvislé odstavce jedné kapitoly po ~max_words slovech. Klíče jsou stabilní."""
    chunks: list[dict] = []
    first = True
    for ch in book["chapters"]:
        current: list[dict] = []
        words = 0
        part = 0
        if first and book.get("title"):
            # titul a nadpisy kapitol putují pipeline jako pseudo-odstavce, aby se přeložily
            current.append({"id": "book-title", "text": book["title"]})
            words += len(book["title"].split())
        first = False
        if ch.get("heading"):
            current.append({"id": f"{ch['id']}-h000", "text": ch["heading"]})
            words += len(ch["heading"].split())
        for p in ch["paragraphs"]:
            n = len(p["text"].split())
            if current and words + n > max_words:
                chunks.append({"key": f"{ch['id']}-{part}", "chapter": ch["id"], "paragraphs": current})
                part += 1
                current, words = [], 0
            current.append(p)
            words += n
        if current:
            chunks.append({"key": f"{ch['id']}-{part}", "chapter": ch["id"], "paragraphs": current})
    return chunks


def chunk_user_prompt(chunk: dict, synopses: dict, book: dict) -> str:
    chapter_ids = [c["id"] for c in book["chapters"]]
    prior = [cid for cid in chapter_ids if cid < chunk["chapter"]]
    context = "\n".join(f"{cid}: {synopses.get(cid, '')}" for cid in prior if synopses.get(cid))
    paragraphs = {p["id"]: p["text"] for p in chunk["paragraphs"]}
    return (
        f"SOURCE LANGUAGE: {book.get('source_lang', 'unknown')}\n"
        f"SYNOPSES OF PREVIOUS CHAPTERS:\n{context or '(beginning of book)'}\n\n"
        f"PARAGRAPHS_JSON:\n{json.dumps(paragraphs, ensure_ascii=False)}"
    )


def translate_chunk(engine, system: str, chunk: dict, synopses: dict, book: dict, depth: int = 0) -> dict:
    ids = [p["id"] for p in chunk["paragraphs"]]
    user = chunk_user_prompt(chunk, synopses, book)
    for attempt in range(2):
        raw = engine.api.complete(engine.config.model_draft, system, user, json_mode=True)
        try:
            parsed = parse_json_map(raw)
        except ValueError:
            parsed = {}
        missing = [i for i in ids if not parsed.get(i, "").strip()]
        if not missing:
            return {i: parsed[i] for i in ids}
        log.warning("chunk %s attempt %d: %d missing ids", chunk["key"], attempt + 1, len(missing))
        user += f"\n\nYour previous answer was missing ids: {missing[:10]}. Return ALL ids."
    # poslední záchrana: rozdělit chunk napůl a zkusit menší kusy
    if len(chunk["paragraphs"]) > 1 and depth < 4:
        mid = len(chunk["paragraphs"]) // 2
        a = {**chunk, "paragraphs": chunk["paragraphs"][:mid]}
        b = {**chunk, "paragraphs": chunk["paragraphs"][mid:]}
        return {**translate_chunk(engine, system, a, synopses, book, depth + 1),
                **translate_chunk(engine, system, b, synopses, book, depth + 1)}
    raise BookTrError("unknown", f"translation failed for chunk {chunk['key']}")


def run(engine):
    job = engine.job
    book = job.read_json("book.json")
    synopses = job.read_json("synopses.json")
    stylesheet = job.read_text("stylesheet.md") if job.exists("stylesheet.md") else ""
    system = SYSTEM_TMPL.format(stylesheet=stylesheet)

    chunks = build_chunks(book)
    parts = engine.run_chunks(
        "translate",
        [(c["key"], c) for c in chunks],
        lambda chunk: translate_chunk(engine, system, chunk, synopses, book),
    )
    draft: dict[str, str] = {}
    for c in chunks:
        draft.update(parts[c["key"]])
    job.write_json("draft.json", draft)
    return draft
