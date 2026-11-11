"""Admin request provenance without adding tokens to every form.

Cookie-free device/API requests keep their own authentication. Legacy clients
with no Fetch Metadata, Origin or Referer are allowed; an explicitly opaque,
invalid or cross-origin provenance is refused. ProxyFix resolves the actual
request origin; public_url is an additional permitted origin.
"""

from __future__ import annotations

import logging
from threading import Lock
from urllib.parse import urlsplit

from flask import Flask, request
from werkzeug.wrappers import Response

logger = logging.getLogger(__name__)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# These blueprints authenticate independently of browser sessions (tokens or
# pairing codes). Only cookie-free calls bypass provenance checks.
# Never exempt /api/* by path: some API routes
# are admin/session actions, including REST device pairing-code issuance.
_COOKIE_FREE_BLUEPRINTS = frozenset({"webhook", "companion_api", "trmnl_api", "bloomin8_api"})
_COOKIE_FREE_ENDPOINTS = frozenset(
    {
        "rest_api.post_tap",
        "rest_api.post_status",
        "rest_api.post_interact",
        "rest_api.post_log",
        "rest_api.post_discover",
        "rest_api.post_register",
    }
)


def _origin(url: str) -> tuple[str, str, int] | None:
    """Canonical HTTP origin, including default ports; invalid/opaque is None."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        port = parts.port
        default_port = 443 if parts.scheme == "https" else 80
        return parts.scheme, parts.hostname, port if port is not None else default_port
    except ValueError:
        return None


def _format(origin: tuple[str, str, int]) -> str:
    """``scheme://host[:port]`` with the default port left off, brackets on IPv6."""
    scheme, host, port = origin
    if ":" in host:
        host = f"[{host}]"
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" if port == default else f"{scheme}://{host}:{port}"


_FIX_PROXY = (
    "If you reach Tesserae through a reverse proxy, the usual cause is that the proxy "
    "doesn't forward the scheme and host. Set Settings → Server → Public URL to the "
    "address you use in the browser, or make the proxy send X-Forwarded-Proto and "
    "X-Forwarded-Host."
)
_FIX_OPAQUE = (
    "Your browser sent this request without saying where it came from. That's usually "
    "something on this computer stripping it: a privacy or ad-blocking extension, "
    'antivirus or "web protection" software, a VPN or work security client, or a browser '
    "setting that removes referrers. Allow Tesserae's address in that tool, or try a private "
    "window with extensions off to confirm. A reverse proxy that adds "
    '"Referrer-Policy: no-referrer" over plain HTTP does the same thing; reload the page '
    "first, in case it was loaded before an update. Serving Tesserae over HTTPS also avoids it."
)
_FIX_CROSS_SITE = (
    "Your browser reported that this request was started by a page on another site. If you "
    "didn't just click something on another site, open Tesserae directly at its own address "
    "and try again."
)


def _explain(kind: str, seen: str, expected: str) -> str:
    """The 403 body: what happened, what Tesserae saw, and what to do."""
    advice = {"opaque": _FIX_OPAQUE, "fetch-site": _FIX_CROSS_SITE}.get(kind, _FIX_PROXY)
    return (
        "Forbidden: cross-origin admin request blocked (CSRF).\n\n"
        "This request looks like it came from another site. "
        f"{advice}\n\n"
        "If Settings can't be saved from this browser, change it from a device that works.\n\n"
        "What Tesserae saw (include this if you ask for help):\n"
        f"  Browser sent: {seen}\n"
        f"  Expected:     {expected}\n"
    )


def install_protection(app: Flask) -> None:
    """Check unsafe admin requests before auth or route handlers can mutate."""
    logged_routes: set[tuple[str, str]] = set()
    log_lock = Lock()

    @app.before_request
    def _check_provenance() -> Response | None:
        if request.method not in _UNSAFE_METHODS:
            return None
        cookie_name = app.config["SESSION_COOKIE_NAME"]
        if cookie_name not in request.cookies:
            if (
                request.blueprint in _COOKIE_FREE_BLUEPRINTS
                or request.endpoint in _COOKIE_FREE_ENDPOINTS
            ):
                return None
            if request.blueprint == "mcp_api":
                # Loopback identifies the connection, not the initiating page.
                # Only an explicit valid credential exempts browser provenance.
                from app.mcp_api import token_is_authorised

                if token_is_authorised():
                    return None

        fetch_site = request.headers.get("Sec-Fetch-Site")
        actual_origin = request.environ.get("tesserae.request_origin", request.host_url)
        expected = [o for o in (_origin(actual_origin), _origin(request.host_url)) if o]
        source_header = None
        source_origin = None
        if fetch_site is not None:
            allowed = fetch_site in {"same-origin", "none"}
        else:
            source_header = "Origin"
            source = request.headers.get("Origin")
            if source is None:
                source_header = "Referer"
                source = request.headers.get("Referer")
            # Preserve header-less legacy browsers and scripted session clients.
            source_origin = _origin(source) if source is not None else None
            allowed = source is None or (source_origin is not None and source_origin in expected)
        if allowed:
            return None

        # What the browser said vs what Tesserae expected, as bare origins
        # only: never a path, query or user-supplied segment, so it is safe to
        # log and to show. This is what turns a support thread into one reply.
        expected_text = " or ".join(dict.fromkeys(_format(o) for o in expected)) or "unknown"
        if fetch_site is not None:
            seen = f"Sec-Fetch-Site: {fetch_site[:32]}"
            kind = "fetch-site"
        elif source_origin is None:
            seen = f"{source_header}: null (no usable origin)"
            kind = "opaque"
        else:
            seen = f"{source_header}: {_format(source_origin)}"
            kind = "mismatch"

        # Route patterns bound cardinality and avoid logging tokens, referrers,
        # ingress prefixes or user-provided path segments. Once per app process
        # per route and kind of failure.
        route = request.url_rule.rule if request.url_rule is not None else "<unmatched>"
        with log_lock:
            if (route, kind) not in logged_routes:
                logged_routes.add((route, kind))
                logger.warning(
                    "CSRF: blocked cross-origin admin request to %s (browser sent %s; expected %s)",
                    route,
                    seen,
                    expected_text,
                )
        return Response(
            _explain(kind, seen, expected_text),
            status=403,
            content_type="text/plain; charset=utf-8",
        )
