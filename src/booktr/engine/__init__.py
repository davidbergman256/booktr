"""Resumable document pipeline with bounded concurrency and measured stages."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..errors import BookTrError, categorize
from .checkpoints import fingerprint, model_settings

log = logging.getLogger("booktr.engine")
MAX_WORKERS = 12
STAGES: list[tuple[str, str | None]] = [
    ("ingest", "pages.json"),
    ("segment", "book.json"),
    ("stylesheet", "synopses.json"),
    ("translate", "patches.json"),
    ("merge", "final.json"),
    ("typeset", None),
]


class Engine:
    def __init__(self, job, api, config, progress_cb=None, pause_event: threading.Event | None = None,
                 cancel_event: threading.Event | None = None):
        self.job = job
        self.api = api
        self.config = config
        self._progress_cb = progress_cb or (lambda stage, cur, tot: None)
        self.pause_event = pause_event if pause_event is not None else threading.Event()
        if pause_event is None:
            self.pause_event.set()
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()

    def report(self, stage: str, cur: int, tot: int) -> None:
        self.job.set_progress(stage=stage, cur=cur, tot=tot, status="running")
        self._progress_cb(stage, cur, tot)

    def checkpoint_wait(self) -> None:
        while True:
            if self.cancel_event.is_set():
                raise BookTrError("cancelled", "Book processing cancelled")
            if self.pause_event.wait(timeout=0.2):
                return

    def run_chunks(self, stage: str, items: list[tuple[str, object]], work, max_workers: int | None = None,
                   validate=None, normalize=None) -> dict:
        """Concurrent API-only work, ordered results and durable per-item commits.

        PDF page parsing/rendering stays outside this pool; PDFium is not thread
        safe. An item may itself draft then review, sharing this concurrency cap.
        """
        results: dict[str, object] = {}
        self.checkpoint_wait()
        todo: list[tuple[str, object]] = []
        for key, item in items:
            if self.job.has_chunk(stage, key):
                try:
                    cached = self.job.load_chunk(stage, key)
                    original = cached
                    if normalize:
                        cached = normalize(item, cached)
                    if validate:
                        validate(item, cached)
                    if cached != original:
                        self.job.save_chunk(stage, key, cached)
                    results[key] = cached
                    continue
                except (OSError, ValueError, TypeError, KeyError):
                    log.warning("ignoring unreadable checkpoint %s/%s", stage, key)
            todo.append((key, item))
        total = len(items)
        done = len(results)
        self.report(stage, done, total)
        workers = max_workers if max_workers is not None else getattr(self.config, "translation_workers", MAX_WORKERS)
        workers = max(1, min(32, int(workers)))

        def one(key: str, item: object):
            self.checkpoint_wait()
            data = work(item)
            if validate:
                validate(item, data)
            self.job.save_chunk(stage, key, data)
            return key, data

        if todo:
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"booktr-{stage}")
            futures = [pool.submit(one, key, item) for key, item in todo]
            try:
                for future in as_completed(futures):
                    key, data = future.result()
                    results[key] = data
                    done += 1
                    self.report(stage, done, total)
            finally:
                for future in futures:
                    future.cancel()
                # Running requests finish and checkpoint; queued requests are not
                # started after a failure. Resuming keeps every completed result.
                pool.shutdown(wait=True, cancel_futures=True)
        return {key: results[key] for key, _ in items}

    def _prepare_extraction(self, ingest, segment) -> None:
        from .document import SCHEMA_VERSION

        with self.job.source_pdf.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        signature = fingerprint(digest, SCHEMA_VERSION, ingest.EXTRACTION_VERSION,
                                ingest.OCR_SYSTEM, ingest.ANCHOR_SYSTEM, segment.LANG_SYSTEM,
                                model_settings(self.config, self.config.model_draft))
        artifacts = ["pages.json", "book.json", "layout.json", "extraction-report.json", "profiles.json", "stylesheet.md",
                     "synopses.json", "draft.json", "patches.json", "final.json"]
        # Source profiles and translations hash their complete inputs and are
        # validated on every load. Extraction changes may leave native prose
        # identical; retain that paid work while rebuilding document artifacts.
        # OCR keys are page indexes, so their extraction/model epoch must reset.
        chunk_stages = ["ingest"]
        self.job.ensure_fingerprint("extraction", signature, artifacts, chunk_stages)
        if self.job.exists("pages.json") and not ingest.artifacts_valid(self.job):
            log.warning("extraction references missing/corrupt assets; rebuilding document")
            # Force invalidation through the same guarded state interface, then
            # restore the content fingerprint before extraction. This also makes
            # interruption during repair safely resumable without an extra redo.
            self.job.ensure_fingerprint("extraction", signature + "-repair", artifacts, chunk_stages)
            self.job.ensure_fingerprint("extraction", signature, artifacts, chunk_stages)

    def _prepare_translation(self, stylesheet, translate, review) -> None:
        from .validation import CONTRACT_VERSION

        signature = fingerprint(
            self.job.read_json("book.json"), CONTRACT_VERSION, stylesheet.SYSTEM, stylesheet.REDUCE_SYSTEM,
            stylesheet.reduction_signature(),
            translate.SYSTEM_TMPL, review.SYSTEM_TMPL, review.HEADINGS_SYSTEM,
            model_settings(self.config, self.config.model_draft),
            model_settings(self.config, self.config.model_review),
            getattr(self.config, "chunk_words", translate.MAX_CHUNK_WORDS),
        )
        self.job.ensure_fingerprint(
            "translation", signature,
            ["profiles.json", "stylesheet.md", "synopses.json", "draft.json", "patches.json", "final.json"],
            [],  # Per-item keys include their complete input, prompt and model;
                 # retaining them preserves independent paid work across changes.
        )

    def run(self) -> Path:
        from . import ingest, merge, pipeline, review, segment, stylesheet, translate, typeset

        modules = {"ingest": ingest, "segment": segment, "stylesheet": stylesheet,
                   "translate": pipeline, "merge": merge, "typeset": typeset}
        try:
            self.job.set_progress(status="running")
            self._prepare_extraction(ingest, segment)
            output: Path | None = None
            timings = {}
            for stage, artifact in STAGES:
                self.checkpoint_wait()
                if stage == "stylesheet":
                    self._prepare_translation(stylesheet, translate, review)
                # Both output maps are needed to resume the overlapped stage.
                complete = artifact and self.job.exists(artifact)
                if stage == "translate":
                    complete = complete and self.job.exists("draft.json")
                if stage == "stylesheet":
                    complete = complete and self.job.exists("stylesheet.md")
                if complete:
                    log.info("stage %s resumed from validated fingerprint", stage)
                    self.report(stage, 1, 1)
                    continue
                start = time.monotonic()
                log.info("stage %s start", stage)
                result = modules[stage].run(self)
                timings[stage] = round(time.monotonic() - start, 3)
                if stage == "typeset":
                    output = result
                log.info("stage %s done in %.2fs", stage, timings[stage])
                self.job.write_json("timings.json", timings)
            if output is None:
                raise BookTrError("unknown", "Typesetting produced no output")
            self.job.set_progress(status="done", output=str(output))
            return output
        except BookTrError:
            raise
        except Exception as exc:
            log.exception("engine failed")
            raise categorize(exc) from exc
