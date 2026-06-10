# BookTr — návrh aplikace / Design Document

**Purpose:** A dead-simple Windows app for a 72-year-old Czech-speaking user. He picks a book PDF (often a clean but unparsed scan), presses one big button, and gets a cleanly typeset Czech PDF on his Desktop. Everything visible to him is in Czech; everything that can go wrong is handled without scary dialogs.

**Pipeline contract (fixed):**
- **Pass 1 (draft):** `gpt5.4-mini` translates the whole book.
- **Pass 2 (review):** `gpt5.5` reviews each chunk and outputs **only the paragraphs it changed**.
- **Merge:** a deterministic script combines draft + review patches into the final text.

---

## 1. Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Single language for GUI + pipeline; easy for the son to maintain |
| GUI | Tkinter (stdlib) | Zero fragile dependencies; large fonts/buttons are trivial; ships inside the .exe |
| PDF text extraction | `pypdfium2` | Fast, liberal license, reliable text-layer detection |
| OCR (scanned pages) | `gpt5.4-mini` vision | Avoids bundling Tesseract; better at book layout (hyphenation, headers, drop caps) |
| API client | `openai` SDK | Both models are OpenAI |
| Typesetting | **Typst** (bundled `typst.exe`, single binary) | Print-quality output, Czech hyphenation, no LaTeX install |
| Packaging | PyInstaller → single `BookTr.exe` | One file to put on Dad's desktop |
| Windows build | GitHub Actions `windows-latest` | Dev machine is macOS; PyInstaller can't cross-compile |

**Process model:** the GUI is a thin shell. The pipeline lives in `engine/` and runs on a worker thread, posting `(stage, current, total, message_cs)` progress events to a `queue.Queue` the Tk main loop polls. The engine is also runnable headless (`python -m booktr.engine <pdf>`) for development and tests.

---

## 2. UI — three screens, 100% Czech

All user-visible strings live in `strings_cs.py` (one module, no i18n framework — the app is Czech-only by design). Minimum font size 16pt, primary buttons ~22pt.

### Screen 1 — Start
- Headline: „Přeložit knihu do češtiny“
- One big button: **„Vybrat knihu (PDF)“** (file dialog filtered to `*.pdf`). Drag & drop onto the window also accepted.
- If an unfinished job exists: instead show „Minule jsme nedokončili překlad knihy **{title}**. Chcete pokračovat?“ → **„Pokračovat“** / „Začít jinou knihu“.

### Screen 2 — Progress
- Big progress bar (overall %, computed from per-stage weights).
- Plain-Czech stage label, e.g.:
  - „Čtu knihu… (strana 34 z 210)“
  - „Připravuji se na překlad…“
  - „Překládám… (kapitola 4 z 12)“
  - „Kontroluji překlad… (kapitola 4 z 12)“
  - „Sázím knihu do PDF…“
- Rough time estimate („Zbývá asi 25 minut“), updated from measured per-chunk throughput.
- Button „Pozastavit“ → „Pokračovat“. Closing the window is always safe (checkpoints, see §5); on close show a calm note: „Překlad je uložen. Až program znovu zapnete, budeme pokračovat.“

### Screen 3 — Done
- „**Hotovo!** Přeložená kniha je na ploše.“
- Buttons: **„Otevřít knihu“** (opens the PDF) and „Přeložit další knihu“.

---

## 3. Pipeline stages

All artifacts are JSON/Markdown files in the job directory (§5), so every stage is resumable and independently testable.

### Stage 0 — Ingest (`engine/ingest.py`)
1. Open PDF with `pypdfium2`. For each page, extract the text layer.
2. **Scanned-page detection:** if a page yields < 50 characters, treat it as image-only → render at 200 DPI to PNG → send to `gpt5.4-mini` (vision) with a transcription prompt: *transcribe verbatim, join hyphenated line breaks, drop running headers/footers and page numbers, mark paragraph breaks with blank lines, mark chapter headings with `## `.* Batched a few pages per request for context continuity.
3. Output: raw per-page text.

### Stage 0b — Segment (`engine/segment.py`)
1. Join pages, split into paragraphs, detect chapter boundaries (heading heuristics: `## ` markers from OCR, all-caps/short lines, numbering patterns; ambiguous cases resolved with one cheap `gpt5.4-mini` call over the candidate list).
2. Assign **stable paragraph IDs** `chNN-pMMM` (e.g. `ch03-p014`). These IDs are the backbone of the whole pipeline — they never change after this stage.
3. Output → `book.json`:
```json
{
  "title": "...", "source_lang": "de",
  "chapters": [
    {"id": "ch01", "heading": "...", "paragraphs": [{"id": "ch01-p001", "text": "..."}]}
  ]
}
```
Source language is auto-detected here (single model call on the first ~2 pages).

### Stage 1 — Style sheet (`engine/stylesheet.py`)
Sequential skim of the book with `gpt5.4-mini` (chapter at a time, carrying a running state), producing:
- `stylesheet.md` — character & place names with **Czech declension forms**, the global **ty/vy register decision per character pair**, recurring terminology with the chosen Czech rendering, tone/era notes.
- `synopses.json` — one-paragraph synopsis per chapter (used as context in passes 1 & 2 so chapters can be processed independently).

### Stage 2 — Translate, pass 1 (`engine/translate.py`, model `gpt5.4-mini`)
- Work unit: **chunk** = consecutive paragraphs of one chapter, ~3 000 source words.
- System prompt: translator instructions + full `stylesheet.md` (sent as a cached prefix) ; user prompt: synopses of all *prior* chapters + the chunk's paragraphs labeled with their IDs.
- Output contract: JSON object `{"ch03-p014": "český text", ...}` covering **every** input ID (structured-output/JSON mode enforced).
- Output → `draft.json` (flat map of all paragraph IDs → Czech).

### Stage 3 — Review, pass 2 (`engine/review.py`, model `gpt5.5`)
- Same chunking. Input: style sheet + source paragraphs + draft paragraphs (ID-labeled).
- Instruction: *compare draft to source for accuracy, register, naturalness, and style-sheet compliance. Return **only** paragraphs you are improving, as JSON `{"id": "revised text"}`. Return `{}` if the chunk needs no changes. Never return an ID you were not given.*
- Output → `patches.json` (flat map; typically a small fraction of the book).

### Stage 4 — Merge (`engine/merge.py`, no model)
Deterministic, pure Python:
1. `final = draft | patches` (patch wins).
2. **Validation:** every ID in `book.json` present exactly once in `final`; no empty strings; no IDs in `patches` that don't exist in `book.json` (such patches are dropped and logged).
3. Output → `final.json`.

### Stage 5 — Typeset (`engine/typeset.py`)
1. Render `final.json` through a Typst template (`book.typ`):
   - `set text(lang: "cs")` → Czech hyphenation,
   - smart Czech quotes „takhle“, em-dash conventions,
   - first-line paragraph indents, no inter-paragraph gap (book convention),
   - chapter title pages, page numbers, generous margins for home printing (A5 layout printed 2-up on A4, plus a plain A4 variant — configurable),
   - widow/orphan control (`par(justify: true)` + Typst defaults).
2. Shell out to bundled `typst compile`.
3. Output: `Kniha – {title} (česky).pdf` written to the Desktop (configurable).

---

## 4. API layer (`api.py`)

One thin wrapper around the OpenAI SDK used by every stage:
- `call(model, system, user, json_schema=None, images=None)`.
- **Retries:** 5 attempts, exponential backoff with jitter (2s → 60s). Rate-limit responses honor `retry-after` silently.
- **Offline detection:** connection errors flip a global `OFFLINE` flag → engine pauses, GUI shows the offline banner (§6), a poller probes every 30 s and auto-resumes.
- **JSON discipline:** structured outputs / JSON mode wherever supported; one re-ask on schema-invalid output, then stage-specific fallback (review: keep draft for that chunk; translate: split chunk in half and retry).
- Token/cost accounting appended to the log for the son's curiosity.

---

## 5. State & resume (`state.py`)

- Job directory: `%APPDATA%\BookTr\jobs\{sha1(pdf bytes)}\`
  - `source.pdf` (copy), `book.json`, `stylesheet.md`, `synopses.json`, `draft.json`, `patches.json`, `final.json`, `progress.json`, per-chunk partials in `chunks/`.
- **Checkpoint granularity = one chunk.** After each chunk's response is validated, it is written to `chunks/` and `progress.json` is updated atomically (write-temp-then-rename). A crash or power cut loses at most one chunk.
- On startup, scan for jobs where `progress.json` ≠ done → offer resume (Screen 1). Stages skip any chunk whose partial already exists, so resume is idempotent.
- Finished jobs are kept (small) so re-running the same PDF is instant; „Začít jinou knihu“ never deletes anything.

---

## 6. Error handling — the anti-panic layer (`errors.py`)

Principles: **no stack traces, no English, no dead ends.** Every error screen has exactly one obvious action.

| Category (detected from exception/HTTP code) | Czech message | Action |
|---|---|---|
| Network down / DNS / timeout | Banner (not a dialog): „Internet je odpojen. Až bude připojení znovu fungovat, budu sám pokračovat.“ | automatic — auto-resume poller |
| Invalid API key (401) | „Program se nemůže přihlásit ke službě překladu. Zavolejte prosím: {helper_name} {helper_phone}.“ | „Zkusit znovu“ |
| Quota/billing (402/429-hard) | „Služba překladu je momentálně vyčerpaná. Zavolejte prosím: {helper_name} {helper_phone}.“ | „Zkusit znovu“ |
| Unreadable/corrupt/encrypted PDF | „Tento soubor se nepodařilo přečíst. Zkuste prosím jiný soubor s knihou.“ | „Vybrat jinou knihu“ |
| Disk full | „Na počítači není dost místa. Smažte prosím nepotřebné soubory, nebo zavolejte: {helper_name}.“ | „Zkusit znovu“ |
| Output PDF open/locked | „Kniha je otevřená v jiném programu. Zavřete ji prosím a zkuste to znovu.“ | „Zkusit znovu“ |
| Anything else (catch-all) | „Něco se nepovedlo, ale kniha není ztracena. Zkuste program vypnout a znovu zapnout.“ | „Zavřít“ |

- Every error (with full traceback and the last API request ID) goes to `%APPDATA%\BookTr\booktr.log` (rotating, 5 MB) so the son can debug over the phone or TeamViewer.
- The helper name/phone comes from config — the messages literally tell Dad to call David.
- The worker thread has a top-level exception handler; the Tk main loop can never die from an engine error.

---

## 7. Configuration (`config.py`)

`%APPDATA%\BookTr\config.json` — created by the son once, never surfaced in the UI:

```json
{
  "openai_api_key": "sk-...",
  "model_draft": "gpt5.4-mini",
  "model_review": "gpt5.5",
  "helper_name": "David",
  "helper_phone": "+420 ...",
  "output_dir": null,
  "page_format": "a4"
}
```

Missing/invalid config at startup → the invalid-key error screen (call David), not a settings dialog.

---

## 8. Repository layout

```
booktr/
  booktr.md                      ← this document
  pyproject.toml
  src/booktr/
    __main__.py                  # launches GUI; `--headless <pdf>` runs engine only
    gui.py                       # Tkinter shell, 3 screens, progress queue polling
    strings_cs.py                # every user-visible string
    config.py  state.py  errors.py  api.py
    engine/
      __init__.py                # pipeline orchestrator (stage order, checkpoints)
      ingest.py  segment.py  stylesheet.py
      translate.py  review.py  merge.py  typeset.py
    assets/book.typ              # Typst template
  tests/
    test_merge.py  test_segment.py  test_resume.py
    mock_api.py                  # canned responses; full dry-run without network
    fixtures/tiny_book.pdf       # 6-page public-domain scan
  build/
    booktr.spec                  # PyInstaller (bundles typst.exe + assets)
  .github/workflows/build-windows.yml
```

---

## 9. Implementation phases

1. **Engine core, headless** — ingest → segment → merge with `mock_api.py`; unit tests for merge validation, ID stability, and resume idempotency.
2. **Real API + typesetting** — live calls for all stages; Typst template; end-to-end on `tiny_book.pdf`.
3. **GUI** — three screens, progress queue, pause/close/resume flow, all Czech strings.
4. **Packaging** — PyInstaller spec bundling `typst.exe`; GitHub Actions workflow producing `BookTr.exe` artifact on tag push.
5. **Hardening tests** — kill the process mid-translation and verify resume; run with network disabled and verify the offline banner + auto-resume; corrupt-PDF and locked-output-file scenarios.

## 10. Verification checklist

- [ ] Scanned (no text layer) and born-digital PDFs both produce a typeset Czech PDF.
- [ ] Review pass returns only changed paragraphs; merge validation rejects invented IDs.
- [ ] Kill mid-run → relaunch → „Pokračovat?“ → finishes without re-translating completed chunks.
- [ ] Network unplugged mid-run → banner, no dialog → replug → continues by itself.
- [ ] Wrong API key → friendly Czech message with David's phone number; nothing in English anywhere in the UI.
- [ ] Output PDF: Czech hyphenation, „uvozovky“, chapter pages, page numbers; prints cleanly on A4.
