"""Stav a obnova: každý kousek práce se hned ukládá, pád programu nic neztratí.

Adresář úlohy: <app_dir>/jobs/<sha1 PDF>/ — artefakty fází + progress.json.
Zápisy jsou atomické (zápis do dočasného souboru a přejmenování).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

from .config import app_dir


def jobs_base_dir() -> Path:
    d = app_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class Job:
    """Jedna kniha = jedna úloha s vlastním adresářem artefaktů."""

    def __init__(self, job_dir: Path):
        self._lock = threading.RLock()
        self.dir = Path(job_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "chunks").mkdir(exist_ok=True)

    # ---- vytvoření / nalezení -------------------------------------------------
    @classmethod
    def from_pdf(cls, pdf_path: Path, base_dir: Path | None = None) -> "Job":
        pdf_path = Path(pdf_path)
        with pdf_path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha1').hexdigest()
        job = cls((base_dir or jobs_base_dir()) / digest)
        copied_digest = None
        if job.source_pdf.exists():
            with job.source_pdf.open('rb') as stream:
                copied_digest = hashlib.file_digest(stream, 'sha1').hexdigest()
        if copied_digest != digest:
            temporary = job.dir / f'.source-{uuid.uuid4().hex}.tmp'
            try:
                shutil.copyfile(pdf_path, temporary)
                with temporary.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha1').hexdigest() != digest:
                        raise OSError('Source PDF changed while copying')
                os.replace(temporary, job.source_pdf)
            finally:
                temporary.unlink(missing_ok=True)
        if not job.progress():
            job.set_progress(title=pdf_path.stem, status="new", stage="", cur=0, tot=0)
        return job

    @property
    def source_pdf(self) -> Path:
        return self.dir / "source.pdf"

    # ---- artefakty fází -------------------------------------------------------
    def exists(self, name: str) -> bool:
        return (self.dir / name).is_file()

    def read_json(self, name: str):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def write_json(self, name: str, data) -> None:
        atomic_write_text(self.dir / name, json.dumps(data, ensure_ascii=False, indent=1))

    def read_text(self, name: str) -> str:
        return (self.dir / name).read_text(encoding="utf-8")

    def write_text(self, name: str, text: str) -> None:
        atomic_write_text(self.dir / name, text)

    # ---- dílčí kousky (checkpoint po každém chunku) ---------------------------
    def _chunk_path(self, stage: str, key: str) -> Path:
        if not stage.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid checkpoint stage")
        safe = key.replace("/", "_").replace("\\", "_")
        return self.dir / "chunks" / f"{stage}-{safe}.json"

    def ensure_fingerprint(self, scope: str, signature: str, artifacts: list[str], chunk_stages: list[str]) -> bool:
        """Discard only incompatible derived data, including legacy unversioned work."""
        with self._lock:
            try:
                fingerprints = self.read_json("fingerprints.json") if self.exists("fingerprints.json") else {}
                if not isinstance(fingerprints, dict):
                    fingerprints = {}
            except (OSError, ValueError):
                fingerprints = {}
            if fingerprints.get(scope) == signature:
                return True
            for name in artifacts:
                path = self.dir / name
                if path.parent != self.dir or name == "source.pdf":
                    raise ValueError("invalid derived artifact")
                path.unlink(missing_ok=True)
            for stage in chunk_stages:
                self._chunk_path(stage, "validate")
                for path in (self.dir / "chunks").glob(f"{stage}-*.json"):
                    path.unlink()
            fingerprints[scope] = signature
            self.write_json("fingerprints.json", fingerprints)
            return False

    def has_chunk(self, stage: str, key: str) -> bool:
        return self._chunk_path(stage, key).is_file()

    def load_chunk(self, stage: str, key: str):
        return json.loads(self._chunk_path(stage, key).read_text(encoding="utf-8"))

    def save_chunk(self, stage: str, key: str, data) -> None:
        atomic_write_text(self._chunk_path(stage, key), json.dumps(data, ensure_ascii=False))

    # ---- průběh ---------------------------------------------------------------
    def progress(self) -> dict:
        try:
            return self.read_json("progress.json")
        except (OSError, ValueError):
            return {}

    def set_progress(self, **fields) -> None:
        with self._lock:
            data = self.progress()
            data.update(fields)
            self.write_json("progress.json", data)

    @property
    def title(self) -> str:
        return self.progress().get("title", "kniha")


def list_unfinished(base_dir: Path | None = None) -> list[Job]:
    """Úlohy, které byly rozpracované (nabídnou se k pokračování)."""
    base = base_dir or jobs_base_dir()
    found = []
    for d in sorted(base.iterdir() if base.is_dir() else []):
        if not d.is_dir():
            continue
        job = Job(d)
        status = job.progress().get("status")
        if status == "running" and job.source_pdf.exists():
            found.append(job)
    return found
