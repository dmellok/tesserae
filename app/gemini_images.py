"""Google's Gemini API as a third way to paint, next to fal.ai and OpenAI.

Nano Banana and Nano Banana Pro were reachable only through fal.ai, which
relays them at its own price. With a Gemini key under Settings › Generative AI
(``app.gemini.api_key``, else ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY`` in the
environment), :class:`app.fal.FalClient` sends Nano Banana calls straight to
Google instead, and an install with only a Gemini key paints every picture
with Nano Banana. Nothing else changes for a widget: it still asks
``job.fal.generate(model, prompt, refs=...)``.

One call: ``models/<model>:generateContent`` with the words and any reference
pictures as inline parts. The answer carries the picture inline as base64.
Reference pictures that are URLs are fetched through :mod:`app.net_guard`, so
a widget cannot point this at the LAN.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# Nano Banana and Nano Banana Pro, as fal.ai's ``nano_banana`` and
# ``nano_banana_pro`` relay them.
MODEL = "gemini-2.5-flash-image"
MODEL_PRO = "gemini-3-pro-image"
USER_AGENT = "tesserae/artwork"
TIMEOUT_S = 180.0
MAX_REF_BYTES = 20 * 1024 * 1024
ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
FIELDS: list[dict[str, Any]] = [{"name": "api_key", "secret": True}]
# A free call that needs a valid key: the model list.
CHECK_URL = f"{API_BASE}/models?pageSize=1000"

_BOUND_STORE: Any = None


class GeminiError(Exception):
    """A call that did not produce a picture, with a sentence for the widget."""

    def __init__(self, message: str, *, auth: bool = False) -> None:
        super().__init__(message)
        self.auth = auth


def bind_store(store: Any) -> None:
    """Remember the settings store, so a painting thread with no Flask app
    context can still find the key."""
    global _BOUND_STORE
    _BOUND_STORE = store


def _stored_key(store: Any) -> str:
    if store is None:
        return ""
    try:
        vals = store.get_for_runtime("app", "gemini", FIELDS)
    except Exception:
        return ""
    return str(vals.get("api_key") or "").strip()


def env_key() -> str:
    return (
        os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    )


def resolve_key() -> str:
    """The Gemini key: the one under Settings, else ``GEMINI_API_KEY`` or
    ``GOOGLE_API_KEY``."""
    store = None
    with contextlib.suppress(Exception):
        from flask import current_app

        store = current_app.config.get("SETTINGS_STORE")
    key = _stored_key(store) or _stored_key(_BOUND_STORE)
    return key or env_key()


def aspect_for(aspect: str) -> str:
    """An aspect Gemini takes: the one asked for, else landscape 4:3."""
    return aspect if aspect in ASPECTS else "4:3"


def _ref_part(ref: str) -> dict[str, Any]:
    """A reference picture as an inline part: a data URI passed through, a
    public http(s) URL fetched through the outbound guard."""
    if ref.startswith("data:"):
        head, _, body = ref.partition(",")
        mime = head[5:].split(";", 1)[0] or "image/jpeg"
        if ";base64" not in head:
            raise GeminiError("a reference picture could not be read")
        return {"inline_data": {"mime_type": mime, "data": body}}
    from app import net_guard

    try:
        raw, mime = net_guard.fetch_bytes(ref, timeout=30, max_bytes=MAX_REF_BYTES)
    except Exception as err:
        raise GeminiError(f"a reference picture could not be fetched ({err})") from err
    return {
        "inline_data": {
            "mime_type": mime or "image/jpeg",
            "data": base64.b64encode(raw).decode("ascii"),
        }
    }


def request_body(model: str, prompt: str, refs: Sequence[str], aspect: str) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    parts.extend(_ref_part(r) for r in refs)
    image_config: dict[str, Any] = {"aspectRatio": aspect_for(aspect)}
    if model == MODEL_PRO:
        image_config["imageSize"] = "1K"
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": image_config,
        },
    }


def _post(url: str, body: bytes, key: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def explain(err: Exception) -> str:
    if isinstance(err, urllib.error.HTTPError):
        detail = ""
        with contextlib.suppress(Exception):
            payload = json.loads(err.read(2000).decode("utf-8", "replace"))
            detail = str((payload.get("error") or {}).get("message") or "")
        if err.code in (401, 403) or "API key not valid" in detail:
            return f"Google refused the key (HTTP {err.code})"
        if err.code == 429:
            return f"Google is rate-limiting or the project is out of quota (HTTP 429{': ' + detail[:160] if detail else ''})"
        return f"Google answered HTTP {err.code}{': ' + detail[:200] if detail else ''}"
    if isinstance(err, urllib.error.URLError):
        return f"Google could not be reached ({err.reason})"
    if isinstance(err, TimeoutError):
        return "Google took too long to answer"
    return f"{type(err).__name__}: {str(err)[:200]}"


def pick_image(answer: Any) -> bytes:
    """The first picture in a generateContent answer. Raises
    :class:`GeminiError` saying why there is none."""
    if not isinstance(answer, dict):
        raise GeminiError("Google sent no picture")
    blocked = (answer.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise GeminiError(f"Google declined the prompt ({blocked})")
    candidates = answer.get("candidates") or []
    first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    for part in (first.get("content") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        inline = part.get("inlineData") or part.get("inline_data")
        if isinstance(inline, dict) and inline.get("data"):
            try:
                raw = base64.b64decode(inline["data"])
            except (ValueError, TypeError) as err:
                raise GeminiError("Google sent a picture that could not be read") from err
            if raw:
                return raw
    reason = first.get("finishReason")
    if reason and reason != "STOP":
        raise GeminiError(f"Google sent no picture ({reason})")
    raise GeminiError("Google sent no picture")


def generate(
    key: str,
    prompt: str,
    *,
    refs: Sequence[str] = (),
    aspect: str = "4:3",
    model: str = MODEL,
    timeout: float = TIMEOUT_S,
) -> bytes:
    """One picture from Nano Banana (or ``model``): an edit from ``refs``
    when there are any, else a picture from words alone. Raises
    :class:`GeminiError`."""
    if not key:
        raise GeminiError("no Gemini key")
    body = json.dumps(request_body(model, prompt, refs, aspect)).encode("utf-8")
    try:
        answer = _post(f"{API_BASE}/models/{model}:generateContent", body, key, timeout)
    except Exception as err:
        # Google answers a bad key with 400 INVALID_ARGUMENT as often as 401
        # or 403, so the sentence is what says it was the key.
        message = explain(err)
        raise GeminiError(message, auth="refused the key" in message) from err
    return pick_image(answer)


def check_key(key: str, timeout: float = 8.0) -> tuple[bool, str]:
    """(ok, a sentence). Lists the models the key can use, which costs nothing."""
    key = (key or "").strip()
    if not key:
        return False, "No key to check."
    req = urllib.request.Request(
        CHECK_URL, headers={"x-goog-api-key": key, "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        if err.code in (400, 401, 403):
            return False, "Google refused the key."
        return False, f"Google answered HTTP {err.code}."
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        return False, f"Google could not be reached ({type(err).__name__})."
    ids = {
        str(m.get("name") or "").removeprefix("models/")
        for m in (payload.get("models") or [])
        if isinstance(m, dict)
    }
    if ids and MODEL not in ids and MODEL_PRO not in ids:
        return True, "Google accepted the key, but this project does not list Nano Banana yet."
    return True, "Google accepted the key."
