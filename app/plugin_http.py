"""HTTP helpers for plugin server.py modules.

Centralises the "fetch a JSON document from a flaky upstream API"
pattern that the weather / sky / finance widgets repeat. Adds a single
retry with backoff so a brief TLS handshake timeout or DNS blip doesn't
flash an error in the cell on every refresh tick.

Plugins remain free to use ``urllib`` directly when they need something
more specific (cookies, streaming, multipart) — this is just the common
case made one line.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Conservative defaults — open-meteo, alpha-vantage, etc. are usually
# sub-second but occasionally hang on TLS handshake under flaky LAN.
DEFAULT_TIMEOUT_S: float = 15.0
DEFAULT_USER_AGENT: str = "tesserae/widget (+https://github.com/dmellok/tesserae)"


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    retries: int = 1,
    backoff_s: float = 1.0,
) -> Any:
    """GET ``url`` and decode the response as JSON.

    Retries on ``URLError``/``HTTPError``/``TimeoutError``/``OSError`` —
    the categories that cover transient TLS handshake failures, DNS
    blips, and 5xx-from-the-upstream. ``retries=1`` means at most two
    total attempts (initial + one retry); the call still raises on the
    final failure, so the existing per-widget ``except`` blocks keep
    surfacing the error to the cell.

    ``headers`` defaults to a single User-Agent. Pass an explicit dict
    to override (e.g. when an API insists on a specific UA string).
    """
    req_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        req_headers.update(headers)
    last_err: Exception | None = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
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
