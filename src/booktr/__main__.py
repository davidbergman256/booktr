"""Spuštění: bez argumentů GUI; `--headless kniha.pdf` pro vývoj a testy."""
from __future__ import annotations

import argparse
import sys

from .config import setup_logging


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="booktr")
    parser.add_argument("--headless", metavar="PDF", help="přeloží knihu bez GUI")
    args = parser.parse_args()

    if args.headless:
        from pathlib import Path

        from .api import ApiClient
        from .config import load_config
        from .engine import Engine
        from .state import Job

        cfg = load_config()
        job = Job.from_pdf(Path(args.headless))
        api = ApiClient(cfg,
                        on_offline=lambda: print("! offline — čekám na připojení"),
                        on_online=lambda: print("! online — pokračuji"))
        engine = Engine(job, api, cfg,
                        progress_cb=lambda st, c, t: print(f"  {st}: {c}/{t}"))
        output = engine.run()
        print(f"Hotovo: {output}")
        return 0

    from .gui import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
