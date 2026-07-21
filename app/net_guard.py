"""SSRF-guarded outbound JSON fetch for user-supplied URLs.

Anywhere the app fetches a URL the *user* (or the MCP agent) chose, rather
than a URL baked into a reviewed plugin, it goes through here. The threat is
server-side request forgery: without a guard, a canvas author could point a
"fetch this API" source at ``http://127.0.0.1:8765`` (Tesserae's own loopback
API), a LAN service, or the cloud metadata endpoint, and the server would
dutifully fetch it and hand the body back.

Guards, best-effort (not a substitute for network isolation):

* **Scheme allowlist.** http / https only.
* **Host classification.** Every address the host resolves to is checked;
  loopback / private / link-local / reserved / unspecified are refused.
* **Redirect re-validation.** urllib follows redirects transparently, so a
  public URL that 302s to ``http://169.254.169.254`` would otherwise slip the
  initial host check. Each redirect hop is re-validated before it's followed.
* **Response cap.** Reads at most ``max_bytes`` so a hostile or runaway
  endpoint can't stream gigabytes into memory.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

DEFAULT_TIMEOUT_S: float = 8.0
DEFAULT_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MiB is plenty for a JSON API
_USER_AGENT: str = "tesserae/code-source (+https://github.com/dmellok/tesserae)"


class BlockedURLError(ValueError):
    """A URL was refused by the guard (bad scheme or a private/loopback host).

    Distinct from a network error so callers can tell "we wouldn't fetch this"
    apart from "the fetch failed"."""


def host_is_blocked(host: str) -> bool:
    """True when ``host`` is loopback / private / link-local / reserved, i.e.
    somewhere an untrusted URL must not be allowed to reach.

    Resolves the name and checks every returned address, so a public-looking
    hostname that resolves to ``127.0.0.1`` (or an internal 10.x) is caught.
    An unresolvable host is treated as its literal value so a bare IP literal
    is still classified."""
    if not host or host.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        candidates = [info[4][0] for info in socket.getaddrinfo(host, None)]
    except OSError:
        candidates = [host]
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError("url must be http(s)")
    if host_is_blocked(parsed.hostname or ""):
        raise BlockedURLError("host is loopback/private and not allowed")


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-run the URL guard on every redirect target before following it."""

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Any:
    """GET a user-supplied ``url`` and return the parsed JSON.

    Raises :class:`BlockedURLError` if the URL (or any redirect hop) is
    refused by the guard, ``urllib.error.URLError`` / ``OSError`` /
    ``TimeoutError`` on a network failure, ``ValueError`` if the response is
    over ``max_bytes`` or isn't valid JSON. Callers translate these into an
    ``{"error": ...}`` payload for the cell rather than letting them bubble."""
    _validate_url(url)
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    opener = urllib.request.build_opener(_GuardedRedirect())
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} byte cap")
    return json.loads(raw.decode("utf-8"))
