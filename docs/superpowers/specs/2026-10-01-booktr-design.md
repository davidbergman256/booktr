# BookTr rebuild

Build a reliable Czech book translator and audiobook maker for a 72-year-old Windows user. The main screen has two large actions, Přeložit knihu and Audiokniha, plus Nastavení. Settings persist PDF font size (12–24 points) and voice mode: Normální (~250 Kč / kniha, Google Chirp 3 HD Czech), Pokročilý (~700 Kč / kniha, ElevenLabs v4 Czech). Prices are indicative and depend on length and provider plan.

## Translation

Retain faithful translation and a complete editorial pass. Replace sequential chapter profiling and whole-book draft/review barriers with parallel profiles, a compact shared guide plus bounded canonical entity/relationship batches, and paragraph-aligned chunks of approximately 1,200 words. Canonical groups retain all source-ordered evidence and relevant full summaries, including late-book discoveries and changing ty/vy relationships. Each draft immediately enters editorial review; bounded shared concurrency defaults to 12. Each request receives immutable paragraph IDs, adjacent source context and the canonical style sheet. Validate exact ID coverage, nonempty strings and exact footnote token multiplicities. Never silently accept dropped notes. Checkpoints are keyed by content, model, prompt and relevant settings. Preserve all completed work across interruptions; invalidate stale artifacts when inputs change. Record timing and request usage. Ten minutes is a measurable target, not a guarantee without representative live books and account quotas.

## Document fidelity

Extract page geometry with pdfplumber and render/extract images using existing PDFium. Preserve the first page as a cover, all meaningful illustration occurrences and reading-order placement. Scans use structured vision OCR including explicit figure bounds and footnotes. The book IR keeps existing chapter/paragraph structure and adds images, cover and footnote records. Inline [[FN:id]] anchors link to separate translated footnote bodies. Typst owns footnote numbering and placement on the new pages, and inserts the source cover and illustrations. Font-size changes only rerender the PDF.

## Speech

Normal speech uses cs-CZ Chirp 3 HD, respecting Google's 5,000-byte limit. Advanced uses the official Eleven v4 dialogue API and Czech language. Synthesize bounded chunks with recoverable checkpoints; validate PCM and stitch to a single playable WAV with chapter timestamps. Prefer M4B when a usable ffmpeg is present. Authentication stays in private local configuration/ADC and never in source or logs. Provide repeatable Cloud setup instructions/scripts and perform live setup if account access is available.

## UI and packaging

Use CustomTkinter for a calm, modern desktop interface with warm light surfaces, dark readable text, green primary controls, large targets and clear Czech instructions. Keep settings separate from provider credentials. Progress is monotonic, errors preserve work, and retry cannot start duplicate workers. Keep Windows single-executable packaging; include CustomTkinter assets and Typst. CLI supports translation, audiobook mode and configurable font size.

## Acceptance

Tests cover cache invalidation, bounded concurrency and pipelining, exact footnote markers, geometry and scanned OCR, cover/image presence, real Typst compilation at different sizes, audio splitting/stitching/provider contracts, persisted settings and resumed work. Run the full suite and packaging smoke tests. Report live provider/account blockers and measured performance honestly.
