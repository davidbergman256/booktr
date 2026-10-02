# Live translation benchmark

The workload is a locally generated, 349-page PDF of the public-domain *Pride and Prejudice*. Extraction yields 122,052 prose words, 62 sections and 2,164 paragraphs. The pipeline translates 136 chunks and performs source-based editorial review of every chunk, followed by a common title/heading review and PDF generation. Both models use the actual configured account: GPT-6 Luna with reasoning `none` for drafts, GPT-6.1 Sol with reasoning `low` for editorial review. Total concurrency is 12 and the chunk target is 1,200 source words.

The source PDF SHA-256 is `b6fe24b6a00d800861e60a44cad1d7071def8c539b7191c47189007c42878ac1`. The script uses a new local job directory to prevent reuse of extraction, profiles, drafts or reviews. Provider-side automatic prompt caching may still apply; this is ordinary API behavior and differs from reusing local translation results.

## First complete cold run

The first run completed in **578.236 seconds (9 minutes 38 seconds)**. Its report is preserved in `outputs/cold-benchmark/benchmark-report.json`. This run used the earlier batched reducer; the subsequent release candidate also includes complete contributing profile context for every identity, plus additional footnote handling. This first timing therefore measures the earlier implementation.

| Stage | Seconds |
| --- | ---: |
| PDF extraction | 12.920 |
| Source language and segmentation | 2.949 |
| Full-book profiling and canonical memory | 171.854 |
| Pipelined translation, full review and heading review | 390.078 |
| Merge | 0.020 |
| PDF typesetting | 0.395 |

There were 371 actual request attempts, with peak concurrency 12. Source profiling made 64 calls for 62 valid profiles, followed by one shared-guide request and 28 canonical identity batches. Drafting made 137 attempts for 136 chunks; one 120-second request timed out and was retried. Editorial review made 136 calls, and heading harmonization made one call. No prose review was omitted to reach the measured time.

The output is a native 254-page PDF. All 2,227 source IDs have exactly one nonempty final entry. Rendered checks of the original cover, translated opening, middle and ending are preserved as `outputs/cold-benchmark/proof-{cover,opening,middle,ending}.png`.

| Request class | Median seconds | 95th percentile seconds |
| --- | ---: | ---: |
| Source profile | 13.756 | 16.969 |
| Canonical identity batch | 23.349 | 27.037 |
| Translation draft | 13.400 | 16.188 |
| Source-based review | 20.142 | 30.374 |

The primary latency is model work: profiling/canonical memory takes about three minutes, and the overlapped draft/review stage takes about six and a half minutes. Local merge and PDF compilation take less than a second together. A faster renderer would make little difference on this digital-PDF workload.

## Full-context cold run and validated upgrade

The second cold run completed in **566.364 seconds (9 minutes 26 seconds)** with zero local checkpoint files at startup. It used extraction version 6 and the full-context canonical reducer: 62 source profiles, one shared guide and 60 parallel identity batches. The exact starting application source hash is `cb8be92168b9160eb8d9399747b7f2e8ee1738649d36fbb6232d2749e929163a`.

| Stage | Seconds |
| --- | ---: |
| PDF extraction | 13.035 |
| Source language and segmentation | 2.645 |
| Full-book profiling and canonical memory | 156.963 |
| Pipelined translation, full review and heading review | 393.316 |
| Merge | 0.021 |
| PDF typesetting | 0.367 |

The run made 409 requests with peak concurrency 12. There were no transport failures. Seven invalid draft responses were rejected for omitted/empty IDs and repaired; recursive splitting added two further calls, yielding 145 draft calls for 136 chunks. All 136 chunks received editorial review, followed by heading harmonization. Canonical batch median latency was 11.308 seconds; draft and review medians were 13.626 and 20.318 seconds. The resulting PDF has 253 pages and exactly 2,227 nonempty final entries.

Raw timed evidence, including the PDF, report and checkpoints, is preserved in `outputs/final-benchmark-cold-evidence/`. The cached input basename was `source.pdf`, giving the test job a generic display title and output filename. All 2,226 other source entries exactly match the first run; this metadata difference affects one title entry rather than the novel's prose workload.

Several reliability changes landed after the timed process had imported its modules: strict duplicate-ID rejection in the legacy API parser, atomic PDF publication, exact copying of entries consisting only of punctuation/numbers/note anchors, complete language-aware source-profile cache keys, and cache-preserving extraction invalidation. Extraction version 7 changes OCR note-coordinate and duplicate-definition handling; native digital extraction remains unchanged. Live scanned-note fixtures verify that OCR change separately. The 566.364-second measurement belongs to the recorded starting snapshot, rather than every subsequent guard patch.

The complete novel was then rebuilt with extraction version 7 and the current guards in **15.684 seconds**. This was a warm upgrade, not a cold throughput result: one cover-OCR request and three language requests ran, while all profile, canonical, draft, review and heading results were reused. A one-time benchmark-only profile-cache migration required the observed English request provenance, exact old unit/prompt/model keys, validated values and equality with the archived profile results. Product code does not guess legacy language provenance; new profile keys hash the complete actual model input.

The upgrade restored two cached closing-bracket entries that had one extra whitespace character. Every alphabetic translated entry remained byte-for-byte identical to the accepted cold result. All 33 entries consisting only of punctuation/numbers/opaque note anchors now exactly equal their source, and the stronger complete-ID/text contract validates all 2,227 final entries. The output remains 253 pages. Warm evidence is in `outputs/final-benchmark/benchmark-report.json`; migration provenance is recorded separately without adding metadata to model profiles. The warm application source hash is `fb1a3d4160522a2ea07727443c520d72690f917e0f7dd68c5a94b2bf4d9c8f84`.

The current code passes 137 tests and the complete offline PDF smoke test. Rendered checks of the final opening, middle and ending are preserved as `outputs/final-benchmark/proof-{opening,middle,ending}-final.png`.

## Interpretation

This is a measured full-book workload, not a ten-minute guarantee for arbitrary books or account quotas. The sample has no author footnotes or interior illustrations; their correctness is verified separately with real PDF fixtures containing cover art, repeated images, scans with text layers, multiple note labels, and notes continuing across pages. A separate 25-page fidelity demonstration retains 12 notes and 13 figures, alongside the original cover. Larger books, scanned-page OCR, provider timeouts and lower quotas can increase latency. Full editorial coverage does not establish professional literary accuracy without an independent human evaluation.

`scripts/benchmark_book.py` records the source-code hash at startup, request timings and peaks, stage timings, source counts and the output filename without recording credentials or request contents. Later runs also record request failures and per-class latency summaries. Use its optional `--title` when benchmarking a cached file named `source.pdf`, because ordinary job title inference uses the input basename.
