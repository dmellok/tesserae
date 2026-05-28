"""End-to-end auth + settings flow via Flask test client.

These exercise the gate (setup-on-first-run, login redirect, loopback
bypass for /compose), the settings page rendering against real plugin /
renderer manifests, and the form-driven update path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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
    # First run drops into the setup wizard, not straight to /settings.
    assert "/onboarding" in resp.location
    # Auth state visible to the next request; /settings redirects to the
    # Server sub-page, which renders for an authed user.
    resp = client.get("/settings", follow_redirects=True)
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


def test_plugin_asset_loopback_bypass(app_with_gate: Flask) -> None:
    """The Playwright renderer pulls /plugins/<id>/client.js while it
    renders /compose/<id> — no session is available, so this path must
    bypass the gate from loopback (same as /compose/). Without this,
    dynamic imports inside the composer fail and the panel push errors
    with 'failed to fetch dynamically imported module'."""
    client = app_with_gate.test_client()
    resp = client.get(
        "/plugins/themes_core/client.js",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    # themes_core has no client.js, so the asset route should 404. The
    # important thing is we hit the route — not a 302 to /login or /setup.
    assert resp.status_code in (200, 404)


def test_plugin_asset_blocked_from_non_loopback(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    resp = client.get(
        "/plugins/themes_core/client.js",
        environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
    )
    assert resp.status_code == 403


def test_plugin_index_still_gated(app_with_gate: Flask) -> None:
    """/plugins/ (admin index) lists plugin internals and loader errors —
    must stay behind auth, even from loopback."""
    client = app_with_gate.test_client()
    resp = client.get("/plugins/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    # First-run (no password yet) sends every unauthed request to /setup.
    assert resp.status_code == 302
    assert "/setup" in resp.location or "/login" in resp.location


def test_settings_page_lists_loaded_renderers(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.get("/settings/renderers")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Renderer: Pi PNG client" in body
    assert "tesserae/pi_png/frame/png" in body  # topic shown in meta


def test_settings_redirects_to_server_subpage(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.get("/settings", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("/settings/server")


def test_unknown_settings_area_404s(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.get("/settings/nope", follow_redirects=False)
    assert resp.status_code == 404


def test_subpage_only_shows_its_own_sections(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    server_body = client.get("/settings/server").get_data(as_text=True)
    plugins_body = client.get("/settings/plugins").get_data(as_text=True)
    # Server section content stays out of the Plugins sub-page.
    assert "MQTT broker" in server_body
    assert "MQTT broker" not in plugins_body
    # And vice versa: plugin sections don't bleed into the Server page.
    assert "Plugin:" not in server_body


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


def test_add_device_instance_creates_clone(app_with_gate: Flask, tmp_path: Path) -> None:
    """Multi-head: POSTing the Add-device form materialises a new device
    instance + cloned renderer with the instance's id substituted into
    the topic prefix, without restarting the app."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/devices/add",
        data={
            "id": "esp32_kitchen",
            "kind": "esp32_client",
            "name": "Kitchen panel",
            "panel_w": "640",
            "panel_h": "384",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.location.endswith("#device-esp32_kitchen")

    devices = app_with_gate.config["DEVICE_REGISTRY"]
    new_dev = devices.get("esp32_kitchen")
    assert new_dev is not None
    assert new_dev.kind_of == "esp32_client"
    assert new_dev.status_topic == "tesserae/esp32_kitchen/status"
    assert new_dev.panel == {"w": 640, "h": 384, "orientation": "landscape", "name": "ESP32 e-paper"}

    renderers = app_with_gate.config["RENDERER_REGISTRY"]
    clone = renderers.get("esp32_bin__esp32_kitchen")
    assert clone is not None
    assert clone.topic == "tesserae/esp32_kitchen/frame/bin"

    inst_file = tmp_path / "devices" / "esp32_kitchen.json"
    assert inst_file.exists()


def test_add_device_with_panel_preset(app_with_gate: Flask) -> None:
    """Preset dropdown wins over manual width/height inputs so the
    common case (pick a known panel) is a single click."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/devices/add",
        data={
            "id": "pi_office",
            "kind": "pi_png_client",
            "panel_preset": "inky_7_3",
            # Bogus manual values should be ignored when a preset is picked.
            "panel_w": "9999",
            "panel_h": "9999",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    dev = app_with_gate.config["DEVICE_REGISTRY"].get("pi_office")
    assert dev is not None and dev.panel is not None
    assert (dev.panel["w"], dev.panel["h"]) == (800, 480)


def test_add_device_rejects_invalid_id(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/devices/add",
        data={"id": "Has-Caps", "kind": "esp32_client"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert app_with_gate.config["DEVICE_REGISTRY"].get("Has-Caps") is None


def test_add_device_rejects_built_in_kind_as_target(app_with_gate: Flask) -> None:
    """Instance must reference a built-in kind, not another instance."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/devices/add",
        data={"id": "ghost", "kind": "definitely_not_a_kind"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert app_with_gate.config["DEVICE_REGISTRY"].get("ghost") is None


def test_delete_device_instance_removes_clones(app_with_gate: Flask, tmp_path: Path) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_kitchen", "kind": "esp32_client"},
    )
    resp = client.post("/settings/devices/esp32_kitchen/delete", follow_redirects=False)
    assert resp.status_code == 302
    devices = app_with_gate.config["DEVICE_REGISTRY"]
    renderers = app_with_gate.config["RENDERER_REGISTRY"]
    assert devices.get("esp32_kitchen") is None
    assert renderers.get("esp32_bin__esp32_kitchen") is None
    assert not (tmp_path / "devices" / "esp32_kitchen.json").exists()


def test_update_panel_changes_instance_dims(app_with_gate: Flask, tmp_path: Path) -> None:
    """The per-instance panel form rewrites the instance JSON and reloads
    it so the new dims are live without a restart."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_lab", "kind": "esp32_client", "panel_preset": "inky_7_3"},
    )
    resp = client.post(
        "/settings/devices/esp32_lab/panel",
        data={"panel_w": "600", "panel_h": "448", "panel_orientation": "portrait"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    dev = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_lab")
    assert dev is not None and dev.panel is not None
    assert (dev.panel["w"], dev.panel["h"]) == (600, 448)
    assert dev.panel["orientation"] == "portrait"
    # Persisted to disk.
    import json as _json

    saved = _json.loads((tmp_path / "devices" / "esp32_lab.json").read_text())
    assert saved["panel"]["w"] == 600
    assert saved["panel"]["orientation"] == "portrait"


def test_update_panel_refuses_built_in_kind(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/devices/esp32_client/panel",
        data={"panel_w": "100", "panel_h": "100"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Kind's panel is untouched (still its manifest default).
    dev = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_client")
    assert dev is not None and dev.panel is not None
    assert (dev.panel["w"], dev.panel["h"]) != (100, 100)


def test_calibrate_pushes_card_and_shows_answer_form(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post("/settings/devices/add", data={"id": "esp32_lab", "kind": "esp32_client"})
    resp = client.post("/settings/devices/esp32_lab/calibrate", follow_redirects=False)
    assert resp.status_code == 302
    assert "calibrating=esp32_lab" in resp.location
    # The follow-up page renders the "which number is top-left?" choices.
    body = client.get("/settings/devices?calibrating=esp32_lab").get_data(as_text=True)
    assert "which number is in the top-left" in body.lower()
    assert 'name="top_left" value="1"' in body


def test_calibrate_apply_sets_orientation_from_answer(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    # esp32_client default panel is landscape (800x480).
    client.post("/settings/devices/add", data={"id": "esp32_lab", "kind": "esp32_client"})
    # Pushed landscape; user reports ④ in the top-left → 180° off → landscape_flipped.
    resp = client.post(
        "/settings/devices/esp32_lab/calibrate/apply",
        data={"top_left": "4"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    dev = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_lab")
    assert dev is not None and dev.panel is not None
    assert dev.panel["orientation"] == "landscape_flipped"

    # ③ in the top-left → 90° → portrait_flipped, and the canvas flips to tall.
    client.post("/settings/devices/add", data={"id": "esp32_two", "kind": "esp32_client"})
    client.post(
        "/settings/devices/esp32_two/calibrate/apply",
        data={"top_left": "3"},
        follow_redirects=False,
    )
    dev2 = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_two")
    assert dev2 is not None and dev2.panel is not None
    assert dev2.panel["orientation"] == "portrait_flipped"
    assert dev2.panel["w"] < dev2.panel["h"]  # swapped to tall


def test_dismiss_clears_retained_message(app_with_gate: Flask) -> None:
    """Dismiss must publish an empty retained payload to the device's
    status topic so the broker stops replaying the ghost on reconnect."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    app_with_gate.config["DISCOVERY_CACHE"].record(
        "esp32", b'{"kind":"esp32_client"}'
    )
    transport = MagicMock()
    app_with_gate.config["MQTT_TRANSPORT"] = transport
    resp = client.post("/settings/devices/discovery/esp32/dismiss", follow_redirects=False)
    assert resp.status_code == 302
    assert app_with_gate.config["DISCOVERY_CACHE"].get("esp32") is None
    transport.publish.assert_called_once_with(
        "tesserae/esp32/status", b"", qos=1, retain=True
    )


def test_discovered_json_lists_only_unregistered(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    cache = app_with_gate.config["DISCOVERY_CACHE"]
    cache.record("esp32_attic", b'{"kind":"esp32_client"}')
    body = client.get("/settings/devices/discovered.json")
    assert body.status_code == 200
    data = body.get_json()
    ids = {d["id"] for d in data["devices"]}
    assert ids == {"esp32_attic"}
    # Once registered it drops out of the discovered feed.
    client.post("/settings/devices/add", data={"id": "esp32_attic", "kind": "esp32_client"})
    cache.record("esp32_attic", b'{"kind":"esp32_client"}')  # a late heartbeat
    data = client.get("/settings/devices/discovered.json").get_json()
    assert "esp32_attic" not in {d["id"] for d in data["devices"]}


def test_delete_refuses_built_in_kind(app_with_gate: Flask) -> None:
    """Built-in kinds ship with the app and must not be deletable."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post("/settings/devices/esp32_client/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert app_with_gate.config["DEVICE_REGISTRY"].get("esp32_client") is not None


def test_healthz_is_open(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.data == b"ok"
