"""Fáze 5 — sazba: final.json → Typst → PDF na ploše.

Typst binárka je přibalená v .exe (PyInstaller), případně se vezme z PATH.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..errors import BookTrError

log = logging.getLogger("booktr.typeset")

_TYPST_SPECIALS = "\\#$*_`[]<>@~"


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
    title = escape_typst(book.get("title", "Kniha"))
    parts.append(f'#align(center + horizon)[#text(size: 26pt, weight: "bold")[{title}]]')
    parts.append("#pagebreak()")
    for ch in book["chapters"]:
        if ch.get("heading"):
            parts.append(f"= {escape_typst(ch['heading'])}")
        else:
            parts.append(f"= Kapitola {int(ch['id'][2:])}")
        parts.append("")
        for p in ch["paragraphs"]:
            parts.append(escape_typst(final[p["id"]]))
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

    typ_file = job.dir / "book.typ"
    typ_file.write_text(render_typ(book, final, preamble), encoding="utf-8")

    out_tmp = job.dir / "book.pdf"
    compile_typ(typ_file, out_tmp)

    dest = cfg.output_path / f"Kniha – {safe_filename(book.get('title', 'kniha'))} (česky).pdf"
    try:
        shutil.copyfile(out_tmp, dest)
    except PermissionError as exc:
        raise BookTrError("output_locked", str(exc)) from exc

    engine.report("typeset", 1, 1)
    log.info("book written to %s", dest)
    return dest
