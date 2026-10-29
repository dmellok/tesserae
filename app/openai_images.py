"""OpenAI's Images API as a second way to paint, next to fal.ai.

GPT Image was reachable only through fal.ai, which relays it at its own
price. With an OpenAI key under Settings › Generative AI (``app.openai.api_key``,
else ``OPENAI_API_KEY`` in the environment), :class:`app.fal.FalClient` sends
GPT Image calls straight to OpenAI instead, and an install with only an
OpenAI key paints every picture with GPT Image. Nothing else changes for a
widget: it still asks ``job.fal.generate(model, prompt, refs=...)``.

Two calls: ``/v1/images/generations`` (JSON, words alone) and
``/v1/images/edits`` (multipart, up to 16 reference pictures as files). The
answer carries the picture inline as base64. Reference pictures that are URLs
are fetched through :mod:`app.net_guard`, so a widget cannot point this at
the LAN.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from typing import Any

API_BASE = "https://api.openai.com/v1"
MODEL = "gpt-image-2"
USER_AGENT = "tesserae/artwork"
TIMEOUT_S = 180.0
MAX_REFS = 16
MAX_REF_BYTES = 20 * 1024 * 1024
FIELDS: list[dict[str, Any]] = [{"name": "api_key", "secret": True}]
# A free call that needs a valid key: the model list.
CHECK_URL = f"{API_BASE}/models"

_BOUND_STORE: Any = None


class OpenAIError(Exception):
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
        vals = store.get_for_runtime("app", "openai", FIELDS)
    except Exception:
        return ""
    return str(vals.get("api_key") or "").strip()


def resolve_key() -> str:
    """The OpenAI key: the one under Settings, else ``OPENAI_API_KEY``."""
    store = None
    with contextlib.suppress(Exception):
        from flask import current_app

        store = current_app.config.get("SETTINGS_STORE")
    key = _stored_key(store) or _stored_key(_BOUND_STORE)
    return key or os.environ.get("OPENAI_API_KEY", "").strip()


def size_for(aspect: str) -> str:
    """The Images API size nearest the aspect a widget asked for."""
    if aspect in ("1:1",):
        return "1024x1024"
    if aspect in ("3:4", "9:16", "2:3", "4:5"):
        return "1024x1536"
    return "1536x1024"


def _ref_bytes(ref: str) -> tuple[bytes, str]:
    """A reference picture as bytes and a MIME type: a data URI decoded, a
    public http(s) URL fetched through the outbound guard."""
    if ref.startswith("data:"):
        head, _, body = ref.partition(",")
        mime = head[5:].split(";", 1)[0] or "image/jpeg"
        try:
            raw = base64.b64decode(body, validate=False)
        except (ValueError, TypeError) as err:
            raise OpenAIError("a reference picture could not be read") from err
        return raw, mime
    from app import net_guard

    try:
        raw, mime = net_guard.fetch_bytes(ref, timeout=30, max_bytes=MAX_REF_BYTES)
    except Exception as err:
        raise OpenAIError(f"a reference picture could not be fetched ({err})") from err
    return raw, (mime or "image/jpeg")


def _multipart(fields: dict[str, str], files: list[tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"tesserae-{uuid.uuid4().hex}"
    out: list[bytes] = []
    for name, value in fields.items():
        out.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for i, (name, raw, mime) in enumerate(files):
        ext = "png" if mime.endswith("png") else "webp" if mime.endswith("webp") else "jpg"
        out.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="ref{i}.{ext}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode()
            + raw
            + b"\r\n"
        )
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def _post(url: str, body: bytes, content_type: str, key: str, timeout: float) -> Any:
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": content_type,
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
        if err.code == 401:
            return "OpenAI refused the key (HTTP 401)"
        if err.code == 403:
            return f"OpenAI refused the request (HTTP 403{': ' + detail[:200] if detail else ''})"
        if err.code == 429:
            return f"OpenAI is rate-limiting or the account is out of credit (HTTP 429{': ' + detail[:160] if detail else ''})"
        return f"OpenAI answered HTTP {err.code}{': ' + detail[:200] if detail else ''}"
    if isinstance(err, urllib.error.URLError):
        return f"OpenAI could not be reached ({err.reason})"
    if isinstance(err, TimeoutError):
        return "OpenAI took too long to answer"
    return f"{type(err).__name__}: {str(err)[:200]}"


def generate(
    key: str,
    prompt: str,
    *,
    refs: Sequence[str] = (),
    aspect: str = "4:3",
    timeout: float = TIMEOUT_S,
) -> bytes:
    """One JPEG from GPT Image: an edit from ``refs`` when there are any,
    else a picture from words alone. Raises :class:`OpenAIError`."""
    if not key:
        raise OpenAIError("no OpenAI key")
    fields = {
        "model": MODEL,
        "prompt": prompt,
        "size": size_for(aspect),
        "quality": "medium",
        "output_format": "jpeg",
        "n": "1",
    }
    try:
        if refs:
            files = [("image[]", *_ref_bytes(r)) for r in list(refs)[:MAX_REFS]]
            body, ctype = _multipart(fields, files)
            answer = _post(f"{API_BASE}/images/edits", body, ctype, key, timeout)
        else:
            payload = {k: (1 if k == "n" else v) for k, v in fields.items()}
            answer = _post(
                f"{API_BASE}/images/generations",
                json.dumps(payload).encode("utf-8"),
                "application/json",
                key,
                timeout,
            )
    except OpenAIError:
        raise
    except Exception as err:
        auth = isinstance(err, urllib.error.HTTPError) and err.code == 401
        raise OpenAIError(explain(err), auth=auth) from err
    data = answer.get("data") if isinstance(answer, dict) else None
    first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    b64 = first.get("b64_json")
    if not b64:
        raise OpenAIError("OpenAI sent no picture")
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError) as err:
        raise OpenAIError("OpenAI sent a picture that could not be read") from err
    if not raw:
        raise OpenAIError("OpenAI sent an empty picture")
    return raw


def check_key(key: str, timeout: float = 8.0) -> tuple[bool, str]:
    """(ok, a sentence). Lists the account's models, which costs nothing."""
    key = (key or "").strip()
    if not key:
        return False, "No key to check."
    req = urllib.request.Request(
        CHECK_URL, headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            return False, "OpenAI refused the key."
        return False, f"OpenAI answered HTTP {err.code}."
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        return False, f"OpenAI could not be reached ({type(err).__name__})."
    ids = {str(m.get("id")) for m in (payload.get("data") or []) if isinstance(m, dict)}
    if ids and MODEL not in ids:
        return True, f"OpenAI accepted the key, but this account does not list {MODEL} yet."
    return True, "OpenAI accepted the key."
