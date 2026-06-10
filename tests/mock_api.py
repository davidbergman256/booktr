"""Mock API: celá pipeline běží nasucho bez sítě.

Routuje podle značky na začátku system promptu ([TRANSLATE], [REVIEW], …),
takže testuje skutečné prompty a skutečné parsování enginu.
"""
from __future__ import annotations

import json


class MockApi:
    def __init__(self, review_changes: dict[str, str] | None = None):
        self.calls: list[str] = []
        self.review_changes = review_changes or {}

    def complete(self, model, system, user, images=None, json_mode=False) -> str:
        tag = system.split("]", 1)[0].lstrip("[")
        self.calls.append(tag)

        if tag == "TRANSLATE":
            paragraphs = _paragraphs_from(user, "PARAGRAPHS_JSON:")
            return json.dumps({k: f"CZ: {v}" for k, v in paragraphs.items()}, ensure_ascii=False)
        if tag == "REVIEW":
            source = _paragraphs_from(user, "SOURCE PARAGRAPHS_JSON:")
            patches = {k: v for k, v in self.review_changes.items() if k in source}
            return json.dumps(patches, ensure_ascii=False)
        if tag == "STYLESHEET":
            return json.dumps({
                "synopsis": "Shrnutí kapitoly.",
                "glossary": [{"term": "Alice", "czech": "Alice (2. p. Alice)"}],
                "register": [],
            })
        if tag == "LANG":
            return "en"
        if tag == "OCR":
            return "Naskenovaný text.\n\nDruhý odstavec."
        return "OK"

    def call_count(self, tag: str) -> int:
        return sum(1 for c in self.calls if c == tag)


def _paragraphs_from(user: str, marker: str) -> dict[str, str]:
    blob = user.split(marker, 1)[1]
    # JSON objekt končí před případným dalším blokem (markery jsou na vlastních řádcích)
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(blob.strip())
    return obj
