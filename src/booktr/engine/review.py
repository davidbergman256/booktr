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
    total = len(chunks)
    patches: dict[str, str] = {}
    for idx, chunk in enumerate(chunks):
        engine.checkpoint_wait()
        if job.has_chunk("review", chunk["key"]):
            part = job.load_chunk("review", chunk["key"])
        else:
            part = review_chunk(engine, system, chunk, draft)
            job.save_chunk("review", chunk["key"], part)
        patches.update(part)
        engine.report("review", idx + 1, total)
    job.write_json("patches.json", patches)
    return patches
