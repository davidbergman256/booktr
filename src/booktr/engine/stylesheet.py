"""Parallel full-book profiling followed by a canonical terminology reduction."""
from __future__ import annotations

import json
import logging

from ..errors import BookTrError
from .checkpoints import cached_call, checkpoint_key, model_settings
from .translate import _pack

log = logging.getLogger("booktr.stylesheet")
PROFILE_WORDS = 6000
REDUCE_BATCH_ITEMS = 20
REDUCE_BATCH_CHARS = 20000
REDUCTION_VERSION = "canonical-evidence-batches-v1"

SYSTEM = """[STYLESHEET] Analyze this source segment for a literary translation into Czech.
Return one JSON object:
{"synopsis": "compact factual summary of this segment, in Czech",
 "glossary": [{"term": "source name or term", "czech": "preferred Czech form with declension notes", "note": "meaning and evidence"}],
 "register": [{"pair": "Character A -> Character B", "form": "ty", "note": "evidence and uncertainty"}],
 "style": "narrative voice, period, register, dialogue and recurring stylistic choices"}
Read the ENTIRE segment. Capture important entities even when they first appear
at its end. Preserve character relationships and distinguish guesses from facts.
Do not invent details or depend on other segments having been processed first.
The register form MUST be exactly one of "ty", "vy", "contextual". Never return
the literal string "ty|vy", "ty/vy", descriptive prose or another spelling as form.
Use "contextual" for a changing relationship or unresolved addressing, and give
explicit scene/time/evidence-based guidance in note. Do not force one form across
a relationship that changes during the story.
Keep the synopsis under 160 words and focus the glossary on recurring or ambiguous
terms, names and culturally specific expressions. No commentary outside JSON."""

GUIDE_SYSTEM = """[STYLE_GUIDE] Create compact shared guidance for a Czech literary translation.
The supplied summaries and narrative styles cover EVERY source segment in order.
Entity candidates list ALL proposed Czech forms; identify aliases and related
characters so separate editorial batches can coordinate names and declensions.
Return ONLY {"style":"canonical narrative/style guidance", "character_context":"name/alias/character guidance"}.
Keep style under 200 words and character_context under 250 words. Be precise,
conservative and faithful; do not invent facts. Preserve changes in narrative
voice, era and relationship context instead of imposing one rule on every scene.
Do not decide individual ty/vy relationships here: their complete scene evidence
will be reviewed separately. No glossary lists or translated book prose."""

REDUCE_SYSTEM = """[STYLE_REDUCE] Canonicalize the supplied evidence groups for a Czech literary translation.
Each identity includes ALL source-ordered candidate translations and evidence,
including late-book discoveries. Identical evidence may be grouped with several
source locations; reconstruct chronology using each source's order number.
Use the shared global narrative/character guide to coordinate aliases, names and
declensions. source_context retains the COMPLETE synopsis/style of every profile
that mentioned these identities; use it when a candidate's short note is ambiguous.
Reconcile conflicts from evidence, never by first occurrence or
majority vote. Do not choose uncertain early guesses over explicit later facts.
Return exactly ONE choice for EVERY supplied glossary term and addressing pair.
Copy each source term/pair identity EXACTLY. Do not add, rename, merge or omit
identities, even for aliases: each must remain independently addressable.
One addressing rule may vary by scene/time. State unresolved ambiguity briefly.
Return ONLY {"glossary": [{"term": "source name or term", "czech": "canonical Czech with declension notes", "note": "brief guidance"}],
"register": [{"pair": "Character A -> Character B", "form": "ty", "note": "brief guidance"}]}.
Register form MUST be exactly "ty", "vy" or "contextual". Use "contextual" when
addressing changes or is unresolved; note MUST explain the context/chronology and
how the translator should decide. Never flatten a changing relationship into a
single global ty/vy choice. Never use "ty|vy" or "ty/vy" as a literal form value.
Keep notes concise (normally under 40 words), while retaining meaningful scene
conditions, unresolved conflicts and essential declension guidance. No book prose,
chapter synopses or narrative-style essay in this response."""


def reduction_signature() -> dict:
    """Engine can invalidate canonical artifacts without discarding source profiles."""
    return {"version": REDUCTION_VERSION, "guide_system": GUIDE_SYSTEM,
            "reduce_system": REDUCE_SYSTEM, "batch_items": REDUCE_BATCH_ITEMS,
            "batch_chars": REDUCE_BATCH_CHARS}


def _validate_profile(data: dict, *, synopsis: bool = True) -> dict:
    if not isinstance(data, dict):
        raise ValueError("profile must be an object")
    if synopsis and (not isinstance(data.get("synopsis"), str) or not data["synopsis"].strip()):
        raise ValueError("profile requires a nonempty synopsis")
    for key, fields in (("glossary", ("term", "czech")), ("register", ("pair", "form"))):
        if not isinstance(data.get(key), list):
            raise ValueError(f"profile {key} must be a list")
        seen = set()
        for entry in data[key]:
            if not isinstance(entry, dict) or any(not isinstance(entry.get(field), str) or not entry[field].strip() for field in fields):
                raise ValueError(f"invalid {key} entry")
            if key == "register" and entry["form"] not in ("ty", "vy", "contextual"):
                raise ValueError("register form must be exactly ty, vy or contextual")
            if key == "register" and entry["form"] == "contextual" and (
                not isinstance(entry.get("note"), str) or not entry["note"].strip()
            ):
                raise ValueError("contextual register requires scene/time/evidence guidance in note")
            identity = entry[fields[0]].strip()
            if not synopsis and identity in seen:
                raise ValueError(f"duplicate canonical {key} choice: {identity}")
            seen.add(identity)
    if "style" in data and not isinstance(data["style"], str):
        raise ValueError("style must be text")
    return data


def _complete_profile(engine, system: str, user: str, *, synopsis: bool = True, model: str | None = None,
                      validate=None) -> dict:
    error = ""
    for attempt in range(2):
        raw = engine.api.complete(model or engine.config.model_draft, system, user, json_mode=True)
        try:
            data = json.loads(raw)
            return validate(data) if validate is not None else _validate_profile(data, synopsis=synopsis)
        except (ValueError, TypeError) as exc:
            error = str(exc)
            log.warning("profile attempt %d: %s", attempt + 1, exc)
            engine.job.save_chunk(
                "diagnostic", checkpoint_key("profile-invalid", system, user, model or engine.config.model_draft, raw),
                {"model": model or engine.config.model_draft, "validation_error": error, "raw_response": raw},
            )
            user += (
                f"\n\nYOUR INVALID RESPONSE_JSON:\n{raw}\n\nVALIDATION ERROR: {exc}.\n"
                'Correct that response and return the complete JSON object. Every register form '
                'must be exactly "ty", "vy" or "contextual"; use contextual with explanatory '
                'guidance for changing relationships or uncertainty. Preserve all valid evidence '
                'and entries while correcting the invalid fields.'
            )
    raise BookTrError("unknown", f"Book profile failed validation: {error}")


def _group_evidence(profiles: list[dict]) -> dict:
    """Deduplicate exact candidates only, retaining every ordered source occurrence."""
    groups = {"glossary": {}, "register": {}}
    candidate_indexes = {"glossary": {}, "register": {}}
    for order, profile in enumerate(profiles):
        _validate_profile(profile)
        source = {"chapter": profile.get("chapter", ""), "segment": profile.get("segment", ""), "order": order}
        for kind, identity_field, choice_field in (("glossary", "term", "czech"), ("register", "pair", "form")):
            for entry in profile[kind]:
                identity = entry[identity_field].strip()
                group = groups[kind].setdefault(identity, {identity_field: identity, "candidates": []})
                candidate = {choice_field: entry[choice_field], "note": entry.get("note", "")}
                key = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                known = candidate_indexes[kind].setdefault(identity, {})
                if key not in known:
                    known[key] = {**candidate, "sources": []}
                    group["candidates"].append(known[key])
                if source not in known[key]["sources"]:
                    known[key]["sources"].append(dict(source))
    return {kind: list(identities.values()) for kind, identities in groups.items()}


def _guide_input(profiles: list[dict], evidence: dict, title: str) -> dict:
    # Exact repeated summaries/styles share source locations. Evidence notes are
    # never shortened: full notes remain in their identity's editorial batch.
    narrative = {}
    for order, profile in enumerate(profiles):
        value = {"synopsis": profile["synopsis"], "style": profile.get("style", "")}
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        narrative.setdefault(key, {**value, "source_orders": []})["source_orders"].append(order)
    return {"title": title, "narrative": list(narrative.values()),
            "entity_candidates": [{"term": group["term"],
                                   "czech_forms": list(dict.fromkeys(candidate["czech"] for candidate in group["candidates"]))}
                                  for group in evidence["glossary"]]}


def _validate_guide(data: dict) -> dict:
    if (not isinstance(data, dict) or not isinstance(data.get("style"), str) or not data["style"].strip()
            or not isinstance(data.get("character_context"), str)):
        raise ValueError("global guide requires style and character_context text")
    if len((data["style"] + " " + data["character_context"]).split()) > 900:
        raise ValueError("global guide must stay compact: at most 900 words")
    return {"style": data["style"], "character_context": data["character_context"]}


def _reduction_batches(evidence: dict, profiles: list[dict] | None = None) -> list[dict]:
    def empty():
        return {"glossary": [], "register": [], "source_context": {}}

    def add(batch, kind, group):
        candidate = {"glossary": list(batch["glossary"]), "register": list(batch["register"]),
                     "source_context": dict(batch["source_context"])}
        candidate[kind].append(group)
        if profiles is not None:
            for proposal in group["candidates"]:
                for source in proposal["sources"]:
                    order = source["order"]
                    profile = profiles[order]
                    candidate["source_context"][str(order)] = {
                        "chapter": source["chapter"], "segment": source["segment"],
                        "synopsis": profile["synopsis"], "style": profile.get("style", ""),
                    }
        return candidate

    batches, current = [], empty()
    count = 0
    for kind in ("glossary", "register"):
        for group in evidence[kind]:
            candidate = add(current, kind, group)
            if count and (count >= REDUCE_BATCH_ITEMS or len(json.dumps(candidate, ensure_ascii=False)) > REDUCE_BATCH_CHARS):
                batches.append(current)
                candidate, count = add(empty(), kind, group), 0
            # One identity's complete evidence is indivisible. A particularly
            # frequent identity may exceed the input budget, but emits one choice.
            current = candidate
            count += 1
    if count:
        batches.append(current)
    return batches


def _validate_canonical_batch(expected: dict, data: dict) -> dict:
    _validate_profile(data, synopsis=False)
    for kind, identity in (("glossary", "term"), ("register", "pair")):
        source = {entry[identity] for entry in expected[kind]}
        result = {entry[identity] for entry in data[kind]}
        if source != result:
            raise ValueError(f"canonical {kind} coverage mismatch: missing={sorted(source - result)[:8]}, extra={sorted(result - source)[:8]}")
    return data


def reduce_profiles(engine, profiles: list[dict], title: str) -> dict:
    """Bound canonical output latency while preserving whole-book entity evidence."""
    evidence = _group_evidence(profiles)
    settings = model_settings(engine.config, engine.config.model_review)
    guide_user = "GLOBAL_CONTEXT_JSON:\n" + json.dumps(_guide_input(profiles, evidence, title), ensure_ascii=False)
    guide_key = checkpoint_key("canonical-guide", reduction_signature(), settings, guide_user)
    guide = cached_call(engine, "stylesheet", guide_key,
                        lambda: _complete_profile(engine, GUIDE_SYSTEM, guide_user, model=engine.config.model_review,
                                                  validate=_validate_guide), _validate_guide)
    items = []
    for index, batch in enumerate(_reduction_batches(evidence, profiles)):
        user = (f"BOOK TITLE: {title}\nSHARED_GLOBAL_GUIDE_JSON:\n{json.dumps(guide, ensure_ascii=False)}\n\n"
                f"CANONICAL_EVIDENCE_JSON:\n{json.dumps(batch, ensure_ascii=False)}")
        key = checkpoint_key(f"canonical-{index:04d}", reduction_signature(), settings, user)
        items.append((key, {"evidence": batch, "user": user}))

    def validate_batch(item, result):
        return _validate_canonical_batch(item["evidence"], result)

    def reduce_batch(item):
        return _complete_profile(engine, REDUCE_SYSTEM, item["user"], synopsis=False,
                                 model=engine.config.model_review,
                                 validate=lambda result: validate_batch(item, result))

    parts = engine.run_chunks("stylesheet", items, reduce_batch, validate=validate_batch)
    canonical = {"glossary": [], "register": [], "style": guide["style"]}
    for kind, identity in (("glossary", "term"), ("register", "pair")):
        choices = {entry[identity]: entry for key, _ in items for entry in parts[key][kind]}
        canonical[kind] = [choices[group[identity]] for group in evidence[kind]]
    _validate_canonical_batch(evidence, canonical)
    return canonical


def run(engine):
    job = engine.job
    book = job.read_json("book.json")
    if book.get("source_lang") == "cs":
        job.write_text("stylesheet.md", "# Český originál\nText zachovat beze změn.\n")
        job.write_json("synopses.json", {})
        engine.report("stylesheet", 1, 1)
        return
    items: list[tuple[str, dict]] = []
    for chapter in book["chapters"]:
        for chunk in _pack(chapter["paragraphs"], chapter["id"], PROFILE_WORDS):
            chunk["heading"] = chapter.get("heading", "")
            key = checkpoint_key(chunk["key"], SYSTEM, model_settings(engine.config, engine.config.model_draft), chunk)
            items.append((key, chunk))
    # Footnotes often explain names and historical references and must inform memory.
    notes = [{"id": n["id"], "text": n["text"]} for n in book.get("footnotes", [])]
    for chunk in _pack(notes, "footnotes", PROFILE_WORDS):
        chunk["heading"] = "Original author footnotes"
        items.append((checkpoint_key(chunk["key"], SYSTEM, model_settings(engine.config, engine.config.model_draft), chunk), chunk))

    def profile(chunk):
        text = "\n\n".join(p["text"] for p in chunk["paragraphs"])
        user = f"SOURCE LANGUAGE: {book.get('source_lang', 'unknown')}\nCHAPTER {chunk['chapter']} ({chunk['heading']}):\n{text}"
        return _complete_profile(engine, SYSTEM, user)

    parts = engine.run_chunks("stylesheet", items, profile,
                              validate=lambda chunk, data: _validate_profile(data))
    profiles = []
    synopses: dict[str, str] = {}
    for key, chunk in items:
        data = _validate_profile(parts[key])
        profiles.append({"chapter": chunk["chapter"], "segment": chunk["key"], **data})
        if chunk["chapter"] != "footnotes":
            # All segment summaries contribute; no character or chapter-tail truncation.
            existing = synopses.get(chunk["chapter"], "")
            synopses[chunk["chapter"]] = (existing + "\n" + data["synopsis"]).strip()
    # Persist complete source evidence before any editorial request can fail.
    job.write_json("profiles.json", profiles)
    canonical = (reduce_profiles(engine, profiles, book.get("title", "")) if profiles
                 else {"glossary": [], "register": [], "style": ""})
    job.write_text("stylesheet.md", _render(canonical["glossary"], canonical["register"], canonical.get("style", "")))
    job.write_json("synopses.json", synopses)


def _render(glossary: list[dict], register: list[dict], style: str = "") -> str:
    lines = ["# Stylesheet překladu", "", "## Vypravěč a styl", style, "", "## Jména, místa a skloňování"]
    seen = set()
    for entry in glossary:
        term = entry.get("term", "")
        if not term or term in seen:
            continue
        seen.add(term)
        note = f" — {entry['note']}" if entry.get("note") else ""
        lines.append(f"- {term} → {entry.get('czech', '')}{note}")
    lines += ["", "## Oslovení (ty/vy)"]
    for entry in register:
        note = f" — {entry['note']}" if entry.get("note") else ""
        lines.append(f"- {entry.get('pair', '')}: {entry.get('form', '')}{note}")
    return "\n".join(lines) + "\n"
