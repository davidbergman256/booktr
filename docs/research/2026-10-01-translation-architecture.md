# Book translation architecture research

Verified on 2026-10-01. The recommendation is a practical quality/latency tradeoff for this application, not a claim that one published architecture is universally optimal for Czech novels.

## Evidence and implications

[Karpinska and Iyyer, WMT 2023](https://aclanthology.org/2023.wmt-1.41/) evaluated literary translation with professional translators across 18 language pairs. Translating complete paragraphs improved quality over isolated sentences, including fewer mistranslations, grammatical errors and inconsistent stylistic choices. Critical omissions still occurred. Keep paragraph boundaries and perform a source-based editorial review of every translated entry.

[DocBlocks, COLM 2025](https://arxiv.org/abs/2504.12140) studies document and contextual chunk translation, including literary corpora. It supports using document context around chunks; its speed/quality results rely on document-specific fine-tuning and do not prove an off-the-shelf model or a fixed 1,200-word threshold is optimal. Preserve nearby source context, and benchmark the chunk size on representative books.

[Loong, May 2026 preprint](https://arxiv.org/abs/2605.30274) maintains summaries, bilingual exemplars and entity records, then selects relevant context instead of feeding all historical text indiscriminately. Its sequential adaptive policy and reinforcement training are a research result across English/Chinese/German/French, not a validated Czech ten-minute system. The application adopts the useful memory principle with a parallel full-coverage profile pass and a canonical glossary/character register, avoiding a sequential agent loop in the critical path.

[OpenAI latency guidance](https://developers.openai.com/api/docs/guides/latency-optimization) identifies generated tokens as a major source of latency and recommends parallel independent calls. A whole-book context window does not make a whole-book translation decode in parallel. Retain all translated prose; save latency by shorter independently decoded chunks, more simultaneous requests and review patches containing only actual corrections.

[OpenAI rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits) documents request/token limits and response headers including Retry-After. Concurrency must respect the actual account and model quota. Twelve shared workers cap total simultaneous draft and review calls; retries respect provider backoff. This cannot establish a ten-minute SLA independently of provider throughput, scanned-page OCR, book length and quota.

[OpenAI prompt-caching guidance](https://developers.openai.com/api/docs/guides/prompt-caching) supports stable shared prefixes. Keep the canonical style sheet in the stable system prefix, and put each chunk's changing source and context later. Cached prefixes reduce prefill work; they do not eliminate output generation or serve as application resume checkpoints.

## Current model contract

Official [GPT-6 Luna documentation](https://developers.openai.com/api/docs/models/gpt-6-luna) supports reasoning effort `none`, and the official [GPT-6.1 Sol documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol) supports `low` through `max`, excluding `none`. Both support structured outputs and 1.05M input context/128K output limits. Sol permits Chat Completions without tools; a translation-only client need not migrate endpoints just to use it.

The current standard prices per million tokens are Luna $0.10 input/$0.50 output and Sol $2 input/$10 output, before cache, processing-tier and regional adjustments. Published Tier 1 limits are 500 requests/minute and 500,000 tokens/minute for both; inspect the user's account for effective limits. These specifications are time-sensitive.

The live probe produced valid translations for an approximately 996-word English vignette: GPT-5.4 Mini low 11.41s; GPT-6 Luna none 12.58s; GPT-6.1 Sol low 27.98s. This was one duplicated short scene, not a representative literary evaluation. Mini produced a clear Czech agreement error (“bylo modřina”), while Luna retained an awkward river calque; Sol used more natural phrasing. This supports testing Luna drafts with a complete Sol editorial pass, not advertising human translation quality or skipping review.

## Implemented architecture

1. Deterministic extraction produces the book structure, images, cover, author notes and immutable note anchors.
2. Full-coverage source profiles run concurrently. Long chapters are profiled in complete paragraph-aligned segments. A compact shared narrative/character guide coordinates aliases and style, then bounded parallel identity batches reconcile names and ty/vy relationships. Every identity retains all chronological candidate evidence and the complete synopsis/style of its contributing source profiles; only exact duplicate candidates are combined, preserving every source location. Each batch must return exactly its requested identities. This removes a single large-output reduction barrier while retaining late-book evidence and changing relationships.
3. Paragraph-aligned output chunks target 1,200 source words. Each receives the canonical style sheet, the relevant chapter/neighbor summaries and up to 400 source words from each adjacent chunk. Original notes are translated as separate entries using their original stable IDs.
4. Each worker translates a chunk and immediately sends it to source-based editorial review. At most twelve requests are active in total. Full-book draft/review barriers are removed; every chunk is still reviewed.
5. Model output must cover exactly the requested IDs with nonempty strings and preserve the ordered immutable note markers. Review patches may contain only supplied IDs. Duplicate JSON keys, damaged markers, missing text and invalid reviews are rejected. Failed draft requests can split into smaller paragraph groups; review failures preserve the successful draft and stop completion.
6. Accepted drafts and completed reviews have separate content/model/prompt/settings checkpoints. Changed input invalidates stale stage artifacts. Closing the UI cancels queued work; pause waits check cancellation. Only unanimous valid language samples from the source's start, middle and end permit a Czech original to bypass translation and review; mixed or unknown samples take the complete translation path.
7. Deterministic merge checks complete source coverage and note references, then Typst controls new pagination and footnote placement. Font-size changes only rerender the final output.

## Scheduling benchmark

`PYTHONPATH=src .venv/bin/python tests/benchmark_pipeline.py` uses a 90,000-word/75-chunk workload and fixed synthetic service delays, with the same 150 draft/review calls. A local run measured 0.909s with the old four-worker stage barrier and 0.338s with twelve pipelined workers (2.69×). Instrumentation verifies peak concurrency of twelve and that review starts before the last draft finishes. This measures scheduling only; the representative live-book benchmark must determine actual end-to-end latency.
