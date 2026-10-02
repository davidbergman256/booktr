"""Czech audiobooks with resumable provider-specific speech synthesis."""
from .service import AudiobookService

__all__ = ["AudiobookService", "run"]


def run(engine):
    return AudiobookService(engine.job, engine.config, progress_cb=engine.report,
                            pause_event=engine.pause_event, cancel_event=getattr(engine, "cancel_event", None)).run(
        engine.job.read_json("book.json"), engine.job.read_json("final.json"))
