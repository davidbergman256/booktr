"""Private provider configuration and separately persisted reader preferences."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BookTrError


def app_dir() -> Path:
    """%APPDATA%\\BookTr na Windows; rozumné ekvivalenty jinde (vývoj na macOS)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
        d = base / "BookTr"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "BookTr"
    else:
        d = Path.home() / ".booktr"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Config:
    openai_api_key: str = field(default="", repr=False)
    model_draft: str = "gpt-6-luna"
    model_review: str = "gpt-6.1-sol"
    helper_name: str = ""
    helper_phone: str = ""
    output_dir: str | None = None
    page_format: str = "a4"
    font_size: int = 14
    voice_mode: str = "normal"
    translation_workers: int = 12
    chunk_words: int = 1200
    api_timeout: float = 120.0
    reasoning_effort: str = "low"
    draft_reasoning_effort: str = "none"
    google_cloud_project: str = ""
    google_credentials_file: str = ""
    google_api_key: str = field(default="", repr=False)
    google_voice: str = "cs-CZ-Chirp3-HD-Gacrux"
    elevenlabs_api_key: str = field(default="", repr=False)
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_v4"
    audio_workers: int = 3
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        # Older private configs may change the model without knowing its new
        # reasoning floor. Sol/Astra do not support the no-reasoning setting.
        for model, effort in ((self.model_draft, 'draft_reasoning_effort'), (self.model_review, 'reasoning_effort')):
            if model.startswith(('gpt-6-sol', 'gpt-6.1-sol', 'gpt-6-astra')) and getattr(self, effort) in ('none', 'minimal'):
                setattr(self, effort, 'low')
        if isinstance(self.font_size, bool) or not isinstance(self.font_size, int) or not 12 <= self.font_size <= 24:
            raise ValueError("font_size must be an integer from 12 to 24")
        if self.voice_mode not in ("normal", "advanced"):
            raise ValueError("voice_mode must be normal or advanced")
        if self.page_format not in ("a4", "a5"):
            raise ValueError("page_format must be a4 or a5")
        for name, low, high in (("translation_workers", 1, 32), ("chunk_words", 200, 6000), ("audio_workers", 1, 8)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}")
        if not isinstance(self.api_timeout, (int, float)) or not 10 <= self.api_timeout <= 600:
            raise ValueError("api_timeout must be between 10 and 600 seconds")
        for name in ("reasoning_effort", "draft_reasoning_effort"):
            if getattr(self, name) not in ("none", "minimal", "low", "medium", "high"):
                raise ValueError(f"invalid {name}")

    def require_translation(self) -> None:
        if not self.openai_api_key or "VLOZTE" in self.openai_api_key:
            raise BookTrError("config", "openai_api_key missing")

    @property
    def output_path(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        desktop = Path.home() / "Desktop"
        return desktop if desktop.is_dir() else Path.home()


def config_path() -> Path:
    if path := os.environ.get("BOOKTR_CONFIG_FILE"):
        return Path(path).expanduser()
    return app_dir() / "config.json"


_TEMPLATE = {
    "openai_api_key": "sk-SEM-VLOZTE-KLIC",
    "model_draft": "gpt-6-luna",
    "model_review": "gpt-6.1-sol",
    "helper_name": "",
    "helper_phone": "",
    "output_dir": None,
    "page_format": "a4",
    "font_size": 14,
    "voice_mode": "normal",
    "translation_workers": 12,
    "chunk_words": 1200,
    "reasoning_effort": "low",
    "draft_reasoning_effort": "none",
    "google_cloud_project": "",
    "google_credentials_file": "",
    "google_api_key": "",
    "google_voice": "cs-CZ-Chirp3-HD-Gacrux",
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id": "",
    "elevenlabs_model": "eleven_v4",
    "audio_workers": 3,
}


def env_path() -> Path:
    """Use one explicitly selected private file, never search the checkout."""
    if path := os.environ.get("BOOKTR_ENV_FILE"):
        return Path(path).expanduser()
    return app_dir() / ".env"


def _private_env_values() -> dict[str, str | None]:
    from dotenv import dotenv_values

    try:
        path = env_path()
        if not path.exists():
            return {}
        return dotenv_values(path, interpolate=False, encoding="utf-8-sig")
    except (OSError, UnicodeError):
        # Decoder/OSError messages can contain private file contents or paths.
        raise BookTrError("config", "unreadable private environment file") from None


def load_config(validate: bool = True) -> Config:
    path = config_path()
    if not path.is_file():
        # první spuštění: založíme šablonu, ať ji stačí jen vyplnit
        try:
            path.write_text(json.dumps(_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        except OSError:
            pass
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config must be a JSON object")
    except (OSError, ValueError) as exc:
        raise BookTrError("config", f"unreadable config: {exc}") from exc

    known = {f for f in Config.__dataclass_fields__ if f != "extra"}
    kwargs = {k: v for k, v in raw.items() if k in known}
    env_fields = {
        "openai_api_key": "OPENAI_API_KEY", "google_cloud_project": "GOOGLE_CLOUD_PROJECT",
        "google_credentials_file": "GOOGLE_APPLICATION_CREDENTIALS",
        "google_api_key": "GOOGLE_API_KEY", "elevenlabs_api_key": "ELEVENLABS_API_KEY",
        "elevenlabs_voice_id": "ELEVENLABS_VOICE_ID",
    }
    private_env = _private_env_values()
    for name, env_name in env_fields.items():
        if env_name in os.environ:
            kwargs[name] = os.environ[env_name]
        elif private_env.get(env_name) is not None:
            kwargs[name] = private_env[env_name]
    prefs_path = app_dir() / "preferences.json"
    if prefs_path.exists():
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            if not isinstance(prefs, dict):
                raise ValueError("preferences must be an object")
            kwargs.update({k: prefs[k] for k in ("font_size", "voice_mode", "page_format") if k in prefs})
        except (OSError, ValueError) as exc:
            raise BookTrError("config", "unreadable reader preferences") from exc
    try:
        cfg = Config(**kwargs, extra={k: v for k, v in raw.items() if k not in known})
    except (TypeError, ValueError) as exc:
        raise BookTrError("config", str(exc)) from exc
    if validate:
        cfg.require_translation()
    return cfg


def save_preferences(config: Config) -> None:
    from .state import atomic_write_text

    config.__post_init__()
    path = app_dir() / "preferences.json"
    data = {k: getattr(config, k) for k in ("font_size", "voice_mode", "page_format")}
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def setup_logging() -> None:
    """Vše technické do rotujícího logu; na obrazovku nikdy."""
    handler = logging.handlers.RotatingFileHandler(
        app_dir() / "booktr.log", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("booktr")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
