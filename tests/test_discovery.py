"""Discovery cache + wildcard dispatcher + register-from-discovery flow.

Covers the three slices independently:
* DiscoveryCache module-level: malformed ids rejected, payload parsing
  is lenient, snapshot order is most-recent-first.
* Wildcard wiring in main: heartbeats for unknown ids land in the
  cache, registered devices are filtered out, known kind status topics
  don't pollute discovery.
* Settings routes: one-click register copies kind / panel from the
  cache into a real instance, dismiss drops the cached entry.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from flask import Flask

from app.discovery import DiscoveryCache, device_id_from_status_topic, record_trmnl_discovery
from app.main import create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    app = create_app(testing=True, data_root=tmp_path)
    app.config["TESTING"] = True
    return app


# -- DiscoveryCache --------------------------------------------------------


def test_cache_records_valid_payload() -> None:
    cache = DiscoveryCache()
    entry = cache.record(
        "esp32_hallway",
        b'{"kind":"esp32_client","panel_w":800,"panel_h":480,"fw_version":"0.2"}',
    )
    assert entry is not None
    assert entry.kind == "esp32_client"
    assert entry.panel_w == 800
    assert entry.panel_h == 480
    assert entry.fw_version == "0.2"


def test_cache_rejects_malformed_id() -> None:
    cache = DiscoveryCache()
    # Same regex the add-device flow enforces, start with a letter,
    # 2–32 chars, lowercase + digits + underscore / hyphen.
    assert cache.record("Has-Caps", b"{}") is None
    assert cache.record("", b"{}") is None
    assert cache.record("a", b"{}") is None
    assert cache.record("1starts_with_digit", b"{}") is None


def test_cache_ignores_empty_tombstone_payload() -> None:
    # An empty payload is what the broker delivers after a retained
    # heartbeat is cleared (Dismiss). It must NOT create a ghost entry,
    # otherwise clearing one would immediately re-add a kind-less device.
    cache = DiscoveryCache()
    assert cache.record("esp32", b"") is None
    assert cache.record("esp32", b"   ") is None
    assert cache.all() == []


def test_wildcard_clear_does_not_resurrect(app: Flask) -> None:
    # Simulate the dismiss flow at the broker level: a real heartbeat
    # caches the device; the empty retained tombstone must clear it for
    # good, not bounce it back.
    transport = app.config["MQTT_TRANSPORT"]
    cache = app.config["DISCOVERY_CACHE"]
    transport._on_message(
        None, None, _fake_msg("tesserae/esp32/status", b'{"kind":"esp32_client"}')
    )
    assert cache.get("esp32") is not None
    cache.forget("esp32")
    transport._on_message(None, None, _fake_msg("tesserae/esp32/status", b""))  # tombstone
    assert cache.get("esp32") is None


def test_cache_merges_so_kind_persists_across_heartbeats() -> None:
    # A full heartbeat establishes kind/panel; a later lean one (e.g. a
    # device that drops to {"state":"idle"}) must NOT erase them, or the
    # Register button would vanish. Mirrors the retained-LWT problem.
    cache = DiscoveryCache()
    cache.record("esp32_office", b'{"kind":"esp32_client","panel_w":1200,"panel_h":1600}')
    cache.record("esp32_office", b'{"state":"idle"}')
    e = cache.get("esp32_office")
    assert e is not None
    assert e.kind == "esp32_client"  # preserved
    assert (e.panel_w, e.panel_h) == (1200, 1600)  # preserved
    assert e.parsed["state"] == "idle"  # newest value applied


def test_cache_tolerates_non_json_payload() -> None:
    cache = DiscoveryCache()
    entry = cache.record("pi_kitchen", b"plain text")
    assert entry is not None
    assert entry.parsed == {"raw": "plain text"}


def test_cache_snapshot_is_most_recent_first() -> None:
    cache = DiscoveryCache()
    cache.record("first", b"{}")
    time.sleep(0.01)
    cache.record("second", b"{}")
    ids = [d.id for d in cache.all()]
    assert ids == ["second", "first"]


def test_trmnl_discovery_picks_up_panel_dims_case_insensitively() -> None:
    """KOReader sends ``Png-Width`` / ``Png-Height`` (Title-case), the
    recon scripts send ``png-width`` (lowercase), native TRMNL sends
    ``Width``. All three should land in the cache so the discovered-
    device strip pre-fills the panel dims and the user doesn't have to
    type them in after one-click register."""
    cache = DiscoveryCache()
    entry = record_trmnl_discovery(
        cache,
        token="kfrxz",
        headers={"Png-Width": "758", "Png-Height": "1024", "User-Agent": "KOReader/2024"},
        remote_addr="192.168.1.42",
    )
    assert entry is not None
    assert entry.panel_w == 758
    assert entry.panel_h == 1024
    assert entry.fw_version == "KOReader/2024"
    assert entry.ip == "192.168.1.42"


def test_cache_forget_returns_truthy_only_when_present() -> None:
    cache = DiscoveryCache()
    cache.record("ghost", b"{}")
    assert cache.forget("ghost") is True
    assert cache.forget("ghost") is False  # already gone


def test_topic_helper_extracts_id() -> None:
    assert device_id_from_status_topic("tesserae/esp32_kitchen/status") == "esp32_kitchen"
    assert device_id_from_status_topic("tesserae/esp32_kitchen/frame/bin") is None
    assert device_id_from_status_topic("not/tesserae/esp32/status") is None


# -- Wildcard dispatcher in main ------------------------------------------


def _fake_msg(topic: str, payload: bytes):
    return type("Msg", (), {"topic": topic, "payload": payload})()


def test_wildcard_caches_unknown_device(app: Flask) -> None:
    transport = app.config["MQTT_TRANSPORT"]
    cache = app.config["DISCOVERY_CACHE"]
    transport._on_message(
        None,
        None,
        _fake_msg(
            "tesserae/esp32_hallway/status",
            b'{"kind":"esp32_client","panel_w":800,"panel_h":480}',
        ),
    )
    assert [d.id for d in cache.all()] == ["esp32_hallway"]
    assert cache.get("esp32_hallway").kind == "esp32_client"


def test_wildcard_caches_kind_default_topics(app: Flask) -> None:
    """Heartbeats on a kind's default topic (e.g. fresh pi_bin install
    publishing to tesserae/pi_bin/status) DO show up as discovered -
    kinds are templates, not bindable devices, so anything publishing
    there is a not-yet-registered physical device."""
    transport = app.config["MQTT_TRANSPORT"]
    cache = app.config["DISCOVERY_CACHE"]
    transport._on_message(
        None, None, _fake_msg("tesserae/esp32/status", b'{"kind":"esp32_client"}')
    )
    transport._on_message(
        None, None, _fake_msg("tesserae/pi_bin/status", b'{"kind":"pi_bin_client"}')
    )
    assert {d.id for d in cache.all()} == {"esp32", "pi_bin"}


def test_wildcard_skips_registered_instances(app: Flask) -> None:
    """Once an instance is registered, its status topic is owned by
    the per-device handler and shouldn't pollute discovery."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post("/settings/devices/add", data={"id": "esp32_lab", "kind": "esp32_client"})
    transport = app.config["MQTT_TRANSPORT"]
    cache = app.config["DISCOVERY_CACHE"]
    transport._on_message(None, None, _fake_msg("tesserae/esp32_lab/status", b'{"battery_pct":80}'))
    assert cache.get("esp32_lab") is None


# -- Settings routes ------------------------------------------------------


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_discovered_devices_render_on_devices_page(app: Flask) -> None:
    cache = app.config["DISCOVERY_CACHE"]
    cache.record(
        "esp32_attic",
        b'{"kind":"esp32_client","panel_w":640,"panel_h":384,"fw_version":"0.3"}',
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/devices").get_data(as_text=True)
    # Issue #17 retitled the section from "Discovered devices" to just
    # "Discovered" (the description carries the explainer now), and
    # the row is grouped under "MQTT-DISCOVERED" since the test entry
    # carries no ``transport`` hint.
    assert "Discovered" in body
    assert "MQTT-discovered" in body
    assert "esp32_attic" in body
    assert "esp32_client" in body
    assert "640×384" in body
    assert "Register" in body


def test_register_discovered_materialises_instance(app: Flask, tmp_path: Path) -> None:
    cache = app.config["DISCOVERY_CACHE"]
    cache.record(
        "esp32_attic",
        b'{"kind":"esp32_client","panel_w":640,"panel_h":384}',
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/devices/discovery/esp32_attic/register", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("#device-esp32_attic")

    dev = app.config["DEVICE_REGISTRY"].get("esp32_attic")
    assert dev is not None
    assert dev.kind_of == "esp32_client"
    assert dev.panel == {"w": 640, "h": 384, "orientation": "landscape", "name": "ESP32 e-paper"}
    assert dev.status_topic == "tesserae/esp32_attic/status"

    # Renderer clone created.
    assert app.config["RENDERER_REGISTRY"].get("esp32_bin__esp32_attic") is not None
    # Discovery entry removed once registered, no duplicate row.
    assert cache.get("esp32_attic") is None
    # JSON file persisted.
    assert (tmp_path / "devices" / "esp32_attic.json").exists()


def test_register_discovered_honours_bmp_format(app: Flask, tmp_path: Path) -> None:
    # A memory-constrained CircuitPython client declares format:"bmp" in
    # its discover payload so registering it binds the uncompressed-BMP
    # renderer, not the default PNG one.
    cache = app.config["DISCOVERY_CACHE"]
    cache.record(
        "cp_kitchen",
        b'{"kind":"circuitpython_generic","panel_w":400,"panel_h":300,'
        b'"gamut":"bwr_3","format":"bmp"}',
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/devices/discovery/cp_kitchen/register", follow_redirects=False)
    assert resp.status_code == 302

    dev = app.config["DEVICE_REGISTRY"].get("cp_kitchen")
    assert dev is not None
    assert dev.renderer_ids == ["circuitpython_bmp__cp_kitchen"]
    registry = app.config["RENDERER_REGISTRY"]
    assert registry.get("circuitpython_bmp__cp_kitchen") is not None
    assert registry.get("circuitpython_png__cp_kitchen") is None


def test_register_discovered_defaults_to_png_without_format(app: Flask, tmp_path: Path) -> None:
    cache = app.config["DISCOVERY_CACHE"]
    cache.record(
        "cp_hall",
        b'{"kind":"circuitpython_generic","panel_w":400,"panel_h":300,"gamut":"bwr_3"}',
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/devices/discovery/cp_hall/register", follow_redirects=False)
    assert resp.status_code == 302

    dev = app.config["DEVICE_REGISTRY"].get("cp_hall")
    assert dev is not None
    assert dev.renderer_ids == ["circuitpython_png__cp_hall"]
    registry = app.config["RENDERER_REGISTRY"]
    assert registry.get("circuitpython_bmp__cp_hall") is None


def test_register_refuses_without_kind(app: Flask) -> None:
    """A heartbeat with no 'kind' field can't be one-click registered
    , the form falls back to the manual Add-device path."""
    cache = app.config["DISCOVERY_CACHE"]
    cache.record("mystery_box", b'{"battery_pct":50}')  # no kind field
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/devices/discovery/mystery_box/register", follow_redirects=False)
    assert resp.status_code == 302
    assert app.config["DEVICE_REGISTRY"].get("mystery_box") is None
    # Cache entry stays so the user can still see it in the list.
    assert cache.get("mystery_box") is not None


def test_dismiss_drops_from_cache(app: Flask) -> None:
    cache = app.config["DISCOVERY_CACHE"]
    cache.record("noise", b"{}")
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/devices/discovery/noise/dismiss", follow_redirects=False)
    assert resp.status_code == 302
    assert cache.get("noise") is None


def test_trmnl_discovery_flags_placeholder_token_as_unpaired() -> None:
    """The official TRMNL DIY-kit firmware ships with a literal
    placeholder token (``paste-a-server-issued-token-into-your-client``)
    that the user is expected to replace via the pairing flow. If we
    let create_instance preserve it the new device's secret would be
    a publicly-known string. record_trmnl_discovery flags this with
    needs_pairing and drops the access_token from the cache so the
    register flow mints a fresh one."""
    cache = DiscoveryCache()
    entry = record_trmnl_discovery(
        cache,
        token="paste-a-server-issued-token-into-your-client",
        headers={
            "Id": "E0:72:A1:D8:28:9C",
            "Model": "xiao_epaper_display",
            "Width": "800",
            "Height": "480",
            "Fw-Version": "1.5.12",
        },
        remote_addr="192.168.50.125",
    )
    assert entry is not None
    assert entry.parsed.get("needs_pairing") is True
    assert "access_token" not in entry.parsed
    # MAC + model wired through to the discovered card via parsed.
    assert entry.parsed.get("mac") == "E0:72:A1:D8:28:9C"
    assert entry.parsed.get("model") == "xiao_epaper_display"
    # MAC-keyed synthetic id is stable across reboots (token would
    # change after first pairing).
    assert entry.id.startswith("trmnl_e072a1d8289c") or entry.id == "trmnl_e072a1d8289c"


def test_trmnl_discovery_preserves_real_token() -> None:
    """KOReader users already paste a real token before discovery
    fires; preserve it so register doesn't make them re-paste."""
    cache = DiscoveryCache()
    entry = record_trmnl_discovery(
        cache,
        token="kfrxz",
        headers={"User-Agent": "KOReader/2024"},
        remote_addr="192.168.1.42",
    )
    assert entry is not None
    assert entry.parsed.get("access_token") == "kfrxz"
    assert entry.parsed.get("needs_pairing") is not True
