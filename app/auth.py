"""Single-password auth gate.

A single shared password protects the admin surface. The hash + salt live
in ``settings.json`` under the ``auth`` section; sessions are Flask's
built-in signed-cookie sessions keyed off ``app.config['SECRET_KEY']``.

First run: the gate sees no password and redirects every request (except
``/setup``, ``/static``, and the loopback ``/compose`` bypass) to
``/setup`` so the user can pick one.

Loopback bypass: ``/compose/<page_id>`` and ``/renders/<…>`` are reachable
from ``127.0.0.1`` even without a session, the in-process Playwright
renderer needs both, and forcing a session cookie on it just to screenshot
its own server is silly. Anything that isn't loopback still needs auth.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
from dataclasses import dataclass
from typing import Any, Final

from flask import Flask, current_app, redirect, request, session, url_for
from werkzeug.wrappers import Response

from app.state.settings_store import SettingsStore

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
    # auth in app.mcp_api._gate, and 404s unless the ``mcp`` experiment is on,
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
)
_LAN_PATHS: Final[tuple[str, ...]] = ("/renders/", "/preview/", "/mirror/")
# Plugin assets, /plugins/<id>/<asset> only, NOT /plugins/ (the admin
# index, which stays authed). The composer's dynamic import pulls
# /plugins/<id>/client.js while rendering from loopback, so it has to
# pass without a session. The index page is sensitive enough (lists
# loader errors, plugin contents) to keep behind auth.
_PLUGIN_ASSET_PREFIX: Final[str] = "/plugins/"
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


def password_required(settings: SettingsStore) -> bool:
    """True iff the gate should enforce a session. The Settings UI exposes
    a switch that writes ``auth.disabled: true`` here; default is enforce."""
    return not bool(settings.get_section("auth").get("disabled"))


def set_password_disabled(settings: SettingsStore, disabled: bool) -> None:
    """Flip the disabled flag without touching the stored hash. Re-enabling
    later restores the existing password, useful as a temporary "trust LAN"
    toggle without losing the credential."""
    settings.patch_section("auth", {"disabled": bool(disabled)})


# -- session helpers ---------------------------------------------------


def is_authed() -> bool:
    return bool(session.get(SESSION_KEY))


def login() -> None:
    session[SESSION_KEY] = True


def logout() -> None:
    session.pop(SESSION_KEY, None)


# -- before_request gate -----------------------------------------------


def _is_loopback() -> bool:
    return request.remote_addr in _LOOPBACK_HOSTS


def _is_private_client() -> bool:
    addr = request.remote_addr
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_NETWORKS)


def _path_is_open(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _OPEN_PATHS)


def _path_is_loopback_only(path: str) -> bool:
    if any(path.startswith(p) for p in _LOOPBACK_PATHS):
        return True
    # /plugins/<id>/<asset> bypasses for the renderer; bare /plugins/
    # (admin index) does not. Require at least two more segments after
    # the prefix so the index is still gated.
    if path.startswith(_PLUGIN_ASSET_PREFIX):
        tail = path[len(_PLUGIN_ASSET_PREFIX) :]
        return "/" in tail and tail.split("/", 1)[0] != ""
    return False


def _path_is_lan_reachable(path: str) -> bool:
    return any(path.startswith(p) for p in _LAN_PATHS)


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
        if _path_is_loopback_only(path):
            if _is_loopback():
                return None
            if is_authed():
                return None
            return Response("forbidden", status=403)
        # /renders/ is reachable from any private-network client so the
        # Pi / ESP32 can fetch frame artifacts without a session. Still
        # blocked for anything coming in on a public IP.
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
