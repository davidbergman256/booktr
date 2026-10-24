"""Geometry-aware book interchange data, independent of translation providers.

Assets and note bodies have stable IDs. Only prose changes during translation;
note anchors remain opaque tokens until the renderer resolves them.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter

SCHEMA_VERSION = 2
NOTE_TOKEN = re.compile(r"\[\[FN:([\w.-]+)\]\]")
_NOTE_START = re.compile(r"^(\d{1,3}|[*†‡§]+)(?:[.)]?\s+|(?=[A-Za-zÀ-ž]))(.+)$")


def note_tokens(text: str) -> list[str]:
    return NOTE_TOKEN.findall(text)


def footnote_id(page: int, number: int) -> str:
    return f"fn-p{page:04d}-{number:03d}"


def _lines(chars: list[dict]) -> list[dict]:
    """Group baselines before small superscripts, preserving positioned markers."""
    lines: list[dict] = []
    for char in sorted(chars, key=lambda c: (-float(c["size"]), c["top"], c["x0"])):
        if not char.get("text") or char["text"] in ("\r", "\n", "\t"):
            continue
        size = float(char["size"])
        matches = [line for line in lines if (
            abs(char["bottom"] - line["baseline"]) <= max(1.8, size * 0.22)
            or (size < line["size"] * .85
                and char["bottom"] >= line["top"]
                and char["top"] <= line["baseline"] - line["size"] * .25)
        )]
        if matches:
            line = min(matches, key=lambda line: abs(char["bottom"] - line["baseline"]))
            line["chars"].append(char)
            line["top"] = min(line["top"], char["top"])
            line["bottom"] = max(line["bottom"], char["bottom"])
        else:
            lines.append({"chars": [char], "baseline": char["bottom"], "size": size,
                          "top": char["top"], "bottom": char["bottom"]})
    for line in lines:
        line["chars"].sort(key=lambda c: c["x0"])
        line["x0"] = min(c["x0"] for c in line["chars"])
        line["x1"] = max(c["x1"] for c in line["chars"])
    return sorted(lines, key=lambda line: (line["top"], line["x0"]))


def _line_text(line: dict, labels: dict[str, str] | None = None) -> str:
    parts: list[str] = []
    chars = line["chars"]
    previous = None
    i = 0
    while i < len(chars):
        char = chars[i]
        # A raised, smaller run may contain multiple digits (10, 11, ...).
        raised = (bool(char.get("note_id")) or char.get("note_label") in (labels or {}) or (char["size"] < line["size"] * .85
                  and char["bottom"] < line["baseline"] - line["size"] * .12)
                  )
        if raised and (labels or char.get("note_id")):
            j = i + 1
            while (j < len(chars) and (
                   (char.get("note_label") and chars[j].get("note_label") == char["note_label"])
                   or (chars[j]["size"] < line["size"] * .85
                   and chars[j]["bottom"] < line["baseline"] - line["size"] * .12))
                   and chars[j]["x0"] - chars[j - 1]["x1"] < line["size"] * .5):
                j += 1
            label = "".join(c["text"] for c in chars[i:j])
            ident = char.get("note_id") or (labels or {}).get(label)
            if ident:
                parts.append(f"[[FN:{ident}]]")
                previous = chars[j - 1]
                i = j
                continue
        if (previous and not previous["text"].isspace() and not char["text"].isspace()
                and char["x0"] - previous["x1"] > max(.7, line["size"] * .08)):
            parts.append(" ")
        parts.append(char["text"])
        previous = char
        i += 1
    text = "".join(parts).strip()
    if labels:
        for label, ident in labels.items():
            text = text.replace(f"[{label}]", f"[[FN:{ident}]]")
    return text


def _join_lines(lines: list[str]) -> str:
    text = ""
    for line in lines:
        if text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        else:
            text += (" " if text else "") + line
    return text


def _raised_runs(line: dict) -> list[tuple[int, str]]:
    """Read actual smaller, raised glyph runs, never ordinary words starting a/i."""
    runs = []
    chars = line["chars"]

    def raised(char):
        return (char["size"] < line["size"] * .85
                and char["bottom"] < line["baseline"] - line["size"] * .12)

    index = 0
    while index < len(chars):
        if not raised(chars[index]) or chars[index]["text"].isspace():
            index += 1
            continue
        end = index + 1
        while (end < len(chars) and raised(chars[end]) and not chars[end]["text"].isspace()
               and chars[end]["x0"] - chars[end - 1]["x1"] < line["size"] * .5):
            end += 1
        runs.append((index, "".join(char["text"] for char in chars[index:end])))
        index = end
    return runs


def _original_note_start(line: dict, definition: dict | None, raised_labels: set[str]) -> tuple[str, str] | None:
    text = _line_text(line)
    label = definition.get("footnote_label") if definition else None
    if label and text.startswith(label):
        body = re.sub(r"^[.)]?\s*", "", text[len(label):])
        return (label, body) if body.strip() else None
    legacy = _NOTE_START.match(text)
    if legacy:
        return legacy[1], legacy[2]
    # Alphabetic/Roman labels without reciprocal links need a raised marker both
    # in the body and at the beginning of this small note-region line.
    for start, label in _raised_runs(line):
        if start == 0 and label in raised_labels and re.fullmatch(r"[A-Za-z]{1,8}", label) and text.startswith(label):
            body = re.sub(r"^[.)]?\s*", "", text[len(label):])
            return (label, body) if body.strip() else None
    return None


def margin_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip())).casefold()


def without_running_margins(chars: list[dict], height: float,
                            repeated: set[str]) -> list[dict]:
    if not repeated:
        return chars
    lines = _lines(chars)
    sizes = Counter(round(float(c["size"]), 1) for c in chars if c.get("text", "").strip())
    body_size = sizes.most_common(1)[0][0] if sizes else 0
    omitted = set()
    for line in lines:
        in_margin = line["bottom"] < height * .085 or line["top"] > height * .94
        if in_margin and line["size"] <= body_size * 1.1 and margin_key(_line_text(line)) in repeated:
            omitted.update(id(char) for char in line["chars"])
    return [char for char in chars if id(char) not in omitted]


def extract_layout(chars: list[dict], width: float, height: float, page: int,
                   separators: list[float] | None = None,
                   annotations: list[dict] | None = None,
                   previous_note: dict | None = None) -> dict:
    """Recover paragraphs and local footnotes from digital PDF characters.

    PDF has no semantic footnote objects. Detection uses small bottom-region
    type, note labels and raised body references; all raw geometry remains in
    the ingest artifact for inspection instead of inventing missing content.
    """
    if not chars:
        return {"blocks": [], "footnotes": [], "text": "", "warnings": []}
    sizes = Counter(round(float(c["size"]), 1) for c in chars if c.get("text", "").strip())
    if not sizes:
        return {"blocks": [], "footnotes": [], "text": "", "warnings": []}
    body_size = sizes.most_common(1)[0][0]
    lines = _lines(chars)
    definitions = {}
    for annotation in annotations or []:
        if not annotation.get("footnote_id"):
            continue
        for n, line in enumerate(lines):
            if (line["top"] < annotation["bottom"] and line["bottom"] > annotation["top"]
                    and line["x0"] <= annotation["x1"] and line["x1"] >= annotation["x0"]):
                definitions[n] = annotation
                break
    first_definition = min((lines[n]["top"] for n in definitions), default=height)
    reference_bottom = min(separators or [height * .58])
    raised_labels = {label for line in lines if line["bottom"] < reference_bottom
                     for _, label in _raised_runs(line) if re.fullmatch(r"[A-Za-z]{1,8}", label)}
    candidates = [y for y in (separators or []) if y < first_definition and first_definition - y < 35]
    separator = max(candidates) if candidates else min((y for y in (separators or []) if y > height * .45), default=height)
    if not definitions:
        for y in sorted(separators or []):
            following = next((line for line in lines if line["top"] > y), None)
            if following and following["top"] - y < 25 and _original_note_start(following, None, raised_labels):
                separator = y
                break
    continuation_region = False
    if previous_note and separators:
        for y in sorted(separators):
            following = next((line for line in lines if line["top"] > y), None)
            if (following and following["top"] - y < 25 and
                    abs(following["size"] - previous_note.get("font_size", 0)) < 1.5):
                separator = min(separator, y)
                continuation_region = True
                break
    body_chars = [c for line in lines if line["bottom"] < separator for c in line["chars"]]
    if body_chars:
        body_size = Counter(round(float(c["size"]), 1) for c in body_chars).most_common(1)[0][0]
    footnotes: list[dict] = []
    note_lines: set[int] = set()
    current = None
    continuations = []
    active_continuation = None
    for n, line in enumerate(lines):
        text = _line_text(line)
        small = line["size"] < body_size * .92
        in_region = line["top"] > height * .58 or line["top"] > separator
        match = (_original_note_start(line, definitions.get(n), raised_labels)
                 if n in definitions or (in_region and (small or line["top"] > separator)) else None)
        if match:
            ident = definitions[n]["footnote_id"] if n in definitions else footnote_id(page, len(footnotes) + 1)
            current = {"id": ident, "label": match[0], "font_size": line["size"],
                       "text": match[1].strip(), "source_page": page}
            footnotes.append(current)
            note_lines.add(n)
            active_continuation = None
        elif current and in_region and abs(line["size"] - current["font_size"]) < 2 and not re.fullmatch(r"\d{1,4}", text):
            current["text"] = _join_lines([current["text"], text])
            note_lines.add(n)
        elif (continuation_region and not footnotes and line["top"] > separator
              and abs(line["size"] - previous_note.get("font_size", 0)) < 1.5
              and not re.fullmatch(r"\d{1,4}", text)):
            if active_continuation is None:
                active_continuation = {"id": previous_note["id"], "text": text, "source_page": page}
                continuations.append(active_continuation)
            else:
                active_continuation["text"] = _join_lines([active_continuation["text"], text])
            note_lines.add(n)
        else:
            current = None
    labels = {note["label"]: note["id"] for note in footnotes}
    # OpenType superscript glyphs may retain the body's nominal font size and
    # baseline in PDF. Internal PDF links identify these markers precisely.
    note_top = min((lines[n]["top"] for n in note_lines), default=height)
    for annotation in annotations or []:
        if annotation.get("uri") or annotation.get("footnote_id"):
            continue
        contents = str(annotation.get("contents") or "").strip()
        label = annotation.get("note_label") or next((label for label in labels if contents in (label, f"Footnote {label}")), None)
        if not annotation.get("note_id") and annotation["top"] >= note_top:
            continue
        if not label:
            continue
        for n, line in enumerate(lines):
            if n in note_lines:
                continue
            for char in line["chars"]:
                if (char["x0"] >= annotation["x0"] - .3 and char["x1"] <= annotation["x1"] + .3
                        and char["top"] < annotation["bottom"] and char["bottom"] > annotation["top"]):
                    char["note_label"] = label
                    if annotation.get("note_id"):
                        char["note_id"] = annotation["note_id"]
    body = [(line, _line_text(line, labels)) for n, line in enumerate(lines) if n not in note_lines]
    gaps = [b[0]["baseline"] - a[0]["baseline"] for a, b in zip(body, body[1:])
            if 0 < b[0]["baseline"] - a[0]["baseline"] < body_size * 2]
    leading = statistics.median(gaps) if gaps else body_size * 1.3
    left_margin = statistics.median(line["x0"] for line, _ in body) if body else 0
    blocks: list[dict] = []
    previous = None
    for line, text in body:
        # Only isolated page numbers in the outer margin are safe to drop.
        if re.fullmatch(r"\d{1,4}", text) and (line["top"] < height * .045 or line["bottom"] > height * .95):
            continue
        heading = line["size"] >= body_size * 1.24 and len(text) < 150
        gap = line["baseline"] - previous["baseline"] if previous else leading * 2
        indented = line["x0"] > left_margin + body_size * .65
        new = (not blocks or heading or blocks[-1]["type"] == "heading"
               or gap > leading * 1.4 or (indented and gap >= leading * .8))
        if new:
            blocks.append({"type": "heading" if heading else "paragraph", "text": text,
                           "bbox": [line["x0"], line["top"], line["x1"], line["bottom"]]})
        else:
            blocks[-1]["text"] = _join_lines([blocks[-1]["text"], text])
            blocks[-1]["bbox"][2] = max(blocks[-1]["bbox"][2], line["x1"])
            blocks[-1]["bbox"][3] = line["bottom"]
        previous = line
    text = "\n\n".join(("## " if b["type"] == "heading" else "") + b["text"] for b in blocks)
    anchored = set(note_tokens(text))
    semantic_definitions = {annotation["footnote_id"] for annotation in annotations or [] if annotation.get("footnote_id")}
    warnings = [f"Footnote {note['label']} on source page {page} has no confident body anchor."
                for note in footnotes if note["id"] not in anchored and note["id"] not in semantic_definitions]
    external = sorted({annotation["note_id"] for annotation in annotations or [] if annotation.get("note_id")})
    return {"blocks": blocks, "footnotes": footnotes, "text": text, "warnings": warnings,
            "footnote_continuations": continuations, "external_footnotes": external}
