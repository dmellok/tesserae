"""One HTTP call shape for every upstream this widget talks to.

Feeds, article pages, Groq and four image providers all want the same
thing: a request that never raises, a status code (0 when nothing answered),
the body capped at a size, and a short reason on failure. Callers branch on
that instead of on urllib's exception zoo, and the tests replace
:func:`request` alone to keep every byte off the network.

Three guards hold for every call, because article links come from feeds and
so from anyone:

* **Public addresses only.** The URL and every redirect hop go through the
  host's SSRF guard (``app.net_guard``): no loopback, LAN, link-local or cloud
  metadata targets, so a feed cannot steer the server at an intranet page
  and paste it into a Groq prompt. Redirects are followed here, not by
  urllib, so each hop is checked first and gets only the time left.
* **A total time limit.** ``timeout`` bounds the whole call, connect and every
  read, so a server dripping bytes slowly cannot hold a run past its budget.
* **A size limit on what is decoded,** not just on what crossed the wire, so
  a small gzip bomb cannot expand into memory.

Nothing here logs, and failure reasons never carry exception text that could
hold a header or a URL (a key can sit in either).
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from app.net_guard import BlockedURLError, assert_public_url, canonical_url, guarded_opener

# A news site or an image CDN answering with something bigger than this is
# not sending what we asked for.
MAX_BYTES = 25 * 1024 * 1024
CHUNK = 64 * 1024
# APIs explain an error in their body; this much is enough to read why.
ERROR_BODY_BYTES = 4096
MAX_REDIRECTS = 5
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
API_USER_AGENT = "tesserae/0.1 (+news_illustrated)"
# Article pages and some feeds sit behind bot filters that refuse a
# bot-shaped User-Agent outright (the PHP original learned this the hard way).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Reply:
    """What came back. ``status`` is 0 when no HTTP answer arrived."""

    status: int
    body: bytes = b""
    content_type: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.error

    def json(self) -> Any:
        """The body as JSON, or None when it is not JSON."""
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    def text(self) -> str:
        """The body as text, in the charset the server named (UTF-8 otherwise)."""
        charset = "utf-8"
        for part in self.content_type.split(";")[1:]:
            name, _, value = part.strip().partition("=")
            if name.lower() == "charset" and value:
                charset = value.strip("\"' ")
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def reason(self) -> str:
        """A short, key-free sentence for the run log."""
        if self.error:
            return self.error
        return f"HTTP {self.status}"


class _TooLarge(Exception):
    pass


class _OutOfTime(Exception):
    pass


class _Redirect(Exception):
    def __init__(self, url: str, method: str, body: bytes | None) -> None:
        super().__init__(url)
        self.url, self.method, self.body = url, method, body


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Turn urllib's automatic redirects off: it drains each 3xx body with an
    unbounded read and reuses the first timeout for every hop. A 3xx then
    arrives as an HTTPError and :func:`request` follows it itself."""

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        return None


def request(
    url: str,
    *,
    timeout: float,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    max_bytes: int = MAX_BYTES,
) -> Reply:
    """Make one request in at most ``timeout`` seconds overall, redirects
    included; never raises. An HTTP error status still carries its body
    (truncated), since the APIs explain themselves there."""
    deadline = time.monotonic() + timeout
    if timeout <= 0:
        return Reply(0, error="no time left")
    sent = {"User-Agent": API_USER_AGENT, "Accept-Encoding": "gzip, deflate", **(headers or {})}
    for hop in range(MAX_REDIRECTS + 1):
        try:
            assert_public_url(url)
            url = canonical_url(url)
        except BlockedURLError:
            where = "redirected to a non-public address" if hop else "not a public address"
            return Reply(0, error=f"refused: {where}")
        if deadline - time.monotonic() <= 0:
            return Reply(0, error="timed out")
        try:
            return _once(url, method, sent, body, deadline, max_bytes)
        except _Redirect as moved:
            url, method, body = moved.url, moved.method, moved.body
    return Reply(0, error="too many redirects")


def _once(
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    deadline: float,
    max_bytes: int,
) -> Reply:
    """One hop, with whatever time is left. Raises _Redirect for a 3xx."""
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        # Pinned: the connection goes to an address checked for this very
        # request, never one a second DNS lookup returns (rebinding); this
        # module follows redirects itself, checking each hop.
        opener = guarded_opener(allow_local=False, redirects=_NoRedirects())
        with opener.open(req, timeout=max(0.05, deadline - time.monotonic())) as resp:
            raw = _read(resp, deadline, max_bytes)
            encoding = str(resp.headers.get("Content-Encoding", "") or "")
            content_type = str(resp.headers.get("Content-Type", "") or "")
            return Reply(int(resp.status), decode(raw, encoding, max_bytes), content_type)
    except urllib.error.HTTPError as err:
        err_headers = getattr(err, "headers", None)
        location = str(err_headers.get("Location", "") or "") if err_headers else ""
        if err.code in _REDIRECTS and location:
            err.close()  # not drained: the body of a redirect is never needed
            if err.code == 303 or (err.code in (301, 302) and method == "POST"):
                raise _Redirect(urljoin(url, location), "GET", None) from None
            raise _Redirect(urljoin(url, location), method, body) from None
        try:
            # The error body is read under the same deadline: an API explaining
            # itself slowly must not hold the run past its budget.
            raw = decode(
                _read(err, deadline, ERROR_BODY_BYTES, truncate=True),
                str(err_headers.get("Content-Encoding", "") or "") if err_headers else "",
                64 * 1024,
            )
        except (_OutOfTime, TimeoutError):
            return Reply(0, error="timed out")
        except Exception:
            raw = b""
        finally:
            err.close()
        content_type = str(err_headers.get("Content-Type", "") or "") if err_headers else ""
        return Reply(int(err.code), raw, content_type)
    except BlockedURLError:
        return Reply(0, error="refused: not a public address")
    except _TooLarge:
        return Reply(0, error="response too large")
    except (_OutOfTime, TimeoutError):
        return Reply(0, error="timed out")
    except urllib.error.URLError as err:
        reason = err.reason
        if isinstance(reason, TimeoutError):
            return Reply(0, error="timed out")
        return Reply(0, error=f"could not connect ({type(reason).__name__})")
    except OSError as err:
        return Reply(0, error=f"connection failed ({type(err).__name__})")
    except ValueError:
        # http.client quotes a bad header value (a key with a newline in it)
        # back in its message, so the message is dropped.
        return Reply(0, error="invalid request")


def post_json(
    url: str, payload: dict[str, Any], *, timeout: float, headers: dict[str, str] | None = None
) -> Reply:
    """POST ``payload`` as JSON."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent = {"Content-Type": "application/json", "Accept": "application/json", **(headers or {})}
    return request(url, timeout=timeout, method="POST", headers=sent, body=data)


def _read(resp: Any, deadline: float, max_bytes: int, *, truncate: bool = False) -> bytes:
    """The body, one network read at a time, inside the deadline and the cap.
    Past the cap it is an error, or with ``truncate`` the first ``max_bytes``."""
    chunks: list[bytes] = []
    size = 0
    read = getattr(resp, "read1", None) or resp.read
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _OutOfTime
        _socket_timeout(resp, left)
        chunk: bytes = read(min(CHUNK, max_bytes + 1 - size))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > max_bytes:
            if truncate:
                return b"".join(chunks) + chunk[: max_bytes - (size - len(chunk))]
            raise _TooLarge
        chunks.append(chunk)


def _socket_timeout(resp: Any, seconds: float) -> None:
    """Shrink the socket timeout to what is left, so the read that straddles
    the deadline cannot wait a whole timeout past it. Best effort: the socket
    sits behind private attributes of http.client."""
    with contextlib.suppress(AttributeError, OSError, ValueError):
        resp.fp.raw._sock.settimeout(max(0.05, seconds))


def decode(raw: bytes, content_encoding: str, max_bytes: int) -> bytes:
    """Undo gzip or deflate, refusing output over ``max_bytes``. Other
    encodings pass through untouched."""
    encoding = content_encoding.lower().strip()
    if encoding in ("gzip", "x-gzip"):
        attempts = [zlib.MAX_WBITS | 16]
    elif encoding == "deflate":
        # deflate is ambiguous in the wild: zlib-wrapped or raw.
        attempts = [zlib.MAX_WBITS, -zlib.MAX_WBITS]
    else:
        return raw
    for wbits in attempts:
        inflater = zlib.decompressobj(wbits)
        try:
            out = inflater.decompress(raw, max_bytes + 1)
        except zlib.error:
            continue
        if len(out) > max_bytes or inflater.unconsumed_tail:
            raise _TooLarge
        return out
    return raw
