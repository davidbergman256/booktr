"""Bounded draft→review workers: editing overlaps with unfinished translation.

Every paragraph and author note receives source-based review. A failed review
keeps the accepted draft checkpoint, so resuming spends no tokens redrafting it.
"""
from __future__ import annotations

from ..errors import BookTrError
from . import review, translate
from .checkpoints import cached_call, checkpoint_key, model_settings
from .validation import source_entries, validate_text_map


def run(engine):
    job = engine.job
    book = job.read_json("book.json")
    try:
        source = source_entries(book)
    except ValueError as exc:
        raise BookTrError("unknown", f"Invalid source document: {exc}") from exc
    if book.get("source_lang") == "cs":
        job.write_json("draft.json", source)
        job.write_json("patches.json", {})
        engine.report("translate", 1, 1)
        return source

    synopses = job.read_json("synopses.json")
    style = job.read_text("stylesheet.md") if job.exists("stylesheet.md") else ""
    draft_system = translate.SYSTEM_TMPL.format(stylesheet=style)
    review_system = review.SYSTEM_TMPL.format(stylesheet=style)
    chunks = translate.build_chunks(book, getattr(engine.config, "chunk_words", translate.MAX_CHUNK_WORDS))
    draft_settings = model_settings(engine.config, engine.config.model_draft)
    review_settings = model_settings(engine.config, engine.config.model_review)
    keyed = [(checkpoint_key(chunk["key"], draft_system, review_system, draft_settings,
                             review_settings, translate.chunk_user_prompt(chunk, synopses, book)), chunk)
             for chunk in chunks]

    def work(chunk):
        original = {p["id"]: p["text"] for p in chunk["paragraphs"]}
        context = translate.chunk_user_prompt(chunk, synopses, book)
        draft_key = checkpoint_key(chunk["key"], draft_system, draft_settings, context)
        draft = cached_call(engine, "translate", draft_key,
                            lambda: translate.translate_chunk(engine, draft_system, chunk, synopses, book),
                            lambda data: validate_text_map(original, data))
        review_key = checkpoint_key(chunk["key"], review_system, review_settings, context, draft)
        patches = cached_call(engine, "review", review_key,
                              lambda: review.review_chunk(engine, review_system, chunk, draft, context),
                              lambda data: validate_text_map(original, data, partial=True))
        return {"draft": draft, "patches": patches}

    # One worker performs one API call at a time; the total draft+review request
    # concurrency is the configured bound, rather than two independent pools.
    def validate_chunk(chunk, data):
        original = {p["id"]: p["text"] for p in chunk["paragraphs"]}
        validate_text_map(original, data["draft"])
        validate_text_map(original, data["patches"], partial=True)

    parts = engine.run_chunks("translate", keyed, work, validate=validate_chunk)
    draft: dict[str, str] = {}
    patches: dict[str, str] = {}
    for key, _ in keyed:
        draft.update(parts[key]["draft"])
        patches.update(parts[key]["patches"])
    try:
        validate_text_map(source, draft)
        validate_text_map(source, patches, partial=True)
    except ValueError as exc:
        raise BookTrError("unknown", f"Invalid translation checkpoint: {exc}") from exc
    patches.update(review.review_headings(engine, book, draft, patches))
    validate_text_map(source, {**draft, **patches})
    job.write_json("draft.json", draft)
    job.write_json("patches.json", patches)
    return {**draft, **patches}
