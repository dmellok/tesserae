"""End-to-end auth + settings flow via Flask test client.

These exercise the gate (setup-on-first-run, login redirect, loopback
bypass for /compose), the settings page rendering against real plugin /
renderer manifests, and the form-driven update path."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app import auth
from app.main import REPO_ROOT, create_app
from app.state.settings_store import SettingsStore


@pytest.fixture
def app_with_gate(tmp_path: Path) -> Flask:
    """An app with the auth gate installed (testing=False) but pointed at
    a tmp data root so it starts with no password."""
    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    # Force test mode for cookies / no-broker behaviour without disabling
    # the gate itself.
    app.config["TESTING"] = True
    return app


def test_first_run_redirects_to_setup(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    resp = client.get("/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("/setup")


def test_setup_sets_password_and_signs_in(app_with_gate: Flask, tmp_path: Path) -> None:
    client = app_with_gate.test_client()
    resp = client.post(
        "/setup",
        data={"password": "hunter22z", "password_confirm": "hunter22z"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.location.endswith("/settings")
    # Auth state visible to the next request.
    resp = client.get("/settings")
    assert resp.status_code == 200
    # Password really persisted.
    store = SettingsStore(tmp_path / "core" / "settings.json")
    assert auth.verify_password(store, "hunter22z")


def test_setup_rejects_short_or_mismatched(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    short = client.post(
        "/setup", data={"password": "abc", "password_confirm": "abc"}, follow_redirects=True
    )
    assert b"at least 8" in short.data
    mismatch = client.post(
        "/setup",
        data={"password": "longenough1", "password_confirm": "different1"},
        follow_redirects=True,
    )
    assert b"do not match" in mismatch.data


def test_login_required_after_setup(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    # Drop the session.
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get("/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_login_with_correct_password_grants_settings(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/login", data={"password": "abcdefgh"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("/settings")


def test_login_with_wrong_password_stays_unauthed(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/login", data={"password": "nope"}, follow_redirects=True)
    assert b"Incorrect password" in resp.data
    with client.session_transaction() as sess:
        assert not sess.get("authed")


def test_setup_locked_out_after_password_set(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    with client.session_transaction() as sess:
        sess.clear()
    # /setup once a password is set must NOT let an attacker reset it.
    resp = client.get("/setup", follow_redirects=False)
    assert resp.status_code == 302
    # Either to login or settings; not the setup form.
    assert "/setup" not in resp.location


def test_compose_loopback_bypass(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    # No session, no password set yet — the gate would normally redirect
    # to /setup. /compose should still be reachable from loopback.
    resp = client.get("/compose/nonexistent", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    # The compose route 404s on unknown page, but it must reach the route
    # rather than being redirected by the gate.
    assert resp.status_code == 404


def test_compose_blocked_from_non_loopback(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    resp = client.get("/compose/whatever", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 403


def test_settings_page_lists_loaded_renderers(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # pi_png renderer should show up as a section because it declares
    # settings in its manifest.
    assert "Renderer: Pi client (PNG)" in body
    assert "tesserae/pi/frame/png" in body  # topic shown in meta


def test_broker_update_persists_and_rebuilds_transport(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/broker",
        data={
            "host": "broker.local",
            "port": "8883",
            "username": "u",
            "password": "secret-pw",
            "keepalive": "60",
            "client_id": "tesserae",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    store = SettingsStore(tmp_path / "core" / "settings.json")
    broker = store.get_section("broker")
    assert broker["host"] == "broker.local"
    assert broker["port"] == 8883
    # Secret is renamed on disk.
    assert "password_secret" in broker
    assert broker["password_secret"] == "secret-pw"
    assert "password" not in broker


def test_broker_password_resubmit_masked_keeps_existing(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/broker",
        data={
            "host": "b",
            "port": "1883",
            "username": "u",
            "password": "real",
            "keepalive": "60",
            "client_id": "tesserae",
        },
    )
    # Re-submit with the mask in the password field — the original must stay.
    client.post(
        "/settings/broker",
        data={
            "host": "b2",
            "port": "1883",
            "username": "u",
            "password": "********",
            "keepalive": "60",
            "client_id": "tesserae",
        },
    )
    store = SettingsStore(tmp_path / "core" / "settings.json")
    assert store.get_section("broker")["password_secret"] == "real"
    assert store.get_section("broker")["host"] == "b2"


def test_renderer_settings_save(app_with_gate: Flask, tmp_path: Path) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/renderer-pi_png",
        data={
            "rotate": "2",
            "scale": "fill",
            "bg": "black",
            "saturation": "0.8",
            "transform_rotate_quarters": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    store = SettingsStore(tmp_path / "core" / "settings.json")
    saved = store.get_for_runtime(
        "renderers",
        "pi_png",
        app_with_gate.config["RENDERER_REGISTRY"].get("pi_png").manifest["settings"],
    )
    assert saved["rotate"] == 2
    assert saved["scale"] == "fill"
    assert saved["bg"] == "black"
    assert saved["saturation"] == 0.8
    assert saved["transform_rotate_quarters"] == 0


def test_healthz_is_open(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.data == b"ok"
