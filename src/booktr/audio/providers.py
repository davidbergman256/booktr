"""Small HTTP adapters for Czech Chirp 3 HD and Eleven v4 narration.

    Requests never put secrets in query strings or exception text. Provider
    errors are reported with a status code; neither book text nor response
    bodies containing account data are written to logs.
"""
from __future__ import annotations

import base64
import io
import random
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..errors import BookTrError

SAMPLE_RATE = 24000
_TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class PcmAudio:
    """Interleaved little-endian PCM samples, with no file header."""
    frames: bytes
    sample_rate: int = SAMPLE_RATE
    channels: int = 1
    sample_width: int = 2

    def __post_init__(self):
        if self.sample_rate <= 0 or self.channels <= 0 or self.sample_width not in (1, 2, 3, 4):
            raise ValueError("Speech provider returned an invalid PCM format")
        if not self.frames or len(self.frames) % (self.channels * self.sample_width):
            raise ValueError("Speech provider returned empty or truncated PCM audio")

    @classmethod
    def from_wav(cls, data: bytes) -> "PcmAudio":
        with wave.open(io.BytesIO(data), "rb") as reader:
            return cls(reader.readframes(reader.getnframes()), reader.getframerate(),
                       reader.getnchannels(), reader.getsampwidth())


class HttpProvider:
    def __init__(self, *, client: httpx.Client | None = None, sleep=time.sleep, cancel_event=None):
        self.client = client or httpx.Client(timeout=httpx.Timeout(240, connect=20))
        self._owns_client = client is None
        self._sleep = sleep
        self.cancel_event = cancel_event

    def _check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise BookTrError("cancelled", "Audiobook generation cancelled")

    def _wait(self, seconds):
        self._check_cancelled()
        if self.cancel_event is not None:
            if self.cancel_event.wait(seconds):
                self._check_cancelled()
        else:
            self._sleep(seconds)

    def close(self):
        if self._owns_client:
            self.client.close()

    def _request(self, method: str, url: str, *, json=None, params=None) -> httpx.Response:
        for attempt in range(6):
            self._check_cancelled()
            try:
                response = self.client.request(method, url, headers=self._headers(), json=json, params=params)
            except httpx.TransportError:
                if attempt == 5:
                    raise BookTrError("unknown", "Speech service connection failed after retries") from None
                self._wait(min(30, 2 ** attempt) + random.random())
                continue
            if response.is_success:
                return response
            if response.status_code in _TRANSIENT_STATUSES and attempt < 5:
                delay = _retry_delay(response.headers.get("retry-after"), attempt)
                self._wait(delay)
                continue
            code = {401: "auth", 403: "auth", 402: "audio_payment", 429: "quota"}.get(
                response.status_code, "unknown")
            raise BookTrError(code, f"Speech service returned HTTP {response.status_code}")
        raise AssertionError("unreachable")


def _retry_delay(header: str | None, attempt: int) -> float:
    if header:
        try:
            return min(60, max(0, float(header)))
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
                return min(60, max(0, seconds))
            except (ValueError, TypeError):
                pass
    return min(30, 2 ** attempt) + random.random()


class GoogleProvider(HttpProvider):
    """Google's synchronous API returns a WAV container for LINEAR16."""
    max_chars = None
    max_bytes = 4500  # 5,000 byte API maximum; leave headroom.

    def __init__(self, config, **kwargs):
        super().__init__(**kwargs)
        self.voice = getattr(config, "google_voice", "cs-CZ-Chirp3-HD-Gacrux")
        self.project = getattr(config, "google_cloud_project", "")
        self._api_key = getattr(config, "google_api_key", "")
        self._credentials_file = getattr(config, "google_credentials_file", None)
        self._credentials = None
        self._auth_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_request = 0.0
        self.identity = {"provider": "google", "voice": self.voice, "model": "chirp3-hd",
                         "language": "cs-CZ", "sample_rate": SAMPLE_RATE}

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-goog-api-key"] = self._api_key
        else:
            # Refresh credentials under a lock; HTTP requests remain concurrent.
            with self._auth_lock:
                try:
                    import google.auth
                    from google.auth.transport.requests import Request
                    if self._credentials is None:
                        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
                        if self._credentials_file:
                            from google.oauth2.service_account import Credentials
                            self._credentials = Credentials.from_service_account_file(
                                self._credentials_file, scopes=scopes)
                        else:
                            self._credentials, discovered_project = google.auth.default(scopes=scopes)
                            self.project = self.project or discovered_project or ""
                    if not self._credentials.valid:
                        self._credentials.refresh(Request())
                    headers["Authorization"] = f"Bearer {self._credentials.token}"
                except Exception:
                    raise BookTrError("config", "Google Cloud credentials are unavailable or expired") from None
        # A standard API key already selects its owning billing/quota project.
        # A user-project override requires an authenticated IAM principal and
        # should only accompany OAuth/ADC credentials, never an API key.
        if self.project and not self._api_key:
            headers["x-goog-user-project"] = self.project
        return headers

    def validate(self):
        if not self.voice.startswith("cs-CZ-Chirp3-HD-"):
            raise BookTrError("config", "Choose a Czech Chirp 3 HD voice")
        voices = self._request("GET", "https://texttospeech.googleapis.com/v1/voices",
                               params={"languageCode": "cs-CZ"}).json().get("voices", [])
        if not any(voice.get("name") == self.voice for voice in voices):
            raise BookTrError("config", "The configured Czech Google voice is unavailable")

    def synthesize(self, text: str, **_) -> PcmAudio:
        if len(text.encode("utf-8")) > 5000:
            raise ValueError("Google speech input exceeds 5,000 UTF-8 bytes")
        # 180 starts/minute stays below the documented 200 Chirp requests/min.
        with self._rate_lock:
            now = time.monotonic()
            wait = max(0, self._next_request - now)
            self._next_request = max(now, self._next_request) + 1 / 3
        if wait:
            self._wait(wait)
        response = self._request("POST", "https://texttospeech.googleapis.com/v1/text:synthesize", json={
            "input": {"text": text},
            "voice": {"languageCode": "cs-CZ", "name": self.voice},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": SAMPLE_RATE},
        })
        try:
            audio = PcmAudio.from_wav(base64.b64decode(response.json()["audioContent"], validate=True))
        except (KeyError, ValueError, wave.Error):
            raise BookTrError("unknown", "Google returned invalid speech audio") from None
        if (audio.sample_rate, audio.channels, audio.sample_width) != (SAMPLE_RATE, 1, 2):
            raise BookTrError("unknown", "Google returned an unexpected speech audio format")
        return audio


class ElevenLabsProvider(HttpProvider):
    # Model maximum is 10,000 characters. The dialogue endpoint recommends
    # <=2,000 to prevent early termination; use its stricter reliable limit.
    max_chars = 1800
    max_bytes = None

    def __init__(self, config, **kwargs):
        super().__init__(**kwargs)
        self._api_key = getattr(config, "elevenlabs_api_key", "")
        self.voice = getattr(config, "elevenlabs_voice_id", "")
        self.model = getattr(config, "elevenlabs_model", "eleven_v4")
        self.identity = {"provider": "elevenlabs", "voice": self.voice, "model": self.model,
                         "language": "cs", "sample_rate": SAMPLE_RATE}

    def _headers(self) -> dict:
        if not self._api_key or not self.voice:
            raise BookTrError("config", "ElevenLabs API key and Czech narrator voice are required")
        return {"xi-api-key": self._api_key, "Content-Type": "application/json"}

    def validate(self):
        if self.model != "eleven_v4":
            raise BookTrError("config", "Advanced narration requires the Eleven v4 model")
        models = self._request("GET", "https://api.elevenlabs.io/v1/models").json()
        if not any(model.get("model_id") == self.model and model.get("can_do_text_to_speech") for model in models):
            raise BookTrError("config", "Eleven v4 is not available to this account")
        voice = self._request("GET", f"https://api.elevenlabs.io/v1/voices/{self.voice}").json()
        if voice.get("voice_id") != self.voice:
            raise BookTrError("config", "The configured ElevenLabs narrator voice is unavailable")

    def synthesize(self, text: str, *, previous_text: str = "", future_text: str = "") -> PcmAudio:
        if len(text) > 2000:
            raise ValueError("Eleven v4 dialogue input exceeds the reliable 2,000 character limit")
        body = {"inputs": [{"text": text, "voice_id": self.voice}], "model_id": self.model,
                "language_code": "cs", "apply_text_normalization": "on"}
        if previous_text:
            body["previous_text"] = previous_text[-100:]
        if future_text:
            body["future_text"] = future_text[:100]
        response = self._request("POST", "https://api.elevenlabs.io/v1/text-to-dialogue", json=body,
                                 params={"output_format": "pcm_24000"})
        try:
            return PcmAudio(response.content)
        except ValueError:
            raise BookTrError("unknown", "ElevenLabs returned empty or truncated speech audio") from None


def make_provider(config, **kwargs):
    mode = getattr(config, "voice_mode", "normal")
    if mode == "normal":
        return GoogleProvider(config, **kwargs)
    if mode == "advanced":
        return ElevenLabsProvider(config, **kwargs)
    raise BookTrError("config", "Unknown audiobook voice mode")
