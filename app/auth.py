"""Single-password auth gate.

A single shared password protects the admin surface. The hash + salt live
in ``settings.json`` under the ``auth`` section; sessions are Flask's
built-in signed-cookie sessions keyed off ``app.config['SECRET_KEY']``.

First run: the gate sees no password and redirects every request (except
``/setup``, ``/static``, and the loopback ``/compose`` bypass) to
``/setup`` so the user can pick one.

Loopback bypass: ``/compose/<page_id>`` and ``/renders/<…>`` are reachable
from ``127.0.0.1`` even without a session — the in-process Playwright
renderer needs both, and forcing a session cookie on it just to screenshot
its own server is silly. Anything that isn't loopback still needs auth.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Final

from flask import Flask, redirect, request, session, url_for
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
# reachable from anywhere; compose + renders are reachable only from
# loopback (the embedded renderer).
_OPEN_PATHS: Final[tuple[str, ...]] = ("/static/", "/setup", "/login", "/logout", "/healthz")
_LOOPBACK_PATHS: Final[tuple[str, ...]] = ("/compose/", "/renders/")
_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost"})


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


def _path_is_open(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _OPEN_PATHS)


def _path_is_loopback_only(path: str) -> bool:
    return any(path.startswith(p) for p in _LOOPBACK_PATHS)


def install_gate(app: Flask, settings: SettingsStore) -> None:
    """Register the before_request handler that redirects unauthenticated
    traffic. Idempotent — calling twice replaces the handler (Flask
    deduplicates by function identity)."""

    @app.before_request
    def _gate() -> Response | None:
        path = request.path
        # Open paths and static assets are always reachable.
        if _path_is_open(path):
            return None
        # Compose / renders are reachable from loopback (the in-process
        # Playwright renderer + panel-side artifact fetches) OR from an
        # authed admin session (the editor's preview iframe loads /compose
        # over the LAN). Unauthed LAN traffic still gets 403'd.
        if _path_is_loopback_only(path):
            if _is_loopback():
                return None
            if is_authed():
                return None
            return Response("forbidden", status=403)
        # First-run: no password yet — redirect everything to setup.
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
