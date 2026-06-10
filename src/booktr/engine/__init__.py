"""Orchestrátor: spouští fáze v pořadí, hotové přeskakuje, po každé ukládá stav.

Fáze jsou idempotentní — pokud jejich výstupní artefakt existuje, přeskočí se;
uvnitř fází se navíc přeskakují už hotové chunky (viz state.Job.has_chunk).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..errors import BookTrError, categorize

log = logging.getLogger("booktr.engine")

# (fáze, výstupní artefakt) — typeset nemá artefakt, výsledné PDF leží mimo job dir
STAGES: list[tuple[str, str | None]] = [
    ("ingest", "pages.json"),
    ("segment", "book.json"),
    ("stylesheet", "synopses.json"),
    ("translate", "draft.json"),
    ("review", "patches.json"),
    ("merge", "final.json"),
    ("typeset", None),
]


class Engine:
    def __init__(self, job, api, config, progress_cb=None, pause_event: threading.Event | None = None):
        self.job = job
        self.api = api
        self.config = config
        self._progress_cb = progress_cb or (lambda stage, cur, tot: None)
        # pause_event: nastavený = běžíme, smazaný = pauza
        self.pause_event = pause_event or threading.Event()
        self.pause_event.set()

    # ---- pomocné pro fáze -----------------------------------------------------
    def report(self, stage: str, cur: int, tot: int) -> None:
        self.job.set_progress(stage=stage, cur=cur, tot=tot, status="running")
        self._progress_cb(stage, cur, tot)

    def checkpoint_wait(self) -> None:
        """Mezi chunky: tady je bezpečné pauznout."""
        self.pause_event.wait()

    # ---- běh ------------------------------------------------------------------
    def run(self) -> Path:
        from . import ingest, segment, stylesheet, translate, review, merge, typeset

        modules = {
            "ingest": ingest, "segment": segment, "stylesheet": stylesheet,
            "translate": translate, "review": review, "merge": merge, "typeset": typeset,
        }
        try:
            self.job.set_progress(status="running")
            output: Path | None = None
            for stage, artifact in STAGES:
                if artifact and self.job.exists(artifact):
                    log.info("stage %s skipped (artifact exists)", stage)
                    self.report(stage, 1, 1)
                    continue
                log.info("stage %s start", stage)
                result = modules[stage].run(self)
                if stage == "typeset":
                    output = result
                log.info("stage %s done", stage)
            assert output is not None
            self.job.set_progress(status="done", output=str(output))
            return output
        except BookTrError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("engine failed")
            raise categorize(exc) from exc
