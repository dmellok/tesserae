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


def test_share_step_renders_consent(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/onboarding/share").get_data(as_text=True)
    assert "Yes, count me in" in body and "Keep this install offline" in body
    assert "api.tesserae.ink" in body  # first-party disclosure


def test_share_opt_in_enables_and_advances_to_dashboard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    # Share sits before the final dashboard step, so it records the choice and
    # advances rather than finishing setup.
    resp = client.post("/onboarding/share", data={"online_features": "1"}, follow_redirects=False)
    assert resp.status_code == 302 and resp.location.endswith("/onboarding/dashboard")
    assert app.config["SETTINGS_STORE"].get_section("app").get("online_features") is True


def test_share_opt_out_disables_and_advances_to_dashboard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/onboarding/share", data={}, follow_redirects=False)  # no field = off
    assert resp.status_code == 302 and resp.location.endswith("/onboarding/dashboard")
    assert app.config["SETTINGS_STORE"].get_section("app").get("online_features") is False


def test_wizard_steps_render(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    for step in ("welcome", "timezone", "broker", "device", "share", "dashboard"):
        resp = client.get(f"/onboarding/{step}")
        assert resp.status_code == 200, step
    # Unknown step falls back to welcome.
    resp = client.get("/onboarding/bogus", follow_redirects=False)
    assert "/onboarding/welcome" in resp.location


def test_timezone_step_picker_renders_with_choices(app: Flask) -> None:
    """The wizard's timezone step should render a <select> populated
    with real IANA names. Picker should land pre-selected with a
    sensible value rather than blank."""
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/onboarding/timezone")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Picker is present + selectable.
    assert 'name="timezone"' in body
    # At least one real IANA name appears in the options list.
    assert "Australia/Melbourne" in body or "Europe/London" in body or "America/New_York" in body
    # Either the detected timezone is shown or the "couldn't detect"
    # fallback copy is rendered — both are valid landing states.
    assert "Detected from your host" in body or "couldn't auto-detect" in body


def test_timezone_step_saves_and_advances_to_broker(app: Flask) -> None:
    """Picking a real IANA name persists ``settings.app.timezone`` and
    bounces the user into the broker step."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/onboarding/timezone",
        data={"timezone": "Australia/Melbourne"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/onboarding/broker" in resp.location
    stored = app.config["SETTINGS_STORE"].get_section("app")
    assert stored.get("timezone") == "Australia/Melbourne"


def test_timezone_step_rejects_unknown_value(app: Flask) -> None:
    """A hand-typed bogus value falls through to ``system`` (the
    least-surprising default) instead of slipping into settings."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/onboarding/timezone",
        data={"timezone": "Not/A/Real/Zone"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    stored = app.config["SETTINGS_STORE"].get_section("app")
    assert stored.get("timezone") == "system"


def test_finish_marks_onboarded(app: Flask) -> None:
    """The final Finish POST persists onboarded=True so the wizard isn't
    shown again."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/onboarding/finish")
    assert resp.status_code == 302
    app_section = app.config["SETTINGS_STORE"].get_section("app")
    assert app_section["onboarded"] is True


def test_broker_builtin_enables_embedded(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    # v0.52: explicit transport=mqtt because the default is now REST
    # and an absent transport field short-circuits broker setup.
    resp = client.post(
        "/onboarding/broker",
        data={"transport": "mqtt", "use_builtin": "on"},
        follow_redirects=False,
    )
    assert resp.location.endswith("/onboarding/device")
    assert app.config["SETTINGS_STORE"].get_section("broker")["embedded_enabled"] is True


def test_broker_builtin_binds_all_and_saves_creds(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/onboarding/broker",
        data={
            "transport": "mqtt",
            "use_builtin": "on",
            "builtin_username": "tess",
            "builtin_password": "s3cret",
        },
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
        data={
            "transport": "mqtt",
            "host": "192.168.1.50",
            "port": "1883",
            "username": "u",
            "password": "p",
        },
    )
    broker = app.config["SETTINGS_STORE"].get_section("broker")
    assert broker["host"] == "192.168.1.50"
    assert broker["embedded_enabled"] is False
    assert broker["password_secret"] == "p"


def test_broker_step_under_ha_hides_builtin_toggle_and_suggests_core_mosquitto(
    tmp_path: Path,
) -> None:
    """Under HA the built-in broker is off-limits (port clash with the
    Mosquitto add-on). The wizard hides the toggle and pre-fills the
    external host with ``core-mosquitto``."""
    a = create_app(testing=True, data_root=tmp_path, devices_dir=REPO_ROOT / "devices")
    a.config["TESTING"] = True
    a.config["HA_INGRESS_MODE"] = True
    client = a.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    body = client.get("/onboarding/broker").get_data(as_text=True)
    assert 'name="use_builtin"' not in body
    assert "core-mosquitto" in body


def test_broker_step_under_ha_ignores_stale_use_builtin_post(tmp_path: Path) -> None:
    """A stale form post with use_builtin=on must not re-enable the
    embedded broker under HA, the runtime guard would skip it anyway,
    but settings still need to record host so onboarding completes."""
    a = create_app(testing=True, data_root=tmp_path, devices_dir=REPO_ROOT / "devices")
    a.config["TESTING"] = True
    a.config["HA_INGRESS_MODE"] = True
    client = a.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/onboarding/broker",
        data={
            "transport": "mqtt",
            "use_builtin": "on",
            "host": "core-mosquitto",
            "port": "1883",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    broker = a.config["SETTINGS_STORE"].get_section("broker")
    assert broker["embedded_enabled"] is False
    assert broker["host"] == "core-mosquitto"


def test_device_manual_add_via_wizard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/onboarding/device", data={"id": "esp32_hallway", "kind": "esp32_client"})
    assert app.config["DEVICE_REGISTRY"].get("esp32_hallway") is not None


def test_device_manual_add_panel_preset_overrides_kind_default(app: Flask) -> None:
    """The onboarding manual-add form now exposes a panel-size picker;
    picking a preset must override the kind's manifest dims so a user
    on a 13.3″ Inky doesn't end up with the kind's default 800×480."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/onboarding/device",
        data={
            "id": "pi_hall",
            "kind": "pi_png_client",
            "panel_preset": "inky_13_3",
        },
    )
    dev = app.config["DEVICE_REGISTRY"].get("pi_hall")
    assert dev is not None
    assert dev.panel["w"] == 1600 and dev.panel["h"] == 1200


def test_device_manual_add_panel_custom_w_h_overrides(app: Flask) -> None:
    """``panel_preset=custom`` + explicit ``panel_w`` / ``panel_h`` lets
    the user enter dims that aren't in the preset list."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/onboarding/device",
        data={
            "id": "pi_odd",
            "kind": "pi_png_client",
            "panel_preset": "custom",
            "panel_w": "1024",
            "panel_h": "768",
        },
    )
    dev = app.config["DEVICE_REGISTRY"].get("pi_odd")
    assert dev is not None
    assert dev.panel["w"] == 1024 and dev.panel["h"] == 768


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
