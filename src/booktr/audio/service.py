"""Resumable parallel narration and streaming, atomic audiobook assembly."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from ..errors import BookTrError
from ..engine.validation import FOOTNOTE_MARKER
from .chunking import split_text
from .providers import PcmAudio, make_provider

log = logging.getLogger("booktr.audio")
_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class SpeechChunk:
    index: int
    chapter_id: str
    chapter_title: str
    text: str
    previous_text: str = ""
    future_text: str = ""


def _temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.stem}-", suffix=destination.suffix,
                                dir=destination.parent)
    os.close(fd)
    return Path(name)


def _write_json(destination: Path, data):
    temporary = _temporary_path(destination)
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_wav(destination: Path, audio: PcmAudio):
    temporary = _temporary_path(destination)
    try:
        with wave.open(str(temporary), "wb") as writer:
            writer.setnchannels(audio.channels)
            writer.setsampwidth(audio.sample_width)
            writer.setframerate(audio.sample_rate)
            writer.writeframes(audio.frames)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _wav_info(path: Path) -> tuple[tuple[int, int, int], int]:
    with wave.open(str(path), "rb") as reader:
        info = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate())
        count = reader.getnframes()
        if count < 1 or reader.getcomptype() != "NONE":
            raise ValueError("Empty or compressed WAV checkpoint")
        # A valid header can still describe a truncated file after a failed copy.
        reader.setpos(count - 1)
        if len(reader.readframes(1)) != info[0] * info[1]:
            raise ValueError("Truncated WAV checkpoint")
        return info, count


def stitch_wav(sources: list[Path], destination: Path) -> list[dict]:
    """Copy PCM frames in order, without loading the entire audiobook in RAM."""
    if not sources:
        raise ValueError("No speech chunks to assemble")
    formats_and_lengths = [_wav_info(path) for path in sources]
    pcm_format = formats_and_lengths[0][0]
    if any(item[0] != pcm_format for item in formats_and_lengths):
        raise ValueError("Speech chunk audio format mismatch")
    channels, sample_width, rate = pcm_format
    total_frames = sum(item[1] for item in formats_and_lengths)
    if total_frames * channels * sample_width > 0xFFFFFFFF - 36:
        raise BookTrError("unknown", "Audiobook exceeds the WAV 4 GB limit; split the source book")
    temporary = _temporary_path(destination)
    offsets = []
    try:
        with wave.open(str(temporary), "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(sample_width)
            writer.setframerate(rate)
            writer.setnframes(total_frames)
            frame_offset = 0
            for path, (_, count) in zip(sources, formats_and_lengths):
                offsets.append({"start_frame": frame_offset, "frames": count,
                                "start_ms": round(1000 * frame_offset / rate),
                                "end_ms": round(1000 * (frame_offset + count) / rate)})
                with wave.open(str(path), "rb") as reader:
                    while data := reader.readframes(65536):
                        writer.writeframesraw(data)
                frame_offset += count
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return offsets


def find_ffmpeg() -> Path | None:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "ffmpeg" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        if candidate.is_file():
            return candidate
    executable = shutil.which("ffmpeg")
    if executable:
        return Path(executable)
    try:
        import imageio_ffmpeg
        executable = Path(imageio_ffmpeg.get_ffmpeg_exe())
        return executable if executable.is_file() else None
    except (ImportError, RuntimeError, OSError):
        return None


def _metadata_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", " ").replace("\r", " ").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")


def _make_m4b(wav_path: Path, manifest: dict, ffmpeg: Path, cancel_event=None) -> Path | None:
    output = wav_path.with_suffix(".m4b")
    temporary = _temporary_path(output)
    metadata = _temporary_path(wav_path.with_suffix(".ffmetadata"))
    lines = [";FFMETADATA1", f"title={_metadata_escape(manifest['title'])}"]
    for chapter in manifest["chapters"]:
        lines.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={chapter['start_ms']}",
                      f"END={chapter['end_ms']}", f"title={_metadata_escape(chapter['title'])}"])
    process = None
    try:
        metadata.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(wav_path), "-i", str(metadata), "-map_metadata", "1",
                   "-map_chapters", "1", "-c:a", "aac", "-b:a", "96k",
                   "-movflags", "+faststart", str(temporary)]
        deadline = time.monotonic() + 1800
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise BookTrError("cancelled", "Audiobook encoding cancelled")
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(command, 1800)
            try:
                process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0 or temporary.stat().st_size < 100:
            log.warning("M4B packaging failed; complete WAV audiobook remains available")
            return None
        os.replace(temporary, output)
        return output
    except (OSError, subprocess.TimeoutExpired):
        log.warning("M4B packaging unavailable; complete WAV audiobook remains available")
        return None
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        temporary.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)


def _speech_text(text: str) -> str:
    # Resolve the canonical document tokens before sending prose to a narrator.
    return re.sub(r"\[\^[^\]]+\]", "", FOOTNOTE_MARKER.sub("", str(text)))


def _plan(book: dict, final: dict, provider) -> list[SpeechChunk]:
    chunks = []
    notes = book.get("footnotes", [])
    note_order = {note["id"]: i for i, note in enumerate(notes)}
    read_notes = set()
    anchored_notes = {note_id for chapter in book.get("chapters", [])
                      for source_id in [f"{chapter['id']}-h000"] + [p["id"] for p in chapter.get("paragraphs", [])]
                      for note_id in FOOTNOTE_MARKER.findall(str(final.get(source_id, "")))}

    def note_text(note_id):
        if note_id not in note_order:
            raise BookTrError("unknown", "Audiobook has an unresolved footnote reference")
        translated = final.get(note_id)
        if not isinstance(translated, str) or not translated.strip():
            raise BookTrError("unknown", "Audiobook requires every translated footnote body")
        read_notes.add(note_id)
        return f"Poznámka: {_speech_text(translated)}"

    for chapter_number, chapter in enumerate(book.get("chapters", []), 1):
        chapter_id = chapter["id"]
        heading = final.get(f"{chapter_id}-h000") or f"Kapitola {chapter_number}"
        text_parts = [_speech_text(heading)]
        heading_notes = set(FOOTNOTE_MARKER.findall(heading)) - read_notes
        if heading_notes - set(note_order):
            raise BookTrError("unknown", "Audiobook has an unresolved heading footnote reference")
        text_parts.extend(note_text(note_id) for note_id in sorted(heading_notes, key=note_order.get))
        for paragraph in chapter.get("paragraphs", []):
            translated = final.get(paragraph["id"])
            if not isinstance(translated, str) or not translated.strip():
                raise BookTrError("unknown", "Audiobook requires a complete translated book")
            text_parts.append(_speech_text(translated))
            # Read a repeated note only once, immediately after its first owner.
            referenced = set(FOOTNOTE_MARKER.findall(translated)) - read_notes
            if referenced - set(note_order):
                raise BookTrError("unknown", "Audiobook has an unresolved footnote reference")
            text_parts.extend(note_text(note_id) for note_id in sorted(referenced, key=note_order.get))
        source_pages = {page for paragraph in chapter.get("paragraphs", [])
                        for page in paragraph.get("source_pages", [paragraph.get("source_page")])}
        for note in notes:
            if note["id"] not in read_notes and note["id"] not in anchored_notes and (
                note.get("source_page") in source_pages or chapter_number == len(book.get("chapters", []))
            ):
                text_parts.append(note_text(note["id"]))
        texts = split_text("\n\n".join(text_parts), max_chars=provider.max_chars, max_bytes=provider.max_bytes)
        for i, text in enumerate(texts):
            chunks.append(SpeechChunk(len(chunks), chapter_id, str(heading), text,
                                      texts[i - 1][-100:] if i > 0 else "",
                                      texts[i + 1][:100] if i + 1 < len(texts) else ""))
    if not chunks:
        raise BookTrError("unknown", "No translated chapters available for narration")
    return chunks


class AudiobookService:
    def __init__(self, job, config, progress_cb=None, pause_event: threading.Event | None = None, *, provider=None, cancel_event=None, output_mode="audio", provider_validated=False):
        self.job, self.config = job, config
        self.output_mode = output_mode
        self.progress_cb = progress_cb or (lambda stage, cur, tot: None)
        self.pause_event = pause_event if pause_event is not None else threading.Event()
        if pause_event is None:
            self.pause_event.set()
        self.provider = provider
        self.provider_validated = provider_validated
        self.cancel_event = cancel_event or threading.Event()
        self._stop_event = threading.Event()

    def _check_cancelled(self):
        if self.cancel_event.is_set() or self._stop_event.is_set():
            raise BookTrError("cancelled", "Audiobook generation cancelled")

    def _checkpoint_wait(self):
        self._check_cancelled()
        while not self.pause_event.wait(.2):
            self._check_cancelled()
        self._check_cancelled()

    def _report(self, cur: int, total: int):
        self.job.set_progress(stage="audio", cur=cur, tot=total, status="running")
        self.progress_cb("audio", cur, total)

    def run(self, book: dict, final: dict) -> Path:
        self._stop_event.clear()
        self._check_cancelled()
        provider = self.provider or make_provider(self.config, cancel_event=self.cancel_event)
        try:
            chunks = _plan(book, final, provider)
            fingerprint_data = {"version": _MANIFEST_VERSION, "provider": provider.identity,
                                "chunks": [chunk.__dict__ for chunk in chunks]}
            fingerprint = hashlib.sha256(json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True)
                                         .encode("utf-8")).hexdigest()
            provider_fingerprint = hashlib.sha256(json.dumps(provider.identity, sort_keys=True).encode()).hexdigest()
            checkpoint_dir = self.job.dir / "audio" / provider_fingerprint
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            paths = [checkpoint_dir / (hashlib.sha256(json.dumps({
                "text": chunk.text, "previous_text": chunk.previous_text, "future_text": chunk.future_text,
            }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() + ".wav") for chunk in chunks]
            todo = {}
            for chunk, path in zip(chunks, paths):
                try:
                    pcm_format, _ = _wav_info(path)
                    if pcm_format != (1, 2, provider.identity.get("sample_rate", provider.identity.get("rate", 24000))):
                        raise ValueError("Checkpoint audio format differs from provider settings")
                except (OSError, ValueError, EOFError, wave.Error):
                    # Identical spoken text and neighboring context can share
                    # one immutable checkpoint, even across repeated passages.
                    previous = todo.get(path)
                    todo[path] = (chunk, previous[1] + 1 if previous else 1)
            completed = len(chunks) - sum(count for _, count in todo.values())
            self._report(completed, len(chunks))
            if todo:
                if not self.provider_validated:
                    provider.validate()  # Account/voice validation before paid generation.
                workers = max(1, min(16, int(getattr(self.config, "audio_workers", 3))))
                def narrate(chunk, path):
                    self._checkpoint_wait()
                    audio = provider.synthesize(chunk.text, previous_text=chunk.previous_text,
                                                future_text=chunk.future_text)
                    _write_wav(path, audio)
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="booktr-audio") as pool:
                    futures = {pool.submit(narrate, chunk, path): count
                               for path, (chunk, count) in todo.items()}
                    try:
                        for future in as_completed(futures):
                            future.result()
                            self._check_cancelled()
                            completed += futures[future]
                            self._report(completed, len(chunks))
                    except BaseException:
                        self._stop_event.set()
                        for future in futures:
                            future.cancel()
                        raise
            self._checkpoint_wait()
            title = str(final.get("book-title") or book.get("title") or self.job.title)
            filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", title).strip(" .")[:120] or "kniha"
            destination = Path(self.config.output_path) / f"{filename} — audiokniha.wav"
            offsets = stitch_wav(paths, destination)
            chapters = []
            for chunk, offset in zip(chunks, offsets):
                if not chapters or chapters[-1]["id"] != chunk.chapter_id:
                    chapters.append({"id": chunk.chapter_id, "title": chunk.chapter_title,
                                     "start_ms": offset["start_ms"], "end_ms": offset["end_ms"]})
                else:
                    chapters[-1]["end_ms"] = offset["end_ms"]
            manifest = {"version": _MANIFEST_VERSION, "fingerprint": fingerprint, "title": title,
                        "provider": provider.identity, "audio": destination.name,
                        "duration_ms": offsets[-1]["end_ms"], "chapters": chapters}
            _write_json(destination.with_suffix(".chapters.json"), manifest)
            ffmpeg = find_ffmpeg()
            output = _make_m4b(destination, manifest, ffmpeg, self.cancel_event) if ffmpeg else None
            output = output or destination
            manifest["audio"] = output.name
            _write_json(destination.with_suffix(".chapters.json"), manifest)
            self._report(len(chunks), len(chunks))
            self.job.set_progress(status="done", output_mode=self.output_mode, audio_output=str(output), audio_fingerprint=fingerprint)
            return output
        finally:
            if self.provider is None:
                provider.close()
