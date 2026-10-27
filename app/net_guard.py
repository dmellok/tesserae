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
* **Pinned connections.** ``fetch_bytes`` / ``fetch_json`` (and anything
  using :func:`guarded_opener`) connect to an address that was checked,
  trying each checked address in turn, never one a second DNS lookup
  returns; the Host header and TLS name are the IDNA form of the host.
* **No proxies.** That path ignores ``http_proxy`` / ``https_proxy``: a
  proxy would resolve the host itself and undo the check. A deployment
  that can only reach the web through a proxy can't fetch user-chosen URLs
  (remote images, fonts, REST widgets, comics imports) until this grows an
  explicit, checked proxy setting. Other outbound calls (updates, the
  marketplace) are unaffected.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import functools
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
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


def _ip_blocked(addr: str, *, allow_local: bool) -> bool:
    """Whether one resolved address is somewhere a fetch must not reach.
    An IPv4 address wrapped in IPv6 (``::ffff:127.0.0.1``, 6to4, NAT64's
    well-known prefix) is judged as the IPv4 address it carries."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    # Never a legitimate target, even for operator flows: link-local
    # (cloud metadata), multicast, unspecified. is_reserved is deliberately
    # NOT here: IPv6 ::1 loopback sits inside a reserved block, and operator
    # flows must be able to allow loopback.
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    # Loopback + RFC1918 private (+ other reserved bogons): strict by
    # default, permitted for operator flows (same-host / LAN capture).
    return not allow_local and (ip.is_loopback or ip.is_private or ip.is_reserved)


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NUMERIC_HOST_RE = re.compile(r"^(?:0x[0-9a-f]*|[0-9]+)(?:\.(?:0x[0-9a-f]*|[0-9]+))*$")


def canonical_host(raw: str) -> str:
    """``raw`` (a URL's host) the way the socket layer will see it:
    percent-decoded (urllib decodes ``127%2e0%2e0%2e1`` before connecting),
    lower-cased, without a trailing dot, and IDNA-encoded. Raises
    :class:`BlockedURLError` for a host that can't be one."""
    host = urllib.parse.unquote(raw or "").strip().rstrip(".").lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or any(c in host for c in "%/\\@?#[] \t\r\n\x00"):
        raise BlockedURLError("malformed host")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return host
    except ValueError:
        pass
    if _NUMERIC_HOST_RE.match(host):
        # 2130706433, 0177.0.0.1, 0x7f.1: resolvers disagree on what these
        # mean (decimal, octal, hex), so none is taken.
        raise BlockedURLError("ambiguous numeric host")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as err:
        raise BlockedURLError("malformed host") from err


def resolve_checked(host: str, port: int | None = None, *, allow_local: bool = False) -> list[str]:
    """Every address ``host`` resolves to, each checked; raises
    :class:`BlockedURLError` when the host doesn't resolve or any address is
    off limits. Decimal, octal and other inet_aton spellings of an address
    resolve to it here, so they are judged by what they mean."""
    host = canonical_host(host)
    if host in ("localhost", "localhost.localdomain") and not allow_local:
        raise BlockedURLError("host is loopback")
    try:
        infos = socket.getaddrinfo(host, port or 0, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as err:
        raise BlockedURLError("host doesn't resolve") from err
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise BlockedURLError("host doesn't resolve")
    for addr in addresses:
        if _ip_blocked(addr, allow_local=allow_local):
            raise BlockedURLError(
                "host is link-local/metadata (or loopback/private in strict mode)"
            )
    return addresses


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

    The host is canonicalised first (percent-decoding, IDNA), every address
    it resolves to is checked (IPv4-in-IPv6 as its IPv4 address), and a host
    that doesn't resolve, or can't be a host, is blocked."""
    try:
        resolve_checked(host, allow_local=allow_local)
    except BlockedURLError:
        return True
    return False


def _validate_url(url: str, *, allow_local: bool = False) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as err:
        raise BlockedURLError("malformed url") from err
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError("url must be http(s)")
    resolve_checked(parsed.hostname or "", port, allow_local=allow_local)


def assert_operator_url(url: str) -> None:
    """Guard an operator-supplied URL (webpage screenshot, remote image push).

    Allows same-host loopback and RFC1918 LAN targets but still refuses
    link-local / cloud metadata. Raises :class:`BlockedURLError` on refusal.
    Use this for the
    initial-URL check on fetch paths that follow redirects outside this module
    (e.g. Playwright ``page.goto``); the resolve-and-check here catches the
    obvious cases, but such paths don't re-validate every redirect hop."""
    _validate_url(url, allow_local=True)


def assert_public_url(url: str) -> None:
    """Strict variant of :func:`assert_operator_url` for untrusted callers.

    Refuses loopback, private (RFC1918), link-local, reserved, and cloud
    metadata destinations with no override. Used by the Companion link-send
    routes, which must never reach the operator's LAN. This is only the
    initial-URL check: fetch / render paths that follow redirects must ALSO
    run in strict mode (``fetch_bytes(allow_local=False)`` re-validates every
    hop; the webpage renderer's request interceptor guards each Chromium
    navigation), because this call alone can't see a redirect to a private
    host."""
    _validate_url(url, allow_local=False)


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-run the URL guard on every redirect target before following it."""

    def __init__(self, *, allow_local: bool = False) -> None:
        super().__init__()
        self._allow_local = allow_local

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        _validate_url(newurl, allow_local=self._allow_local)
        return super().redirect_request(req, fp, code, msg, headers, canonical_url(newurl))


# -- connecting to the address that was checked ---------------------------------
#
# Checking a host and then letting urllib resolve it again would leave a gap:
# a DNS answer that changes between the two (rebinding) sends the request to
# an address nobody checked. These connections resolve and check once per
# request (each redirect hop is a request) and connect to that address,
# keeping the URL's host for the Host header and TLS (SNI, certificate).


def canonical_url(url: str) -> str:
    """``url`` with its host as the socket and the wire will see it:
    percent-decoded and IDNA-encoded (``bücher.example`` becomes
    ``xn--bcher-kva.example``), the port kept, user info dropped. The Host
    header and TLS's SNI are then plain ASCII, the name that was checked.
    Raises :class:`BlockedURLError` for a URL that can't be one."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as err:
        raise BlockedURLError("malformed url") from err
    host = canonical_host(parts.hostname or "")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _pin(req: urllib.request.Request, allow_local: bool) -> list[str]:
    try:
        parts = urllib.parse.urlsplit(req.full_url)
        port = parts.port
    except ValueError as err:
        raise BlockedURLError("malformed url") from err
    return resolve_checked(parts.hostname or "", port, allow_local=allow_local)


def _connect(addresses: list[str], port: int, timeout: Any, source: Any) -> socket.socket:
    """A connection to the first of the checked ``addresses`` that answers,
    in the order the resolver gave them (an IPv6 address that can't be
    reached falls back to IPv4), all inside ``timeout``."""
    deadline = None
    if isinstance(timeout, (int, float)):
        deadline = time.monotonic() + timeout
    failure: OSError | None = None
    for addr in addresses:
        left = timeout
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                break
        try:
            return socket.create_connection((addr, port), left, source)
        except OSError as err:
            failure = err
    raise failure or TimeoutError("timed out")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, pinned: list[str], **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned = pinned

    def connect(self) -> None:
        self.sock = _connect(
            self._pinned, self.port, self.timeout, getattr(self, "source_address", None)
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, host: str, *, pinned: list[str], context: ssl.SSLContext, **kwargs: Any
    ) -> None:
        super().__init__(host, context=context, **kwargs)
        self._pinned = pinned
        self._tls = context

    def connect(self) -> None:
        deadline = (
            time.monotonic() + self.timeout if isinstance(self.timeout, (int, float)) else None
        )
        sock = _connect(
            self._pinned, self.port, self.timeout, getattr(self, "source_address", None)
        )
        try:
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("timed out")
                sock.settimeout(left)
            self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, *, allow_local: bool = False) -> None:
        super().__init__()
        self._allow_local = allow_local

    def http_open(self, req: urllib.request.Request) -> Any:
        pinned = _pin(req, self._allow_local)
        return self.do_open(functools.partial(_PinnedHTTPConnection, pinned=pinned), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, *, allow_local: bool = False) -> None:
        self._tls = ssl.create_default_context()
        super().__init__(context=self._tls)
        self._allow_local = allow_local

    def https_open(self, req: urllib.request.Request) -> Any:
        pinned = _pin(req, self._allow_local)
        return self.do_open(
            functools.partial(_PinnedHTTPSConnection, pinned=pinned, context=self._tls), req
        )


def guarded_opener(
    *,
    allow_local: bool = False,
    redirects: urllib.request.BaseHandler | None = None,
) -> urllib.request.OpenerDirector:
    """An opener whose every request (and redirect hop) is checked and
    connects to an address it was checked at. ``redirects`` replaces the
    guarded redirect follower (a caller that follows redirects itself passes
    a handler that doesn't). Proxies are not used, deliberately: a proxy
    resolves the host itself, which would undo the check, so the
    ``http_proxy`` / ``https_proxy`` environment variables are ignored on
    this path (user-chosen URLs only; other outbound calls keep them)."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        redirects if redirects is not None else _GuardedRedirect(allow_local=allow_local),
        _PinnedHTTPHandler(allow_local=allow_local),
        _PinnedHTTPSHandler(allow_local=allow_local),
    )


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
    req = urllib.request.Request(canonical_url(url), headers=req_headers)
    opener = guarded_opener(allow_local=allow_local)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(max_bytes + 1)
        content_type = str(resp.headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
    if len(raw) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} byte cap")
    return raw, content_type
