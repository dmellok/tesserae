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

import threading
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
    assert "not registered" in body
    assert "MQTT" in body
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


def test_topbar_chip_lists_unregistered_devices(app: Flask) -> None:
    """The base template lights a "New devices" chip while the discovery
    cache holds a device that is not registered, linking to the Devices
    tab with the register rows opened. Registered ids and a cleared cache
    leave the topbar alone."""
    cache = app.config["DISCOVERY_CACHE"]
    client = app.test_client()
    _sign_in(client)

    body = client.get("/settings/server").get_data(as_text=True)
    assert "topbar-discovered" not in body

    cache.record("esp32_attic", b'{"kind":"esp32_client","ip":"192.168.50.91"}')
    body = client.get("/settings/server").get_data(as_text=True)
    assert 'class="tg tg--teal tg--lg topbar-discovered topbar-discovered--topbar"' in body
    assert "1 new device heard" in body
    assert "/settings/devices?discovered=1" in body
    # The drawer entry names the device for the phone layout.
    assert "esp32_attic" in body

    cache.record("pi_hall", b'{"kind":"pi_bin_client"}')
    body = client.get("/settings/server").get_data(as_text=True)
    assert "2 new devices heard" in body

    cache.forget("esp32_attic")
    cache.forget("pi_hall")
    body = client.get("/settings/server").get_data(as_text=True)
    assert "topbar-discovered" not in body


def test_register_discovered_visionect_sign_keeps_its_uuid(app: Flask) -> None:
    # A Visionect sign announces its UUID. Registering it, even under a
    # custom id, must bind that device to that sign, not to whichever
    # unknown sign happens to connect next.
    cache = app.config["DISCOVERY_CACHE"]
    cache.record(
        "visionect_6a7e40",
        b'{"kind":"visionect","transport":"visionect",'
        b'"uuid":"aabbccdd-0000-1111-2222-333344445555","panel_w":1024,"panel_h":758}',
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/discovery/visionect_6a7e40/register",
        data={"id": "desk_joan", "name": "Desk Joan"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    dev = app.config["DEVICE_REGISTRY"].get("desk_joan")
    assert dev is not None
    assert dev.kind_of == "visionect"
    assert dev.transport == "push"
    section = app.config["SETTINGS_STORE"].get_section("devices")
    assert section["desk_joan"]["uuid"] == "aabbccdd-0000-1111-2222-333344445555"


def test_adding_a_visionect_sign_with_a_bad_uuid_creates_nothing(app: Flask) -> None:
    # A mistyped UUID used to be dropped after the device was created,
    # leaving a blank device that binds whichever unknown sign connects next.
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/add",
        data={"kind": "visionect", "id": "hall_sign", "name": "Hall", "uuid": "36002000-1247"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Device not added" in resp.get_data(as_text=True)
    assert app.config["DEVICE_REGISTRY"].get("hall_sign") is None
    section = app.config["SETTINGS_STORE"].get_section("devices")
    assert "hall_sign" not in section


def test_one_sign_uuid_binds_one_device(app: Flask) -> None:
    sign = "aabbccdd-0000-1111-2222-333344445555"
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/add",
        data={"kind": "visionect", "id": "hall_sign", "name": "Hall", "uuid": sign.upper()},
    )
    assert resp.status_code == 302
    assert app.config["DEVICE_REGISTRY"].get("hall_sign") is not None
    # The same sign again, written without dashes: refused.
    resp = client.post(
        "/settings/devices/add",
        data={
            "kind": "visionect",
            "id": "lobby_sign",
            "name": "Lobby",
            "uuid": sign.replace("-", ""),
        },
        follow_redirects=True,
    )
    assert "already bound to device &#39;hall_sign&#39;" in resp.get_data(as_text=True)
    assert app.config["DEVICE_REGISTRY"].get("lobby_sign") is None
    # Registering a discovered entry that claims the same sign: refused too.
    app.config["DISCOVERY_CACHE"].record(
        "visionect_aa11bb22cc33dd44ee55",
        ('{"kind":"visionect","transport":"visionect","uuid":"' + sign + '"}').encode(),
    )
    resp = client.post(
        "/settings/devices/discovery/visionect_aa11bb22cc33dd44ee55/register",
        data={"id": "desk_sign", "name": "Desk"},
        follow_redirects=True,
    )
    assert "Device not registered" in resp.get_data(as_text=True)
    assert app.config["DEVICE_REGISTRY"].get("desk_sign") is None
    # Nor can a device card's Save move a bound sign onto a second device.
    client.post(
        "/settings/devices/add", data={"kind": "visionect", "id": "spare_sign", "name": "Spare"}
    )
    resp = client.post(
        "/settings/devices/spare_sign/save", data={"uuid": sign}, follow_redirects=True
    )
    assert "already bound to device &#39;hall_sign&#39;" in resp.get_data(as_text=True)
    section = app.config["SETTINGS_STORE"].get_section("devices")
    assert not (section.get("spare_sign") or {}).get("uuid")


def test_visionect_device_band_says_whether_auto_bind_is_open_and_shows_refusals(
    app: Flask, monkeypatch
) -> None:
    import time as _time
    from types import SimpleNamespace

    from app import visionect
    from app.settings import visionect_routes

    client = app.test_client()
    _sign_in(client)
    client.post("/settings/devices/add", data={"kind": "visionect", "id": "hall", "name": "Hall"})
    device = app.config["DEVICE_REGISTRY"].get("hall")
    state = {"uuid": "", "open": True, "view": {}}
    fake = SimpleNamespace(
        running=True,
        port=11113,
        start_error=None,
        device_uuid=lambda _d: state["uuid"],
        auto_bind_open=lambda _d: state["open"],
        sign_view=lambda _u: state["view"],
    )
    monkeypatch.setattr(visionect, "service", lambda: fake)
    with app.test_request_context():
        assert "within an hour" in visionect_routes.setup_band(device)["body"]
        state["open"] = False
        assert "listed under discovered devices" in visionect_routes.setup_band(device)["body"]
        state["uuid"] = "aabbccdd-0000-1111-2222-333344445555"
        state["view"] = {
            "connected": True,
            "remote": "10.0.0.5",
            "in_sync": True,
            "refused": {"remote": "10.0.0.66", "at": _time.time()},
        }
        band = visionect_routes.setup_band(device)
    assert "from 10.0.0.66 claimed this frame's UUID and was refused" in band["body"]
    assert band["ok"] is False


def test_visionect_device_page_shows_the_signs_report_and_its_actions(
    app: Flask, monkeypatch
) -> None:
    """A bound sign's last report (firmware, battery, Wi-Fi, check-in) and
    its Report in / Full refresh / Reboot buttons, which go to the service
    and say why when it can't."""
    import time as _time
    from types import SimpleNamespace

    from app import visionect

    client = app.test_client()
    _sign_in(client)
    client.post("/settings/devices/add", data={"kind": "visionect", "id": "hall", "name": "Hall"})
    asked: list[tuple[str, str]] = []
    state: dict[str, str | None] = {"problem": None}

    def request_action(device_id: str, action: str) -> str | None:
        asked.append((device_id, action))
        return state["problem"]

    fake = SimpleNamespace(
        running=True,
        port=11113,
        start_error=None,
        device_uuid=lambda _d: "aabbccdd-0000-1111-2222-333344445555",
        auto_bind_open=lambda _d: False,
        sign_view=lambda _u: {
            "connected": True,
            "in_sync": True,
            "connected_at": _time.time(),
            "connect_reason": "wakeup",
            "status": {"firmware": "6.17.4168", "battery_pct": 61, "rssi": -53},
            "heartbeat_min": 5,
            "sleep_disabled": 1,
            "wifi": "home-wifi",
            "param_error": "",
            "action": {"kind": "refresh", "at": _time.time()},
            "exchanges": [
                (_time.time(), "out", "picture abc123"),
                (_time.time(), "in", "ack: picture"),
            ],
        },
        request_action=request_action,
        on_push=lambda *_a: None,
    )
    monkeypatch.setattr(visionect, "service", lambda: fake)
    page = client.get("/settings/devices/hall").get_data(as_text=True)
    for text in (
        "6.17.4168",
        "61%",
        "home-wifi · -53 dBm",
        "Every 5 min",
        "Off (stays awake)",
        "Full refresh asked",
        "Check-in interval",
        "Sleep between check-ins",
    ):
        assert text in page, text
    for action in ("status", "refresh", "reboot"):
        assert f"/settings/devices/hall/visionect/{action}" in page
    # The frame log replaces the diagnostics that only repeated the report.
    assert "Frame log" in page and "→  picture abc123" in page and "←  ack: picture" in page
    assert "Advanced diagnostics" not in page
    resp = client.post("/settings/devices/hall/visionect/reboot", follow_redirects=True)
    assert asked == [("hall", "reboot")]
    assert "Sent Hall a reboot" in resp.get_data(as_text=True)
    state["problem"] = "The frame isn't connected right now."
    resp = client.post("/settings/devices/hall/visionect/status", follow_redirects=True)
    assert "The frame isn&#39;t connected right now." in resp.get_data(as_text=True)


def test_every_config_save_checks_the_sign_binding_and_adding_arms_auto_bind(
    app: Flask,
) -> None:
    from app import visionect

    sign = "aabbccdd-0000-1111-2222-333344445555"
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={"kind": "visionect", "id": "hall_sign", "name": "Hall", "uuid": sign},
    )
    client.post("/settings/devices/add", data={"kind": "visionect", "id": "spare", "name": "Spare"})
    section = app.config["SETTINGS_STORE"].get_section("devices")
    # A blank device just added opens its auto-bind window; a bound one doesn't.
    assert isinstance(section["spare"].get(visionect.AUTO_BIND_KEY), float)
    assert not section["hall_sign"].get(visionect.AUTO_BIND_KEY)
    # The generic device settings route refuses a sign already bound elsewhere.
    resp = client.post("/settings/device-spare", data={"uuid": sign}, follow_redirects=True)
    assert "already bound to device &#39;hall_sign&#39;" in resp.get_data(as_text=True)
    section = app.config["SETTINGS_STORE"].get_section("devices")
    assert not section["spare"].get("uuid")


def test_registering_a_discovered_visionect_sign_runs_the_added_hook(app: Flask) -> None:
    """The added hook fires for a device registered from the discovery strip
    too: a blank one (a discovery that announced no UUID) arms auto-bind, one
    registered with its announced UUID does not."""
    from app import visionect

    sign = "aabbccdd-0000-1111-2222-333344445555"
    cache = app.config["DISCOVERY_CACHE"]
    cache.record("visionect_blank01", b'{"kind":"visionect","transport":"visionect"}')
    cache.record(
        "visionect_bound01",
        ('{"kind":"visionect","transport":"visionect","uuid":"' + sign + '"}').encode(),
    )
    client = app.test_client()
    _sign_in(client)
    for discovered_id, device_id in (
        ("visionect_blank01", "blank"),
        ("visionect_bound01", "bound"),
    ):
        resp = client.post(
            f"/settings/devices/discovery/{discovered_id}/register",
            data={"id": device_id, "name": device_id.title()},
        )
        assert resp.status_code == 302
    section = app.config["SETTINGS_STORE"].get_section("devices")
    assert not section["blank"].get("uuid")
    assert isinstance(section["blank"].get(visionect.AUTO_BIND_KEY), float)
    assert section["bound"]["uuid"] == sign
    assert not section["bound"].get(visionect.AUTO_BIND_KEY)


def test_a_config_save_does_not_wait_for_the_listener_to_start(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kind's saved hook runs under CONFIG_SAVE_LOCK. Starting the stopped
    listener there waits for the bind (up to five seconds) and holds every
    other config save; the hook defers the start to its own thread instead."""
    from app import device_service, visionect

    client = app.test_client()
    _sign_in(client)
    client.post("/settings/devices/add", data={"kind": "visionect", "id": "hall", "name": "Hall"})
    svc = visionect.VisionectService(port=0)
    starting = threading.Event()
    release = threading.Event()
    started_on: list[threading.Thread] = []

    def slow_start() -> bool:
        started_on.append(threading.current_thread())
        starting.set()
        release.wait(10)
        return True

    monkeypatch.setattr(svc, "maybe_start", slow_start)
    monkeypatch.setattr(visionect, "service", lambda: svc)
    registry, store = app.config["DEVICE_REGISTRY"], app.config["SETTINGS_STORE"]
    sign = "aabbccdd-0000-1111-2222-333344445555"
    try:
        problem = device_service.save_device_config(
            store, registry, registry.get("hall"), {"uuid": sign}, [{"name": "uuid"}]
        )
        # The save returned (and released the lock) while the start is still
        # in progress, on another thread.
        assert problem is None
        assert not release.is_set()
        assert starting.wait(5)
        assert started_on[0] is not threading.current_thread()
        assert device_service.CONFIG_SAVE_LOCK.acquire(blocking=False)
        device_service.CONFIG_SAVE_LOCK.release()
    finally:
        release.set()
        for thread in started_on:
            thread.join(5)


def test_a_place_and_play_32_device_page_has_no_button_or_touch_controls(app: Flask) -> None:
    """The Place & Play 32" has no buttons and no touch: its device page has
    no Controls section (button map, touch monitor), while a device that may
    have buttons keeps it."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={"kind": "visionect_place_and_play_32", "id": "hall", "name": "Hall"},
    )
    client.post(
        "/settings/devices/add", data={"kind": "esp32_client", "id": "attic", "name": "Attic"}
    )
    registry = app.config["DEVICE_REGISTRY"]
    assert registry.get("attic") is not None
    sign = client.get("/settings/devices/hall").get_data(as_text=True)
    other = client.get("/settings/devices/attic").get_data(as_text=True)
    assert 'id="controls"' not in sign and "Button map" not in sign
    assert 'href="#controls"' not in sign
    assert 'id="controls"' in other and "Button map" in other


@pytest.mark.parametrize(
    "fields",
    [
        {"refresh_mode": "full"},
        {"button_map_json": '{"left": "refresh"}'},
        {"wake_align_mode": "interval", "wake_align_anchor": "06:00"},
    ],
    ids=["refresh mode", "button map", "wake alignment"],
)
def test_a_device_save_cant_write_back_a_sign_binding_moved_meanwhile(
    app: Flask, fields: dict[str, str]
) -> None:
    """Sol's sequence: device A's save reads A's entry (bound to sign X);
    meanwhile X is cleared from A and bound to B; A's save then writes its
    one key. It used to write the whole copied entry back, leaving both
    devices bound to X."""
    from app import device_service, visionect

    sign = "aabbccdd-0000-1111-2222-333344445555"
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={"kind": "visionect", "id": "hall", "name": "Hall", "uuid": sign},
    )
    client.post("/settings/devices/add", data={"kind": "visionect", "id": "spare", "name": "S"})
    store = app.config["SETTINGS_STORE"]
    registry = app.config["DEVICE_REGISTRY"]
    fields_uuid = [{"name": "uuid"}]
    moved: list[bool] = []

    def _move_the_sign() -> None:
        if moved:
            return
        moved.append(True)
        hall, spare = registry.get("hall"), registry.get("spare")
        assert (
            device_service.save_device_config(store, registry, hall, {"uuid": ""}, fields_uuid)
            is None
        )
        assert (
            device_service.save_device_config(store, registry, spare, {"uuid": sign}, fields_uuid)
            is None
        )

    real_update, real_patch = store.update_item, store.patch_section

    def _update_item(namespace: str, item_id: str, values: dict, **kw: object) -> None:
        if namespace == "devices" and item_id == "hall":
            _move_the_sign()
        real_update(namespace, item_id, values, **kw)

    def _patch_section(name: str, values: dict) -> None:
        if name == "devices" and "hall" in values:
            _move_the_sign()
        real_patch(name, values)

    store.update_item, store.patch_section = _update_item, _patch_section
    try:
        resp = client.post("/settings/devices/hall/save", data={"uuid": sign, **fields})
    finally:
        store.update_item, store.patch_section = real_update, real_patch
    assert resp.status_code == 302 and moved
    section = store.get_section("devices")
    bound = [
        d for d in ("hall", "spare") if visionect.normalise_uuid(section[d].get("uuid")) == sign
    ]
    assert bound == ["spare"]
