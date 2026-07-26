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


def host_is_blocked(host: str, *, allow_local: bool = False) -> bool:
    """True when ``host`` is somewhere an outbound fetch must not reach.

    Always blocks link-local / reserved / multicast / unspecified, regardless
    of ``allow_local``: link-local (169.254.169.254) is the cloud-instance
    metadata endpoint, and the rest are never a legitimate fetch target.

    Loopback and RFC1918 private ranges are blocked by default (the strict
    mode the generic widget fetch path uses), but ``allow_local=True`` permits
    them. The webpage-screenshot and remote-image *operator* flows run with
    ``allow_local=True`` so a self-hosted appliance can capture a same-host
    service (``127.0.0.1:3000``) or a LAN dashboard / NAS. That deliberately
    leaves the loopback auth-bypass surface (see ``app/auth.py``) reachable
    from those operator flows, an accepted tradeoff for same-host capture.

    Resolves the name and checks every returned address, so a public-looking
    hostname that resolves to ``127.0.0.1`` (or an internal 10.x) is caught.
    An unresolvable host is treated as its literal value so a bare IP literal
    is still classified."""
    if not host:
        return True
    if host.lower() in ("localhost", "localhost.localdomain"):
        return not allow_local
    try:
        candidates = [info[4][0] for info in socket.getaddrinfo(host, None)]
    except OSError:
        candidates = [host]
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        # Never a legitimate target, even for operator flows: link-local
        # (cloud metadata), multicast, unspecified. is_reserved is deliberately
        # NOT here: IPv6 ::1 loopback sits inside a reserved block, and operator
        # flows must be able to allow loopback.
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return True
        # Loopback + RFC1918 private (+ other reserved bogons): strict by
        # default, permitted for operator flows (same-host / LAN capture).
        if not allow_local and (ip.is_loopback or ip.is_private or ip.is_reserved):
            return True
    return False


def _validate_url(url: str, *, allow_local: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError("url must be http(s)")
    if host_is_blocked(parsed.hostname or "", allow_local=allow_local):
        raise BlockedURLError("host is link-local/metadata (or loopback/private in strict mode)")


def assert_operator_url(url: str) -> None:
    """Guard an operator-supplied URL (webpage screenshot, remote image push).

    Allows same-host loopback and RFC1918 LAN targets but still refuses
    link-local / cloud metadata. Raises :class:`BlockedURLError` on refusal.
    Use this for the
    initial-URL check on fetch paths that follow redirects outside this module
    (e.g. Playwright ``page.goto``); the resolve-and-check here catches the
    obvious cases, but such paths don't re-validate every redirect hop."""
    _validate_url(url, allow_local=True)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-run the URL guard on every redirect target before following it."""

    def __init__(self, *, allow_local: bool = False) -> None:
        super().__init__()
        self._allow_local = allow_local

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        _validate_url(newurl, allow_local=self._allow_local)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_local: bool = False,
) -> Any:
    """GET a user-supplied ``url`` and return the parsed JSON.

    Raises :class:`BlockedURLError` if the URL (or any redirect hop) is
    refused by the guard, ``urllib.error.URLError`` / ``OSError`` /
    ``TimeoutError`` on a network failure, ``ValueError`` if the response is
    over ``max_bytes`` or isn't valid JSON. Callers translate these into an
    ``{"error": ...}`` payload for the cell rather than letting them bubble."""
    raw, _ = fetch_bytes(
        url, headers=headers, timeout=timeout, max_bytes=max_bytes, allow_local=allow_local
    )
    return json.loads(raw.decode("utf-8"))


def fetch_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_local: bool = False,
) -> tuple[bytes, str]:
    """GET a user-supplied ``url`` and return ``(body, content_type)``.

    Same SSRF guard, redirect re-validation, and size cap as
    :func:`fetch_json`; used for caching remote images into a dashboard's asset
    folder. ``content_type`` is the (lower-cased, parameter-stripped) response
    ``Content-Type``, or ``""`` if the server didn't send one. ``allow_local``
    permits same-host loopback and RFC1918 LAN targets for operator-driven
    flows (still refuses link-local / cloud metadata)."""
    _validate_url(url, allow_local=allow_local)
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    opener = urllib.request.build_opener(_GuardedRedirect(allow_local=allow_local))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)
        content_type = str(resp.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    if len(raw) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} byte cap")
    return raw, content_type
