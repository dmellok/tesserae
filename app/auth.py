"""Single-password auth gate.

A single shared password protects the admin surface. The hash + salt live
in ``settings.json`` under the ``auth`` section; sessions are Flask's
built-in signed-cookie sessions keyed off ``app.config['SECRET_KEY']``.

First run: the gate sees no password and redirects every request (except
``/setup``, ``/static``, and the loopback ``/compose`` bypass) to
``/setup`` so the user can pick one. A client outside the local network
must also present the setup token from the server log (see
``setup_requires_token``).

Loopback bypass: ``/compose/<page_id>`` and ``/renders/<…>`` are reachable
from ``127.0.0.1`` even without a session, the in-process Playwright
renderer needs both, and forcing a session cookie on it just to screenshot
its own server is silly. Anything that isn't loopback still needs auth.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Final

from flask import Flask, current_app, redirect, request, session, url_for
from werkzeug.wrappers import Response

from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)

# PBKDF2-HMAC-SHA256, 200k iterations is the rough 2024 floor for shared
# passwords; bump if/when CPU budget allows. Hashes + salts are stored as
# lowercase hex so they're greppable on disk.
PBKDF2_ITERATIONS: Final[int] = 200_000
SALT_BYTES: Final[int] = 16
KEY_BYTES: Final[int] = 32

SESSION_KEY: Final[str] = "authed"

# Routes that bypass the auth gate entirely. Static + setup + login are
# reachable from anywhere; compose is reachable from loopback (the
# embedded Playwright renderer); renders is reachable from any private-
# network client (the Pi / ESP32 fetching frame artifacts).
_OPEN_PATHS: Final[tuple[str, ...]] = (
    "/static/",
    "/setup",
    "/login",
    "/logout",
    "/healthz",
    # Webhook endpoints under /api/v1/ carry their own token-based auth
    # (see app.webhook_routes._presented_token); the session gate would
    # otherwise bounce every external caller to /login.
    "/api/v1/",
    # MCP API (agentic canvas dashboards). Carries its own token-or-loopback
    # auth in app.mcp_api._gate, and 404s while the ``mcp`` switch is off,
    # so the session gate must not bounce it to /login first.
    "/api/mcp/",
    # TRMNL BYOS protocol endpoints, each request carries an
    # ``access-token`` header that ``app.trmnl_api`` resolves to a
    # device. Jailbroken Kindles + TRMNL devices don't carry
    # sessions, so the gate has to let them through.
    "/api/display",
    "/api/setup",
    "/api/log",
    "/api/trmnl/",
    # Bloomin8 Canvas Schedule Pull API. /eink_pull and /eink_signal carry
    # an ``X-Access-Token`` that ``app.bloomin8_api`` resolves to a device;
    # the frame route serves only frames a pull just handed that device.
    "/api/bloomin8/",
    # Companion API (/api/app/v1/*) for community client apps. Every route
    # bar the unauthenticated capability probe carries its own scoped
    # bearer token (app.companion_api._require_companion); the session gate
    # must not bounce companion clients, which never hold a Flask session,
    # to /login.
    "/api/app/",
)
_LOOPBACK_PATHS: Final[tuple[str, ...]] = (
    "/compose/",
    # Theme CSS endpoints. ``/compose/<id>`` references both via
    # ``<link>`` tags, so the Playwright renderer fetches them while
    # building a panel push. Without the bypass the gate redirects to
    # /login, the CSS comes back as HTML, and community/user themes
    # silently fall back to bundled defaults on the panel even though
    # the in-browser preview (authed session) renders them correctly.
    "/themes/user.css",
    "/themes/community.css",
    # Single-widget dev render behind /api/mcp/widgets/<id>/render.png. The
    # in-process Playwright renderer fetches it over loopback with no session,
    # exactly like /compose/<id>; without this the gate redirects to /login on a
    # password-protected instance and the screenshot captures the login page.
    # Scoped to /_test/render specifically so the rest of the dev gallery
    # (/_test/widgets, ...) stays behind the session gate. composer.test_render
    # is itself 404 unless debug/testing or loopback, so this exposes nothing new.
    "/_test/render",
    # Self-contained @font-face CSS (woff2 as data: URLs) for the code element
    # sandbox. The composer's decorate.js pulls these over loopback while
    # rendering a canvas, same as /plugins/<id>/client.js. Exposes only the
    # already-installed font files, nothing sensitive.
    "/fonts/face/",
    # Cached webfont files (app/font_cache.py): the page CSS references them by
    # same-origin URL and the renderer fetches them over loopback while
    # composing, same as /page-assets. Font bytes only, content-addressed by
    # family slug and face name.
    "/page-fonts/",
    # Per-dashboard cached images. The loopback renderer loads them while
    # composing a canvas; the authed editor preview loads them too. Only images
    # the admin cached into a dashboard's own folder are reachable here.
    "/page-assets/",
)
_LAN_PATHS: Final[tuple[str, ...]] = ("/renders/", "/preview/", "/mirror/")
# The ONE plugin route the loopback exemption is for: the static asset
# handler in app.plugin_loader, whose own allowlist (_ALLOWED_ASSETS)
# bounds what it will serve. The composer's dynamic import pulls
# /plugins/<id>/client.js while rendering from loopback, so it has to
# pass without a session.
#
# Matched by ENDPOINT, not path shape. Plugin-provided blueprints mount
# under the same /plugins/<id>/ prefix, so any prefix test also exempts
# every route a plugin registers: gallery folder deletes (shutil.rmtree),
# calendar feed writes, and CalDAV discovery against an arbitrary URL,
# all reachable with no session from anything that can make the server
# issue a loopback request. The operator screenshot flow is exactly that
# (see app/net_guard.py: allow_local skips the interceptor entirely), and
# there are no CSRF tokens to fall back on.
_PLUGIN_ASSET_ENDPOINT: Final[str] = "plugins.plugin_asset"
# Prefix every plugin blueprint mounts under, used to tell a plugin's own
# sub-route from its index (see _path_is_loopback_only).
_PLUGIN_ROUTE_PREFIX: Final[str] = "/plugins/"
_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost"})
# RFC1918 + loopback + link-local: addresses that can only originate
# from a trusted local network. /renders/ payloads are content-addressed
# (digest in the URL) so guessing one requires knowing what was rendered.
_PRIVATE_NETWORKS: Final[tuple[Any, ...]] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)
# Operator-declared networks that count as private on top of the fixed
# ranges above: a globally routable IPv6 prefix delegated by an ISP, a
# VPN range, a routed IPv4 block. Stored as ``auth.trusted_networks`` (a
# list of CIDR strings) and also taken from ``TESSERAE_TRUSTED_NETWORKS``
# (comma-separated) for Docker / HA installs that template settings.
TRUSTED_NETWORKS_KEY: Final[str] = "trusted_networks"
TRUSTED_NETWORKS_ENV: Final[str] = "TESSERAE_TRUSTED_NETWORKS"


@dataclass(frozen=True)
class _StoredCredential:
    salt_hex: str
    hash_hex: str
    iterations: int

    @classmethod
    def from_section(cls, section: dict[str, object]) -> _StoredCredential | None:
        salt = section.get("password_salt")
        digest = section.get("password_hash_secret") or section.get("password_hash")
        iterations_raw = section.get("password_iterations")
        if not isinstance(salt, str) or not isinstance(digest, str):
            return None
        iterations = (
            int(iterations_raw) if isinstance(iterations_raw, int | str) else PBKDF2_ITERATIONS
        )
        return cls(salt_hex=salt, hash_hex=digest, iterations=iterations)


def hash_password(password: str, *, salt: bytes | None = None) -> tuple[str, str]:
    """Return ``(salt_hex, hash_hex)`` for the given password.

    Generates a fresh random salt unless one is provided. Callers that want
    to re-verify an existing hash should pass the original salt and compare
    against the returned hash with ``hmac.compare_digest``.
    """
    if salt is None:
        salt = os.urandom(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, KEY_BYTES
    )
    return salt.hex(), derived.hex()


def password_is_set(settings: SettingsStore) -> bool:
    """True iff the auth section contains a usable password hash."""
    return _StoredCredential.from_section(settings.get_section("auth")) is not None


def set_password(settings: SettingsStore, password: str) -> None:
    """Hash and persist ``password`` to the auth section."""
    salt_hex, hash_hex = hash_password(password)
    # Store the hash under ``password_hash_secret`` so the on-disk grep for
    # ``secret`` reveals it. Salt is non-secret (knowing it doesn't help an
    # attacker who already has the file).
    settings.patch_section(
        "auth",
        {
            "password_salt": salt_hex,
            "password_hash_secret": hash_hex,
            "password_iterations": PBKDF2_ITERATIONS,
        },
    )


def verify_password(settings: SettingsStore, password: str) -> bool:
    """Constant-time verify the candidate ``password`` against the stored
    hash. Returns False if no password is set or the hash doesn't match."""
    cred = _StoredCredential.from_section(settings.get_section("auth"))
    if cred is None:
        return False
    try:
        salt = bytes.fromhex(cred.salt_hex)
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, cred.iterations, KEY_BYTES
    )
    try:
        expected = bytes.fromhex(cred.hash_hex)
    except ValueError:
        return False
    return hmac.compare_digest(derived, expected)


def clear_password(settings: SettingsStore) -> None:
    """Wipe the auth section. Used by ``tesserae --reset-password`` so the
    next request drops to ``/setup`` and a fresh password can be picked."""
    settings.update_section("auth", {})


_PASSWORD_KEYS = ("password_salt", "password_hash_secret", "password_hash", "password_iterations")


def forget_password(settings: SettingsStore) -> None:
    """Drop the stored hash and nothing else (the disabled flag and the
    trusted networks stay), so enabling the gate again asks for a new
    password instead of restoring the old one (#373)."""
    section = dict(settings.get_section("auth"))
    for key in _PASSWORD_KEYS:
        section.pop(key, None)
    settings.update_section("auth", section)


# First-run setup token. ``/setup`` is open to anyone who can reach the
# port while no password is set, so on an internet-reachable host the first
# stranger to arrive could pick the admin password. A browser on the local
# network (loopback, the private ranges, the operator's trusted networks)
# still sets the password with no extra step; anyone else must also present
# this token, which only someone who can read the server log or the
# container environment knows.
SETUP_TOKEN_KEY: Final[str] = "setup_token_secret"
SETUP_TOKEN_ENV: Final[str] = "TESSERAE_SETUP_TOKEN"
_SETUP_TOKEN_LOCK = threading.Lock()


def _ensure_setup_token(settings: SettingsStore) -> tuple[str, bool, bool]:
    """Return ``(token, from_env, created)``. The environment variable wins;
    otherwise the token is generated once and kept in the auth section so it
    survives a restart. ``clear_password`` wipes it with the rest of the
    section, so a password reset gets a fresh one."""
    configured = os.environ.get(SETUP_TOKEN_ENV, "").strip()
    if configured:
        return configured, True, False
    with _SETUP_TOKEN_LOCK:
        existing = settings.get_section("auth").get(SETUP_TOKEN_KEY)
        if isinstance(existing, str) and existing:
            return existing, False, False
        fresh = secrets.token_urlsafe(16)
        settings.patch_section("auth", {SETUP_TOKEN_KEY: fresh})
        return fresh, False, True


def _log_setup_token(token: str, from_env: bool) -> None:
    if from_env:
        logger.warning(
            "No admin password is set. Browsers on the local network can open /setup "
            "directly; from any other address, setup asks for the token in %s.",
            SETUP_TOKEN_ENV,
        )
        return
    logger.warning(
        "No admin password is set. Browsers on the local network can open /setup "
        "directly; from any other address, setup asks for this setup token: %s",
        token,
    )


def setup_token(settings: SettingsStore) -> str:
    """The first-run setup token, generated (and written to the log) the
    first time it is needed."""
    token, from_env, created = _ensure_setup_token(settings)
    if created:
        _log_setup_token(token, from_env)
    return token


def announce_setup_token(settings: SettingsStore) -> None:
    """Write the setup token to the log at startup while no password is
    set, so it sits near the top of ``docker logs``."""
    if password_is_set(settings):
        return
    token, from_env, _created = _ensure_setup_token(settings)
    _log_setup_token(token, from_env)


def verify_setup_token(settings: SettingsStore, candidate: str) -> bool:
    """Constant-time check of a submitted setup token."""
    expected = setup_token(settings)
    return hmac.compare_digest(candidate.strip().encode("utf-8"), expected.encode("utf-8"))


def discard_setup_token(settings: SettingsStore) -> None:
    """Drop the stored token once a password exists. A later reset
    generates a new one."""
    section = dict(settings.get_section("auth"))
    if section.pop(SETUP_TOKEN_KEY, None) is not None:
        settings.update_section("auth", section)


def setup_requires_token() -> bool:
    """Whether this request must present the setup token to set the first
    password: true for any client the gate does not treat as local."""
    return not (_is_loopback() or _is_private_client())


def password_required(settings: SettingsStore) -> bool:
    """True iff the gate should enforce a session. The Settings UI exposes
    a switch that writes ``auth.disabled: true`` here; default is enforce."""
    return not bool(settings.get_section("auth").get("disabled"))


def set_password_disabled(settings: SettingsStore, disabled: bool) -> None:
    """Flip the disabled flag without touching the stored hash. Re-enabling
    later restores the existing password, useful as a temporary "trust LAN"
    toggle without losing the credential."""
    settings.patch_section("auth", {"disabled": bool(disabled)})


def parse_trusted_networks(raw: str) -> list[str]:
    """Split operator input (one network per line or comma) into
    normalised CIDR strings. Raises ``ValueError`` naming the first entry
    that isn't a network, or that would trust everything (``/0``)."""
    nets: list[str] = []
    for token in raw.replace(",", "\n").splitlines():
        entry = token.strip()
        if not entry:
            continue
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise ValueError(
                f"{entry!r} is not an IP network (use CIDR, e.g. 2001:db8::/48)"
            ) from exc
        if net.prefixlen == 0:
            raise ValueError(f"{entry!r} would trust every address; list your own prefix instead")
        text = str(net)
        if text not in nets:
            nets.append(text)
    return nets


def trusted_networks(settings: SettingsStore) -> list[str]:
    """The operator's stored trusted networks, as CIDR strings."""
    raw = settings.get_section("auth").get(TRUSTED_NETWORKS_KEY)
    if not isinstance(raw, list):
        return []
    return [str(entry) for entry in raw if isinstance(entry, str) and entry]


def set_trusted_networks(settings: SettingsStore, raw: str) -> list[str]:
    """Validate and store the operator's trusted networks. Returns what
    was stored; raises ``ValueError`` (nothing written) on a bad entry."""
    nets = parse_trusted_networks(raw)
    settings.patch_section("auth", {TRUSTED_NETWORKS_KEY: nets})
    return nets


@functools.lru_cache(maxsize=8)
def _parsed_networks(entries: tuple[str, ...]) -> tuple[Any, ...]:
    """CIDR strings to network objects, memoised on the exact tuple so the
    per-request gate doesn't re-parse. Entries that don't parse (a hand-
    edited settings.json, a typo in the env var) are skipped with one
    warning rather than taking the gate down."""
    nets: list[Any] = []
    for entry in entries:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            logger.warning("auth: ignoring trusted network %r (not a CIDR)", entry)
            continue
        if net.prefixlen == 0:
            logger.warning("auth: ignoring trusted network %r (/0 trusts everything)", entry)
            continue
        nets.append(net)
    return tuple(nets)


def _operator_trusted_networks() -> tuple[Any, ...]:
    """Networks the operator added, from settings and the env var. Empty
    outside an app that carries a settings store (bare test contexts)."""
    entries: list[str] = []
    env_raw = current_app.config.get("TRUSTED_NETWORKS")
    if isinstance(env_raw, str) and env_raw.strip():
        entries.extend(t.strip() for t in env_raw.replace(",", "\n").splitlines() if t.strip())
    store = current_app.config.get("SETTINGS_STORE")
    if isinstance(store, SettingsStore):
        entries.extend(trusted_networks(store))
    return _parsed_networks(tuple(entries)) if entries else ()


# -- session helpers ---------------------------------------------------


def is_authed() -> bool:
    return bool(session.get(SESSION_KEY))


def login() -> None:
    session[SESSION_KEY] = True


def logout() -> None:
    session.pop(SESSION_KEY, None)


def shell_locked() -> bool:
    """True when the current page is rendering for someone the gate has
    not admitted as the operator: the sign-in and first-run setup pages
    seen without a session. The admin shell (top nav, device battery
    popover, update badge, version footer) must not render then, since
    it names every registered device and links every admin page to
    whoever can reach the port.

    Every page outside ``_OPEN_PATHS`` only renders after the gate let
    the request through, so those are never locked. The open paths that
    render a template are ``/login`` and ``/setup``; the rest are APIs
    and assets. Mirrors the gate's own operator checks so a signed-in
    admin who lands on ``/login`` still sees the shell."""
    if not _path_is_open(request.path):
        return False
    if is_authed():
        return False
    if current_app.config.get("HA_INGRESS_MODE") and request.headers.get("X-Ingress-Path"):
        return False
    store = current_app.config.get("SETTINGS_STORE")
    if isinstance(store, SettingsStore) and not password_required(store):
        return not (_is_loopback() or _is_private_client())
    return True


# -- before_request gate -----------------------------------------------


def _canonical_ip(addr: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``addr`` parsed to an address object, with IPv4-mapped IPv6
    (``::ffff:a.b.c.d``, what a dual-stack ``::`` bind reports for IPv4
    clients) unwrapped to the plain IPv4 address so the loopback and
    private-range checks below see the family they expect. None when
    ``addr`` is empty or not an IP literal (e.g. ``localhost``)."""
    if not addr:
        return None
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


# Headers a reverse proxy adds when it relays a request. Their presence means
# the real caller is somewhere else, whatever address the request arrives from.
_FORWARDING_HEADERS: Final[tuple[str, ...]] = ("X-Forwarded-For", "Forwarded", "X-Real-IP")


def _direct_peer() -> str | None:
    """The address that actually opened the connection, before ProxyFix
    rewrote ``remote_addr`` from ``X-Forwarded-For``."""
    orig = request.environ.get("werkzeug.proxy_fix.orig") or {}
    return orig.get("REMOTE_ADDR") or request.remote_addr


def _is_loopback() -> bool:
    """A caller on this machine that connected directly. Judged on the real
    peer address with no forwarding headers, so neither a forged
    ``X-Forwarded-For: 127.0.0.1`` from the network nor a reverse proxy on
    the same host (which relays everyone from 127.0.0.1) passes as local.
    The in-process renderer and a co-located agent send none of these."""
    if any(request.headers.get(h) for h in _FORWARDING_HEADERS):
        return False
    peer = _direct_peer()
    if peer in _LOOPBACK_HOSTS:
        return True
    ip = _canonical_ip(peer)
    return ip is not None and ip.is_loopback


def peer_may_forward(addr: str | None, extra_cidrs: str = "") -> bool:
    """Trust the direct proxy peer using private defaults plus explicit CIDRs.

    ``extra_cidrs`` comes from the separate TRUSTED_PROXIES app config. This
    runs before a Flask context exists and never uses client-authorisation
    networks. Invalid or universal CIDRs are ignored by the shared parser.
    """
    ip = _canonical_ip(addr)
    if ip is None:
        return False
    if any(ip in net for net in _PRIVATE_NETWORKS):
        return True
    entries = tuple(
        part.strip() for part in extra_cidrs.replace(",", "\n").splitlines() if part.strip()
    )
    return any(ip in net for net in _parsed_networks(entries))


def _is_private_client() -> bool:
    """Whether the request comes from a network the gate treats as the
    local LAN: the fixed private ranges, plus anything the operator listed
    under Settings → System → Authentication → Trusted networks (or the
    ``TESSERAE_TRUSTED_NETWORKS`` env var). The second half is what lets a
    home network on a globally routable IPv6 prefix count as local."""
    ip = _canonical_ip(request.remote_addr)
    if ip is None:
        return False
    if any(ip in net for net in _PRIVATE_NETWORKS):
        return True
    return any(ip in net for net in _operator_trusted_networks())


def _path_is_open(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _OPEN_PATHS)


def _path_is_loopback_only(
    path: str,
    endpoint: str | None = None,
    render_safe: frozenset[str] = frozenset(),
    method: str = "GET",
) -> bool:
    """Whether this request may skip the password gate when it comes from
    loopback.

    ``endpoint`` is Flask's resolved endpoint (available by the time
    ``before_request`` runs); None, as for an unmatched path, grants nothing.
    ``render_safe`` is the set of plugin endpoints a plugin declared through
    ``RENDER_SAFE_ENDPOINTS``, collected by the loader.

    Three things pass:

    1. The static asset handler, which the composer needs for every widget's
       ``client.js`` and whose own allowlist bounds what it serves.
    2. Anything a plugin explicitly declared render-safe.
    3. A **read** of a plugin's own sub-route.

    Rule 3 exists because rules 1 and 2 were not enough (#255). A widget that
    serves its own media from its blueprint, which an installed catalog widget
    may well do, renders with a broken image on every panel and no way for its
    author to know why: the declaration is an in-tree convention they never
    saw. Read-only is the line that keeps the original hole shut, because
    every dangerous route found when this was tightened was a mutation:
    ``shutil.rmtree`` on a gallery folder, calendar feed writes, CalDAV
    discovery against a caller-supplied URL. Those stay gated.

    A plugin's INDEX (``/plugins/<id>/``) also stays gated even on a read: it
    lists loader errors and plugin contents, and gtfs turns a query argument
    on it into an outbound request. That is the case #221 closed and this must
    not reopen it, so the exemption needs a non-empty tail after the plugin id.
    """
    if any(path.startswith(p) for p in _LOOPBACK_PATHS):
        return True
    if endpoint is None:
        return False
    if endpoint == _PLUGIN_ASSET_ENDPOINT or endpoint in render_safe:
        return True
    if method.upper() not in ("GET", "HEAD"):
        return False
    if not path.startswith(_PLUGIN_ROUTE_PREFIX):
        return False
    _plugin_id, _, tail = path[len(_PLUGIN_ROUTE_PREFIX) :].partition("/")
    return bool(_plugin_id) and bool(tail)


def _path_is_lan_reachable(path: str) -> bool:
    return any(path.startswith(p) for p in _LAN_PATHS)


def _public_rest_clients_enabled() -> bool:
    """Operator opt-in (Settings → Server → Network, default off). Enabling
    it accepts that a device's signed, short-lived frame URL is fetchable
    from a public address; off, render artifacts stay LAN/session only."""
    store = current_app.config.get("SETTINGS_STORE")
    if store is None:
        return False
    return bool((store.get_section("app") or {}).get("public_rest_clients_enabled"))


def _has_valid_render_signature(path: str) -> bool:
    """True iff public REST access is enabled AND the request carries a valid
    render signature for ``path``.

    Gated behind the operator opt-in so a public instance doesn't serve
    signed render URLs unless the admin deliberately turned it on. Only
    ``/renders/`` artifacts are ever handed out signed by ``/frame``;
    ``/preview/`` and ``/mirror/`` never carry one, so they stay strictly
    LAN/session-reachable. Keyed off the Flask session secret."""
    if not path.startswith("/renders/"):
        return False
    if not _public_rest_clients_enabled():
        return False
    from app.render_signing import render_signature_valid

    return render_signature_valid(current_app.secret_key, path, request.args.get("sig"))


def install_gate(app: Flask, settings: SettingsStore) -> None:
    """Register the before_request handler that redirects unauthenticated
    traffic. Idempotent, calling twice replaces the handler (Flask
    deduplicates by function identity)."""

    @app.before_request
    def _gate() -> Response | None:
        path = request.path
        # Open paths and static assets are always reachable.
        if _path_is_open(path):
            return None
        # Home Assistant Ingress: HA Supervisor reverse-proxies this
        # request and the ``X-Ingress-Path`` header is its proof that
        # the user is already authenticated upstream. Trust it and
        # bypass our own password gate entirely. We require BOTH the
        # env-var opt-in AND the header so a stray header from a
        # misconfigured reverse proxy on a non-ingress install can't
        # bypass auth.
        if current_app.config.get("HA_INGRESS_MODE") and request.headers.get("X-Ingress-Path"):
            return None
        # A valid, unexpired render signature (minted by /frame) grants
        # access to that one artifact from any origin, independent of the
        # password gate and the LAN check (issue #151). Scoped to
        # ``/renders/`` inside the helper, so nothing else is affected. This
        # sits ahead of the password-disabled branch so a public-hosted
        # device can still fetch its own frame on a no-password install.
        if _has_valid_render_signature(path):
            return None
        # Admin opted out of the password (Settings → System → Auth).
        # Private network + loopback clients reach anything; public IPs
        # still 403 so a disabled-auth LAN install doesn't expose the
        # admin UI to the open internet by accident.
        if not password_required(settings):
            if _is_loopback() or _is_private_client():
                return None
            return Response("forbidden", status=403)
        # Compose is loopback-only (the in-process Playwright renderer)
        # OR authed (the editor's preview iframe loads it over the LAN).
        if _path_is_loopback_only(
            path,
            request.endpoint,
            current_app.config.get("RENDER_SAFE_ENDPOINTS", frozenset()),
            request.method,
        ):
            if _is_loopback():
                return None
            if is_authed():
                return None
            return Response("forbidden", status=403)
        # /renders/ is reachable from any private-network client so the
        # Pi / ESP32 can fetch frame artifacts without a session. Still
        # blocked for anything coming in on a public IP (a valid render
        # signature is handled earlier and lets a public-hosted device
        # through without opening /renders/ to the internet wholesale).
        if _path_is_lan_reachable(path):
            if _is_private_client():
                return None
            if is_authed():
                return None
            return Response("forbidden", status=403)
        # First-run: no password yet, redirect everything to setup.
        if not password_is_set(settings):
            return redirect(url_for("auth.setup"))
        # Authed sessions pass.
        if is_authed():
            return None
        return redirect(url_for("auth.login_view", next=path))


def secret_key(settings: SettingsStore) -> bytes:
    """Return the Flask session secret. Persists a fresh random key on
    first call so sessions survive process restarts."""
    section = settings.get_section("app")
    existing = section.get("session_secret_secret") if isinstance(section, dict) else None
    if isinstance(existing, str) and existing:
        try:
            return bytes.fromhex(existing)
        except ValueError:
            pass
    fresh = secrets.token_bytes(32)
    settings.patch_section("app", {"session_secret_secret": fresh.hex()})
    return fresh
