"""End-to-end device-section behaviour via the test client.

Covers: status block renders even with no heartbeat, status appears after a
heartbeat is delivered, config form rejects invalid values + accepts good
ones + publishes them retained to the broker."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app, merge_status_parsed
from app.state.settings_store import SettingsStore


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    # testing=False so the auth gate is installed; then sign in via /setup.
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _add_instance(client, *, id: str, kind: str, name: str = "") -> None:
    """Register a device instance via the Add-device endpoint so tests
    can exercise the instance-only UI without poking the registry by
    hand."""
    client.post(
        "/settings/devices/add",
        data={"id": id, "kind": kind, "name": name},
        follow_redirects=False,
    )


def test_device_section_renders_with_no_heartbeat(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="esp32_lab", kind="esp32_client", name="Lab ESP32")
    _add_instance(client, id="pi_bin_kitchen", kind="pi_bin_client", name="Kitchen Pi")
    resp = client.get("/settings/devices")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Built-in kind cards are hidden, only instances appear.
    assert "Pi BIN client</span>" not in body
    assert "Pi PNG client</span>" not in body
    assert "ESP32 client</span>" not in body
    # Registered instances do show up, with the "no heartbeat" status state.
    # The handoff-redesigned device card uses the bare device name in
    # the header (no "Device: " prefix); the data layer's title still
    # carries the prefix for non-device callers.
    assert "Lab ESP32" in body
    assert "Kitchen Pi" in body
    assert "no heartbeat received yet" in body
    # ESP32 instance inherits its kind's config_topic, so the sleep
    # interval form lives on the instance card.
    assert "Sleep interval" in body
    assert 'name="sleep_interval_s"' in body
    # Pi instances inherit no config_topic, no config form on theirs.
    # Slice the Pi card by its deterministic anchor id (card order isn't
    # guaranteed alphabetical).
    pi_start = body.index('id="device-pi_bin_kitchen"')
    pi_end = body.find('id="device-', pi_start + 1)
    pi_section = body[pi_start : pi_end if pi_end != -1 else len(body)]
    assert 'name="sleep_interval_s"' not in pi_section


def test_status_cache_renders_after_heartbeat(app: Flask) -> None:
    # Push a status payload through the status cache the way the MQTT
    # dispatcher would, then re-render and check the parsed fields show up.
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="esp32_lab", kind="esp32_client", name="Lab ESP32")
    app.config["DEVICE_STATUS"]["esp32_lab"] = {
        "received_at": time.time(),
        "parsed": {
            "battery_mv": 3820,
            "battery_pct": 67,
            "rssi": -58,
            "ip": "10.0.0.42",
        },
    }
    body = client.get("/settings/devices").get_data(as_text=True)
    # Fresh heartbeat -> "ok" status dot + parsed fields visible.
    assert "is-ok" in body
    assert "3820" in body
    assert "10.0.0.42" in body


def test_merge_keeps_prev_values_when_new_is_none() -> None:
    """An LWT typically carries only state=offline; merge must preserve
    the last known battery / rssi / ip rather than blanking them."""
    prev = {"battery_mv": 3820, "battery_pct": 67, "rssi": -58, "ip": "10.0.0.42"}
    lwt = {"battery_mv": None, "battery_pct": None, "rssi": None, "ip": None, "state": "offline"}
    merged = merge_status_parsed(prev, lwt)
    assert merged["battery_mv"] == 3820
    assert merged["battery_pct"] == 67
    assert merged["rssi"] == -58
    assert merged["ip"] == "10.0.0.42"
    assert merged["state"] == "offline"


def test_merge_takes_new_values_when_present() -> None:
    """A fresh heartbeat with numbers wins over the cached snapshot."""
    prev = {"battery_mv": 3820, "battery_pct": 67, "state": "offline"}
    new = {"battery_mv": 3700, "battery_pct": 55, "rssi": -60, "ip": "10.0.0.42"}
    merged = merge_status_parsed(prev, new)
    assert merged["battery_mv"] == 3700
    assert merged["battery_pct"] == 55
    assert merged["rssi"] == -60
    # Keys absent from new are preserved (state lingers until something
    # overwrites it, accepted limitation; firmware can re-publish state).
    assert merged["state"] == "offline"


def test_merge_derives_battery_pct_from_mv_for_trmnl() -> None:
    """TRMNL kit firmware reports raw mV only. The merge step must
    derive battery_pct so the topbar indicator and HA discovery both
    pick up the device. Curve: 4200 mV = 100%, 3300 mV = 0%."""
    # Fresh heartbeat with only voltage, no prior cache.
    merged = merge_status_parsed({}, {"battery_mv": 4200, "battery_pct": None})
    assert merged["battery_pct"] == 100
    # Mid-range: linear from the curve. 3750 = 50% (midpoint 3300-4200).
    merged = merge_status_parsed({}, {"battery_mv": 3750, "battery_pct": None})
    assert merged["battery_pct"] == 50
    # At/below cutoff clamps to 0.
    merged = merge_status_parsed({}, {"battery_mv": 3300, "battery_pct": None})
    assert merged["battery_pct"] == 0
    merged = merge_status_parsed({}, {"battery_mv": 3100, "battery_pct": None})
    assert merged["battery_pct"] == 0
    # Above full clamps to 100 (USB-attached charging spike).
    merged = merge_status_parsed({}, {"battery_mv": 4400, "battery_pct": None})
    assert merged["battery_pct"] == 100


def test_merge_keeps_explicit_pct_over_derived() -> None:
    """ESP32 firmware sends both mV and explicit pct; the explicit value
    must win because the curve is a rough linear approximation."""
    merged = merge_status_parsed({}, {"battery_mv": 3700, "battery_pct": 88})
    # 3700 mV would have derived to ~44%; explicit 88 must survive.
    assert merged["battery_pct"] == 88


def test_merge_no_battery_data_leaves_pct_none() -> None:
    """Pi clients (mains-powered) don't report either field; merge
    should leave battery_pct as None so the topbar indicator skips them."""
    merged = merge_status_parsed({}, {"battery_pct": None, "battery_mv": None})
    assert merged.get("battery_pct") is None


def test_merge_derives_pct_when_new_only_brings_mv() -> None:
    """A heartbeat that drops the pct but still carries mV (e.g. firmware
    upgrade to a build that stops sending the percent header) should
    still produce a usable battery_pct downstream."""
    prev = {"battery_mv": 3800, "battery_pct": 56}
    new = {"battery_mv": 4000, "battery_pct": None}
    merged = merge_status_parsed(prev, new)
    assert merged["battery_mv"] == 4000
    # 4000 mV on the curve: (4000-3300) / 900 * 100 = 77.77 -> 78.
    assert merged["battery_pct"] == 78


def test_stale_heartbeat_renders_warn(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="esp32_lab", kind="esp32_client")
    app.config["DEVICE_STATUS"]["esp32_lab"] = {
        "received_at": time.time() - 200,  # past 90s fresh threshold
        "parsed": {"battery_mv": 3700},
    }
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "is-warn" in body


def test_config_form_rejects_out_of_bounds(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="esp32_lab", kind="esp32_client", name="Lab ESP32")
    # 5 seconds is below the 30-second min the device declares.
    resp = client.post(
        "/settings/device-esp32_lab",
        data={"sleep_interval_s": "5"},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "Invalid Lab ESP32 config" in body
    # The bad config value is not persisted. (An unrelated
    # ``palette_profile_slug`` entry may appear on the device from the
    # v0.71.x calibration self-heal that fires when the settings page
    # renders after the redirect — it fills a supported gamut's default
    # slug so the tone editor stays visible. Check the specific field.)
    store = SettingsStore(tmp_path / "core" / "settings.json")
    dev_section = store.get_section("devices").get("esp32_lab", {})
    assert "sleep_interval_s" not in dev_section


def test_config_form_saves_and_publishes_on_valid_input(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="esp32_lab", kind="esp32_client")
    client.post(
        "/settings/device-esp32_lab",
        data={"sleep_interval_s": "1800"},
        follow_redirects=False,
    )
    store = SettingsStore(tmp_path / "core" / "settings.json")
    saved = store.get_section("devices")
    # Persisted under devices.<id>.
    assert saved["esp32_lab"]["sleep_interval_s"] == 1800


def test_device_status_subscription_dispatches_to_cache(app: Flask) -> None:
    # Register an instance, then simulate a heartbeat on its status
    # topic. The per-device handler updates the status cache; the
    # wildcard listener does NOT cache it (it's an instance, not a
    # discovered device).
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="esp32_lab", kind="esp32_client")
    transport = app.config["MQTT_TRANSPORT"]
    payload = json.dumps({"battery_mv": 4000, "rssi": -55, "ip": "1.2.3.4"}).encode()

    class _Msg:
        topic = "tesserae/esp32_lab/status"
        payload_attr = payload

    msg = _Msg()
    msg.payload = payload  # type: ignore[attr-defined]
    transport._on_message(None, None, msg)

    cache = app.config["DEVICE_STATUS"]
    assert "esp32_lab" in cache
    assert cache["esp32_lab"]["parsed"]["battery_mv"] == 4000
    assert cache["esp32_lab"]["parsed"]["ip"] == "1.2.3.4"


def test_instance_status_subscriptions_replayed_on_broker_rebuild(app: Flask) -> None:
    # Trigger a broker rebuild (via the same callable settings_routes uses
    # on save) and verify the instance subscription is re-installed on
    # the new transport instance. Kinds are not subscribed, their
    # heartbeats flow to discovery instead.
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="esp32_lab", kind="esp32_client")
    app.config["REBUILD_TRANSPORT"]()
    new_transport = app.config["MQTT_TRANSPORT"]
    assert "tesserae/esp32_lab/status" in new_transport.topic_subscriptions
    assert "tesserae/+/status" in new_transport.topic_subscriptions  # discovery wildcard
    # Kind default topics are NOT directly subscribed any more.
    assert "tesserae/esp32/status" not in new_transport.topic_subscriptions


def test_trmnl_api_setup_auto_provisions_native_device_by_mac(app: Flask) -> None:
    """0.44.1: full Terminus BYOS contract. When a native TRMNL
    device (any client that sends its MAC in the ``Id`` header) hits
    /api/setup with no recognised auth, Tesserae auto-creates a
    device instance keyed by MAC, mints a high-entropy 20-char
    api_key, and returns it. The device immediately starts polling
    /api/display with a real, recognised token; no admin click, no
    Discovered → Register two-step, no TRMNL mobile app."""
    client = app.test_client()
    resp = client.get(
        "/api/setup",
        headers={
            "Id": "E0:72:A1:D8:28:9C",
            "Model": "xiao_epaper_display",
            "Width": "800",
            "Height": "480",
            "Fw-Version": "1.5.12",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, dict)
    api_key = body["api_key"]
    # Native api_keys are 20-char alphanumeric (Terminus parity), not
    # the typeable 5-char form (that stays for KOReader path only).
    assert len(api_key) == 20
    assert api_key.isalnum()
    # friendly_id is six characters from the unambiguous alphabet.
    friendly = body["friendly_id"]
    assert len(friendly) == 6

    # The device was AUTO-CREATED, not parked in the Discovered cache.
    # Find it via the registry by MAC.
    devs = app.config["DEVICE_REGISTRY"]
    matches = [d for d in devs.all() if d.manifest.get("mac") == "E0:72:A1:D8:28:9C"]
    assert len(matches) == 1
    device = matches[0]
    assert device.manifest["access_token"] == api_key
    assert device.manifest["friendly_id"] == friendly
    # Panel dims should have been picked up from the Width/Height
    # headers, not the default.
    assert device.manifest["panel"]["w"] == 800
    assert device.manifest["panel"]["h"] == 480


def test_trmnl_api_setup_picks_trmnl_x_panel_from_model_header(app: Flask) -> None:
    """0.49.2 regression: native TRMNL firmware doesn't send Width/Height
    on /api/setup (only on /api/display), so auto-provision must look up
    panel dims from the ``Model`` header instead. A TRMNL X (Model: "x")
    should be provisioned at its native 1872x1404, not the original-TRMNL
    800x480 default. Reported by @tommerty on discussion #8.

    Without this branch the device's stored panel stays 800x480, the
    composer designs the dashboard at the wrong canvas size, and the
    rendered PNG comes out blurry on the panel even though the /api/
    display path serves a correctly-sized image (per-request Width/
    Height take over there)."""
    client = app.test_client()
    resp = client.get(
        "/api/setup",
        headers={
            "Id": "A1:B2:C3:D4:E5:F6",
            "Model": "x",
            "Fw-Version": "1.6.0",
            # No Width / Height, matches buildSetupHeaders in
            # the native firmware.
        },
    )
    assert resp.status_code == 200
    devs = app.config["DEVICE_REGISTRY"]
    matches = [d for d in devs.all() if d.manifest.get("mac") == "A1:B2:C3:D4:E5:F6"]
    assert len(matches) == 1
    device = matches[0]
    assert device.manifest["panel"]["w"] == 1872
    assert device.manifest["panel"]["h"] == 1404


def test_trmnl_api_setup_unknown_model_falls_back_to_original_panel(app: Flask) -> None:
    """Unknown ``Model`` values (a future TRMNL we haven't added yet,
    or a community fork) fall back to the original 800x480 default
    rather than crashing or guessing. The user can adjust on the
    device-settings page; the table of known models can grow in a
    follow-up."""
    client = app.test_client()
    resp = client.get(
        "/api/setup",
        headers={"Id": "11:22:33:44:55:66", "Model": "future_trmnl_y_2030"},
    )
    assert resp.status_code == 200
    devs = app.config["DEVICE_REGISTRY"]
    matches = [d for d in devs.all() if d.manifest.get("mac") == "11:22:33:44:55:66"]
    assert len(matches) == 1
    assert matches[0].manifest["panel"]["w"] == 800
    assert matches[0].manifest["panel"]["h"] == 480


def test_trmnl_api_setup_returns_same_credentials_for_known_mac(app: Flask) -> None:
    """Second /api/setup call from the same MAC must hand back the
    same api_key + friendly_id, not auto-create another instance."""
    client = app.test_client()
    headers = {"Id": "F0:AB:CD:11:22:33", "Width": "800", "Height": "480"}
    first = client.get("/api/setup", headers=headers).get_json()
    second = client.get("/api/setup", headers=headers).get_json()
    assert first["api_key"] == second["api_key"]
    assert first["friendly_id"] == second["friendly_id"]
    # Only one instance in the registry.
    devs = app.config["DEVICE_REGISTRY"]
    macs = [d.manifest.get("mac") for d in devs.all() if d.manifest.get("mac")]
    assert macs.count("F0:AB:CD:11:22:33") == 1


def test_trmnl_api_setup_koreader_path_falls_back_to_discovery(app: Flask) -> None:
    """Clients without a MAC (KOReader on Kindle) still hit the
    discovery-cache fallback: token minted, Discovered entry created,
    admin clicks Register. The pre-0.44.1 flow continues to work."""
    client = app.test_client()
    resp = client.get("/api/setup", headers={"User-Agent": "KOReader/2024"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["api_key"]) == 5  # typeable token for hand-entry
    # Discovery cache has an entry.
    cache = app.config["DISCOVERY_CACHE"]
    assert any(e.id.startswith("trmnl_") for e in cache.all())


def test_trmnl_api_log_accepts_flat_logs_array(app: Flask, caplog) -> None:
    """0.44.8: /api/log/ parses the Terminus payload shape.

    Flat shape: ``{"logs": [{...}, {...}]}``. Each entry must surface
    as its own log line so it stays readable in journald / docker
    logs rather than collapsing into one blob."""
    import logging

    client = app.test_client()
    body = {
        "logs": [
            {"creation_timestamp": "2026-06-10T09:00:00Z", "message": "boot ok"},
            {"creation_timestamp": "2026-06-10T09:00:05Z", "message": "wifi up"},
        ]
    }
    with caplog.at_level(logging.INFO, logger="app.trmnl_api"):
        resp = client.post("/api/log", json=body)
    assert resp.status_code == 200
    # Two entries -> two info log lines.
    matching = [r for r in caplog.records if "trmnl: /api/log/ from" in r.getMessage()]
    assert len(matching) == 2
    assert any("boot ok" in r.getMessage() for r in matching)
    assert any("wifi up" in r.getMessage() for r in matching)


def test_trmnl_api_log_accepts_nested_logs_array(app: Flask, caplog) -> None:
    """0.44.8: nested shape ``{"log": {"logs_array": [...]}}`` also
    parses; older TRMNL firmwares ship this envelope."""
    import logging

    client = app.test_client()
    body = {
        "log": {
            "logs_array": [{"creation_timestamp": 1234567890, "message": "hello"}],
        }
    }
    with caplog.at_level(logging.INFO, logger="app.trmnl_api"):
        resp = client.post("/api/log", json=body)
    assert resp.status_code == 200
    matching = [r for r in caplog.records if "trmnl: /api/log/ from" in r.getMessage()]
    assert len(matching) == 1
    assert "hello" in matching[0].getMessage()


def test_trmnl_api_log_falls_back_to_raw_body_when_not_terminus_shape(app: Flask, caplog) -> None:
    """Unknown payloads still return 200 (firmware won't tolerate 4xx
    on /api/log/) but get logged as raw text rather than crashing."""
    import logging

    client = app.test_client()
    with caplog.at_level(logging.INFO, logger="app.trmnl_api"):
        resp = client.post("/api/log", data=b"not json at all")
    assert resp.status_code == 200
    matching = [r for r in caplog.records if "trmnl: /api/log/ from" in r.getMessage()]
    assert matching
    # The raw-body branch labels by byte count; the structured path doesn't.
    assert any("bytes" in r.getMessage() for r in matching)


def test_trmnl_api_log_level_acks_for_native_firmware(app: Flask) -> None:
    """Native TRMNL firmware queries /api/log/level on boot for the
    server's preferred log verbosity. Tesserae doesn't actually drive
    remote log levels, but the firmware refuses to continue polling
    if the endpoint 404s — we just acknowledge with 200."""
    client = app.test_client()
    resp = client.post("/api/log/level", data=b"")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, dict)
    assert body.get("status") == 200


def test_trmnl_api_setup_returns_manifest_friendly_id_for_known_device(app: Flask) -> None:
    """Devices created in 0.44.0+ get a six-character friendly_id
    auto-populated on the manifest by device_service.create_instance.
    The /api/setup response returns that value as ``friendly_id`` so
    TRMNL firmwares can show it on their setup / about screens."""
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="kindle_test", kind="trmnl_client")
    devs = app.config["DEVICE_REGISTRY"]
    instance = devs.get("kindle_test")
    assert instance is not None
    token = instance.manifest["access_token"]
    friendly = instance.manifest["friendly_id"]
    assert len(friendly) == 6
    assert all(c.isupper() or c.isdigit() for c in friendly)

    resp = client.get("/api/setup", headers={"Access-Token": token})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("friendly_id") == friendly


def test_trmnl_api_display_envelope_matches_terminus_shape(app: Flask) -> None:
    """0.44.1: /api/display response shape matches the official
    Terminus BYOS contract. Every field a native TRMNL firmware
    expects is present; the invented fields from 0.44.0
    (``pending_status_change``, ``network_diagnostics_url``) are
    gone."""
    client = app.test_client()
    _sign_in(client)
    _add_instance(client, id="trmnl_envelope_test", kind="trmnl_client")
    devs = app.config["DEVICE_REGISTRY"]
    token = devs.get("trmnl_envelope_test").manifest["access_token"]

    resp = client.get("/api/display", headers={"Access-Token": token})
    assert resp.status_code == 200
    body = resp.get_json()
    # Terminus envelope fields.
    expected = {
        "status",
        "filename",
        "image_url",
        "image_url_timeout",
        "refresh_rate",
        "special_function",
        "firmware_url",
        "firmware_version",
        "update_firmware",
        "reset_firmware",
        "maximum_compatibility",
        "friendly_id",
    }
    assert expected <= set(body.keys())
    # special_function default aligned with Terminus in 0.44.8; "none"
    # caused some firmware to skip deep-sleep, which drains a LiPo.
    assert body["special_function"] == "sleep"
    assert body["maximum_compatibility"] is False
    # Invented-by-Tesserae fields removed in 0.44.1.
    assert "pending_status_change" not in body
    assert "network_diagnostics_url" not in body


def test_trmnl_api_display_auths_by_mac_when_id_header_present(app: Flask) -> None:
    """0.44.1: MAC-first auth precedence. A device with a manifest
    ``mac`` resolves through the ``Id`` header even if the
    Access-Token header is missing entirely."""
    client = app.test_client()
    # Auto-provision via /api/setup to get a device with a stored MAC.
    setup = client.get(
        "/api/setup",
        headers={"Id": "AB:CD:EF:01:23:45", "Width": "800", "Height": "480"},
    ).get_json()
    assert setup["api_key"]
    # /api/display with ONLY the Id header (no Access-Token) should
    # still resolve the device.
    resp = client.get("/api/display", headers={"Id": "AB:CD:EF:01:23:45"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["friendly_id"] == setup["friendly_id"]


def test_trmnl_api_display_auto_provisions_when_only_a_mac_arrives(app: Flask) -> None:
    """Real-world: the XIAO firmware caches whatever api_key it got at
    setup (potentially a placeholder from a pre-0.44.0 Tesserae) and
    keeps polling /api/display with it. The token won't be
    recognised, but the MAC will be present. /api/display should
    auto-create the device on first sight rather than dropping it
    into the Discovered strip and making the admin click Register.
    Matches Terminus's behaviour, where /api/display is implicitly
    a registration trigger if the device isn't known."""
    client = app.test_client()
    resp = client.get(
        "/api/display",
        headers={
            "Id": "BB:CC:DD:EE:FF:00",
            "Access-Token": "paste-a-server-issued-token-into-your-client",
            "Width": "800",
            "Height": "480",
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # Sanity-check: real frame envelope, not a 404 problem-details.
    assert "image_url" in body
    # Device is registered and resolvable by MAC.
    devs = app.config["DEVICE_REGISTRY"]
    matches = [d for d in devs.all() if d.manifest.get("mac") == "BB:CC:DD:EE:FF:00"]
    assert len(matches) == 1
