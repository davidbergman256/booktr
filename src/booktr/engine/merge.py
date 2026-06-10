"""Fáze 4 — sloučení: deterministické, bez modelu.

final = draft přepsaný patchi z revize. Validace: každé ID z book.json má
právě jeden neprázdný český odstavec; patche s vymyšlenými ID se zahazují.
"""
from __future__ import annotations

import logging

from ..errors import BookTrError
from .segment import all_ids

log = logging.getLogger("booktr.merge")


def merge_book(book: dict, draft: dict, patches: dict) -> dict:
    ids = all_ids(book)
    known = set(ids)

    final = dict(draft)
    for key, value in patches.items():
        if key not in known:
            log.warning("merge: dropping patch for unknown id %r", key)
            continue
        if not str(value).strip():
            log.warning("merge: dropping empty patch for id %r", key)
            continue
        final[key] = value

    missing = [i for i in ids if not str(final.get(i, "")).strip()]
    if missing:
        raise BookTrError("unknown", f"merge: {len(missing)} paragraphs missing, e.g. {missing[:5]}")
    return {i: final[i] for i in ids}  # v pořadí knihy, jen známá ID


def run(engine):
    job = engine.job
    engine.report("merge", 0, 1)
    final = merge_book(
        job.read_json("book.json"),
        job.read_json("draft.json"),
        job.read_json("patches.json"),
    )
    job.write_json("final.json", final)
    engine.report("merge", 1, 1)
    return final
