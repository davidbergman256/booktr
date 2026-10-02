"""GUI, headless translation, or narration of an existing Czech book."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import setup_logging


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(prog="booktr")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--headless", metavar="PDF", help="přeloží knihu bez GUI")
    modes.add_argument("--audiobook-only", metavar="SOURCE", help="namluví hotový český překlad, české PDF nebo TXT bez překládání")
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
    if (args.audiobook or args.font_size) and not args.headless:
        parser.error("--audiobook a --font-size vyžadují --headless PDF")
    if args.voice_mode and not (args.headless or args.audiobook_only):
        parser.error("--voice-mode vyžaduje --headless PDF nebo --audiobook-only SOURCE")

    if args.audiobook_only:
        from .audio import run_saved, run_source
        from .config import load_config
        from .state import Job, jobs_base_dir

        source = Path(args.audiobook_only).expanduser()
        if not source.is_dir() and not source.is_file():
            parser.error("zdroj audioknihy neexistuje")
        if source.is_file() and source.suffix.lower() not in (".pdf", ".txt"):
            parser.error("--audiobook-only přijímá české PDF, TXT nebo adresář hotového překladu")
        cfg = load_config(validate=False)
        if args.voice_mode:
            cfg.voice_mode = args.voice_mode
        cfg.__post_init__()
        progress = lambda stage, current, total: print(f"  {stage}: {current}/{total}")
        if source.is_dir():
            output = run_saved(Job(source), cfg, progress_cb=progress)
        else:
            narration_dir = jobs_base_dir() / "narration"
            if source.suffix.lower() == ".pdf":
                job = Job.from_pdf(source, base_dir=narration_dir)
                source_kind = "czech_pdf"
            else:
                job = Job.from_text(source, base_dir=narration_dir)
                source_kind = "czech_text"
            job.set_progress(output_mode="audio_only", audio_source_kind=source_kind)
            output = run_source(job, cfg, progress_cb=progress)
        print(f"Audiokniha: {output}")
        return 0

    if args.headless:
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
            from .audio import run_saved
            job.set_progress(output_mode="audio_saved", status="running")
            output = run_saved(job, cfg, progress_cb=engine.report,
                               pause_event=engine.pause_event, cancel_event=engine.cancel_event)
            print(f"Audiokniha: {output}")
        return 0

    from .gui import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
