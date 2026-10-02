"""Deterministic contracts for text handed to, and returned by, models."""
from __future__ import annotations

import json
import re

FOOTNOTE_MARKER = re.compile(r"\[\[FN:([^\]\s]+)\]\]")
CONTRACT_VERSION = "source-text-v3"


def immutable_source_entries(source: dict[str, str]) -> dict[str, str]:
    """Numbers, punctuation and opaque note anchors need exact copying, not inference."""
    return {key: text for key, text in source.items()
            if not any(char.isalpha() for char in FOOTNOTE_MARKER.sub("", text))}


def normalize_cached_text_map(source: dict[str, str], output: dict, *, partial: bool = False) -> dict:
    """Migrate only source-authoritative constants; never rewrite cached prose.

    Previous models could add whitespace or change a punctuation-only entry. Its
    current original is the complete answer, so restoring it requires no new
    inference. Unknown/missing IDs and every alphabetic entry still pass the full
    strict contract; this migration is used only while loading old checkpoints.
    """
    if not isinstance(output, dict):
        raise ValueError("translation must be an ID-to-text object")
    normalized = dict(output)
    for key, text in immutable_source_entries(source).items():
        if key in normalized:
            normalized[key] = text
    return validate_text_map(source, normalized, partial=partial)


def parse_text_map(raw: str) -> dict[str, str]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON ID: {key}")
            result[key] = value
        return result

    data = json.loads(cleaned, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in data.items()
    ):
        raise ValueError("expected an object mapping IDs to text strings")
    return data


def validate_text_map(source: dict[str, str], output: dict[str, str], *, partial: bool = False) -> dict[str, str]:
    if not isinstance(output, dict):
        raise ValueError("translation must be an ID-to-text object")
    extra = set(output) - set(source)
    missing = set(source) - set(output) if not partial else set()
    if extra or missing:
        raise ValueError(f"ID mismatch: missing={sorted(missing)[:8]}, extra={sorted(extra)[:8]}")
    immutable = immutable_source_entries(source)
    for key, translated in output.items():
        if not isinstance(translated, str) or not translated.strip():
            raise ValueError(f"empty or non-text translation for {key}")
        if FOOTNOTE_MARKER.findall(source[key]) != FOOTNOTE_MARKER.findall(translated):
            raise ValueError(f"footnote markers changed in {key}; copy every [[FN:id]] exactly")
        if key in immutable and translated != source[key]:
            raise ValueError(f"nonlinguistic source changed in {key}; preserve it verbatim")
    return output


def source_entries(book: dict) -> dict[str, str]:
    entries: list[tuple[str, str]] = []
    if book.get("title"):
        entries.append(("book-title", book["title"]))
    for chapter in book["chapters"]:
        if chapter.get("heading"):
            entries.append((f"{chapter['id']}-h000", chapter["heading"]))
        entries.extend((p["id"], p["text"]) for p in chapter["paragraphs"])
    entries.extend((note["id"], note["text"]) for note in book.get("footnotes", []))
    result: dict[str, str] = {}
    for key, value in entries:
        if key in result:
            raise ValueError(f"duplicate source ID: {key}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"empty or non-text source for {key}")
        result[key] = value
    notes = {note["id"] for note in book.get("footnotes", [])}
    for key, text in result.items():
        unknown = set(FOOTNOTE_MARKER.findall(text)) - notes
        if unknown:
            raise ValueError(f"unresolved footnotes in {key}: {sorted(unknown)}")
    return result
