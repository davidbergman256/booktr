"""Sentence-aware speech chunks, bounded by UTF-8 bytes as well as characters."""
from __future__ import annotations

import re


def split_text(text: str, *, max_chars: int | None = None, max_bytes: int | None = None) -> list[str]:
    """Keep every spoken character; prefer sentence boundaries to abrupt splits.

    Google counts UTF-8 bytes, whereas ElevenLabs counts Unicode characters.
    A long unbroken word is the only case that must be split inside a word.
    Whitespace is normalized, as it has no semantic effect on audiobook speech.
    """
    if max_chars is None and max_bytes is None:
        raise ValueError("At least one speech chunk limit is required")
    if max_chars is not None and max_chars < 1 or max_bytes is not None and max_bytes < 4:
        raise ValueError("Speech chunk limits must accommodate a Unicode character")
    text = " ".join(str(text).split())
    position = 0
    chunks = []
    while position < len(text):
        cut, used_bytes = 0, 0
        # At most max_bytes Unicode characters fit in max_bytes UTF-8 bytes.
        # Slice only the next candidate, avoiding quadratic copying of the book.
        ceiling = min(limit for limit in (max_chars, max_bytes) if limit is not None)
        candidate = text[position:position + ceiling]
        for char in candidate:
            next_bytes = used_bytes + len(char.encode("utf-8"))
            if max_chars is not None and cut >= max_chars:
                break
            if max_bytes is not None and next_bytes > max_bytes:
                break
            cut += 1
            used_bytes = next_bytes
        if position + cut < len(text):
            prefix = candidate[:cut]
            sentences = list(re.finditer(r"[.!?…][\"'”’»)]?\s+", prefix))
            if sentences:
                cut = sentences[-1].end()
            else:
                last_space = prefix.rfind(" ")
                if last_space > 0:
                    cut = last_space
        chunk = candidate[:cut].strip()
        if not chunk:
            raise ValueError("Speech chunk limit is too small")
        chunks.append(chunk)
        position += cut
        while position < len(text) and text[position].isspace():
            position += 1
    return chunks
