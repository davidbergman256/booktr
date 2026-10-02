# BookTr Rebuild Implementation Plan

> **For agentic workers:** Execute independent subsystems in parallel, followed by integration and fresh review. Human authorization is the request to replace the architecture and set up providers; do not add intermediate permission gates for reversible implementation.

**Goal:** Fast, faithful Czech PDF translation and audiobook generation with a readable modern desktop UI.

**Architecture:** Structured book IR, parallel canonical profiling, pipelined translation/review, content-addressed checkpoints, native Typst layout, separate resumable speech providers.

**Tech Stack:** Python 3.11+, OpenAI SDK, PDFium, pdfplumber, Typst, CustomTkinter, httpx, google-auth.

**Spec:** docs/superpowers/specs/2026-10-01-booktr-design.md

## Global constraints

- Preserve existing user edits and private credentials; no keys in source, tests or logs.
- Czech user-facing text; large readable controls; Windows packaging remains supported.
- PDF font_size 12–24, default 14; voice_mode normal|advanced.
- Exact paragraph and footnote coverage; no unsupported ten-minute claims.

## Review focus

- Scanned or mixed PDFs: do not mistake scanned page bitmaps for illustrations or lose real figures.
- Footnote references at page boundaries and repeated labels: link the right source note and reflow correctly.
- Changed model/settings or partial interruption: do not reuse incompatible translations or audio.
- Provider quota/auth failures: retain completed work and present useful Czech errors.
- Large font, short desktop windows and audiobook-only retries: controls remain usable and duplicate workers cannot start.

### Task 1: Document fidelity (layout agent)

Files: engine/ingest.py, segment.py, merge.py, typeset.py, assets/book.typ, document helpers, tests/test_document.py.
Produces: book with chapters/paragraphs, footnotes[{id,text,label,source_page}], images[{id,path,source_page,after_paragraph}], cover.
- [x] Write geometry/image/footnote fixtures and failing assertions.
- [x] Extract structured digital pages and structured OCR fallback.
- [x] Carry provenance through segmentation and render Typst native notes/images.
- [x] Compile real PDFs at 12 and 24 points; inspect extracted text/page counts/images.

### Task 2: Translation scheduling (translation agent)

Files: engine/__init__.py, stylesheet.py, translate.py, review.py, pipeline/validation helpers, translation tests.
Consumes: document IR above; Job.ensure_fingerprint(scope,signature,artifacts,chunk_stages).
Produces: canonical stylesheet/synopses and draft.json, patches.json, final.json compatible maps.
- [x] Write failing tests for concurrency, exact tokens, full profiling and cache invalidation.
- [x] Implement parallel profile map/reduce and 1,200-word chunks with neighbor context.
- [x] Schedule draft→review under shared bounded workers and immediate checkpoints.
- [x] Benchmark old barrier vs pipelining using identical controlled request latencies.

### Task 3: Audio providers (speech agent)

Files: audio package, tests/test_audio.py, scripts/cloud_setup.py.
Consumes: book IR/final translation and private config.
Produces: AudiobookService(job,config,progress_cb,pause_event).run(book,final)->Path; audio.run(engine)->Path.
- [x] Write failing tests for Unicode byte splitting, provider contracts and real PCM stitching.
- [x] Implement Chirp3 HD and Eleven v4 adapters with bounded retries and auth.
- [x] Persist chunks, stitch playable WAV and chapter timestamps, optional M4B.
- [x] Provide repeatable Cloud provisioning script.
- [ ] Connect signed-in accounts and verify live speech from both providers.

### Task 4: App shell and integration (root)

Files: config.py, state.py, api.py, gui.py, strings_cs.py, __main__.py, pyproject.toml, build/booktr.spec, tests/test_settings.py, tests/test_api.py.
- [x] Write failing persisted-preference and invalidation tests; run them.
- [x] Add validated config fields, private preferences, scope fingerprints and atomic locks.
- [x] Add bounded API timeouts/retries and strict JSON map parsing.
- [x] Build accessible Czech CustomTkinter main/settings/progress/results flows.
- [x] Wire audiobook worker/CLI and update distribution assets.

### Task 5: Verification and delivery

- [x] Run full pytest, offline scanned/digital PDF/audio checks and real desktop widget checks; inspect rendered PDF output.
- [ ] Verify current provider models and credentials without exposing secrets.
- [x] Complete first cold full-book benchmark: 349 pages / 122,052 words in 578.236 seconds with full review.
- [x] Repeat cold benchmark with extraction v6 and full-context canonical batches: 566.364 seconds, full review. Validate OCR-only extraction v7 separately with live scan fixtures, then upgrade/revalidate the complete novel in 15.684 seconds without regenerating translated prose.
- [x] Fresh review, fix material findings, rerun necessary checks.
- [x] Save research/setup/usage documentation and produce reviewable branch/draft PR.
