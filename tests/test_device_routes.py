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
    assert "Device: Pi BIN client</span>" not in body
    assert "Device: Pi PNG client</span>" not in body
    assert "Device: ESP32 client</span>" not in body
    # Registered instances do show up, with the "no heartbeat" status state.
    assert "Device: Lab ESP32" in body
    assert "Device: Kitchen Pi" in body
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
    # Nothing persisted, nothing published.
    store = SettingsStore(tmp_path / "core" / "settings.json")
    assert "esp32_lab" not in store.get_section("devices")


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


def test_trmnl_api_setup_mints_real_token_for_new_device(app: Flask) -> None:
    """Real-world repro from a XIAO ESP32-C3 TRMNL DIY-kit polling
    Tesserae. The firmware contract is: device sends its MAC in the
    ``Id`` header to GET /api/setup, server hands back an ``api_key``
    the device stores locally. Tesserae used to literally return the
    string ``paste-a-server-issued-token-into-your-client`` as the
    api_key, which the device would dutifully use as its access token
    on every subsequent /api/display poll — leaving the device's
    secret as a publicly-known string forever. Fix: mint a real
    short-form Tesserae token, record a Discovered entry with the
    new token + MAC + Model + panel dims pre-filled, and hand the
    real token back to the device."""
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
    # The literal placeholder must NEVER come back; that was the bug.
    assert api_key != "paste-a-server-issued-token-into-your-client"
    # Real Tesserae tokens are short typeable strings (5 chars from a
    # 30-char alphabet); accept lengths in that range as a sanity check.
    assert 4 <= len(api_key) <= 8
    # friendly_id should also not be the bare placeholder.
    assert body["friendly_id"] not in ("paste-a-server-issued-token-into-your-client",)

    # Discovery cache should have an entry keyed off the MAC with the
    # new token preserved so admin's one-click Register adopts it.
    cache = app.config["DISCOVERY_CACHE"]
    entries = cache.all()
    matches = [e for e in entries if e.id.startswith("trmnl_e072a1d8289c")]
    assert len(matches) == 1
    entry = matches[0]
    assert entry.parsed.get("access_token") == api_key
    assert entry.parsed.get("mac") == "E0:72:A1:D8:28:9C"
    assert entry.parsed.get("model") == "xiao_epaper_display"
    # Discovered entry should NOT carry the needs_pairing flag, the
    # token IS real now.
    assert entry.parsed.get("needs_pairing") is not True
