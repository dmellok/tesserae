"""HTTP helpers for plugin server.py modules.

Centralises the "fetch a JSON document from a flaky upstream API"
pattern that the weather / sky / finance widgets repeat. Adds a single
retry with backoff so a brief TLS handshake timeout or DNS blip doesn't
flash an error in the cell on every refresh tick.

Plugins remain free to use ``urllib`` directly when they need something
more specific (cookies, streaming, multipart), this is just the common
case made one line.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def decode_content_encoding(body: bytes, content_encoding: str) -> bytes:
    """Decode a ``Content-Encoding`` the response carried anyway.

    urllib never sends ``Accept-Encoding``, and RFC 7231 lets a server treat
    an absent header as "any coding acceptable", so a CDN-fronted upstream
    can hand back gzip that urllib does *not* transparently decompress. What
    the caller then parses is a binary blob, and the error it raises names
    the parser rather than the cause: an XML feed reports "not well-formed
    (invalid token): line 1, column 0", which reads like a broken feed
    rather than a compressed one (#168, #212).

    Callers ask for ``identity`` up front; this is the decode for a server
    that ignores that. gzip and deflate are stdlib; anything else (brotli,
    zstd) is left alone, since guessing wrong is worse than passing through.
    """
    enc = content_encoding.lower().strip()
    if enc in ("gzip", "x-gzip"):
        import gzip

        with contextlib.suppress(Exception):
            return gzip.decompress(body)
    elif enc == "deflate":
        import zlib

        # deflate is ambiguous in the wild: zlib-wrapped or raw. Try both.
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            with contextlib.suppress(Exception):
                return zlib.decompress(body, wbits)
    return body


# Conservative defaults, open-meteo, alpha-vantage, etc. are usually
# sub-second but occasionally hang on TLS handshake under flaky LAN.
DEFAULT_TIMEOUT_S: float = 15.0
DEFAULT_USER_AGENT: str = "tesserae/widget (+https://github.com/dmellok/tesserae)"


def _content_encoding(resp: Any) -> str:
    """The response's ``Content-Encoding``, or "" when it hasn't got one.

    A real urllib response always carries ``headers``; a file-like stand-in
    (tests, a caller passing its own opener's result) may not, and a missing
    header is just "no encoding" rather than an error worth failing a fetch
    over."""
    headers = getattr(resp, "headers", None)
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return ""
    return str(getter("Content-Encoding", "") or "")


def decode_body(resp: Any) -> bytes:
    """Read ``resp`` fully and undo any ``Content-Encoding`` it declares.

    For plugins that call ``urllib`` themselves (auth, custom SSL context,
    fallback chains) but still want the compressed-upstream guard the
    ``fetch_*`` helpers apply. Tolerates file-like stand-ins without
    ``headers`` (tests, custom openers)."""
    return decode_content_encoding(resp.read(), _content_encoding(resp))


def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = 0,
    backoff_s: float = 1.0,
) -> str:
    """GET ``url`` and decode the response as UTF-8 text.

    Same retry semantics as ``fetch_json`` but for non-JSON endpoints
    (HTML scrapes, RSS, etc.). Default ``retries=0`` because text
    scrape fallbacks tend to be slow / unreliable upstreams the caller
    is already treating as best-effort, they shouldn't keep the
    dashboard waiting.
    """
    req_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept-Encoding": "identity"}
    if headers:
        req_headers.update(headers)
    last_err: Exception | None = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = decode_content_encoding(resp.read(), _content_encoding(resp))
                text: str = raw.decode("utf-8", errors="ignore")
                return text
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last_err = err
            if attempt < attempts - 1:
                time.sleep(backoff_s)
                continue
            break
    assert last_err is not None
    raise last_err


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = 1,
    backoff_s: float = 1.0,
) -> Any:
    """GET ``url`` and decode the response as JSON.

    Retries on ``URLError``/``HTTPError``/``TimeoutError``/``OSError`` -
    the categories that cover transient TLS handshake failures, DNS
    blips, and 5xx-from-the-upstream. ``retries=1`` means at most two
    total attempts (initial + one retry); the call still raises on the
    final failure, so the existing per-widget ``except`` blocks keep
    surfacing the error to the cell.

    ``headers`` defaults to a single User-Agent. Pass an explicit dict
    to override (e.g. when an API insists on a specific UA string).
    """
    req_headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept-Encoding": "identity"}
    if headers:
        req_headers.update(headers)
    last_err: Exception | None = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = decode_content_encoding(resp.read(), _content_encoding(resp))
                return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
            last_err = err
            if attempt < attempts - 1:
                logger.debug(
                    "plugin_http: %s on %s (attempt %d/%d), retrying in %.1fs",
                    type(err).__name__,
                    url,
                    attempt + 1,
                    attempts,
                    backoff_s,
                )
                time.sleep(backoff_s)
                continue
            break
    assert last_err is not None
    raise last_err
