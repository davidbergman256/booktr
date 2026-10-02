# Czech audiobook providers

BookTr's Normal mode uses Google Cloud `cs-CZ-Chirp3-HD-Gacrux`. Advanced uses
ElevenLabs `eleven_v4` through `POST /v1/text-to-dialogue`, with one chosen Czech
narrator. It does not silently substitute an older ElevenLabs model.

## Private credentials

The desktop app and `scripts/cloud_setup.py` automatically read a private `.env`
file from `%APPDATA%\BookTr\.env` on Windows,
`~/Library/Application Support/BookTr/.env` on macOS, or `~/.booktr/.env` on Linux.
Set `BOOKTR_ENV_FILE` to choose another private file. The app does not search the
checkout for credentials. Keep this file outside the repository; on macOS/Linux,
restrict it to its owner with `chmod 600`.

Supported entries are:

```dotenv
OPENAI_API_KEY=your-translation-key
GOOGLE_API_KEY=your-restricted-speech-key
GOOGLE_CLOUD_PROJECT=your-google-project
ELEVENLABS_API_KEY=your-restricted-elevenlabs-key
ELEVENLABS_VOICE_ID=your-czech-narrator-id
```

`GOOGLE_APPLICATION_CREDENTIALS` can point to a private service-account JSON file
instead of using a Google API key. Process environment values take priority over
`.env`, and `.env` takes priority over provider fields in the selected private
`config.json`. An explicit empty value clears a lower-priority value; an entry
without `=` is ignored. Values are literal: `${...}` is never expanded. Loading
this file does not change the process environment or write credentials into
reader preferences. Other configuration fields remain in `config.json`.

## Google Cloud

Use an existing project with billing enabled. Enable `texttospeech.googleapis.com`.
For local development, install the Google Cloud CLI and run:

```sh
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
python scripts/cloud_setup.py --config config.json --project YOUR_PROJECT_ID --provider google --enable-google-api
```

The provisioning script uses the administrator's existing OAuth credentials,
resolves the project number, enables the API, and waits for completion. It needs
`serviceusage.services.enable` and `resourcemanager.projects.get` permission.
Creating an API key additionally requires `apikeys.keys.create` and
`apikeys.keys.getKeyString`. The script does not change IAM or billing accounts.

For a Windows desktop installation, a key restricted to the Text-to-Speech API
avoids shipping an administrator's OAuth login. This command creates or reuses
the fixed `booktr-narrator` key, restricts it to Text-to-Speech, and saves the key
directly to the private configuration file without printing it:

```sh
python scripts/cloud_setup.py --config config.json --project YOUR_PROJECT_ID --provider google --create-google-key
```

Alternatively, set `google_credentials_file` to a dedicated service account's
JSON file, or use Application Default Credentials. Set `google_cloud_project`
to the billing/quota project. Keep private config and credentials outside Git;
BookTr reads `BOOKTR_CONFIG_FILE`, `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT`, and
`GOOGLE_APPLICATION_CREDENTIALS` environment variables too. No credentials go
into the reader settings dialog or command output.

Verify availability without generating paid audio:

```sh
python scripts/cloud_setup.py --config config.json --provider google
```

## ElevenLabs

Create an ElevenLabs API key with Text-to-Speech, voice-read, and model-read access.
Choose a Czech narrator from the voice library, audition it, and set
`elevenlabs_api_key` and `elevenlabs_voice_id` in private config, or set
`ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`. Leave `elevenlabs_model` as
`eleven_v4`. Validation checks that this account can use that exact model and voice.

Library voices can require a paid plan even when voice/model lookup succeeds.
A speech HTTP 402 now explains this in Czech and suggests **Nastavení → Normální**.
The included multilingual narrator George (`JBFqnCBsd6RMkjVDRZzb`) can generate
Czech with v4 within the account’s available allowance. A full book needs enough
included or purchased credits; BookTr does not purchase plans or credits automatically.

```sh
python scripts/cloud_setup.py --config config.json --provider elevenlabs
python scripts/cloud_setup.py --config config.json --provider both --sample-dir build/audio-samples
python scripts/cloud_setup.py --config config.json --provider both --sample-dir build/audio-samples --verify-encoding
```

The sample commands generate short Czech samples and therefore incur the
providers' normal character-based charges. Listen to these before a whole book.
`--verify-encoding` also checks the PCM format and encodes/decodes a chaptered
M4B locally without making additional provider requests.
Default audio concurrency is 3; choose a value within the account's concurrency
limit. The [ElevenLabs model documentation](https://elevenlabs.io/docs/overview/models)
lists Starter at 3 HTTP generations and Creator at 5. Voice availability and
actual account limits must be verified with the account credentials.

## Read and listen to the same Czech book

The usual workflow is to translate an English or German book with **Přeložit
knihu**, read the resulting Czech PDF, then click **Vytvořit audioknihu** on the
finished-book screen. **Audiokniha** on the main screen also lists saved books.
BookTr narrates its reviewed Czech paragraphs directly, including
the translated chapter titles and footnotes. The saved translation and PDF stay
available while audio is generated. This path does not run language detection,
translation, review, OCR, or PDF layout and does not require an OpenAI key.

To narrate an already Czech PDF or TXT, select that file as the audio source.
The program treats this choice as Czech; it does not decide to translate it.
Native PDFs with selectable text need no OpenAI key. Scanned body pages can use
transcription-only OCR when an OpenAI key is configured; this adds an OCR charge
and still never runs translation or review. Uncertain native footnote references
stop for correction. Plain text accepts UTF-8 (with or without a byte-order
mark), or UTF-16 with a byte-order mark.

The same choices are available without the desktop interface:

```sh
python -m booktr --audiobook-only "/path/to/saved-translation-job" --config config.json --voice-mode normal
python -m booktr --audiobook-only "Kniha česky.pdf" --config config.json --voice-mode normal
python -m booktr --audiobook-only "Kniha česky.txt" --config config.json --voice-mode advanced
```

Saved translation jobs are directories under the app's `jobs` folder containing
`book.json` and the complete `final.json`. BookTr validates every paragraph,
chapter title, and footnote before requesting speech. Imported Czech sources
have separate narration jobs, so they cannot overwrite a translation job for
the same PDF. Re-select the same source to resume interrupted speech generation.
Changing the narrator uses a separate audio cache.

`--headless original.pdf --audiobook` remains the combined translate-then-narrate
command. `--audiobook-only` selects narration alone and cannot be combined with
`--headless`, `--audiobook`, or the PDF-only `--font-size` option.

## Generation and exports

Chirp requests are bounded at 4,500 UTF-8 bytes, below Google's 5,000-byte limit,
and spaced below the default 200 Chirp requests per minute. Eleven v4's model
limit is 10,000 characters, but the dialogue endpoint recommends at most 2,000
for reliable generation; BookTr uses 1,800 and provides neighboring text for
prosodic continuity. Chunks stop at sentence boundaries where possible.

Each chunk is checkpointed as valid 24 kHz, 16-bit mono PCM in a WAV container.
Content, neighboring context, voice, and model determine its cache key. Changing
a paragraph regenerates affected chunks; changing the voice regenerates narration.
Repeated requests do not needlessly regenerate unchanged paid audio. Footnote
markers are removed and the translated note is read once after its first owning
paragraph. Unanchored notes are read at the end of the owning chapter.

BookTr streams all completed chunks into one WAV atomically, records precise
chapter offsets in `.chapters.json`, and creates a compact M4B with embedded
chapter navigation through bundled `imageio-ffmpeg` or a local `ffmpeg`. If
encoding is unavailable, the complete WAV remains usable. An existing final
audio file survives an interrupted or failed assembly. WAV has a 4 GB limit;
very large source books should be split.

## Cost labels

The requested ~250 Kč Normal / ~700 Kč Advanced labels are approximate book
estimates, not fixed prices. Characters, exchange rates, provider plans, included
usage, and retries affect actual charges. For 400,000 characters, Google's list
price is $12 and Eleven v4's standard list price is $32 before any included usage.
Eleven v4 currently advertises $0.022 per 1,000 characters through October 12,
2026, down from $0.08; this temporary rate should not determine a permanent label.

Official sources checked October 1, 2026:

- [Google Chirp 3 HD and Czech voices](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd)
- [Google voice inventory](https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types)
- [Google quotas and 5,000-byte request limit](https://docs.cloud.google.com/text-to-speech/quotas)
- [Google pricing: $30 per million Chirp 3 HD characters](https://cloud.google.com/text-to-speech/pricing)
- [Google authentication](https://docs.cloud.google.com/text-to-speech/docs/authentication)
- [Google API-key creation](https://docs.cloud.google.com/api-keys/docs/reference/rest/v2/projects.locations.keys/create)
- [Eleven v4, Czech language support, and concurrency](https://elevenlabs.io/docs/overview/models)
- [ElevenLabs dialogue API and reliability limit](https://elevenlabs.io/docs/api-reference/text-to-dialogue/convert)
- [ElevenLabs current API pricing](https://elevenlabs.io/pricing/api)
