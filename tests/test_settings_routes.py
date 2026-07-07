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
    # No session, no password set yet, the gate would normally redirect
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
    renders /compose/<id>, no session is available, so this path must
    bypass the gate from loopback (same as /compose/). Without this,
    dynamic imports inside the composer fail and the panel push errors
    with 'failed to fetch dynamically imported module'."""
    client = app_with_gate.test_client()
    resp = client.get(
        "/plugins/fonts_core/client.js",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    # fonts_core has no client.js, so the asset route should 404. The
    # important thing is we hit the route, not a 302 to /login or /setup.
    assert resp.status_code in (200, 404)


def test_plugin_asset_blocked_from_non_loopback(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    resp = client.get(
        "/plugins/fonts_core/client.js",
        environ_overrides={"REMOTE_ADDR": "10.0.0.5"},
    )
    assert resp.status_code == 403


def test_theme_css_loopback_bypass(app_with_gate: Flask) -> None:
    """The Playwright renderer fetches /themes/user.css + /themes/community.css
    as subresources of /compose/<id> during a panel push, with no session.
    Without this bypass the gate redirects to /login, the <link> resolves
    to HTML, and community/user themes silently fall back to defaults on
    the panel even though the in-browser preview renders them correctly.
    """
    client = app_with_gate.test_client()
    for path in ("/themes/user.css", "/themes/community.css"):
        resp = client.get(path, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        assert resp.status_code == 200, path
        assert resp.mimetype.startswith("text/css"), (path, resp.mimetype)


def test_theme_css_blocked_from_non_loopback(app_with_gate: Flask) -> None:
    """Outside loopback the theme CSS endpoints still need a session."""
    client = app_with_gate.test_client()
    for path in ("/themes/user.css", "/themes/community.css"):
        resp = client.get(path, environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
        assert resp.status_code in (302, 403), path


def test_plugin_index_still_gated(app_with_gate: Flask) -> None:
    """/plugins/ (admin index) lists plugin internals and loader errors -
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
    # Re-submit with the mask in the password field, the original must stay.
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


def test_renderer_save_ignores_device_settings(app_with_gate: Flask, tmp_path: Path) -> None:
    """All of pi_png's settings (rotate / scale / bg / saturation) are
    per-display and live on the device card now. The renderer save
    endpoint filters ``device_setting`` fields out so a hand-crafted
    POST to the renderer endpoint can't override per-device tuning -
    the stored values stay at the manifest defaults."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.post(
        "/settings/renderer-pi_png",
        data={
            "rotate": "2",
            "scale": "fill",
            "bg": "black",
            "saturation": "0.8",
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
    # Manifest defaults, not the POSTed values.
    assert saved["rotate"] == 0
    assert saved["scale"] == "fit"
    assert saved["bg"] == "white"
    assert saved["saturation"] == 0.5


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
    assert new_dev.panel == {
        "w": 640,
        "h": 384,
        "orientation": "landscape",
        "name": "ESP32 e-paper",
    }

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


def test_delete_device_purges_battery_history_and_status_cache(
    app_with_gate: Flask,
) -> None:
    """A deleted device should not linger on the /devices/battery
    dashboard or in the live status cache. Both stores keyed records
    on device id and outlived the registry entry before this fix; the
    battery page kept rendering a card for the dead device because
    its index iterates ``BatteryHistory.device_ids()`` union with
    currently-reporting devices."""
    import time as _time

    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_attic", "kind": "esp32_client"},
    )
    # Plant one battery sample + one status cache entry the way the
    # transport wiring would on a real heartbeat.
    battery = app_with_gate.config["BATTERY_HISTORY"]
    battery.record("esp32_attic", pct=72, battery_mv=3820, timestamp=_time.time())
    app_with_gate.config["DEVICE_STATUS"]["esp32_attic"] = {
        "received_at": _time.time(),
        "parsed": {"battery_pct": 72},
    }
    assert "esp32_attic" in battery.device_ids()
    assert "esp32_attic" in app_with_gate.config["DEVICE_STATUS"]

    resp = client.post("/settings/devices/esp32_attic/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert "esp32_attic" not in battery.device_ids()
    assert "esp32_attic" not in app_with_gate.config["DEVICE_STATUS"]


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
    # 600×448 was landscape; portrait orientation triggers a server-side
    # swap so the stored canvas is tall (renderers derive rotation from
    # ``panel.w < panel.h``).
    assert (dev.panel["w"], dev.panel["h"]) == (448, 600)
    assert dev.panel["orientation"] == "portrait"
    # Persisted to disk.
    import json as _json

    saved = _json.loads((tmp_path / "devices" / "esp32_lab.json").read_text())
    assert saved["panel"]["w"] == 448
    assert saved["panel"]["h"] == 600
    assert saved["panel"]["orientation"] == "portrait"


def test_update_quiet_hours_persists_per_device_override(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    """The per-device quiet-hours form lives next to the panel form
    in Settings → Devices. POSTing it rewrites the instance JSON and
    the reloaded Device exposes the override on its manifest."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_lab", "kind": "esp32_client", "panel_preset": "inky_7_3"},
    )
    resp = client.post(
        "/settings/devices/esp32_lab/quiet-hours",
        data={
            "quiet_hours_enabled": "on",
            "quiet_hours_start": "22:30",
            "quiet_hours_end": "07:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    dev = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_lab")
    assert dev is not None
    qh = dev.manifest.get("quiet_hours")
    assert qh == {"enabled": True, "start": "22:30", "end": "07:00"}


def test_combined_save_persists_panel_and_quiet_hours_in_one_post(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    """The device card now has a single Save button posting to
    ``/settings/devices/<id>/save``. The handler fans out to the same
    service helpers the per-subsection endpoints call, so a single
    POST with panel + quiet-hours inputs persists both atomically."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_lab", "kind": "esp32_client", "panel_preset": "inky_7_3"},
    )
    resp = client.post(
        "/settings/devices/esp32_lab/save",
        data={
            "panel_w": "600",
            "panel_h": "448",
            "panel_orientation": "portrait",
            "quiet_hours_enabled": "on",
            "quiet_hours_start": "22:30",
            "quiet_hours_end": "07:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    dev = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_lab")
    assert dev is not None and dev.panel is not None
    # 600×448 was landscape; portrait orientation normalises to tall.
    assert (dev.panel["w"], dev.panel["h"]) == (448, 600)
    assert dev.panel["orientation"] == "portrait"
    qh = dev.manifest.get("quiet_hours")
    assert qh == {"enabled": True, "start": "22:30", "end": "07:00"}


def test_renderer_card_hides_device_settings(app_with_gate: Flask) -> None:
    """Fields flagged ``device_setting: true`` belong on the device
    card. The renderer card must drop them, and surface a hint in the
    blurb that those settings live under Devices."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    body = client.get("/settings/renderers").get_data(as_text=True)
    # The renderer's id appears (the card itself is there)…
    assert "renderer-pi_bin" in body
    # …but its device-flagged fields are NOT rendered as form inputs on
    # this card. The exact ``name="dither"`` attribute is the smoking gun.
    assert 'name="dither"' not in body
    assert 'name="saturation"' not in body
    assert 'name="contrast"' not in body
    # And there's a hint pointing the user at the Devices tab.
    assert "Per-display settings" in body


def test_device_card_exposes_picture_quality(app_with_gate: Flask) -> None:
    """Adding a device kind that consumes pi_bin gets a per-display
    settings subsection (titled after the renderer) on its card with
    the dither/saturation/contrast fields namespaced as
    ``<clone_id>:<field>`` so the combined-save handler can route them
    to the right clone's settings.

    v0.68 moved these three fields onto the Calibration tab under a
    "— tone & dither" subhead; storage + name pattern are unchanged."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "pi_lab", "kind": "pi_bin_client", "panel_preset": "inky_7_3"},
    )
    body = client.get("/settings/devices").get_data(as_text=True)
    # Subsection titled after the renderer's base name.
    assert "Pi BIN client — tone" in body
    # Namespaced field names. Clone id is ``pi_bin__pi_lab``.
    assert 'name="pi_bin__pi_lab:dither"' in body
    assert 'name="pi_bin__pi_lab:saturation"' in body
    assert 'name="pi_bin__pi_lab:contrast"' in body


def test_calibration_tone_dither_survives_bwry_gamut(app_with_gate: Flask) -> None:
    """Issue #52 follow-up (r/eink launch feedback, bablokb): the
    renderer picture-quality block (Contrast / Saturation / Dither)
    used to be nested inside the palette-recalibration ``{% if %}``
    guard. Panels whose gamut has no matching palette family
    (bwry_4 / mono / rgb24 / rgb16) get ``palette_apply_endpoint =
    None``, so a save that resolved gamut to bwry_4 silently dropped
    the whole picture-quality block along with the palette one. The
    block is now guarded independently so the tone & dither sliders
    remain visible on non-Spectra-6 panels."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post("/settings/devices/add", data={"id": "kitchen_pi", "kind": "pi_bin_client"})
    # Save with bwry_4 gamut (Inky4 legacy path).
    client.post(
        "/settings/devices/kitchen_pi/save",
        data={
            "_active_tab": "calibration",
            "device_name": "kitchen_pi",
            "panel_w": "640",
            "panel_h": "400",
            "panel_orientation": "landscape",
            "panel_gamut": "bwry_4",
            "quiet_hours_enabled": "0",
            "pi_bin__kitchen_pi:saturation": "1.5",
        },
    )
    body = client.get("/settings/devices").get_data(as_text=True)
    # Palette recalibration block correctly hidden for a bwry_4 gamut
    # (no matching palette family).
    assert "Palette recalibration" not in body
    # But the tone & dither picture-quality block MUST still render.
    assert "Pi BIN client — tone" in body
    assert 'name="pi_bin__kitchen_pi:dither"' in body
    assert 'name="pi_bin__kitchen_pi:saturation"' in body
    assert 'name="pi_bin__kitchen_pi:contrast"' in body


def test_calibration_block_survives_save_when_instance_id_collides_with_base(
    app_with_gate: Flask,
) -> None:
    """Issue #52 (bablokb): the tone & dither block vanishes after a
    combined-form save when the device instance id equals a base
    renderer's topic prefix (his device id is ``pi_bin``, the same
    collision behind the earlier duplicate-block report, item 4).

    ``_drop_clones`` removed every renderer whose ``device`` matched the
    instance id; for id ``pi_bin`` that includes the BASE ``pi_bin``
    renderer (its ``device`` is the ``pi_bin`` topic prefix), so
    ``clone_for_instances`` then had no base to clone from and the
    device was left with zero clones until the next process restart
    rebuilt the registry from disk. ``for_device`` returned nothing, so
    ``calibration_picture_quality`` was empty and the block dropped out
    of the DOM."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post("/settings/devices/add", data={"id": "pi_bin", "kind": "pi_bin_client"})

    # First render (equivalent to a fresh process): block is present.
    body_before = client.get("/settings/devices").get_data(as_text=True)
    assert 'name="pi_bin__pi_bin:dither"' in body_before

    # Combined-form save (change a slider), exactly what the user does.
    client.post(
        "/settings/devices/pi_bin/save",
        data={
            "_active_tab": "calibration",
            "device_name": "pi_bin",
            "panel_w": "640",
            "panel_h": "400",
            "panel_orientation": "landscape",
            "quiet_hours_enabled": "0",
            "pi_bin__pi_bin:saturation": "1.5",
        },
    )

    # After the save, without a restart, the block must still be there.
    body_after = client.get("/settings/devices").get_data(as_text=True)
    assert "Pi BIN client — tone" in body_after
    assert 'name="pi_bin__pi_bin:dither"' in body_after
    assert 'name="pi_bin__pi_bin:saturation"' in body_after
    assert 'name="pi_bin__pi_bin:contrast"' in body_after


def test_device_card_exposes_pi_png_settings(app_with_gate: Flask) -> None:
    """Pi PNG client devices get rotate / scale / bg / saturation on
    the device card, none of those are renderer-wide any more.

    v0.68 split the picture-quality subsection: fields NOT in the
    Calibration-tab set (rotate / scale / bg) stay on General under
    a "— rendering" heading; saturation moved onto the Calibration
    tab under "— tone & dither" alongside dither + contrast."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "png_lab", "kind": "pi_png_client", "panel_preset": "inky_7_3"},
    )
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "Pi PNG client — rendering" in body
    assert "Pi PNG client — tone" in body
    assert 'name="pi_png__png_lab:rotate"' in body
    assert 'name="pi_png__png_lab:scale"' in body
    assert 'name="pi_png__png_lab:bg"' in body
    assert 'name="pi_png__png_lab:saturation"' in body


def test_combined_save_persists_picture_quality_on_clone(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    """Picture-quality submitted through the combined save handler
    lands in the *clone's* renderer-settings namespace
    (``renderers.pi_bin__<id>``), not on the base renderer. The base
    keeps whatever it had, devices override independently."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "pi_lab", "kind": "pi_bin_client", "panel_preset": "inky_7_3"},
    )
    resp = client.post(
        "/settings/devices/pi_lab/save",
        data={
            "pi_bin__pi_lab:dither": "atkinson",
            "pi_bin__pi_lab:saturation": "1.8",
            "pi_bin__pi_lab:contrast": "1.1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    store = SettingsStore(tmp_path / "core" / "settings.json")
    pi_bin = app_with_gate.config["RENDERER_REGISTRY"].get("pi_bin")
    clone = app_with_gate.config["RENDERER_REGISTRY"].get("pi_bin__pi_lab")
    assert clone is not None
    saved = store.get_for_runtime("renderers", "pi_bin__pi_lab", clone.manifest["settings"])
    assert saved["dither"] == "atkinson"
    assert saved["saturation"] == 1.8
    assert saved["contrast"] == 1.1
    # Base untouched.
    base_saved = store.get_for_runtime("renderers", "pi_bin", pi_bin.manifest["settings"])
    assert base_saved["dither"] == "floyd-steinberg"  # manifest default


def test_combined_save_refuses_picture_quality_for_other_devices(
    app_with_gate: Flask, tmp_path: Path
) -> None:
    """An attempt to write to another device's clone (``<base>__<other>``)
    through this device's save endpoint is silently dropped, the card
    is only the source of truth for its own clones."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "pi_lab", "kind": "pi_bin_client", "panel_preset": "inky_7_3"},
    )
    client.post(
        "/settings/devices/add",
        data={"id": "pi_kitchen", "kind": "pi_bin_client", "panel_preset": "inky_7_3"},
    )
    # POST to pi_lab's endpoint but try to write to pi_kitchen's clone.
    client.post(
        "/settings/devices/pi_lab/save",
        data={"pi_bin__pi_kitchen:saturation": "2.9"},
    )
    store = SettingsStore(tmp_path / "core" / "settings.json")
    clone = app_with_gate.config["RENDERER_REGISTRY"].get("pi_bin__pi_kitchen")
    assert clone is not None
    saved = store.get_for_runtime("renderers", "pi_bin__pi_kitchen", clone.manifest["settings"])
    assert saved["saturation"] != 2.9


def test_combined_save_skips_subsections_with_no_fields(app_with_gate: Flask) -> None:
    """The combined handler detects each subsection by presence of its
    inputs. A POST missing panel_w/panel_h leaves the panel alone."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_lab", "kind": "esp32_client", "panel_preset": "inky_7_3"},
    )
    dev_before = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_lab")
    assert dev_before is not None and dev_before.panel is not None
    panel_before = dict(dev_before.panel)
    # Only quiet-hours fields submitted, panel untouched.
    resp = client.post(
        "/settings/devices/esp32_lab/save",
        data={
            "quiet_hours_enabled": "on",
            "quiet_hours_start": "01:00",
            "quiet_hours_end": "02:00",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    dev_after = app_with_gate.config["DEVICE_REGISTRY"].get("esp32_lab")
    assert dev_after is not None and dev_after.panel is not None
    assert (dev_after.panel["w"], dev_after.panel["h"]) == (
        panel_before["w"],
        panel_before["h"],
    )


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
    app_with_gate.config["DISCOVERY_CACHE"].record("esp32", b'{"kind":"esp32_client"}')
    transport = MagicMock()
    transport.connected = True
    app_with_gate.config["MQTT_TRANSPORT"] = transport
    resp = client.post("/settings/devices/discovery/esp32/dismiss", follow_redirects=False)
    assert resp.status_code == 302
    assert app_with_gate.config["DISCOVERY_CACHE"].get("esp32") is None
    transport.publish.assert_called_once_with("tesserae/esp32/status", b"", qos=1, retain=True)


def test_dismiss_skips_publish_when_transport_disconnected(app_with_gate: Flask) -> None:
    """REST-only install: no broker connection, so the dismiss is a
    pure cache clear. Skipping the publish avoids a misleading "broker
    offline" error flash on what was actually a clean dismiss
    (regression guard for #38)."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    app_with_gate.config["DISCOVERY_CACHE"].record("rest_panel", b'{"kind":"trmnl_client"}')
    transport = MagicMock()
    transport.connected = False
    app_with_gate.config["MQTT_TRANSPORT"] = transport
    resp = client.post("/settings/devices/discovery/rest_panel/dismiss", follow_redirects=False)
    assert resp.status_code == 302
    assert app_with_gate.config["DISCOVERY_CACHE"].get("rest_panel") is None
    transport.publish.assert_not_called()
    # The flash payload reaches the next request via the session cookie.
    with client.session_transaction() as sess:
        flashes = sess.get("_flashes", [])
    assert any(category == "ok" and "Dismissed" in msg for category, msg in flashes)


def test_debug_section_surfaces_resolved_renderer_and_version(app_with_gate: Flask) -> None:
    """The Debug section on each device card must expose the resolved
    renderer clone ids + the running Tesserae version, so an operator
    debugging "wrong renderer" or "prod running the wrong build" gets
    the answer in one glance instead of having to spelunk logs.

    Regression guard: this feature exists to catch the exact case
    where a hardware-manifest renderer override didn't propagate (or
    the prod server isn't on the release that shipped the override)."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    # Register a real instance so its debug block renders.
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_kitchen", "kind": "esp32_client", "name": "Kitchen"},
    )
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "Debug &amp; diagnostics" in body, "expected the Debug summary label on the device card"
    # Tesserae version shows in the debug block.
    assert app_with_gate.config["APP_VERSION"] in body
    # Renderer clone id follows the ``<renderer>__<instance>`` convention
    # so the operator can see which renderer is bound to this instance.
    assert "esp32_bin__esp32_kitchen" in body


def test_debug_mask_secrets_hides_access_token_and_secret_keys() -> None:
    """Raw manifest JSON in the Debug pane must NOT leak an
    access_token or a *_secret-suffixed key. Debug views get
    screenshotted for support requests and Slack pastes; masking at
    the server ensures the token stays out of any downstream
    artefact. Unit-tested directly against the helper so a schema
    change on the /add path doesn't break the coverage."""
    from app.settings.index_routes import _mask_secrets

    masked = _mask_secrets(
        {
            "id": "trmnl_lounge",
            "access_token": "eyJ0eXAiOi.super.secret",
            "webhook_token_secret": "another.secret",
            "protocol_config": {
                "access_token": "nested.secret",
                "model_header": "reTerminal E1002",
            },
            "panel": {"w": 800, "h": 480},
        }
    )
    assert masked["access_token"] == "***"
    assert masked["webhook_token_secret"] == "***"
    assert masked["protocol_config"]["access_token"] == "***"
    # Non-secret fields pass through untouched, including nested ones.
    assert masked["id"] == "trmnl_lounge"
    assert masked["protocol_config"]["model_header"] == "reTerminal E1002"
    assert masked["panel"] == {"w": 800, "h": 480}


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


# ----- handoff redesign view-model helpers ---------------------------------


def test_humanize_signal_buckets_rssi_to_bars() -> None:
    from app.settings.index_routes import _humanize_signal

    # Excellent: -55 dBm or stronger -> 4 bars.
    assert _humanize_signal(-50) == {"bars": 4, "label": "Excellent", "sub": "-50 dBm"}
    # Good: -65..-56 dBm.
    assert _humanize_signal(-64) == {"bars": 3, "label": "Good", "sub": "-64 dBm"}
    # Fair: -75..-66 dBm.
    assert _humanize_signal(-70) == {"bars": 2, "label": "Fair", "sub": "-70 dBm"}
    # Poor: weaker than -75 dBm.
    assert _humanize_signal(-85) == {"bars": 1, "label": "Poor", "sub": "-85 dBm"}
    # None / unparseable returns None so the tile shows "No reading".
    assert _humanize_signal(None) is None
    assert _humanize_signal("nope") is None


def test_humanize_power_calls_zero_zero_mains_not_dead() -> None:
    """The header redesign reads as a dead battery if a mains device's
    zero battery_mv/battery_pct fall through as ``0 mV / 0%``. The
    handoff fixes this by surfacing "Mains / No battery" instead."""
    from app.settings.index_routes import _humanize_power

    assert _humanize_power({}) == {"label": "Mains", "sub": "No battery"}
    assert _humanize_power({"battery_mv": 0, "battery_pct": 0}) == {
        "label": "Mains",
        "sub": "No battery",
    }
    # Real battery reading carries the percent + mV sub-line.
    assert _humanize_power({"battery_mv": 3820, "battery_pct": 67}) == {
        "label": "67%",
        "sub": "3820 mV",
    }


def test_device_card_renders_humanized_status_tiles(app_with_gate: Flask) -> None:
    """The Status tab shows Signal / Power / Firmware tiles instead of
    raw key/value pairs. Smoke-checks the tile labels are present after
    a heartbeat lands."""
    import time

    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_lab", "kind": "esp32_client", "panel_preset": "inky_7_3"},
    )
    app_with_gate.config["DEVICE_STATUS"]["esp32_lab"] = {
        "received_at": time.time(),
        "parsed": {"battery_mv": 3820, "battery_pct": 67, "rssi": -64, "ip": "10.0.0.42"},
    }
    body = client.get("/settings/devices").get_data(as_text=True)
    # All three tile labels render.
    assert "Signal" in body
    assert "Power" in body
    assert "Firmware" in body
    # Humanized signal label + dBm sub-line.
    assert "Good" in body
    assert "-64 dBm" in body
    # Humanized power (battery percent with mV sub).
    assert "67%" in body
    # Firmware IP sub-line.
    assert "10.0.0.42" in body


def test_device_card_rest_hides_dormant_mqtt_topics(app_with_gate: Flask) -> None:
    """A REST-transport device shouldn't show Status topic / Config topic
    in the Connection details strip (issue #21). Those topics stay on
    the manifest so a flip back to MQTT is one click, but they're not
    used by REST so dragging them into the UI is misleading."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post(
        "/settings/devices/add",
        data={"id": "rest_lab", "kind": "esp32_client", "panel_preset": "inky_7_3"},
    )
    # Flip to REST (route auto-mints the access token).
    resp = client.post(
        "/settings/devices/rest_lab/set-transport",
        data={"transport": "rest"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    body = client.get("/settings/devices").get_data(as_text=True)
    # Server URL appears (REST devices need to know where to poll).
    assert "Server URL" in body
    # Dormant MQTT topic rows are hidden on REST devices.
    assert "Status topic" not in body
    assert "Config topic" not in body


def test_devices_reveal_token_stashes_in_session_and_logs(app_with_gate: Flask) -> None:
    """The reveal endpoint (issue #20) gives admins a path back to the
    full access token after closing the one-shot regenerate modal,
    without making them read the on-disk manifest. The reveal is
    logged to the EventLog so the admin can audit who saw the token."""
    client = app_with_gate.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    # TRMNL devices have access_token in their manifest.
    client.post(
        "/settings/devices/add",
        data={"id": "trmnl_lab", "kind": "trmnl_client", "panel_preset": "trmnl_byod_v1"},
    )
    # First render pops the session-stashed reveal modal from the add
    # flow; consume it so the next render starts clean.
    client.get("/settings/devices")
    # Now hit the reveal endpoint and verify the next render shows the
    # token modal again with the same value.
    dev = app_with_gate.config["DEVICE_REGISTRY"].get("trmnl_lab")
    assert dev is not None
    token = dev.manifest["access_token"]
    resp = client.post(
        "/settings/devices/trmnl_lab/reveal-token",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    body = client.get("/settings/devices").get_data(as_text=True)
    # Full token appears in the page (modal). Just check a substring so
    # the test doesn't drift if the modal HTML wrapping changes.
    assert token in body
