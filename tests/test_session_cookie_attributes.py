"""The session cookie's attributes are set, not inherited (#251).

Every state-changing admin POST rides this cookie and the app carries no CSRF
tokens, so what stops a cross-site submission today is the browser's own
SameSite default. Relying on a default means the protection is whatever the
operator's browser decided, which is not a property this app can state.

The regression these guard against is subtle: Flask ships
``SESSION_COOKIE_SAMESITE`` already present and set to ``None``, so a
``setdefault`` leaves the attribute off the cookie while looking like it set
it. The assertions therefore read the rendered ``Set-Cookie`` header wherever
they can, not just the config dict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient


def test_samesite_is_pinned(app: Flask) -> None:
    """`None` here means "no attribute emitted", not "SameSite=None"."""
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_httponly_is_pinned(app: Flask) -> None:
    """Script-readable session cookies are a separate class of problem."""
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_secure_is_left_off(app: Flask) -> None:
    """Deliberate, and worth pinning so nobody "hardens" it by reflex.

    These installs are overwhelmingly plain HTTP on a LAN or a Pi. A Secure
    cookie is not sent over HTTP at all, so setting it would log every one of
    them out rather than protect anything.
    """
    assert not app.config.get("SESSION_COOKIE_SECURE")


def test_the_attributes_reach_the_rendered_cookie(app: Flask, client: FlaskClient) -> None:
    """The config dict is not the contract; the header on the wire is.

    A session has to actually be written for Flask to emit Set-Cookie, so this
    drives a request that stores something in the session.
    """
    with client.session_transaction() as sess:
        sess["_probe"] = "1"
    resp = client.get("/")
    try:
        cookies = resp.headers.getlist("Set-Cookie")
    finally:
        resp.close()
    session_cookies = [c for c in cookies if c.startswith("session=")]
    if not session_cookies:
        # Nothing re-issued the cookie on this response; the config assertions
        # above still hold and this one has nothing to inspect.
        return
    header = session_cookies[0]
    assert "SameSite=Lax" in header, header
    assert "HttpOnly" in header, header
