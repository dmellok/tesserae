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


def test_forget_password_while_disabled_asks_for_a_new_one(app: Flask) -> None:
    """#373: with the gate off, Forget drops the hash (and only the hash),
    so enabling again goes to /setup instead of restoring the old one."""
    client = app.test_client()
    _sign_in(client)
    store = _settings_store(app)
    auth.set_trusted_networks(store, "100.64.0.0/10")
    client.post("/settings/system/auth/disable", data={"confirmed": "1"})
    assert "Forget saved password" in client.get("/settings/system").get_data(as_text=True)

    resp = client.post("/settings/system/auth/forget", data={"confirmed": "1"})
    assert resp.status_code == 302
    assert not auth.password_is_set(store)
    assert auth.password_required(store) is False
    assert auth.trusted_networks(store) == ["100.64.0.0/10"]

    resp = client.post("/settings/system/auth/enable")
    assert resp.headers["Location"].endswith("/setup")


def test_forget_password_refused_while_the_gate_is_on(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/auth/forget", data={"confirmed": "1"})
    assert auth.verify_password(_settings_store(app), "initialpass")


def test_forget_password_requires_confirmation(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/auth/disable", data={"confirmed": "1"})
    client.post("/settings/system/auth/forget")
    assert auth.verify_password(_settings_store(app), "initialpass")


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


def test_setup_goes_to_the_wizard_on_a_fresh_install(app: Flask) -> None:
    resp = app.test_client().post(
        "/setup", data={"password": "longenough", "password_confirm": "longenough"}
    )
    assert resp.status_code == 302
    assert "/onboarding" in resp.location


def test_setup_skips_the_wizard_when_already_onboarded(app: Flask) -> None:
    """Home Assistant's ingress needs no password, so the wizard can be done
    there first; setting a password for direct access later goes home."""
    _settings_store(app).patch_section("app", {"onboarded": True})
    resp = app.test_client().post(
        "/setup", data={"password": "longenough", "password_confirm": "longenough"}
    )
    assert resp.status_code == 302
    assert "/onboarding" not in resp.location


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


# -- forged forwarding headers ----------------------------------------------


def _spoofed(client: FlaskClient, path: str, peer: str, forwarded_for: str):
    return client.get(
        path, environ_overrides={"REMOTE_ADDR": peer}, headers={"X-Forwarded-For": forwarded_for}
    )


def test_lan_client_cannot_pass_for_loopback_with_a_forwarding_header(app: Flask) -> None:
    """A LAN client claiming ``X-Forwarded-For: 127.0.0.1`` used to pass the
    loopback-only bypass (the renderer's /compose/ pages) without a session.
    Loopback now means a direct connection from this machine."""
    _gated(app)
    _sign_in(app.test_client())
    path = "/compose/nope"
    assert _from(app.test_client(), path, "127.0.0.1").status_code != 403
    assert _spoofed(app.test_client(), path, "192.168.1.50", "127.0.0.1").status_code == 403
    # A proxy on this host relays everyone from 127.0.0.1 with the header set.
    assert _spoofed(app.test_client(), path, "127.0.0.1", "203.0.113.9").status_code == 403


def test_public_client_cannot_pass_for_lan_with_a_forwarding_header(app: Flask) -> None:
    """With the password off, LAN clients reach the admin UI. A public client
    setting ``X-Forwarded-For`` to a LAN address used to be believed; only a
    proxy on this machine or the local network may name the real client."""
    _gated(app)
    _sign_in(app.test_client())
    auth.set_password_disabled(_settings_store(app), True)
    assert (
        _spoofed(app.test_client(), "/settings/system", "203.0.113.9", "192.168.1.5").status_code
        == 403
    )
    # A reverse proxy on the LAN still passes its LAN client through.
    assert (
        _spoofed(app.test_client(), "/settings/system", "192.168.1.2", "192.168.1.5").status_code
        == 200
    )
    # And it can't make a public caller look local.
    assert (
        _spoofed(app.test_client(), "/settings/system", "192.168.1.2", "203.0.113.9").status_code
        == 403
    )


# -- first-run setup token ---------------------------------------------------
#
# /setup is open while no password is set. A browser on the local network
# sets the password with no extra step; a client anywhere else must also
# present the setup token from the server log.

_PUBLIC = {"REMOTE_ADDR": "203.0.113.9"}
_LAN = {"REMOTE_ADDR": "192.168.1.50"}
_GOOD = {"password": "longenough", "password_confirm": "longenough"}


def test_setup_from_a_public_address_asks_for_the_token(app: Flask) -> None:
    resp = app.test_client().get("/setup", environ_base=_PUBLIC)
    assert resp.status_code == 200
    assert b'name="setup_token"' in resp.data


def test_setup_from_the_lan_does_not_ask_for_the_token(app: Flask) -> None:
    client = app.test_client()
    assert b'name="setup_token"' not in client.get("/setup", environ_base=_LAN).data
    assert b'name="setup_token"' not in client.get("/setup").data
    resp = client.post("/setup", data=_GOOD, environ_base=_LAN)
    assert resp.status_code == 302
    assert auth.password_is_set(_settings_store(app))


def test_setup_from_a_public_address_without_the_token_is_refused(app: Flask) -> None:
    resp = app.test_client().post("/setup", data=_GOOD, environ_base=_PUBLIC)
    assert resp.status_code == 200
    assert b"setup token is not correct" in resp.data
    assert not auth.password_is_set(_settings_store(app))


def test_setup_from_a_public_address_with_a_wrong_token_is_refused(app: Flask) -> None:
    # The app fixture runs ungated (testing=True), so install the gate to
    # check that no session came out of the failed attempt.
    auth.install_gate(app, _settings_store(app))
    client = app.test_client()
    resp = client.post("/setup", data={**_GOOD, "setup_token": "nope"}, environ_base=_PUBLIC)
    assert resp.status_code == 200
    assert not auth.password_is_set(_settings_store(app))
    # No session was granted either: the admin pages still bounce to setup.
    assert client.get("/", environ_base=_PUBLIC).headers["Location"].endswith("/setup")


def test_setup_from_a_public_address_with_the_token_succeeds(app: Flask) -> None:
    store = _settings_store(app)
    token = auth.setup_token(store)
    resp = app.test_client().post(
        "/setup", data={**_GOOD, "setup_token": f" {token} "}, environ_base=_PUBLIC
    )
    assert resp.status_code == 302
    assert auth.verify_password(store, "longenough")
    # Spent: the stored token goes once a password exists.
    assert auth.SETUP_TOKEN_KEY not in store.get_section("auth")


def test_a_forged_forwarding_header_does_not_skip_the_token(app: Flask) -> None:
    """A public client can't pass for local by claiming a private address."""
    resp = app.test_client().post(
        "/setup",
        data=_GOOD,
        environ_base=_PUBLIC,
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert resp.status_code == 200
    assert not auth.password_is_set(_settings_store(app))


def test_trusted_network_client_does_not_need_the_token(app: Flask) -> None:
    """A home network on a routable IPv6 prefix, declared as trusted, is local."""
    app.config["TRUSTED_NETWORKS"] = "2001:db8:abcd::/48"
    resp = app.test_client().post(
        "/setup", data=_GOOD, environ_base={"REMOTE_ADDR": "2001:db8:abcd::7"}
    )
    assert resp.status_code == 302
    assert auth.password_is_set(_settings_store(app))


def test_setup_token_from_the_environment_wins(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth.SETUP_TOKEN_ENV, "operator-chosen-token")
    client = app.test_client()
    store = _settings_store(app)
    generated = store.get_section("auth").get(auth.SETUP_TOKEN_KEY)
    assert generated is None
    resp = client.post(
        "/setup", data={**_GOOD, "setup_token": "operator-chosen-token"}, environ_base=_PUBLIC
    )
    assert resp.status_code == 302
    assert auth.password_is_set(store)


def test_setup_token_is_stable_logged_once_and_rotated_by_a_reset(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    store = _settings_store(app)
    with caplog.at_level("WARNING", logger="app.auth"):
        first = auth.setup_token(store)
        assert auth.setup_token(store) == first
    logged = [r for r in caplog.records if first in r.getMessage()]
    assert len(logged) == 1
    auth.clear_password(store)
    assert auth.setup_token(store) != first


def test_announce_setup_token_is_silent_once_a_password_exists(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    store = _settings_store(app)
    auth.set_password(store, "longenough")
    with caplog.at_level("WARNING", logger="app.auth"):
        auth.announce_setup_token(store)
    assert not caplog.records
    assert auth.SETUP_TOKEN_KEY not in store.get_section("auth")


def test_setup_stays_closed_once_a_password_exists(app: Flask) -> None:
    """The token never reopens setup on a configured install."""
    store = _settings_store(app)
    token = auth.setup_token(store)
    auth.set_password(store, "originalpass")
    resp = app.test_client().post(
        "/setup", data={**_GOOD, "setup_token": token}, environ_base=_PUBLIC
    )
    assert resp.status_code == 302
    assert auth.verify_password(store, "originalpass")


def test_production_server_announces_the_token_and_keeps_forwarding_headers(
    app: Flask, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``tesserae`` on a fresh install logs the setup token before serving,
    and starts waitress without its forwarding-header stripping. Stripped
    headers make every visitor behind a reverse proxy look local to the
    gate, which would let them skip the token."""
    import logging

    import waitress

    from app import log_buffer, main

    served: dict[str, object] = {}
    # _serve writes these two itself; registering them here restores them.
    monkeypatch.setenv("TESSERAE_BIND_PORT", "8765")
    monkeypatch.setenv("TESSERAE_BIND_HOST", "127.0.0.1")
    monkeypatch.setattr(main, "create_app", lambda **_kw: app)
    monkeypatch.setattr(waitress, "serve", lambda _app, **kw: served.update(kw))
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    try:
        with caplog.at_level("WARNING", logger="app.auth"):
            main._serve(["--host", "127.0.0.1", "--port", "8765"])
    finally:
        for handler in list(root.handlers):
            if handler not in handlers_before and isinstance(handler, log_buffer.LogBuffer):
                root.removeHandler(handler)

    assert served["clear_untrusted_proxy_headers"] is False
    token = _settings_store(app).get_section("auth")[auth.SETUP_TOKEN_KEY]
    assert any(token in r.getMessage() for r in caplog.records)
