"""Prepare narration from reviewed translations or explicitly Czech source text.

The saved-book path reads the exact reviewed text map. Imported Czech books have
an independent job and copy their source text unchanged; only scanned PDF body
pages may need vision transcription. Neither path detects or translates language.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..errors import BookTrError
from ..engine import Engine
from ..engine.checkpoints import fingerprint, model_settings
from ..engine.validation import parse_text_map, source_entries, validate_text_map
from .service import AudiobookService

_SOURCE_VERSION = 2
_IMPORT_KINDS = {"czech_pdf", "czech_text"}


def _validated_source(book: object, final: object) -> tuple[dict, dict[str, str]]:
    """Fail before billing if any paragraph, heading or author note is missing."""
    try:
        if not isinstance(book, dict) or not isinstance(book.get("chapters"), list):
            raise ValueError("missing chapter structure")
        chapter_ids = [chapter["id"] for chapter in book["chapters"]]
        if any(not isinstance(ident, str) or not ident for ident in chapter_ids):
            raise ValueError("invalid chapter ID")
        if len(chapter_ids) != len(set(chapter_ids)):
            raise ValueError("duplicate chapter ID")
        originals = source_entries(book)
        validate_text_map(originals, final)
        if not any(chapter.get("heading") or chapter.get("paragraphs") for chapter in book["chapters"]):
            raise ValueError("No text available for narration")
        return book, final
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise BookTrError("bad_pdf", f"Audiobook requires a complete source text map: {exc}") from exc


def load_saved(job) -> tuple[dict, dict[str, str]]:
    """Load a reviewed translation without changing or regenerating its text."""
    try:
        book = job.read_json("book.json")
        final = parse_text_map(job.read_text("final.json"))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BookTrError("bad_pdf", "Audiobook requires a complete saved translation") from exc
    return _validated_source(book, final)


class _LazyOcrApi:
    """Create and validate the OpenAI client only if PDF transcription needs it."""
    def __init__(self, config, cancel_event, api=None, before_ocr=None):
        self.config, self.cancel_event, self._api = config, cancel_event, api
        self.before_ocr = before_ocr
        self._lock = threading.Lock()

    def complete(self, *args, **kwargs):
        if not kwargs.get("images"):
            raise BookTrError("unknown", "Narration source preparation only permits image transcription")
        if self.before_ocr is not None:
            self.before_ocr()
        with self._lock:
            if self._api is None:
                from ..api import ApiClient

                self.config.require_translation()
                self._api = ApiClient(self.config, cancel_event=self.cancel_event)
            api = self._api
        return api.complete(*args, **kwargs)


class _ExtractionContext:
    """Expose extraction primitives without entering the translation workflow."""
    run_chunks = Engine.run_chunks
    narration_only = True

    def __init__(self, job, config, progress_cb, pause_event, cancel_event, api, before_ocr):
        self.job, self.config = job, config
        self._progress_cb = progress_cb or (lambda stage, cur, total: None)
        self.pause_event = pause_event if pause_event is not None else threading.Event()
        if pause_event is None:
            self.pause_event.set()
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self.api = _LazyOcrApi(config, self.cancel_event, api, before_ocr)

    def checkpoint_wait(self):
        while True:
            if self.cancel_event.is_set():
                raise BookTrError("cancelled", "Narration source preparation cancelled")
            if self.pause_event.wait(.2):
                return

    def report(self, stage, cur, total):
        self.job.set_progress(stage=stage, cur=cur, tot=total, status="running")
        self._progress_cb(stage, cur, total)


def _import_signature(job, config, kind):
    from ..engine import ingest
    from ..engine.document import SCHEMA_VERSION

    source = job.source_pdf if kind == "czech_pdf" else job.dir / "source.txt"
    try:
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise BookTrError("bad_pdf", "Audiobook source file is unavailable") from exc
    settings = [SCHEMA_VERSION, ingest.EXTRACTION_VERSION, ingest.OCR_SYSTEM, ingest.ANCHOR_SYSTEM,
                model_settings(config, config.model_draft)] if kind == "czech_pdf" else []
    return fingerprint("narration-source", _SOURCE_VERSION, kind, digest, job.title, *settings)


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        return data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise BookTrError("bad_pdf", "Czech text must use UTF-8 or UTF-16 encoding") from exc
    except OSError as exc:
        raise BookTrError("bad_pdf", "Audiobook source text is unavailable") from exc


def _import_owned(job) -> bool:
    try:
        return bool(job.read_json("fingerprints.json").get("narration-source"))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def prepare_source(job, config, progress_cb=None, pause_event=None, cancel_event=None, *, api=None, before_ocr=None):
    """Copy an explicitly Czech PDF/TXT into a validated narration source map.

    Caller creates a separate imported job and records ``audio_source_kind`` as
    ``czech_pdf`` or ``czech_text``. Native PDF extraction and all TXT imports need
    no OpenAI client or key. A scanned body page alone may request vision OCR.
    """
    from ..engine import ingest, segment

    kind = job.progress().get("audio_source_kind")
    if kind not in _IMPORT_KINDS:
        raise BookTrError("bad_pdf", "Choose an explicit Czech PDF or text import in its own narration job")
    # A source-kind toggle cannot turn an existing translation into an import.
    # The fingerprint is written before document artifacts, so a crash between
    # their writes still leaves an authoritative import ownership record.
    if (job.exists("book.json") or job.exists("final.json")) and not _import_owned(job):
        raise BookTrError("bad_pdf", "Choose an explicit Czech import in its own narration job")
    context = _ExtractionContext(job, config, progress_cb, pause_event, cancel_event, api, before_ocr)
    context.checkpoint_wait()
    signature = _import_signature(job, config, kind)
    artifacts = ["book.json", "final.json", "pages.json", "extraction-report.json", "audio-source.json"]
    job.ensure_fingerprint("narration-source", signature, artifacts, ["ingest"])
    if job.exists("book.json") and job.exists("final.json") and (
        kind != "czech_pdf" or ingest.artifacts_valid(job)
    ):
        try:
            book, final = load_saved(job)
            metadata = job.read_json("audio-source.json")
            if final != source_entries(book) or metadata != {
                "version": _SOURCE_VERSION, "kind": kind, "fingerprint": signature,
                "text_fingerprint": fingerprint(book, final),
            }:
                raise ValueError("Changed narration source checkpoint")
            return book, final
        except (BookTrError, OSError, ValueError, TypeError, KeyError):
            # Rebuild only the independent import; completed speech checkpoints
            # remain valid because their keys include every spoken character.
            pass
    if kind == "czech_pdf":
        pages = ingest.run(context)
    else:
        context.report("ingest", 0, 1)
        text = _read_text(job.dir / "source.txt")
        if not text.strip():
            raise BookTrError("bad_pdf", "No text available for narration")
        pages = [text]
        context.report("ingest", 1, 1)
    context.checkpoint_wait()
    context.report("segment", 0, 1)
    # Explicit import is the user's language choice. Calling segment.run would
    # detect language, so use only its deterministic document constructor.
    book = segment.build_book(pages, title=job.title, source_lang="cs")
    final = source_entries(book)
    _validated_source(book, final)
    job.write_json("book.json", book)
    job.write_json("final.json", final)
    job.write_json("audio-source.json", {"version": _SOURCE_VERSION, "kind": kind, "fingerprint": signature,
                                         "text_fingerprint": fingerprint(book, final)})
    context.report("segment", 1, 1)
    return book, final


def _saved_import_kind(job, book: dict, final: dict[str, str]) -> str | None:
    """Retain original imported media only when its source record is canonical."""
    try:
        metadata = job.read_json("audio-source.json")
        kind = metadata["kind"]
        signature = job.read_json("fingerprints.json")["narration-source"]
        if kind not in _IMPORT_KINDS or not isinstance(signature, str) or not signature:
            return None
        if book.get("source_lang") != "cs" or final != source_entries(book):
            return None
        if metadata == {
            "version": _SOURCE_VERSION, "kind": kind, "fingerprint": signature,
            "text_fingerprint": fingerprint(book, final),
        }:
            return kind
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return None


def run_saved(job, config, progress_cb=None, pause_event=None, cancel_event=None, *, provider=None):
    """Narrate completed Czech text, retaining its translation or import origin."""
    book, final = load_saved(job)
    kind = _saved_import_kind(job, book, final)
    if _import_owned(job) and kind is None:
        raise BookTrError("bad_pdf", "Saved narration text no longer matches its original Czech source; "
                          "import the original Czech source again")
    job.set_progress(output_mode="audio_saved", audio_source_kind=kind or "translation", status="running")
    return AudiobookService(job, config, progress_cb=progress_cb, pause_event=pause_event,
                            cancel_event=cancel_event, provider=provider, output_mode="audio_saved").run(book, final)


class _SpeechPreflight:
    """Share one account check and provider across concurrent OCR and narration."""
    def __init__(self, config, cancel_event, provider):
        self.config, self.cancel_event, self._provider = config, cancel_event, provider
        self.owns_provider = provider is None
        self.validated = False
        self._failure = None
        self._lock = threading.RLock()

    def provider(self):
        with self._lock:
            if self._provider is None:
                from .service import make_provider
                self._provider = make_provider(self.config, cancel_event=self.cancel_event)
            return self._provider

    def validate(self):
        with self._lock:
            if self._failure is not None:
                raise self._failure
            if not self.validated:
                try:
                    self.provider().validate()
                except Exception as exc:
                    self._failure = exc
                    raise
                self.validated = True

    def close(self):
        if self.owns_provider and self._provider is not None:
            self._provider.close()


def run_source(job, config, progress_cb=None, pause_event=None, cancel_event=None, *, api=None, provider=None):
    """Narrate Czech source; verify speech access before any paid scan OCR."""
    job.set_progress(output_mode="audio_only", status="running")
    preflight = _SpeechPreflight(config, cancel_event, provider)
    try:
        book, final = prepare_source(job, config, progress_cb, pause_event, cancel_event,
                                     api=api, before_ocr=preflight.validate)
        return AudiobookService(job, config, progress_cb=progress_cb, pause_event=pause_event,
                                cancel_event=cancel_event, provider=preflight.provider(),
                                provider_validated=preflight.validated, output_mode="audio_only").run(book, final)
    finally:
        preflight.close()
