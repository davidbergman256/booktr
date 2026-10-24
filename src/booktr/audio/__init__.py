"""Czech audiobooks with resumable provider-specific speech synthesis."""
from .service import AudiobookService
from .sources import load_saved, prepare_source, run_saved, run_source

__all__ = ["AudiobookService", "load_saved", "prepare_source", "run", "run_saved", "run_source"]


def run(engine):
    return AudiobookService(engine.job, engine.config, progress_cb=engine.report,
                            pause_event=engine.pause_event, cancel_event=getattr(engine, "cancel_event", None)).run(
        engine.job.read_json("book.json"), engine.job.read_json("final.json"))
