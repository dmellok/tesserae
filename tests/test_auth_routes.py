"""Settings → System → Authentication: change password, disable, enable.

The CLI escape hatch (``tesserae --reset-password``) is exercised in
``tests/test_auth.py`` since it doesn't need a Flask app.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import auth
from app.main import REPO_ROOT, create_app
from app.state.settings_store import SettingsStore


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _settings_store(app: Flask) -> SettingsStore:
    return app.config["SETTINGS_STORE"]


def _sign_in(client: FlaskClient, password: str = "initialpass") -> None:
    client.post("/setup", data={"password": password, "password_confirm": password})


def test_change_password_rejects_wrong_current(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/system/auth/change-password",
        data={
            "current_password": "wrong",
            "new_password": "newsecretpass",
            "new_password_confirm": "newsecretpass",
        },
    )
    assert resp.status_code == 302
    # Old password still verifies; new one doesn't.
    store = _settings_store(app)
    assert auth.verify_password(store, "initialpass")
    assert not auth.verify_password(store, "newsecretpass")


def test_change_password_rejects_too_short(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/system/auth/change-password",
        data={
            "current_password": "initialpass",
            "new_password": "short",
            "new_password_confirm": "short",
        },
    )
    assert resp.status_code == 302
    assert auth.verify_password(_settings_store(app), "initialpass")


def test_change_password_rejects_mismatched_confirm(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/system/auth/change-password",
        data={
            "current_password": "initialpass",
            "new_password": "newsecretpass",
            "new_password_confirm": "different",
        },
    )
    assert resp.status_code == 302
    assert auth.verify_password(_settings_store(app), "initialpass")


def test_change_password_rotates_hash(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/system/auth/change-password",
        data={
            "current_password": "initialpass",
            "new_password": "newsecretpass",
            "new_password_confirm": "newsecretpass",
        },
    )
    assert resp.status_code == 302
    store = _settings_store(app)
    assert not auth.verify_password(store, "initialpass")
    assert auth.verify_password(store, "newsecretpass")


def test_disable_password_requires_confirmation(app: Flask) -> None:
    """A POST without ``confirmed=1`` must not flip the gate, guards
    against a stray click / hand-crafted form silently dropping auth."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/system/auth/disable", data={})
    assert resp.status_code == 302
    assert auth.password_required(_settings_store(app)) is True


def test_disable_password_with_confirmation_flips_gate(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/system/auth/disable", data={"confirmed": "1"})
    assert resp.status_code == 302
    assert auth.password_required(_settings_store(app)) is False


def test_disabled_password_lets_loopback_caller_through(app: Flask) -> None:
    """With the gate disabled, an unauthed loopback caller (remote_addr
    127.0.0.1, which the test client uses by default) reaches /settings
    without a session."""
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/auth/disable", data={"confirmed": "1"})
    # Drop the session so we know it's the disable flag, not a leftover
    # auth cookie, letting us through.
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get("/settings/system")
    assert resp.status_code == 200


def test_enable_password_restores_existing_hash(app: Flask) -> None:
    """Disabling then re-enabling preserves the original password, the
    stored hash is untouched by the toggle, so users don't have to pick a
    new password just to flip the gate back on."""
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/auth/disable", data={"confirmed": "1"})
    resp = client.post("/settings/system/auth/enable")
    assert resp.status_code == 302
    store = _settings_store(app)
    assert auth.password_required(store) is True
    assert auth.verify_password(store, "initialpass")


def test_enable_password_with_no_hash_redirects_to_setup(app: Flask) -> None:
    """If auth was disabled AND the stored hash was cleared (e.g. by the
    reset CLI) and the user re-enables in the UI, they need to set a
    password, the handler bounces them to /setup."""
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/auth/disable", data={"confirmed": "1"})
    store = _settings_store(app)
    # Simulate a CLI reset wiping the hash while disabled.
    auth.clear_password(store)
    # Re-set the disabled flag (clear_password wipes the whole section).
    auth.set_password_disabled(store, True)

    resp = client.post("/settings/system/auth/enable")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/setup")


def test_setup_short_password_shows_inline_error(app: Flask) -> None:
    """A too-short password re-renders /setup with the error inline in
    the card (not only as an auto-dismissing flash toast, #265)."""
    client = app.test_client()
    resp = client.post("/setup", data={"password": "short", "password_confirm": "short"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "auth-error" in body
    assert "Password must be at least 8 characters." in body
    assert not auth.password_is_set(_settings_store(app))


def test_setup_mismatched_confirm_shows_inline_error(app: Flask) -> None:
    client = app.test_client()
    resp = client.post("/setup", data={"password": "longenough", "password_confirm": "different"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "auth-error" in body
    assert "Passwords do not match." in body
    assert not auth.password_is_set(_settings_store(app))


def test_login_wrong_password_shows_inline_error(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "auth-error" in body
    assert "Incorrect password." in body


# -- trusted networks -------------------------------------------------------


def _from(client: FlaskClient, path: str, remote_addr: str):
    return client.get(path, environ_overrides={"REMOTE_ADDR": remote_addr})


def _gated(app: Flask) -> Flask:
    """``create_app(testing=True)`` leaves the auth gate uninstalled so
    route tests don't have to sign in; these tests are about the gate."""
    auth.install_gate(app, _settings_store(app))
    return app


def test_trusted_networks_parse_normalises_and_rejects_bad_entries() -> None:
    assert auth.parse_trusted_networks("2001:db8:abcd::/48, 2001:db8:abcd::/48\n 10.9.0.7/16 ") == [
        "2001:db8:abcd::/48",
        "10.9.0.0/16",
    ]
    assert auth.parse_trusted_networks("") == []
    with pytest.raises(ValueError, match="not an IP network"):
        auth.parse_trusted_networks("kitchen")
    with pytest.raises(ValueError, match="every address"):
        auth.parse_trusted_networks("::/0")


def test_trusted_networks_settings_route_saves_and_rejects(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/system/auth/trusted-networks",
        data={"trusted_networks": "2001:db8:abcd::/48\n192.0.2.0/24"},
    )
    assert resp.status_code == 302
    assert auth.trusted_networks(_settings_store(app)) == ["2001:db8:abcd::/48", "192.0.2.0/24"]

    resp = client.post("/settings/system/auth/trusted-networks", data={"trusted_networks": "nope"})
    assert resp.status_code == 302
    # A bad entry leaves the previous list untouched.
    assert auth.trusted_networks(_settings_store(app)) == ["2001:db8:abcd::/48", "192.0.2.0/24"]

    resp = client.post("/settings/system/auth/trusted-networks", data={"trusted_networks": ""})
    assert resp.status_code == 302
    assert auth.trusted_networks(_settings_store(app)) == []


def test_public_ipv6_prefix_counts_as_lan_once_trusted(app: Flask) -> None:
    """A home LAN on an ISP-delegated global prefix is public to the fixed
    ranges. Listing the prefix makes it local for /renders/ and, with the
    password off, for the admin UI too. Everything outside stays blocked."""
    _gated(app)
    client = app.test_client()
    _sign_in(client)
    device = "2001:db8:abcd:1::42"
    stranger = "2001:db8:ffff::1"

    # Renders are LAN-only: 403 from the global prefix before it is trusted.
    assert _from(app.test_client(), "/renders/nope.png", device).status_code == 403

    auth.set_trusted_networks(_settings_store(app), "2001:db8:abcd::/48")
    # Gate passes; the missing artifact is the router's 404, not the gate's 403.
    assert _from(app.test_client(), "/renders/nope.png", device).status_code == 404
    assert _from(app.test_client(), "/renders/nope.png", stranger).status_code == 403

    # With the password disabled, the trusted prefix reaches the admin UI.
    auth.set_password_disabled(_settings_store(app), True)
    assert _from(app.test_client(), "/settings/system", device).status_code == 200
    assert _from(app.test_client(), "/settings/system", stranger).status_code == 403


def test_trusted_networks_env_var_merges_with_settings(app: Flask) -> None:
    _gated(app)
    _sign_in(app.test_client())
    app.config["TRUSTED_NETWORKS"] = "203.0.113.0/24, not-a-network"
    auth.set_trusted_networks(_settings_store(app), "2001:db8:abcd::/48")
    assert _from(app.test_client(), "/renders/nope.png", "203.0.113.9").status_code == 404
    assert _from(app.test_client(), "/renders/nope.png", "2001:db8:abcd::9").status_code == 404
    assert _from(app.test_client(), "/renders/nope.png", "203.0.114.9").status_code == 403
