"""Jediné místo, kudy jdou volání modelů.

- 5 pokusů s exponenciálním čekáním (2 s → 60 s, s náhodným rozptylem)
- výpadek internetu nevyhazuje chybu: čeká, každých 30 s zkouší spojení
  a sám pokračuje (GUI mezitím ukazuje klidný proužek)
- JSON odpovědi se vynucují přes response_format, parsování má jeden opravný dotaz
"""
from __future__ import annotations

import base64
import json
import logging
import random
import socket
import threading
import time

from .errors import BookTrError

log = logging.getLogger("booktr.api")

PROBE_HOST = ("api.openai.com", 443)
OFFLINE_POLL_SECONDS = 30
MAX_ATTEMPTS = 5


class ApiClient:
    def __init__(self, config, on_offline=None, on_online=None):
        self.cfg = config
        self.on_offline = on_offline or (lambda: None)
        self.on_online = on_online or (lambda: None)
        self.offline = threading.Event()
        self._client = None

    # ---- veřejné rozhraní -----------------------------------------------------
    def complete(self, model: str, system: str, user: str,
                 images: list[bytes] | None = None, json_mode: bool = False) -> str:
        attempts = 0
        while True:
            self._wait_until_online()
            try:
                return self._request(model, system, user, images, json_mode)
            except Exception as exc:  # noqa: BLE001 — klasifikujeme níže
                kind = self._classify(exc)
                if kind == "offline":
                    self._go_offline()
                    continue  # výpadek sítě nespotřebovává pokusy
                if kind == "retry":
                    attempts += 1
                    if attempts >= MAX_ATTEMPTS:
                        raise BookTrError("unknown", f"API failed after retries: {exc}")
                    delay = min(60.0, 2.0 * (2 ** (attempts - 1))) + random.uniform(0, 1)
                    log.warning("API retry %d/%d in %.1fs: %s", attempts, MAX_ATTEMPTS, delay, exc)
                    time.sleep(delay)
                    continue
                raise BookTrError(kind, str(exc))

    # ---- interní --------------------------------------------------------------
    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.cfg.openai_api_key)
        return self._client

    def _request(self, model, system, user, images, json_mode) -> str:
        client = self._ensure_client()
        if images:
            content = [{"type": "text", "text": user}]
            for png in images:
                b64 = base64.b64encode(png).decode("ascii")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })
        else:
            content = user
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": content}],
            **kwargs,
        )
        usage = getattr(resp, "usage", None)
        if usage:
            log.info("api call model=%s in=%s out=%s", model,
                     usage.prompt_tokens, usage.completion_tokens)
        return resp.choices[0].message.content or ""

    @staticmethod
    def _classify(exc: Exception) -> str:
        name = type(exc).__name__
        text = str(exc)
        if name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "TimeoutError"):
            return "offline"
        if name == "AuthenticationError":
            return "auth"
        if name == "RateLimitError":
            return "quota" if "insufficient_quota" in text else "retry"
        if name in ("InternalServerError", "APIStatusError", "UnprocessableEntityError"):
            return "retry"
        if name == "BadRequestError":
            return "unknown"
        return "retry"

    def _go_offline(self):
        if not self.offline.is_set():
            self.offline.set()
            log.warning("network offline — waiting")
            self.on_offline()

    def _wait_until_online(self):
        first = True
        while self.offline.is_set():
            if not first:
                time.sleep(OFFLINE_POLL_SECONDS)
            first = False
            try:
                socket.create_connection(PROBE_HOST, timeout=5).close()
            except OSError:
                continue
            self.offline.clear()
            log.info("network back online")
            self.on_online()


def parse_json_map(text: str) -> dict[str, str]:
    """Vytáhne z odpovědi JSON objekt {id: text}; toleruje ```json ploty."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return {str(k): str(v) for k, v in data.items()}
