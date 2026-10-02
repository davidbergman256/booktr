"""Content-addressed stage checkpoints; stale results cannot silently resume."""
from __future__ import annotations

import hashlib
import json

PIPELINE_VERSION = "booktr-document-v2"


def fingerprint(*values) -> str:
    payload = json.dumps([PIPELINE_VERSION, *values], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def checkpoint_key(logical_key: str, *values) -> str:
    return f"{logical_key}-{fingerprint(*values)[:24]}"


def model_settings(config, model: str) -> dict:
    effort = getattr(config, "reasoning_effort", "none")
    if model == getattr(config, "model_draft", None):
        effort = getattr(config, "draft_reasoning_effort", effort)
    return {"model": model, "reasoning_effort": effort}


def cached_call(engine, stage: str, key: str, work, validate=None, normalize=None):
    job = engine.job
    if job.has_chunk(stage, key):
        try:
            result = job.load_chunk(stage, key)
            original = result
            if normalize:
                result = normalize(result)
            if validate:
                validate(result)
            if result != original:
                job.save_chunk(stage, key, result)
            return result
        except (ValueError, TypeError, KeyError, OSError):
            pass
    engine.checkpoint_wait()
    result = work()
    if validate:
        validate(result)
    job.save_chunk(stage, key, result)
    return result
