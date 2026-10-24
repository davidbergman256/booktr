"""Fixed-latency scheduling benchmark. No network, credentials or paid requests.

Run: PYTHONPATH=src .venv/bin/python tests/benchmark_pipeline.py
This measures orchestration only; real provider timing is a separate benchmark.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

from booktr.config import Config
from booktr.engine import Engine, pipeline, review, translate
from booktr.state import Job
from mock_api import MockApi


class FixedLatencyApi(MockApi):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.timeline = []

    def complete(self, model, system, user, **kwargs):
        tag = system.split("]", 1)[0][1:]
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.timeline.append((tag, "start", time.monotonic()))
        try:
            time.sleep(0.025 if tag == "TRANSLATE" else 0.015)
            return super().complete(model, system, user, **kwargs)
        finally:
            with self.lock:
                self.timeline.append((tag, "end", time.monotonic()))
                self.active -= 1


def measure(book, directory, workers, overlapped):
    job = Job(directory)
    job.write_json("book.json", book)
    job.write_json("synopses.json", {"ch01": "Synthetic scheduling workload."})
    job.write_text("stylesheet.md", "Synthetic scheduling workload.")
    config = Config(openai_api_key="mock", translation_workers=workers)
    api = FixedLatencyApi()
    engine = Engine(job, api, config)
    start = time.monotonic()
    if overlapped:
        pipeline.run(engine)
    else:
        translate.run(engine)
        review.run(engine)
    elapsed = time.monotonic() - start
    first_review = min(t for tag, phase, t in api.timeline if tag == "REVIEW" and phase == "start")
    last_draft = max(t for tag, phase, t in api.timeline if tag == "TRANSLATE" and phase == "end")
    return {"seconds": round(elapsed, 3), "peak_api_requests": api.peak,
            "calls": len(api.calls), "review_overlaps_drafting": first_review < last_draft}


def main():
    book = {"title": "", "source_lang": "en", "chapters": [{
        "id": "ch01", "heading": "", "paragraphs": [
            {"id": f"p{i:04d}", "text": "word " * 100} for i in range(900)
        ],
    }]}
    with tempfile.TemporaryDirectory(prefix="booktr-scheduling-") as temp:
        legacy = measure(book, Path(temp) / "legacy", 4, False)
        current = measure(book, Path(temp) / "pipeline", 12, True)
    assert not legacy["review_overlaps_drafting"]
    assert current["review_overlaps_drafting"]
    assert current["peak_api_requests"] == 12
    assert current["calls"] == legacy["calls"]
    print(json.dumps({"source_words": 90000, "chunks": 75, "legacy": legacy, "pipeline": current,
                      "speedup": round(legacy["seconds"] / current["seconds"], 2),
                      "scope": "fixed-service-latency scheduling only; no inference benchmark"}, indent=2))


if __name__ == "__main__":
    main()
