# BookTr architecture

The current application documentation is in [README.md](README.md).

The July 2026 design has been replaced by the October rebuild:

- Positioned PDF extraction retains cover, illustration occurrences, author notes and their references.
- Parallel full-book profiling feeds a canonical terminology guide, with every source segment represented.
- Paragraph-aligned chunks draft and receive source-based editorial review under one bounded worker pool.
- Validated, content-addressed checkpoints let interrupted work resume without repeating completed requests.
- Native Typst images and footnotes reflow with the chosen font size.
- Independent speech adapters support Czech Google Chirp 3 HD and ElevenLabs v4, with resumable PCM synthesis and chapter-aware M4B exports.
- The Czech desktop UI presents translation, audiobook generation and reader settings as the main actions.

See the [research report](docs/research/2026-10-01-translation-architecture.md),
[design contract](docs/superpowers/specs/2026-10-01-booktr-design.md), and
[audio account setup](docs/audio-setup.md). Provider access and real book latency
must be verified independently of offline tests. Private credentials are never
part of the Windows build artifact.
