"""Measure a real complete translation in a clean job (incurs provider usage).

Usage: python scripts/benchmark_book.py source.pdf --config private.json --output-dir output
The report contains timings and counts; never source text or provider credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from booktr.api import ApiClient
from booktr.config import Config
from booktr.engine import Engine
from booktr.engine.translate import build_chunks
from booktr.state import Job


class MeasuredApi(ApiClient):
    def __init__(self, config):
        super().__init__(config)
        self.measurements = []
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.active_models = Counter()
        self.model_peaks = Counter()
        self.origin = time.monotonic()

    def _request(self, model, system, user, images, json_mode):
        started = time.monotonic()
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.active_models[model] += 1
            self.model_peaks[model] = max(self.model_peaks[model], self.active_models[model])
        status = 'success'
        error_type = None
        try:
            return super()._request(model, system, user, images, json_mode)
        except BaseException as exc:
            status = 'failed'
            error_type = type(exc).__name__
            raise
        finally:
            ended = time.monotonic()
            with self.lock:
                self.active -= 1
                self.active_models[model] -= 1
                measurement = {'model': model, 'stage': system.split(']', 1)[0].lstrip('['),
                               'seconds': round(ended - started, 3), 'status': status,
                               'started_seconds': round(started - self.origin, 3),
                               'ended_seconds': round(ended - self.origin, 3)}
                if error_type:
                    # Exception bodies can contain a request: preserve the type only.
                    measurement['error_type'] = error_type
                self.measurements.append(measurement)


def request_summary(measurements: list[dict]) -> dict:
    """Aggregate real requests, including failed attempts, without request content."""
    groups = defaultdict(list)
    for request in measurements:
        groups[request['stage']].append(request)
    result = {}
    for stage, requests in groups.items():
        seconds = sorted(request['seconds'] for request in requests)
        result[stage] = {
            'calls': len(requests),
            'failed_attempts': (sum(r['status'] == 'failed' for r in requests)
                                if all('status' in r for r in requests) else None),
            'median_seconds': round(statistics.median(seconds), 3),
            'p95_seconds': seconds[math.ceil(len(seconds) * 0.95) - 1],
            'max_seconds': seconds[-1], 'total_request_seconds': round(sum(seconds), 3),
        }
    return result


def code_fingerprint() -> str:
    """Record the exact source snapshot at start; edits cannot relabel a live run."""
    digest = hashlib.sha256()
    source_dir = Path(__file__).resolve().parents[1] / 'src' / 'booktr'
    for path in sorted(source_dir.rglob('*.py')):
        digest.update(str(path.relative_to(source_dir)).encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--title', help='Original book title when benchmarking a cached source.pdf')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw = json.loads(args.config.read_text())
    fields = Config.__dataclass_fields__
    cfg = Config(**{k:v for k,v in raw.items() if k in fields and k != 'extra'})
    cfg.output_dir = str(args.output_dir)
    cfg.require_translation()
    job = Job.from_pdf(args.pdf, base_dir=args.output_dir / 'jobs')
    if args.title:
        job.set_progress(title=args.title)
    api = MeasuredApi(cfg)
    started_utc = datetime.now(timezone.utc).isoformat()
    source_snapshot = code_fingerprint()
    with job.source_pdf.open('rb') as stream:
        source_pdf_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    initial_chunks = len(list((job.dir / 'chunks').glob('*.json')))
    last = {}
    def progress(stage, cur, total):
        if stage not in last or cur == total or cur - last[stage] >= max(1, total // 10):
            print(f'{stage}: {cur}/{total}', flush=True)
            last[stage] = cur
    engine = Engine(job, api, cfg, progress_cb=progress)
    start = time.monotonic()
    common = {'started_utc': started_utc, 'source_code_sha256': source_snapshot,
              'source_pdf_sha256': source_pdf_hash,
              'cached_chunk_files_at_start': initial_chunks,
              'model_draft': cfg.model_draft, 'model_review': cfg.model_review,
              'draft_reasoning_effort': cfg.draft_reasoning_effort, 'review_reasoning_effort': cfg.reasoning_effort,
              'workers': cfg.translation_workers, 'chunk_words': cfg.chunk_words,
              'scope': 'complete translation, source-based review and PDF; audiobook excluded'}

    def write_report(report):
        report = {**common, **report, 'completed_utc': datetime.now(timezone.utc).isoformat(),
                  'peak_requests': api.peak, 'peak_requests_by_model': dict(api.model_peaks),
                  'api_calls': len(api.measurements), 'request_summary': request_summary(api.measurements),
                  'requests': api.measurements}
        (args.output_dir / 'benchmark-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        return report

    try:
        output = engine.run()
    except BaseException as exc:
        elapsed = round(time.monotonic() - start, 3)
        write_report({'status': 'failed', 'elapsed_seconds': elapsed, 'error_type': type(exc).__name__,
                      'stage_timings': job.read_json('timings.json') if job.exists('timings.json') else {}})
        print(f'Interrupted after {elapsed:.1f}s; validated checkpoints retained.', flush=True)
        raise
    elapsed = time.monotonic() - start
    book = job.read_json('book.json')
    report = {'status': 'done', 'elapsed_seconds': round(elapsed, 3), 'source_pages': len(job.read_json('pages.json')),
              'source_words': sum(len(p['text'].split()) for ch in book['chapters'] for p in ch['paragraphs']),
              'chapters': len(book['chapters']), 'chunks': len(build_chunks(book, cfg.chunk_words)),
              'paragraphs': sum(len(ch['paragraphs']) for ch in book['chapters']),
              'footnotes': len(book.get('footnotes', [])), 'images': len(book.get('images', [])),
              'original_cover': bool(book.get('cover')), 'final_entries': len(job.read_json('final.json')),
              'stage_timings':job.read_json('timings.json'), 'output':output.name}
    report = write_report(report)
    print(json.dumps({k:v for k,v in report.items() if k != 'requests'},ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__':
    main()
