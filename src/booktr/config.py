"""Konfigurace — nastavuje ji jednou syn, tátovi se nikdy neukazuje."""
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
    openai_api_key: str = ""
    model_draft: str = "gpt-5.4-mini"
    model_review: str = "gpt-5.5"
    helper_name: str = ""
    helper_phone: str = ""
    output_dir: str | None = None
    page_format: str = "a4"
    extra: dict = field(default_factory=dict)

    @property
    def output_path(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        desktop = Path.home() / "Desktop"
        return desktop if desktop.is_dir() else Path.home()


def config_path() -> Path:
    return app_dir() / "config.json"


def load_config() -> Config:
    path = config_path()
    if not path.is_file():
        raise BookTrError("config", f"missing {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BookTrError("config", f"unreadable config: {exc}") from exc

    known = {f for f in Config.__dataclass_fields__ if f != "extra"}
    kwargs = {k: v for k, v in raw.items() if k in known}
    cfg = Config(**kwargs, extra={k: v for k, v in raw.items() if k not in known})
    if not cfg.openai_api_key:
        raise BookTrError("config", "openai_api_key missing")
    return cfg


def setup_logging() -> None:
    """Vše technické do rotujícího logu; na obrazovku nikdy."""
    handler = logging.handlers.RotatingFileHandler(
        app_dir() / "booktr.log", maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("booktr")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
