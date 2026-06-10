"""End-to-end kouřový test: obrázkové PDF (sken) → mock API → hotové české PDF.

Spuštění: .venv/bin/python tests/smoke_e2e.py
Ověřuje celý Engine včetně OCR větve, checkpointů a sazby. Síť se nepoužívá.
"""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from PIL import Image, ImageDraw

from booktr.config import Config
from booktr.engine import Engine
from booktr.state import Job
from mock_api import MockApi


def make_scanned_pdf(path: Path, pages: int = 3) -> None:
    """PDF složené jen z obrázků = žádná textová vrstva, vynutí OCR větev."""
    images = []
    for i in range(pages):
        img = Image.new("1", (1240, 1754), 1)  # bilevel jako skutečný sken
        draw = ImageDraw.Draw(img)
        draw.text((100, 100), f"Page {i + 1} - image only", fill=0)
        images.append(img)
    images[0].save(path, save_all=True, append_images=images[1:])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="booktr-smoke-"))
    pdf = tmp / "Moje kniha.pdf"
    make_scanned_pdf(pdf)

    cfg = Config(openai_api_key="mock", helper_name="David", output_dir=str(tmp))
    job = Job.from_pdf(pdf, base_dir=tmp / "jobs")
    api = MockApi(review_changes={"ch01-p001": "Revidovaný první odstavec."})

    pause = threading.Event()
    pause.set()
    engine = Engine(job, api, cfg,
                    progress_cb=lambda st, c, t: print(f"  {st}: {c}/{t}"),
                    pause_event=pause)
    output = engine.run()

    assert output.is_file() and output.stat().st_size > 1000, "PDF nevzniklo"
    assert output.name == "Kniha – Moje kniha (česky).pdf", output.name
    assert api.call_count("OCR") == 3, "OCR mělo běžet pro každou stránku"
    final = job.read_json("final.json")
    assert final["ch01-p001"] == "Revidovaný první odstavec.", "patch z revize se nepropsal"
    assert job.progress()["status"] == "done"

    # druhý běh: vše hotové → žádná nová API volání, jen re-sazba
    calls_before = len(api.calls)
    engine2 = Engine(job, api, cfg, pause_event=pause)
    engine2.run()
    assert len(api.calls) == calls_before, "obnova nesmí volat API znovu"

    print(f"\nOK — kniha: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
