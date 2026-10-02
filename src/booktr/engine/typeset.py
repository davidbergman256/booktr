"""Fáze 5 — sazba: final.json → Typst → PDF na ploše.

Typst binárka je přibalená v .exe (PyInstaller), případně se vezme z PATH.
"""
from __future__ import annotations

import logging
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..errors import BookTrError
from .document import NOTE_TOKEN

log = logging.getLogger("booktr.typeset")

_TYPST_SPECIALS = "\\#$*_`[]<>@~=/"


def escape_typst(text: str) -> str:
    out = []
    for c in text:
        out.append("\\" + c if c in _TYPST_SPECIALS else c)
    return "".join(out)


def find_typst() -> Path | None:
    """Cesta k typst binárce; None = použije se python balíček `typst` (vývoj)."""
    # 1) přibalená binárka vedle .exe (PyInstaller onefile rozbaluje do _MEIPASS)
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "typst" / ("typst.exe" if sys.platform == "win32" else "typst")
        if candidate.is_file():
            return candidate
    # 2) PATH (vývoj)
    on_path = shutil.which("typst")
    if on_path:
        return Path(on_path)
    return None


def compile_typ(typ_file: Path, out_pdf: Path) -> None:
    typst_bin = find_typst()
    if typst_bin is not None:
        proc = subprocess.run(
            [str(typst_bin), "compile", str(typ_file), str(out_pdf)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0 or not out_pdf.is_file():
            log.error("typst failed: %s", proc.stderr[-2000:])
            raise BookTrError("typeset", proc.stderr[-500:])
        return
    try:  # vývojový fallback: python balíček typst
        import typst

        typst.compile(str(typ_file), output=str(out_pdf))
    except ImportError as exc:
        raise BookTrError("typeset", "typst binary not found") from exc
    except Exception as exc:  # noqa: BLE001
        log.error("typst (python) failed: %s", exc)
        raise BookTrError("typeset", str(exc)) from exc


def render_typ(book: dict, final: dict, preamble: str) -> str:
    parts = [preamble, ""]
    notes = {note["id"]: note for note in book.get("footnotes", [])}
    used_notes: set[str] = set()
    shown_images: set[str] = set()

    def content(text: str) -> str:
        result = []
        last = 0
        for match in NOTE_TOKEN.finditer(text):
            result.append(escape_typst(text[last:match.start()]))
            ident = match[1]
            if ident not in notes or ident not in final:
                raise BookTrError("typeset", f"Footnote {ident} has no translated body")
            if ident in used_notes:
                result.append(f"#footnote(<note-{ident}>)")
            else:
                result.append(f"#footnote[{escape_typst(final[ident])}]<note-{ident}>")
                used_notes.add(ident)
            last = match.end()
        result.append(escape_typst(text[last:]))
        return "".join(result)

    def image(asset: dict):
        if asset["id"] in shown_images or asset.get("role") in ("scan_background", "cover_artwork"):
            return
        shown_images.add(asset["id"])
        path = json.dumps(asset["path"], ensure_ascii=False)
        bbox = asset.get("bbox")
        sizing = "width: 100%"
        if bbox:
            source_width, source_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            width_percent = max(20, min(100, round(source_width / 420 * 100)))
            # Set only one dimension to retain intrinsic aspect ratio without
            # reserving an enormous empty box around a small illustration.
            sizing = ("height: 65%" if source_height > 0 and
                      width_percent / max(.01, source_width / source_height) > 90
                      else f"width: {width_percent}%")
        parts.append(f'#block(width: 100%, breakable: false, above: 1em, below: 1em)[#align(center)[#image({path}, {sizing})]]')
        parts.append("")

    cover = book.get("cover")
    if cover:
        path = json.dumps(cover["path"], ensure_ascii=False)
        parts.append(f'#page(margin: 0pt, numbering: none)[#image({path}, width: 100%, height: 100%, fit: "contain")]')
    title = escape_typst(final.get("book-title") or book.get("title", "Kniha"))
    parts.append(f'#page(numbering: none)[#align(center + horizon)[#text(size: 26pt, weight: "bold")[{title}]]]')
    parts.append("#counter(page).update(1)")
    assets = book.get("images", [])
    for ch in book["chapters"]:
        if ch.get("heading"):
            for asset in assets:
                if asset.get("before_heading") == f"{ch['id']}-h000":
                    image(asset)
            heading = final.get(f"{ch['id']}-h000") or ch["heading"]
            parts.append(f"= {content(heading)}")
        parts.append("")
        for p in ch["paragraphs"]:
            for asset in assets:
                if asset.get("before_paragraph") == p["id"]:
                    image(asset)
            parts.append(content(final[p["id"]]))
            parts.append("")
            for asset in assets:
                if asset.get("after_paragraph") == p["id"]:
                    image(asset)
    # Image-only pages and orphan note bodies are retained even when the source
    # PDF does not provide enough geometry to place them confidently.
    for asset in assets:
        image(asset)
    unused = [note for ident, note in notes.items() if ident not in used_notes]
    if unused:
        parts.extend(["= Poznámky", ""])
        for note in unused:
            if note["id"] not in final:
                raise BookTrError("typeset", f"Untranslated source footnote {note['id']}")
            parts.append(f"{escape_typst(note['label'])}. {escape_typst(final[note['id']])}")
            parts.append("")
    return "\n".join(parts)


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", name).strip() or "kniha"


def run(engine) -> Path:
    job, cfg = engine.job, engine.config
    engine.report("typeset", 0, 1)

    book = job.read_json("book.json")
    final = job.read_json("final.json")

    preamble_path = Path(__file__).resolve().parent.parent / "assets" / "book.typ"
    preamble = preamble_path.read_text(encoding="utf-8")
    preamble = preamble.replace("{{PAPER}}", "a5" if cfg.page_format == "a5" else "a4")
    font_size = max(12, min(24, int(getattr(cfg, "font_size", 14))))
    preamble = preamble.replace("{{FONT_SIZE}}", str(font_size))

    typ_file = job.dir / "book.typ"
    typ_file.write_text(render_typ(book, final, preamble), encoding="utf-8")

    out_tmp = job.dir / "book.pdf"
    compile_typ(typ_file, out_tmp)

    cfg.output_path.mkdir(parents=True, exist_ok=True)
    dest = cfg.output_path / f"Kniha – {safe_filename(book.get('title', 'kniha'))} (česky).pdf"
    publication_tmp = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".booktr-", suffix=".pdf", dir=dest.parent,
                                         delete=False) as pending:
            publication_tmp = Path(pending.name)
        shutil.copyfile(out_tmp, publication_tmp)
        os.replace(publication_tmp, dest)
    except PermissionError as exc:
        raise BookTrError("output_locked", str(exc)) from exc
    finally:
        if publication_tmp is not None:
            publication_tmp.unlink(missing_ok=True)

    engine.report("typeset", 1, 1)
    log.info("book written to %s", dest)
    return dest
