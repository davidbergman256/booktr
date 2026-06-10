"""Fáze 1 — stylesheet: globální konzistence (jména, skloňování, ty/vy, termíny)
+ synopse kapitol, díky kterým mohou další fáze zpracovávat kapitoly nezávisle.
"""
from __future__ import annotations

import json
import logging

from ..api import parse_json_map  # noqa: F401  (re-export pro testy)

log = logging.getLogger("booktr.stylesheet")

CHAPTER_CHAR_CAP = 20_000  # u dlouhých kapitol stačí začátek pro jména/termíny

SYSTEM = """[STYLESHEET] You prepare a style sheet for a literary translation into Czech.
You read the book chapter by chapter. For each chapter return a JSON object:
{
  "synopsis": "one paragraph summary of the chapter's events, in Czech",
  "glossary": [{"term": "source term or name", "czech": "chosen Czech form incl. declension notes", "note": "optional"}],
  "register": [{"pair": "Character A -> Character B", "form": "ty|vy", "note": "optional"}]
}
Only add NEW glossary/register entries not present in the existing list you are given.
Choose Czech name forms and ty/vy decisions once and keep them stable for the whole book."""


def run(engine):
    job, api, cfg = engine.job, engine.api, engine.config
    book = job.read_json("book.json")
    chapters = book["chapters"]
    total = len(chapters)

    glossary: list[dict] = []
    register: list[dict] = []
    synopses: dict[str, str] = {}

    for i, ch in enumerate(chapters):
        engine.checkpoint_wait()
        if job.has_chunk("stylesheet", ch["id"]):
            data = job.load_chunk("stylesheet", ch["id"])
        else:
            text = "\n\n".join(p["text"] for p in ch["paragraphs"])[:CHAPTER_CHAR_CAP]
            known = ", ".join(g["term"] for g in glossary) or "(none yet)"
            user = (
                f"EXISTING GLOSSARY TERMS: {known}\n\n"
                f"CHAPTER {ch['id']} ({ch['heading'] or 'bez názvu'}):\n{text}"
            )
            raw = api.complete(cfg.model_draft, SYSTEM, user, json_mode=True)
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                log.warning("stylesheet chapter %s: invalid JSON, skipping additions", ch["id"])
                data = {"synopsis": "", "glossary": [], "register": []}
            job.save_chunk("stylesheet", ch["id"], data)
        synopses[ch["id"]] = str(data.get("synopsis", ""))
        glossary.extend(g for g in data.get("glossary", []) if isinstance(g, dict))
        register.extend(r for r in data.get("register", []) if isinstance(r, dict))
        engine.report("stylesheet", i + 1, total)

    job.write_text("stylesheet.md", _render(glossary, register))
    job.write_json("synopses.json", synopses)


def _render(glossary: list[dict], register: list[dict]) -> str:
    lines = ["# Stylesheet překladu", "", "## Jména, místa a skloňování"]
    seen = set()
    for g in glossary:
        term = g.get("term", "")
        if not term or term in seen:
            continue
        seen.add(term)
        note = f" — {g['note']}" if g.get("note") else ""
        lines.append(f"- {term} → {g.get('czech', '')}{note}")
    lines += ["", "## Oslovení (ty/vy)"]
    for r in register:
        note = f" — {r['note']}" if r.get("note") else ""
        lines.append(f"- {r.get('pair', '')}: {r.get('form', '')}{note}")
    return "\n".join(lines) + "\n"
