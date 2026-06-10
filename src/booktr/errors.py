"""Chybová vrstva proti panice: každá výjimka se přeloží na klidnou českou zprávu.

Technické detaily jdou vždy do logu, nikdy na obrazovku.
"""
from __future__ import annotations

import errno
import logging

from . import strings_cs

log = logging.getLogger("booktr")


class BookTrError(Exception):
    """Chyba s kategorií, na kterou má GUI přátelskou českou hlášku."""

    def __init__(self, code: str, detail: str = ""):
        if code not in strings_cs.ERRORS:
            code = "unknown"
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def categorize(exc: Exception) -> BookTrError:
    """Zařadí libovolnou výjimku do kategorie s českou hláškou."""
    if isinstance(exc, BookTrError):
        return exc
    name = type(exc).__name__
    text = str(exc)

    if name == "AuthenticationError":
        return BookTrError("auth", text)
    if name == "RateLimitError" and "insufficient_quota" in text:
        return BookTrError("quota", text)
    if name == "PdfiumError" or "PDF" in name:
        return BookTrError("bad_pdf", text)
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return BookTrError("disk_full", text)
    if isinstance(exc, PermissionError):
        return BookTrError("output_locked", text)
    return BookTrError("unknown", f"{name}: {text}")


def friendly_message(err: BookTrError, config) -> str:
    """Česká hláška pro uživatele, doplněná o jméno a telefon pomocníka."""
    template = strings_cs.ERRORS[err.code]
    return template.format(
        helper_name=getattr(config, "helper_name", ""),
        helper_phone=getattr(config, "helper_phone", ""),
    ).strip()


def action_label(err: BookTrError) -> str:
    return strings_cs.ERROR_ACTIONS.get(err.code, strings_cs.QUIT)
