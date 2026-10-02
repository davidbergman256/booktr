"""Spuštění: bez argumentů GUI; `--headless kniha.pdf` pro vývoj a testy."""
from __future__ import annotations

import argparse
import sys

from .config import setup_logging


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="booktr")
    parser.add_argument("--headless", metavar="PDF", help="přeloží knihu bez GUI")
    parser.add_argument("--audiobook", action="store_true", help="vytvoří také audioknihu (s --headless)")
    parser.add_argument("--font-size", type=int, choices=range(12, 25), help="velikost písma v PDF")
    parser.add_argument("--voice-mode", choices=("normal", "advanced"), help="hlas audioknihy")
    parser.add_argument("--config", metavar="JSON", help="soukromá konfigurace služeb")
    parser.add_argument("--self-test", action="store_true", help="ověří sazbu a audioknihu bez volání placených služeb")
    args = parser.parse_args()
    if args.config:
        import os
        os.environ["BOOKTR_CONFIG_FILE"] = args.config
    if args.self_test:
        from .selftest import run
        run()
        return 0
    if (args.audiobook or args.font_size or args.voice_mode) and not args.headless:
        parser.error("--audiobook, --font-size a --voice-mode vyžadují --headless PDF")

    if args.headless:
        from pathlib import Path

        from .api import ApiClient
        from .config import load_config
        from .engine import Engine
        from .state import Job

        cfg = load_config()
        if args.font_size:
            cfg.font_size = args.font_size
        if args.voice_mode:
            cfg.voice_mode = args.voice_mode
        cfg.__post_init__()
        if args.audiobook:
            from .audio.providers import make_provider
            provider = make_provider(cfg)
            try:
                provider.validate()
            finally:
                provider.close()
        job = Job.from_pdf(Path(args.headless))
        api = ApiClient(cfg,
                        on_offline=lambda: print("! offline — čekám na připojení"),
                        on_online=lambda: print("! online — pokračuji"))
        engine = Engine(job, api, cfg,
                        progress_cb=lambda st, c, t: print(f"  {st}: {c}/{t}"))
        output = engine.run()
        print(f"Hotovo: {output}")
        if args.audiobook:
            from .audio import run as make_audio
            job.set_progress(output_mode="audio", status="running")
            output = make_audio(engine)
            print(f"Audiokniha: {output}")
        return 0

    from .gui import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
