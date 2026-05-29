"""First-run setup wizard: gating, step flow, starter dashboard, skip/finish."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(testing=True, data_root=tmp_path, devices_dir=REPO_ROOT / "devices")
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_setup_redirects_into_wizard(app: Flask) -> None:
    client = app.test_client()
    resp = client.post(
        "/setup",
        data={"password": "abcdefgh", "password_confirm": "abcdefgh"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/onboarding" in resp.location


def test_root_lands_on_wizard_until_onboarded(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    # Not onboarded yet → / redirects into the wizard.
    resp = client.get("/", follow_redirects=False)
    assert "/onboarding" in resp.location
    # Finish → onboarded → / goes to Send.
    client.post("/onboarding/finish")
    resp = client.get("/", follow_redirects=False)
    assert resp.location.endswith("/send")


def test_wizard_steps_render(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    for step in ("welcome", "broker", "device", "dashboard", "telemetry"):
        resp = client.get(f"/onboarding/{step}")
        assert resp.status_code == 200, step
    # Unknown step falls back to welcome.
    resp = client.get("/onboarding/bogus", follow_redirects=False)
    assert "/onboarding/welcome" in resp.location


def test_telemetry_step_shows_a_pre_checked_consent_toggle(app: Flask) -> None:
    """Default-on at the consent screen: the checkbox is rendered pre-
    ticked so a user who just clicks Finish opts in. They can untick
    it — the test below covers that path."""
    client = app.test_client()
    _sign_in(client)
    body = client.get("/onboarding/telemetry").get_data(as_text=True)
    assert 'name="telemetry_enabled"' in body
    assert "checked" in body
    # And the explanation is honest about what's sent.
    assert "app.started" in body
    assert "update.applied" in body


def test_finish_persists_telemetry_opt_in(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/onboarding/finish", data={"telemetry_enabled": "1"})
    assert resp.status_code == 302
    app_section = app.config["SETTINGS_STORE"].get_section("app")
    assert app_section["telemetry_enabled"] is True
    assert app_section["onboarded"] is True


def test_finish_persists_telemetry_opt_out(app: Flask) -> None:
    """Unchecked checkboxes don't post their name at all. Absence must
    record an explicit False, not be silently ignored."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/onboarding/finish", data={})  # no telemetry_enabled
    assert resp.status_code == 302
    app_section = app.config["SETTINGS_STORE"].get_section("app")
    assert app_section["telemetry_enabled"] is False
    assert app_section["onboarded"] is True


def test_broker_builtin_enables_embedded(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/onboarding/broker", data={"use_builtin": "on"}, follow_redirects=False)
    assert resp.location.endswith("/onboarding/device")
    assert app.config["SETTINGS_STORE"].get_section("broker")["embedded_enabled"] is True


def test_broker_builtin_binds_all_and_saves_creds(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/onboarding/broker",
        data={"use_builtin": "on", "builtin_username": "tess", "builtin_password": "s3cret"},
    )
    broker = app.config["SETTINGS_STORE"].get_section("broker")
    assert broker["embedded_enabled"] is True
    assert broker["embedded_bind"] == "0.0.0.0"  # LAN-reachable
    assert broker["embedded_username"] == "tess"
    assert broker["embedded_password_secret"] == "s3cret"


def test_broker_step_shows_builtin_url(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/onboarding/broker").get_data(as_text=True)
    assert "mqtt://" in body and ":1883" in body
    # Password fields are masked.
    assert 'name="builtin_password"' in body and 'type="password"' in body


def test_broker_external_saves_host(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/onboarding/broker",
        data={"host": "192.168.1.50", "port": "1883", "username": "u", "password": "p"},
    )
    broker = app.config["SETTINGS_STORE"].get_section("broker")
    assert broker["host"] == "192.168.1.50"
    assert broker["embedded_enabled"] is False
    assert broker["password_secret"] == "p"


def test_device_manual_add_via_wizard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/onboarding/device", data={"id": "esp32_hallway", "kind": "esp32_client"})
    assert app.config["DEVICE_REGISTRY"].get("esp32_hallway") is not None


def test_register_discovered_via_wizard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    app.config["DISCOVERY_CACHE"].record(
        "esp32_attic", b'{"kind":"esp32_client","panel_w":800,"panel_h":480}'
    )
    resp = client.post("/onboarding/device/esp32_attic/register", follow_redirects=False)
    assert resp.location.endswith("/onboarding/device")
    dev = app.config["DEVICE_REGISTRY"].get("esp32_attic")
    assert dev is not None and dev.kind_of == "esp32_client"
    # Cleared from the discovery cache once registered.
    assert app.config["DISCOVERY_CACHE"].get("esp32_attic") is None


def test_starter_dashboard_created_and_bound(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/onboarding/device", data={"id": "esp32_hallway", "kind": "esp32_client"})
    resp = client.post("/onboarding/dashboard", follow_redirects=False)
    assert resp.location.endswith("/onboarding/dashboard")
    pages = app.config["PAGE_STORE"].list()
    assert len(pages) == 1
    page = pages[0]
    assert page.device_ids == ["esp32_hallway"]  # bound to the registered device
    assert page.cells and page.cells[0].plugin == "clock_analog"


def test_starter_dashboard_without_device_uses_virtual_panel(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/onboarding/dashboard", follow_redirects=False)
    pages = app.config["PAGE_STORE"].list()
    assert len(pages) == 1
    assert pages[0].device_ids == []  # no device registered → unbound


def test_skip_marks_onboarded(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/onboarding/skip", follow_redirects=False)
    assert resp.location.endswith("/send")
    assert app.config["SETTINGS_STORE"].get_section("app").get("onboarded") is True
