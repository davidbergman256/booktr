"""Full source-based editorial review; invalid review results are never accepted."""
from __future__ import annotations

import json
import logging

from ..errors import BookTrError
from .checkpoints import cached_call, checkpoint_key, model_settings
from .translate import MAX_CHUNK_WORDS, build_chunks, chunk_user_prompt
from .validation import immutable_source_entries, normalize_cached_text_map, parse_text_map, validate_text_map

log = logging.getLogger("booktr.review")

SYSTEM_TMPL = """[REVIEW] You are a senior Czech literary translator and editor.
Compare EVERY supplied draft paragraph against its original source for missing
meaning, mistranslation, natural Czech, grammar, dialogue, character voice,
register and style-sheet compliance. Check quotations, numbers and author notes.
Review all entries; rewrite only those with a real error or clear improvement.

CANONICAL BOOK STYLE SHEET:
{stylesheet}

Return ONLY a JSON object of changed paragraph IDs and their complete revised
Czech text strings. Return {{}} only after checking every paragraph and finding
no needed changes. Never add IDs or remove/merge paragraphs. Copy each immutable
[[FN:id]] marker exactly, in order and at its semantic position. Context is for
understanding only and must not appear in the output."""

HEADINGS_SYSTEM = """[HEADINGS] You are a senior Czech literary editor.
Unify the translated book title and chapter headings to one consistent pattern,
capitalisation, register and punctuation, respecting the original meaning and
canonical style sheet. Return one JSON object mapping EVERY supplied ID to its
complete final Czech heading. Preserve all [[FN:id]] markers. No invented IDs."""


def heading_entries(book: dict) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    if book.get("title"):
        items.append(("book-title", book["title"]))
    for chapter in book["chapters"]:
        if chapter.get("heading"):
            items.append((f"{chapter['id']}-h000", chapter["heading"]))
    return items


def harmonize_headings(engine, heads: list[tuple[str, str]], current: dict[str, str]) -> dict:
    source = dict(heads)
    validate_text_map(source, current)
    immutable = immutable_source_entries(source)
    source = {key: text for key, text in source.items() if key not in immutable}
    if not source:
        return {}
    current = {key: current[key] for key in source}
    style = engine.job.read_text("stylesheet.md") if engine.job.exists("stylesheet.md") else ""
    user = (
        f"CANONICAL STYLE SHEET:\n{style}\n\n"
        f"SOURCE HEADINGS_JSON:\n{json.dumps(source, ensure_ascii=False)}\n\n"
        f"CURRENT CZECH_JSON:\n{json.dumps(current, ensure_ascii=False)}"
    )
    error = ""
    for attempt in range(2):
        raw = engine.api.complete(engine.config.model_review, HEADINGS_SYSTEM, user, json_mode=True)
        try:
            parsed = validate_text_map(source, parse_text_map(raw))
            return {key: value for key, value in parsed.items() if value != current[key]}
        except (ValueError, TypeError) as exc:
            error = str(exc)
            log.warning("headings attempt %d: %s", attempt + 1, exc)
            user += f"\n\nValidation failed: {exc}. Return ALL supplied IDs with complete text."
    raise BookTrError("unknown", f"Heading review failed validation: {error}")


def review_chunk(engine, system: str, chunk: dict, draft: dict, context: str = "") -> dict:
    source = {p["id"]: p["text"] for p in chunk["paragraphs"]}
    drafted = {key: draft.get(key, "") for key in source}
    try:
        validate_text_map(source, drafted)
    except ValueError as exc:
        raise BookTrError("unknown", f"Invalid draft before review: {exc}") from exc
    immutable = immutable_source_entries(source)
    source = {key: text for key, text in source.items() if key not in immutable}
    if not source:
        return {}
    drafted = {key: drafted[key] for key in source}
    user = (
        f"READ-ONLY CONTEXT:\n{context}\n\n"
        f"SOURCE PARAGRAPHS_JSON:\n{json.dumps(source, ensure_ascii=False)}\n\n"
        f"DRAFT TRANSLATION_JSON:\n{json.dumps(drafted, ensure_ascii=False)}"
    )
    error = ""
    for attempt in range(2):
        raw = engine.api.complete(engine.config.model_review, system, user, json_mode=True)
        try:
            return validate_text_map(source, parse_text_map(raw), partial=True)
        except (ValueError, TypeError) as exc:
            error = str(exc)
            log.warning("review %s attempt %d: %s", chunk["key"], attempt + 1, exc)
            user += f"\n\nValidation failed: {exc}. Return only valid changed IDs; preserve all footnote markers."
    raise BookTrError("unknown", f"Review validation failed for {chunk['key']}: {error}")


def review_headings(engine, book: dict, draft: dict, patches: dict) -> dict:
    heads = heading_entries(book)
    if len(heads) < 2 or book.get("source_lang") == "cs":
        return {}
    current = {key: patches.get(key) or draft[key] for key, _ in heads}
    style = engine.job.read_text("stylesheet.md") if engine.job.exists("stylesheet.md") else ""
    key = checkpoint_key("headings", model_settings(engine.config, engine.config.model_review), HEADINGS_SYSTEM, heads, current, style)
    return cached_call(engine, "review", key, lambda: harmonize_headings(engine, heads, current),
                       lambda result: validate_text_map(dict(heads), result, partial=True),
                       normalize=lambda result: normalize_cached_text_map(dict(heads), result, partial=True))


def run(engine):
    """Standalone review stage; normal book runs use the overlapped pipeline."""
    job = engine.job
    book = job.read_json("book.json")
    draft = job.read_json("draft.json")
    synopses = job.read_json("synopses.json") if job.exists("synopses.json") else {}
    system = SYSTEM_TMPL.format(stylesheet=job.read_text("stylesheet.md") if job.exists("stylesheet.md") else "")
    chunks = build_chunks(book, getattr(engine.config, "chunk_words", MAX_CHUNK_WORDS))
    keyed = [(checkpoint_key(c["key"], model_settings(engine.config, engine.config.model_review), system,
                             c, {p["id"]: draft.get(p["id"], "") for p in c["paragraphs"]}, synopses), c) for c in chunks]
    parts = engine.run_chunks(
        "review", keyed, lambda c: review_chunk(engine, system, c, draft, chunk_user_prompt(c, synopses, book)),
        validate=lambda c, data: validate_text_map({p["id"]: p["text"] for p in c["paragraphs"]}, data, partial=True),
        normalize=lambda c, data: normalize_cached_text_map({p["id"]: p["text"] for p in c["paragraphs"]}, data, partial=True),
    )
    patches = {key: value for cache_key, _ in keyed for key, value in parts[cache_key].items()}
    patches.update(review_headings(engine, book, draft, patches))
    job.write_json("patches.json", patches)
    return patches
