"""HA discovery: discovery configs published on start, last-render
image URL follows pushes, page-store changes refresh button configs,
HA command topic triggers PushManager.push, default-off toggle works."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.ha_discovery import (
    AVAILABILITY_TOPIC,
    CMD_TOPIC_PUSH_PAGE,
    STATE_TOPIC_BUSY,
    STATE_TOPIC_IMAGE_URL,
    STATE_TOPIC_LAST_ERROR,
    STATE_TOPIC_LAST_PUSH,
    STATE_TOPIC_PUSH_COUNT,
    HomeAssistantDiscovery,
)
from app.main import REPO_ROOT, create_app
from app.push import PushResult, RendererResult
from app.state.page_store import Page, PageStore, Panel
from app.state.settings_store import SettingsStore
from app.transport import BrokerConfig, MqttTransport


class _FakeMqttClient:
    on_connect = on_disconnect = on_message = None

    def __init__(self, *a, **kw):
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.subscribed: list[tuple[str, int]] = []

    def username_pw_set(self, *a, **kw):
        pass

    def connect(self, *a, **kw):
        return 0

    def disconnect(self):
        return 0

    def loop_start(self):
        return 0

    def loop_stop(self):
        return 0

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))
        return type("R", (), {"rc": 0})()

    def subscribe(self, topic, qos):
        self.subscribed.append((topic, qos))
        return (0, 1)


class _FakeDevice:
    def __init__(self, id: str, name: str, kind_of: str | None, panel: dict | None) -> None:
        self.id = id
        self.display_name = name
        self.kind_of = kind_of
        self.panel = panel


class _FakeRegistry:
    def __init__(self, devices: list[_FakeDevice]) -> None:
        self._d = {d.id: d for d in devices}

    def all(self) -> list[_FakeDevice]:
        return list(self._d.values())

    def get(self, device_id: str):
        return self._d.get(device_id)


def _wire(tmp_path: Path, devices=None, device_status=None):
    fakes = {}

    def factory(client_id: str):
        fakes["client"] = _FakeMqttClient(client_id)
        return fakes["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()
    pm = MagicMock()
    pm.add_listener = MagicMock()
    pm.remove_listener = MagicMock()
    page_store = PageStore(tmp_path / "pages.json")
    ha = HomeAssistantDiscovery(
        transport=transport,
        push_manager=pm,
        page_store=page_store,
        base_url_fn=lambda: "http://lan.test:8000",
        device_registry=devices,
        device_status=device_status,
    )
    return ha, fakes["client"], pm, page_store


def _published_topics(client: _FakeMqttClient) -> list[str]:
    return [t for t, *_ in client.published]


def _payload_for(client: _FakeMqttClient, topic: str) -> bytes | None:
    for t, p, *_ in client.published:
        if t == topic:
            return p
    return None


def _payloads_for(client: _FakeMqttClient, topic: str) -> Iterable[bytes]:
    return [p for t, p, *_ in client.published if t == topic]


def test_start_publishes_availability_and_diagnostic_seed(tmp_path: Path) -> None:
    ha, client, _pm, _store = _wire(tmp_path)
    ha.start()
    topics = _published_topics(client)
    assert AVAILABILITY_TOPIC in topics
    assert STATE_TOPIC_PUSH_COUNT in topics
    assert STATE_TOPIC_LAST_ERROR in topics
    assert STATE_TOPIC_BUSY in topics
    # Availability shows as 'online' immediately so HA enables the device.
    assert _payload_for(client, AVAILABILITY_TOPIC) == b"online"


def test_start_subscribes_to_command_topics(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    subs = [t for t, _ in client.subscribed]
    assert CMD_TOPIC_PUSH_PAGE in subs


def test_button_config_per_page(tmp_path: Path) -> None:
    ha, client, _pm, store = _wire(tmp_path)
    store.save(Page(id="home", name="Home", panel=Panel(w=100, h=100)))
    store.save(Page(id="weather", name="Weather panel", panel=Panel(w=100, h=100)))
    ha.start()
    # One discovery config per page id.
    assert any(
        "homeassistant/button/tesserae/page_home/config" in t for t in _published_topics(client)
    )
    assert any(
        "homeassistant/button/tesserae/page_weather/config" in t for t in _published_topics(client)
    )


def test_page_change_listener_republishes(tmp_path: Path) -> None:
    ha, client, _pm, store = _wire(tmp_path)
    ha.start()
    before = len(client.published)
    store.save(Page(id="late", name="Added later", panel=Panel(w=100, h=100)))
    after = len(client.published)
    assert after > before
    assert any("page_late" in t for t in _published_topics(client))


def test_push_result_listener_publishes_image_url(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    client.published.clear()
    ha._on_push_result(
        PushResult(status="sent", page_id="home", composition_digest="abc123def4", duration_s=0.4)
    )
    image_payload = _payload_for(client, STATE_TOPIC_IMAGE_URL)
    assert image_payload == b"http://lan.test:8000/renders/abc123def4.png"
    # Push count increments and lands as the count topic's payload.
    count_payload = _payload_for(client, STATE_TOPIC_PUSH_COUNT)
    assert count_payload == b"1"
    # last_push payload is an ISO timestamp.
    last = _payload_for(client, STATE_TOPIC_LAST_PUSH)
    assert last is not None and b"T" in last and b"+00:00" in last


def test_push_failure_records_last_error(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    client.published.clear()
    ha._on_push_result(PushResult(status="failed", page_id="home", error="renderer kaboom"))
    err = _payload_for(client, STATE_TOPIC_LAST_ERROR)
    assert err == b"renderer kaboom"


def test_push_page_command_calls_push(tmp_path: Path) -> None:
    ha, client, pm, _ = _wire(tmp_path)
    pm.push.return_value = PushResult(status="sent", page_id="home")
    ha.start()
    ha._on_push_page_cmd(CMD_TOPIC_PUSH_PAGE, b"home")
    pm.push.assert_called_once_with("home", source="home_assistant")
    # Busy gets tipped on for the duration.
    assert b"1" in list(_payloads_for(client, STATE_TOPIC_BUSY))


def test_stop_publishes_offline_and_blanks_configs(tmp_path: Path) -> None:
    ha, client, _pm, store = _wire(tmp_path)
    store.save(Page(id="home", name="Home", panel=Panel(w=100, h=100)))
    ha.start()
    client.published.clear()
    ha.stop()
    # availability flips to offline at the end.
    assert _payload_for(client, AVAILABILITY_TOPIC) == b"offline"
    # The button config gets an empty retained payload, telling HA to drop it.
    button_topic = "homeassistant/button/tesserae/page_home/config"
    # The empty publish is the LAST publish to that topic.
    button_payloads = list(_payloads_for(client, button_topic))
    assert button_payloads and button_payloads[-1] == b""


def test_double_start_is_idempotent(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    first = len(client.published)
    ha.start()
    # Second start() must not republish, guard against accidental
    # double-init.
    assert len(client.published) == first


# -- End-to-end: factory respects the toggle, settings change rewires -------


@pytest.fixture
def app(tmp_path: Path) -> Flask:
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


def test_default_off(app: Flask) -> None:
    assert app.config["HA_DISCOVERY"] is None


def test_toggle_on_via_settings_starts_discovery(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/app",
        data={
            "base_url": "http://lan.test:8000",
            "panel_w": "100",
            "panel_h": "100",
            "ha_discovery_enabled": "on",
        },
    )
    assert app.config["HA_DISCOVERY"] is not None
    # Persisted on disk too.
    store = SettingsStore(tmp_path / "core" / "settings.json")
    assert store.get_section("app")["ha_discovery_enabled"] is True


# -- Multi-head: per-display HA devices --------------------------------


def _reg_with_lounge() -> _FakeRegistry:
    return _FakeRegistry(
        [_FakeDevice("lounge", "Lounge frame", "pi_bin_client", {"w": 1424, "h": 1200})]
    )


def test_per_device_configs_published(tmp_path: Path) -> None:
    ha, client, _pm, store = _wire(tmp_path, devices=_reg_with_lounge())
    store.save(Page(id="m", name="Morning", panel=Panel(w=100, h=100), device_ids=["lounge"]))
    ha.start()
    topics = _published_topics(client)
    for leaf in ("frame", "current", "last_update", "last_seen"):
        assert any(f"tesserae/dev_lounge_{leaf}/config" in t for t in topics), leaf
    assert any("select/tesserae/dev_lounge_dashboard/config" in t for t in topics)
    assert any("image/tesserae/dev_lounge_frame/config" in t for t in topics)


def test_per_device_select_lists_bound_dashboards(tmp_path: Path) -> None:
    ha, client, _pm, store = _wire(tmp_path, devices=_reg_with_lounge())
    store.save(Page(id="m", name="Morning", panel=Panel(w=100, h=100), device_ids=["lounge"]))
    store.save(Page(id="o", name="Other", panel=Panel(w=100, h=100), device_ids=[]))
    ha.start()
    cfg = _payload_for(client, "homeassistant/select/tesserae/dev_lounge_dashboard/config")
    assert cfg is not None
    payload = json.loads(cfg)
    assert payload["options"] == ["Morning"]  # only the bound dashboard
    assert payload["device"]["via_device"] == "tesserae"


def test_per_device_push_command_targets_single_display(tmp_path: Path) -> None:
    ha, _client, pm, store = _wire(tmp_path, devices=_reg_with_lounge())
    pm.push.return_value = PushResult(status="sent", page_id="m")
    store.save(Page(id="m", name="Morning", panel=Panel(w=100, h=100), device_ids=["lounge"]))
    ha.start()
    ha._on_device_push_cmd("tesserae/ha/dev/lounge/cmd/push", b"Morning")
    pm.push.assert_called_once_with("m", device_ids={"lounge"}, source="home_assistant")


def test_push_result_updates_per_device_state(tmp_path: Path) -> None:
    ha, client, _pm, store = _wire(tmp_path, devices=_reg_with_lounge())
    store.save(Page(id="m", name="Morning", panel=Panel(w=100, h=100), device_ids=["lounge"]))
    ha.start()
    client.published.clear()
    ha._on_push_result(
        PushResult(
            status="sent",
            page_id="m",
            composition_digest="abc123def4",
            renderers=[
                RendererResult(
                    renderer_id="pi_bin__lounge",
                    topic="t",
                    digest="d",
                    url="u",
                    bytes_written=1,
                )
            ],
        )
    )
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/current_page") == b"Morning"
    assert (
        _payload_for(client, "tesserae/ha/dev/lounge/state/image_url")
        == b"http://lan.test:8000/renders/abc123def4.png"
    )
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/last_update") is not None


def test_image_configs_omit_content_type(tmp_path: Path) -> None:
    """HA's MQTT image schema treats ``content_type`` and ``url_topic`` as
    mutually exclusive, a discovery payload carrying both gets rejected
    with no entity created. The user observed thousands of these errors
    in HA's log; verify both hub-level + per-device image configs are
    free of the offending key."""
    import json as _json

    ha, client, _pm, _store = _wire(tmp_path, devices=_reg_with_lounge())
    ha.start()
    hub = _payload_for(client, "homeassistant/image/tesserae/last_render/config")
    dev = _payload_for(client, "homeassistant/image/tesserae/dev_lounge_frame/config")
    assert hub is not None and dev is not None
    assert "content_type" not in _json.loads(hub.decode())
    assert "content_type" not in _json.loads(dev.decode())
    # The URL-side wiring is still in place so HA knows where to fetch.
    assert "url_topic" in _json.loads(hub.decode())
    assert "url_topic" in _json.loads(dev.decode())


def test_non_page_push_does_not_publish_invalid_select_state(tmp_path: Path) -> None:
    """File / URL / gallery pushes carry a page_id that's actually a
    source label (e.g. ``gallery:japanese_posters/72.jpg``). The HA
    select for current/active dashboard only accepts options that match
    saved page names, publishing the source label drives HA's
    "Invalid option" log warning. We now skip those writes; frame URL
    + last-updated still fire so the dashboard's image entity keeps
    refreshing."""
    ha, client, _pm, _store = _wire(tmp_path, devices=_reg_with_lounge())
    # No matching Page saved, simulating a non-page push.
    ha.start()
    client.published.clear()
    ha._on_push_result(
        PushResult(
            status="sent",
            page_id="gallery:japanese_posters/72.jpg",
            composition_digest="deadbeef0011",
            renderers=[
                RendererResult(
                    renderer_id="pi_bin__lounge",
                    topic="t",
                    digest="d",
                    url="u",
                    bytes_written=1,
                )
            ],
        )
    )
    # No state writes for the constrained selects (would log "Invalid option").
    fresh = [
        item
        for item in client.published
        if item[0] == "tesserae/ha/dev/lounge/state/current_page"
        or item[0] == "tesserae/ha/state/active_page"
    ]
    assert fresh == []
    # But the frame image URL + last-update timestamp still fire, they're
    # informational and not constrained to an options list.
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/image_url") is not None
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/last_update") is not None


def test_start_clears_stale_select_state(tmp_path: Path) -> None:
    """Older Tesserae versions published the raw page_id (digest or
    source label) to the active_page topic, which then lived as a
    retained message HA replayed on every restart. ``start()`` clears
    those topics with an empty retained payload so the next valid push
    can repopulate them with a real name."""
    ha, client, _pm, _store = _wire(tmp_path, devices=_reg_with_lounge())
    ha.start()
    # Empty retained payload IS the MQTT-spec way to delete a retained
    # message; assert both the hub-level select state and the per-device
    # current_page topics get that treatment on start.
    assert _payload_for(client, "tesserae/ha/state/active_page") == b""
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/current_page") == b""


def test_heartbeat_publishes_last_seen_and_dynamic_sensors(tmp_path: Path) -> None:
    ha, client, _pm, _store = _wire(tmp_path, devices=_reg_with_lounge())
    ha.start()
    client.published.clear()
    ha.note_device_heartbeat("lounge", {"battery_pct": 88, "rssi": -61, "ip": "10.0.0.5"})
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/last_seen") is not None
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/battery") == b"88"
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/signal") == b"-61"
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/ip") == b"10.0.0.5"
    # The lazy battery sensor config was published the first time the key appeared.
    assert any("sensor/tesserae/dev_lounge_battery/config" in t for t in _published_topics(client))


def test_stop_blanks_per_device_configs(tmp_path: Path) -> None:
    ha, client, _pm, store = _wire(tmp_path, devices=_reg_with_lounge())
    store.save(Page(id="m", name="Morning", panel=Panel(w=100, h=100), device_ids=["lounge"]))
    ha.start()
    ha.note_device_heartbeat("lounge", {"battery_pct": 88})  # create the dyn sensor
    client.published.clear()
    ha.stop()
    frame_cfg = "homeassistant/image/tesserae/dev_lounge_frame/config"
    payloads = list(_payloads_for(client, frame_cfg))
    assert payloads and payloads[-1] == b""
    batt_cfg = "homeassistant/sensor/tesserae/dev_lounge_battery/config"
    batt_payloads = list(_payloads_for(client, batt_cfg))
    assert batt_payloads and batt_payloads[-1] == b""
