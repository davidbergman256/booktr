"""Canonical memory preserves all evidence while bounding editorial output."""
import copy
import json
import threading
import time

import pytest

from booktr.engine import stylesheet
from booktr.errors import BookTrError


def payload(user, marker):
    return json.JSONDecoder().raw_decode(user.split(marker, 1)[1].strip())[0]


class CanonicalApi:
    """Deterministic editorial service; late evidence changes the actual choice."""

    def __init__(self):
        self.active = self.peak = self.reductions = self.guides = 0
        self.lock = threading.Lock()
        self.omit = None

    def complete(self, model, system, user, **kwargs):
        if system.startswith("[STYLE_GUIDE]"):
            self.guides += 1
            return json.dumps({"style": "Calm period narration.", "character_context": "Alice and Alenka identify the same character."})
        assert system.startswith("[STYLE_REDUCE]")
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.reductions += 1
        try:
            time.sleep(.01)
            evidence = payload(user, "CANONICAL_EVIDENCE_JSON:")
            result = {"glossary": [], "register": []}
            for group in evidence["glossary"]:
                candidates = group["candidates"]
                preferred = next((entry for entry in candidates if "adult identity" in entry.get("note", "")), candidates[0])
                if group["term"] == "Alice" and any("adult identity" in context.get("synopsis", "")
                                                       for context in evidence.get("source_context", {}).values()):
                    preferred = next(entry for entry in candidates if entry["czech"] == "Alice")
                result["glossary"].append({"term": group["term"], "czech": preferred["czech"], "note": preferred.get("note", "")})
            for group in evidence["register"]:
                forms = {entry["form"] for entry in group["candidates"]}
                result["register"].append({"pair": group["pair"], "form": "contextual" if len(forms) > 1 else next(iter(forms)),
                                           "note": "Uses vy in the first chapter, then ty after the wedding in the final chapter."})
            if self.omit:
                result[self.omit] = []
            return json.dumps(result)
        finally:
            with self.lock:
                self.active -= 1


def profiles():
    return [
        {"chapter": "ch01", "segment": "ch01-0", "synopsis": "They meet formally.", "style": "Calm period narration.",
         "glossary": [{"term": "Alice", "czech": "Alenka", "note": "Childhood name, identity uncertain."}],
         "register": [{"pair": "Alice -> Bob", "form": "vy", "note": "Formal strangers before the wedding."}]},
        {"chapter": "ch62", "segment": "ch62-0", "synopsis": "They marry.", "style": "Calm period narration.",
         "glossary": [{"term": "Alice", "czech": "Alice", "note": "The adult identity explicitly requires Alice."},
                      {"term": "Late Entity", "czech": "Pozdní postava", "note": "Revealed in the final chapter."}],
         "register": [{"pair": "Alice -> Bob", "form": "ty", "note": "Intimate spouses after the wedding."}]},
    ]


def test_parallel_canonical_reduction_keeps_late_conflicts_and_chronology(fake_engine):
    fake_engine.api = CanonicalApi()
    result = stylesheet.reduce_profiles(fake_engine, profiles(), "Book")
    choices = {entry["term"]: entry["czech"] for entry in result["glossary"]}
    assert choices == {"Alice": "Alice", "Late Entity": "Pozdní postava"}
    assert result["register"][0]["form"] == "contextual"
    assert "final chapter" in result["register"][0]["note"]
    assert result["style"] == "Calm period narration."


def test_reducer_keeps_disambiguating_full_synopsis_with_its_identity(fake_engine):
    fake_engine.api = CanonicalApi()
    source = profiles()
    source[-1]["glossary"][0]["note"] = "Another translation proposal."
    source[-1]["synopsis"] = "The adult identity explicitly requires Alice. They marry in the final chapter."
    result = stylesheet.reduce_profiles(fake_engine, source, "Book")
    assert next(entry for entry in result["glossary"] if entry["term"] == "Alice")["czech"] == "Alice"


def test_canonical_batches_limit_input_and_keep_indivisible_identity_evidence(monkeypatch):
    monkeypatch.setattr(stylesheet, "REDUCE_BATCH_CHARS", 500)
    source = profiles()
    evidence = stylesheet._group_evidence(source)
    batches = stylesheet._reduction_batches(evidence, source)
    assert sum(len(batch["glossary"]) + len(batch["register"]) for batch in batches) == 3
    for batch in batches:
        identities = len(batch["glossary"]) + len(batch["register"])
        assert len(json.dumps(batch, ensure_ascii=False)) <= 500 or identities == 1
    alice = next(batch for batch in batches if any(group["term"] == "Alice" for group in batch["glossary"]))
    assert {context["chapter"] for context in alice["source_context"].values()} == {"ch01", "ch62"}


def test_batches_cover_every_identity_and_share_bounded_workers(fake_engine, monkeypatch):
    monkeypatch.setattr(stylesheet, "REDUCE_BATCH_ITEMS", 3)
    fake_engine.config.translation_workers = 2
    fake_engine.api = CanonicalApi()
    source = profiles()
    source[0]["glossary"].extend({"term": f"Term {i}", "czech": f"Pojem {i}", "note": "Original evidence."} for i in range(9))
    source[0]["register"].extend({"pair": f"A -> Person {i}", "form": "vy", "note": "Formal relation."} for i in range(4))
    result = stylesheet.reduce_profiles(fake_engine, source, "Book")
    assert len(result["glossary"]) == 11
    assert len(result["register"]) == 5
    assert len({entry["term"] for entry in result["glossary"]}) == 11
    assert len({entry["pair"] for entry in result["register"]}) == 5
    assert fake_engine.api.reductions == 6  # ceil(16 identities / 3)
    assert fake_engine.api.peak == 2
    assert fake_engine.api.guides == 1


def test_identical_candidates_deduplicate_without_losing_source_occurrences():
    source = profiles()
    duplicate = copy.deepcopy(source[0])
    duplicate.update(chapter="ch10", segment="ch10-0")
    source.insert(1, duplicate)
    evidence = stylesheet._group_evidence(source)
    alice = next(group for group in evidence["glossary"] if group["term"] == "Alice")
    assert len(alice["candidates"]) == 2
    assert alice["candidates"][0]["sources"] == [
        {"chapter": "ch01", "segment": "ch01-0", "order": 0},
        {"chapter": "ch10", "segment": "ch10-0", "order": 1},
    ]
    assert alice["candidates"][1]["sources"][0]["chapter"] == "ch62"


def test_canonical_batches_reject_missing_identity_before_publication(fake_engine):
    fake_engine.api = CanonicalApi()
    fake_engine.api.omit = "glossary"
    with pytest.raises(BookTrError, match="coverage"):
        stylesheet.reduce_profiles(fake_engine, profiles(), "Book")


def test_reduction_resume_reuses_all_accepted_groups_and_changed_evidence_invalidates(fake_engine):
    fake_engine.api = CanonicalApi()
    source = profiles()
    stylesheet.reduce_profiles(fake_engine, source, "Book")
    calls = fake_engine.api.reductions
    stylesheet.reduce_profiles(fake_engine, source, "Book")
    assert fake_engine.api.reductions == calls
    assert fake_engine.api.guides == 1
    source[-1]["glossary"][-1]["czech"] = "Nová pozdní postava"
    changed = stylesheet.reduce_profiles(fake_engine, source, "Book")
    assert next(entry for entry in changed["glossary"] if entry["term"] == "Late Entity")["czech"] == "Nová pozdní postava"
    assert fake_engine.api.reductions > calls
