"""Paragraph-aligned translation with book memory and neighboring source context."""
from __future__ import annotations

import json
import logging

from ..errors import BookTrError
from .checkpoints import checkpoint_key, model_settings
from .validation import immutable_source_entries, normalize_cached_text_map, parse_text_map, validate_text_map

log = logging.getLogger("booktr.translate")
MAX_CHUNK_WORDS = 1200
CONTEXT_WORDS = 400

SYSTEM_TMPL = """[TRANSLATE] You are an experienced literary translator into Czech.
Translate faithfully and naturally, preserving every detail, dialogue, tone,
register and era. Never summarize, simplify, censor or omit source material.
Follow the canonical book style sheet, including names, declensions and ty/vy.

STYLE SHEET:
{stylesheet}

Neighboring source text and chapter summaries are context only. Translate ONLY
PARAGRAPHS_JSON. Return one JSON object mapping EVERY supplied ID to a nonempty
Czech text string. Do not add, drop or merge IDs or paragraphs.
Copy every immutable [[FN:id]] marker verbatim at the same semantic position.
Footnotes are original author notes: translate their complete text, preserving
quotations, citations, numbers and meaning. No commentary."""


def _neighbor_text(paragraphs: list[dict], *, tail: bool = False) -> str:
    words = "\n\n".join(p["text"] for p in paragraphs).split()
    return " ".join(words[-CONTEXT_WORDS:] if tail else words[:CONTEXT_WORDS])


def _pack(paragraphs: list[dict], chapter: str, max_words: int, kind: str = "prose") -> list[dict]:
    chunks: list[dict] = []
    current: list[dict] = []
    words = 0
    for paragraph in paragraphs:
        count = len(paragraph["text"].split())
        if current and words + count > max_words:
            chunks.append({"key": f"{chapter}-{len(chunks)}", "chapter": chapter, "kind": kind, "paragraphs": current})
            current, words = [], 0
        current.append(paragraph)
        words += count
    if current:
        chunks.append({"key": f"{chapter}-{len(chunks)}", "chapter": chapter, "kind": kind, "paragraphs": current})
    for index, chunk in enumerate(chunks):
        chunk["context_before"] = _neighbor_text(chunks[index - 1]["paragraphs"], tail=True) if index else ""
        chunk["context_after"] = _neighbor_text(chunks[index + 1]["paragraphs"]) if index + 1 < len(chunks) else ""
    return chunks


def build_chunks(book: dict, max_words: int = MAX_CHUNK_WORDS) -> list[dict]:
    if max_words < 1:
        raise ValueError("chunk word limit must be positive")
    chunks: list[dict] = []
    title_pending = bool(book.get("title"))
    for chapter in book["chapters"]:
        paragraphs: list[dict] = []
        if title_pending:
            paragraphs.append({"id": "book-title", "text": book["title"]})
            title_pending = False
        if chapter.get("heading"):
            paragraphs.append({"id": f"{chapter['id']}-h000", "text": chapter["heading"]})
        paragraphs.extend(chapter["paragraphs"])
        chunks.extend(_pack(paragraphs, chapter["id"], max_words))
    if title_pending:
        chunks.extend(_pack([{"id": "book-title", "text": book["title"]}], "front", max_words))
    notes = [{"id": note["id"], "text": note["text"]} for note in book.get("footnotes", [])]
    if notes:
        chunks.extend(_pack(notes, "footnotes", max_words, "footnotes"))
    return chunks


def chunk_user_prompt(chunk: dict, synopses: dict, book: dict) -> str:
    chapter_ids = [chapter["id"] for chapter in book["chapters"]]
    if chunk["chapter"] in chapter_ids:
        index = chapter_ids.index(chunk["chapter"])
        relevant = chapter_ids[max(0, index - 1):index + 2]
    else:
        relevant = []
    summaries = "\n".join(f"{cid}: {synopses[cid]}" for cid in relevant if synopses.get(cid))
    source = {p["id"]: p["text"] for p in chunk["paragraphs"]}
    return (
        f"SOURCE LANGUAGE: {book.get('source_lang', 'unknown')}\n"
        f"ENTRY TYPE: {chunk.get('kind', 'prose')}\n"
        f"CHAPTER CONTEXT:\n{summaries or '(not available)'}\n\n"
        f"NEIGHBORING SOURCE BEFORE (do not translate):\n{chunk.get('context_before', '')}\n\n"
        f"NEIGHBORING SOURCE AFTER (do not translate):\n{chunk.get('context_after', '')}\n\n"
        f"PARAGRAPHS_JSON:\n{json.dumps(source, ensure_ascii=False)}"
    )


def translate_chunk(engine, system: str, chunk: dict, synopses: dict, book: dict, depth: int = 0) -> dict:
    source = {p["id"]: p["text"] for p in chunk["paragraphs"]}
    immutable = immutable_source_entries(source)
    mutable = {key: text for key, text in source.items() if key not in immutable}
    if not mutable:
        return validate_text_map(source, immutable)
    # Keep original IDs and exact symbols outside the model's editable input.
    # A model may otherwise omit a standalone closing bracket, forcing retries
    # or silently damaging the original punctuation without this contract.
    chunk = {**chunk, "paragraphs": [p for p in chunk["paragraphs"] if p["id"] in mutable]}
    user = chunk_user_prompt(chunk, synopses, book)
    last_error = ""
    for attempt in range(2):
        raw = engine.api.complete(engine.config.model_draft, system, user, json_mode=True)
        try:
            translated = validate_text_map(mutable, parse_text_map(raw))
            return validate_text_map(source, {**immutable, **translated})
        except (ValueError, TypeError) as exc:
            last_error = str(exc)
            log.warning("translation %s attempt %d: %s", chunk["key"], attempt + 1, exc)
            user += f"\n\nYour response failed validation: {exc}. Return ALL and ONLY supplied IDs; preserve all footnote markers."
    if len(chunk["paragraphs"]) > 1 and depth < 4:
        mid = len(chunk["paragraphs"]) // 2
        first = {**chunk, "paragraphs": chunk["paragraphs"][:mid], "context_after": _neighbor_text(chunk["paragraphs"][mid:])}
        second = {**chunk, "paragraphs": chunk["paragraphs"][mid:], "context_before": _neighbor_text(chunk["paragraphs"][:mid], tail=True)}
        translated = {**translate_chunk(engine, system, first, synopses, book, depth + 1),
                      **translate_chunk(engine, system, second, synopses, book, depth + 1)}
        return validate_text_map(source, {**immutable, **translated})
    raise BookTrError("unknown", f"Translation validation failed for {chunk['key']}: {last_error}")


def run(engine):
    """Standalone draft stage retained for tools/tests; Engine uses pipeline.run."""
    job = engine.job
    book = job.read_json("book.json")
    synopses = job.read_json("synopses.json")
    system = SYSTEM_TMPL.format(stylesheet=job.read_text("stylesheet.md") if job.exists("stylesheet.md") else "")
    chunks = build_chunks(book, getattr(engine.config, "chunk_words", MAX_CHUNK_WORDS))
    keyed = [(checkpoint_key(c["key"], model_settings(engine.config, engine.config.model_draft), system,
                             chunk_user_prompt(c, synopses, book)), c) for c in chunks]
    parts = engine.run_chunks(
        "translate", keyed, lambda c: translate_chunk(engine, system, c, synopses, book),
        validate=lambda c, data: validate_text_map({p["id"]: p["text"] for p in c["paragraphs"]}, data),
        normalize=lambda c, data: normalize_cached_text_map({p["id"]: p["text"] for p in c["paragraphs"]}, data),
    )
    draft = {key: value for cache_key, _ in keyed for key, value in parts[cache_key].items()}
    job.write_json("draft.json", draft)
    return draft
