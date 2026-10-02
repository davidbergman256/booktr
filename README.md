# BookTr

A Czech desktop app for translating PDF books and making audiobooks. The main
screen offers **Přeložit knihu**, **Audiokniha**, and **Nastavení**. Reader settings
control PDF font size and the two narrator modes; provider credentials stay in a
private configuration file.

The translation pipeline builds a shared terminology guide from the full book,
then drafts and reviews paragraph-aligned chunks in parallel. Every chunk keeps
stable paragraph/footnote IDs and validated checkpoints. Closing or losing the
connection retains completed work. Images and the original cover are extracted;
Typst reflows real footnotes onto the new pages.

Normal narration uses Czech Google Chirp 3 HD. Advanced uses ElevenLabs v4's
dialogue API. Both produce one playable audiobook with chapter navigation.
The ~250 Kč / ~700 Kč labels are approximate and depend on book length and plan.

## Run locally

Python 3.11 or newer:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m booktr --config config.json
```

On Windows, use `.venv\Scripts\python.exe`. Copy `config.example.json` to private
`config.json` and fill in the OpenAI key. The default models are `gpt-6-luna`
for drafting with no reasoning and `gpt-6.1-sol` for editorial review with low
reasoning. **Existing private configs retain their explicitly chosen models**;
update those two model fields and reasoning settings to use the new defaults.

The installed app reads `%APPDATA%\BookTr\config.json` on Windows or
`~/Library/Application Support/BookTr/config.json` on macOS. `--config` and
`BOOKTR_CONFIG_FILE` select another private file. Reader preferences are stored
separately and never contain keys.

```sh
python -m booktr --headless book.pdf --config config.json --font-size 18
python -m booktr --headless book.pdf --config config.json --audiobook --voice-mode normal
python -m booktr --self-test
```

[Audiobook account setup and private provisioning](docs/audio-setup.md) describes
Google Cloud activation, authentication, ElevenLabs voice selection, live checks,
and short samples. Cloud APIs must be activated and validated with your accounts
before either narrator mode can run.

## Verification and performance

A real cold run translated a 349-page, 122,052-word novel in **9 minutes 26 seconds**
with complete source-based review. [Benchmark results and limits](docs/research/2026-10-01-live-benchmark.md)
record the measured code snapshot, coverage, request timings and subsequent cache-preserving upgrade.

```sh
python -m pytest -q
python tests/smoke_e2e.py
python tests/benchmark_pipeline.py
python scripts/benchmark_book.py book.pdf --config config.json --output-dir outputs/benchmark
```

The last command incurs normal translation API usage. It records whole-book
elapsed time, stage timings, request durations, concurrency and model settings.
Synthetic scheduling tests measure scheduling alone; they do not prove provider
generation speed. Latency depends on source size, scanned pages, provider quotas
and selected models. Full review remains enabled.

## Windows distribution

GitHub Actions tests and builds `BookTr.exe`, including native Typst, CustomTkinter
assets and FFmpeg, then runs an offline PDF/M4B smoke test against the executable.
Private keys are configured after installation and never shipped in the artifact.

## Architecture

- `engine/document.py`, `ingest.py`, `segment.py`: positioned source book and assets.
- `engine/stylesheet.py`: parallel profiles and canonical terminology decisions.
- `engine/pipeline.py`: bounded draft → source-based editorial review workers.
- `engine/validation.py`, `checkpoints.py`, `state.py`: fidelity and durable resume.
- `engine/typeset.py`: native PDF layout and reflowed footnotes.
- `audio/`: narration providers, Unicode-safe chunks, checkpoints and chapter exports.
- `gui.py`, `gui_settings.py`: accessible Czech desktop shell and reader preferences.

[Research and architecture rationale](docs/research/2026-10-01-translation-architecture.md)
links the primary literature and provider documentation used for this rebuild.
Scanned books use vision OCR, so transcription accuracy still depends on scan
quality; uncertain note references stop publication for review instead of being
silently discarded.
