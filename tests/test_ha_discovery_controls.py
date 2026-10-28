"""HA discovery controls: lineup switches / buttons, per-display lineup
select, automation + quiet-hours switches, hold, wake interval, firmware
update, input events, notify, and the stale-lineup sweep."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app import ha_hooks
from app.ha_discovery import (
    CMD_TOPIC_AUTOMATION,
    CMD_TOPIC_NOTIFY,
    CMD_TOPIC_QUIET_HOURS,
    DEVICE_CMD_WILDCARD,
    LINEUP_CMD_WILDCARD,
    LINEUP_NONE,
    STATE_TOPIC_AUTOMATION,
    STATE_TOPIC_QUIET_HOURS,
    HaHooks,
    HomeAssistantDiscovery,
)
from app.push import PushManager, PushResult
from app.state.deck_model import Deck, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.device_rotation_state_model import DeviceRotationState
from app.state.device_rotation_state_store import DeviceRotationStateStore
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
    def __init__(self, id: str, name: str, config_schema: dict | None = None) -> None:
        self.id = id
        self.display_name = name
        self.name = name
        self.kind_of = "esp32"
        self.panel = {"w": 800, "h": 480}
        self.manifest: dict = {}
        self.config_schema = config_schema or {}


class _FakeRegistry:
    def __init__(self, devices: list[_FakeDevice]) -> None:
        self._d = {d.id: d for d in devices}

    def all(self) -> list[_FakeDevice]:
        return list(self._d.values())

    def get(self, device_id: str):
        return self._d.get(device_id)


def _deck(**kw) -> Deck:
    base = dict(
        id="morning",
        name="Morning",
        device_ids=["lounge"],
        pages=[DeckPage(page_id="home"), DeckPage(page_id="weather")],
    )
    base.update(kw)
    return Deck(**base)


def _wire(tmp_path: Path, *, devices=None, hooks: HaHooks | None = None, device_status=None):
    fakes = {}

    def factory(client_id: str):
        fakes["client"] = _FakeMqttClient(client_id)
        return fakes["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    pm.warm_deck_page.return_value = True
    pm.promote_deck_page.return_value = False
    pm.latest_render_for.return_value = None
    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(Page(id="home", name="Home", panel=Panel(w=800, h=480), device_ids=["lounge"]))
    page_store.save(
        Page(id="weather", name="Weather", panel=Panel(w=800, h=480), device_ids=["lounge"])
    )
    ha = HomeAssistantDiscovery(
        transport=transport,
        push_manager=pm,
        page_store=page_store,
        base_url_fn=lambda: "http://lan.test:8000",
        device_registry=devices,
        device_status=device_status,
        hooks=hooks,
        state_refresh_s=0,  # no ticker thread in tests
    )
    return ha, fakes["client"], pm, page_store


def _payload_for(client: _FakeMqttClient, topic: str) -> bytes | None:
    """Last payload published to ``topic`` (retained semantics)."""
    out = None
    for t, p, *_ in client.published:
        if t == topic:
            out = p
    return out


def _topics(client: _FakeMqttClient) -> list[str]:
    return [t for t, *_ in client.published]


def _stores(tmp_path: Path) -> tuple[DeckStore, DeckNavStore, SettingsStore]:
    return (
        DeckStore(tmp_path / "decks.json"),
        DeckNavStore(tmp_path / "nav.json"),
        SettingsStore(tmp_path / "settings.json"),
    )


# ---------- lineups on the hub ----------


def test_lineup_switch_and_buttons_published(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    topics = _topics(client)
    assert "homeassistant/switch/tesserae/lineup_morning/config" in topics
    assert "homeassistant/button/tesserae/lineup_morning_push/config" in topics
    assert "homeassistant/button/tesserae/lineup_morning_next/config" in topics
    assert "homeassistant/button/tesserae/lineup_morning_prev/config" in topics
    cfg = json.loads(_payload_for(client, "homeassistant/switch/tesserae/lineup_morning/config"))
    assert cfg["command_topic"] == "tesserae/ha/lineup/morning/cmd/enabled"
    assert cfg["name"] == "Morning"
    assert _payload_for(client, "tesserae/ha/lineup/morning/state/enabled") == b"ON"
    subs = [t for t, _ in client.subscribed]
    assert LINEUP_CMD_WILDCARD in subs and DEVICE_CMD_WILDCARD in subs


def test_lineup_without_displays_has_no_action_buttons(tmp_path: Path) -> None:
    decks, _nav, settings = _stores(tmp_path)
    decks.upsert(_deck(device_ids=[]))
    ha, client, _pm, _ = _wire(tmp_path, hooks=HaHooks(deck_store=decks, settings_store=settings))
    ha.start()
    topics = _topics(client)
    assert "homeassistant/switch/tesserae/lineup_morning/config" in topics
    assert "homeassistant/button/tesserae/lineup_morning_push/config" not in topics


def test_deck_store_change_republishes_lineup_configs(tmp_path: Path) -> None:
    decks, _nav, settings = _stores(tmp_path)
    ha, client, _pm, _ = _wire(tmp_path, hooks=HaHooks(deck_store=decks, settings_store=settings))
    ha.start()
    assert "homeassistant/switch/tesserae/lineup_evening/config" not in _topics(client)
    decks.upsert(_deck(id="evening", name="Evening"))
    assert "homeassistant/switch/tesserae/lineup_evening/config" in _topics(client)
    client.published.clear()
    decks.delete("evening")
    # Deleted lineup: config blanked (empty retained payload).
    assert _payload_for(client, "homeassistant/switch/tesserae/lineup_evening/config") == b""


def test_lineup_enabled_command_flips_deck_and_invalidates(tmp_path: Path) -> None:
    decks, _nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    invalidated: list[str] = []
    ha, client, _pm, _ = _wire(
        tmp_path,
        hooks=HaHooks(
            deck_store=decks,
            settings_store=settings,
            invalidate_deck_fn=lambda deck: invalidated.append(deck.id),
        ),
    )
    ha.start()
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/enabled", b"OFF")
    assert ha._wait_commands_idle()
    assert decks.get("morning").enabled is False
    assert invalidated == ["morning"]
    assert _payload_for(client, "tesserae/ha/lineup/morning/state/enabled") == b"OFF"
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/enabled", b"ON")
    assert ha._wait_commands_idle()
    assert decks.get("morning").enabled is True
    assert _payload_for(client, "tesserae/ha/lineup/morning/state/enabled") == b"ON"


def test_lineup_push_action_warms_and_pushes_entry(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    ha, _client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"push")
    assert ha._wait_commands_idle()
    assert pm.warm_deck_page.call_count == 2
    pm.push.assert_called_once_with(
        "home",
        device_ids={"lounge"},
        respect_quiet_hours=False,
        force_publish=True,
        source="home_assistant",
        lock_timeout_s=60.0,
        settle_max_s=30.0,
    )
    assert (
        nav.get("lounge") == {"deck_id": "morning", "page_id": "home"}
        or nav.get("lounge")["page_id"] == "home"
    )


def test_lineup_next_and_prev_step_through_pages(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    nav.set("lounge", "morning", "home")
    ha, _client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"next")
    assert ha._wait_commands_idle()
    assert pm.push.call_args.args[0] == "weather"
    assert nav.get("lounge")["page_id"] == "weather"
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"prev")
    assert ha._wait_commands_idle()
    assert pm.push.call_args.args[0] == "home"
    assert nav.get("lounge")["page_id"] == "home"


@pytest.mark.parametrize("status", ["superseded", "busy", "held", "not_found"])
def test_lineup_step_records_a_position_only_when_the_page_was_delivered(
    tmp_path: Path, status: str
) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    nav.set("lounge", "morning", "home")
    ha, _client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    pm.push.return_value = PushResult(status=status, page_id="weather")
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"next")
    assert ha._wait_commands_idle()
    assert nav.get("lounge")["page_id"] == "home"  # the glass never moved
    pm.push.return_value = PushResult(status="no_change", page_id="weather")
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"next")
    assert ha._wait_commands_idle()
    assert nav.get("lounge")["page_id"] == "weather"


# ---------- per-display lineup select ----------


def test_device_lineup_select_state_and_options(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    decks.upsert(_deck(id="evening", name="Evening", device_ids=[]))
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    cfg = json.loads(_payload_for(client, "homeassistant/select/tesserae/dev_lounge_lineup/config"))
    assert cfg["options"] == [LINEUP_NONE, "Evening", "Morning"]
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/lineup") == b"Morning"


def test_device_lineup_select_binds_and_goes_live(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck(id="evening", name="Evening", device_ids=[]))
    ha, client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/lineup", b"Evening")
    assert ha._wait_commands_idle()
    assert "lounge" in decks.get("evening").device_ids
    assert pm.push.call_args.kwargs["device_ids"] == {"lounge"}
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/lineup") == b"Evening"


def test_device_lineup_select_none_unbinds(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    nav.set("lounge", "morning", "home")
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/lineup", LINEUP_NONE.encode())
    assert ha._wait_commands_idle()
    assert decks.get("morning").device_ids == []
    assert nav.get("lounge") is None
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/lineup") == LINEUP_NONE.encode()


# ---------- hub switches ----------


def test_automation_switch_pauses_and_resumes(tmp_path: Path) -> None:
    _decks, _nav, settings = _stores(tmp_path)
    ha, client, _pm, _ = _wire(tmp_path, hooks=HaHooks(settings_store=settings))
    ha.start()
    assert "homeassistant/switch/tesserae/automation/config" in _topics(client)
    assert _payload_for(client, STATE_TOPIC_AUTOMATION) == b"ON"
    ha._on_automation_cmd(CMD_TOPIC_AUTOMATION, b"OFF")
    assert settings.get_section("app")["automation_paused"] is True
    assert _payload_for(client, STATE_TOPIC_AUTOMATION) == b"OFF"
    ha._on_automation_cmd(CMD_TOPIC_AUTOMATION, b"ON")
    assert settings.get_section("app")["automation_paused"] is False
    assert _payload_for(client, STATE_TOPIC_AUTOMATION) == b"ON"


def test_quiet_hours_switch_and_device_quiet_state(tmp_path: Path) -> None:
    _decks, _nav, settings = _stores(tmp_path)
    settings.update_section(
        "app", {"quiet_hours_start": "00:00", "quiet_hours_end": "23:59", "timezone": "UTC"}
    )
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(settings_store=settings, timezone_fn=lambda: UTC),
    )
    ha.start()
    assert _payload_for(client, STATE_TOPIC_QUIET_HOURS) == b"OFF"
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/quiet_now") == b"OFF"
    ha._on_quiet_hours_cmd(CMD_TOPIC_QUIET_HOURS, b"ON")
    assert settings.get_section("app")["quiet_hours_enabled"] is True
    assert _payload_for(client, STATE_TOPIC_QUIET_HOURS) == b"ON"
    # The (nearly) all-day window means the display is quiet right now.
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/quiet_now") == b"ON"


def test_device_quiet_override_switch_calls_hook(tmp_path: Path) -> None:
    _decks, _nav, settings = _stores(tmp_path)
    calls: list[tuple[str, bool]] = []
    device = _FakeDevice("lounge", "Lounge")

    def set_override(device_id: str, enabled: bool) -> str | None:
        calls.append((device_id, enabled))
        device.manifest["quiet_hours"] = {"enabled": enabled, "start": "22:00", "end": "07:00"}
        return None

    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([device]),
        hooks=HaHooks(settings_store=settings, set_device_quiet_override_fn=set_override),
    )
    ha.start()
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/quiet_override") == b"OFF"
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/quiet_override", b"ON")
    assert calls == [("lounge", True)]
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/quiet_override") == b"ON"


# ---------- hold ----------


def test_hold_number_sets_and_clears_override(tmp_path: Path) -> None:
    _decks, _nav, _settings = _stores(tmp_path)
    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(rotation_state_store=rot_state),
    )
    ha.start()
    assert "homeassistant/number/tesserae/dev_lounge_hold/config" in _topics(client)
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/hold") == b"0"
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/hold", b"90")
    state = rot_state.get("lounge")
    assert state is not None and state.override_until is not None
    remaining = (state.override_until - datetime.now(UTC)).total_seconds()
    assert 88 * 60 < remaining <= 90 * 60
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/hold") in (b"89", b"90")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/hold", b"0")
    assert rot_state.get("lounge").override_until is None
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/hold") == b"0"


def test_hold_state_reflects_existing_override(tmp_path: Path) -> None:
    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    rot_state.upsert(
        DeviceRotationState(
            device_id="lounge", override_until=datetime.now(UTC) + timedelta(minutes=30)
        )
    )
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(rotation_state_store=rot_state),
    )
    ha.start()
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/hold") in (b"29", b"30")


# ---------- pause (separate from hold) ----------


def test_pause_number_is_a_separate_entity_next_to_hold(tmp_path: Path) -> None:
    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(rotation_state_store=rot_state),
    )
    ha.start()
    topics = _topics(client)
    assert "homeassistant/number/tesserae/dev_lounge_hold/config" in topics
    assert "homeassistant/number/tesserae/dev_lounge_pause/config" in topics
    pause = json.loads(
        _payload_for(client, "homeassistant/number/tesserae/dev_lounge_pause/config")
    )
    assert pause["name"] == "Pause"
    assert pause["command_topic"] == "tesserae/ha/dev/lounge/cmd/pause"
    assert pause["state_topic"] == "tesserae/ha/dev/lounge/state/pause"
    assert (pause["min"], pause["max"], pause["unit_of_measurement"]) == (0, 720, "min")
    hold = json.loads(_payload_for(client, "homeassistant/number/tesserae/dev_lounge_hold/config"))
    assert hold["name"] == "Hold"
    assert hold["command_topic"] == "tesserae/ha/dev/lounge/cmd/hold"


def test_pause_number_absent_without_the_state_store(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(
        tmp_path, devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]), hooks=HaHooks()
    )
    ha.start()
    assert "homeassistant/number/tesserae/dev_lounge_pause/config" not in _topics(client)


def test_pause_number_sets_and_ends_the_pause_without_touching_hold(tmp_path: Path) -> None:
    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    hold_until = datetime.now(UTC) + timedelta(minutes=45)
    rot_state.upsert(DeviceRotationState(device_id="lounge", override_until=hold_until))
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(rotation_state_store=rot_state),
    )
    ha.start()
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/pause") == b"0"
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/pause", b"90")
    state = rot_state.get("lounge")
    assert state is not None and state.automation_pause_until is not None
    remaining = (state.automation_pause_until - datetime.now(UTC)).total_seconds()
    assert 88 * 60 < remaining <= 90 * 60
    assert state.override_until == hold_until  # Hold's own field is untouched
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/pause") == b"90"
    # Hold keeps reporting its own minutes, not the pause's.
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/hold") in (b"44", b"45")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/pause", b"0")
    state = rot_state.get("lounge")
    # A lapsed value is stored (the scheduler's next tick sees the pause end).
    assert state is not None and state.automation_pause_until is not None
    assert state.automation_pause_until <= datetime.now(UTC)
    assert state.override_until == hold_until
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/pause") == b"0"


def test_hold_number_never_writes_the_pause(tmp_path: Path) -> None:
    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    ha, _client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(rotation_state_store=rot_state),
    )
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/hold", b"30")
    state = rot_state.get("lounge")
    assert state is not None and state.override_until is not None
    assert state.automation_pause_until is None
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/hold", b"0")
    state = rot_state.get("lounge")
    assert state is not None and state.override_until is None
    assert state.automation_pause_until is None


def test_pause_state_reflects_an_existing_pause(tmp_path: Path) -> None:
    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    rot_state.upsert(
        DeviceRotationState(
            device_id="lounge", automation_pause_until=datetime.now(UTC) + timedelta(minutes=30)
        )
    )
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(rotation_state_store=rot_state),
    )
    ha.start()
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/pause") == b"30"
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/hold") == b"0"


# ---------- wake interval ----------


def test_wake_interval_number_published_from_schema_and_clamped(tmp_path: Path) -> None:
    schema = {"sleep_interval_s": {"type": "int", "default": 900, "min": 60, "max": 86400}}
    device = _FakeDevice("lounge", "Lounge", config_schema=schema)
    config = {"sleep_interval_s": 900}
    updates: list[dict] = []

    def set_config(device_id: str, values: dict) -> str | None:
        updates.append(values)
        config.update(values)
        return None

    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([device]),
        hooks=HaHooks(device_config_fn=lambda _id: dict(config), set_device_config_fn=set_config),
    )
    ha.start()
    cfg = json.loads(
        _payload_for(client, "homeassistant/number/tesserae/dev_lounge_wake_interval/config")
    )
    assert cfg["min"] == 60 and cfg["max"] == 86400
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/wake_interval") == b"900"
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/wake_interval", b"10")
    assert updates == [{"sleep_interval_s": 60}]
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/wake_interval") == b"60"


def test_no_wake_interval_entity_without_schema(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(device_config_fn=lambda _id: {}),
    )
    ha.start()
    assert "homeassistant/number/tesserae/dev_lounge_wake_interval/config" not in _topics(client)


# ---------- firmware update ----------


def test_update_entity_only_for_ota_capable_and_install_calls_hook(tmp_path: Path) -> None:
    installs: list[str] = []
    fw = {
        "lounge": {
            "capable": True,
            "installed_version": "1.29.0",
            "latest_version": "1.30.1",
            "in_progress": False,
            "release_url": "https://example.test/r",
        },
        "hall": {"capable": False, "installed_version": None, "latest_version": None},
    }
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge"), _FakeDevice("hall", "Hall")]),
        hooks=HaHooks(
            firmware_state_fn=lambda device_id: fw[device_id],
            firmware_install_fn=lambda device_id: installs.append(device_id),
        ),
    )
    ha.start()
    assert "homeassistant/update/tesserae/dev_lounge_firmware/config" in _topics(client)
    assert "homeassistant/update/tesserae/dev_hall_firmware/config" not in _topics(client)
    state = json.loads(_payload_for(client, "tesserae/ha/dev/lounge/state/firmware"))
    assert state["installed_version"] == "1.29.0"
    assert state["latest_version"] == "1.30.1"
    assert state["release_url"] == "https://example.test/r"
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/firmware_install", b"install")
    assert ha._wait_commands_idle()
    assert installs == ["lounge"]


# ---------- input events, notify, refresh ----------


class _FakeButtonService:
    def __init__(self) -> None:
        self.listeners: list = []

    def add_listener(self, cb) -> None:
        self.listeners.append(cb)

    def remove_listener(self, cb) -> None:
        self.listeners.remove(cb)


def test_a_device_with_no_buttons_and_no_touch_gets_no_input_entity(tmp_path: Path) -> None:
    """A kind declaring ``"buttons": false`` and no touch can never produce an
    input event: no Input entity, and the empty retained config clears one a
    previous session published. Touch alone keeps it."""
    svc = _FakeButtonService()
    plain = _FakeDevice("hall_panel", "Hall")
    plain.manifest["buttons"] = False
    tappable = _FakeDevice("kiosk", "Kiosk")
    tappable.manifest.update(buttons=False, touch=True)
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([plain, tappable, _FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(button_service=svc),
    )
    ha.start()
    topic = "homeassistant/event/tesserae/dev_hall_panel_input/config"
    published = [(p, retain) for t, p, _q, retain in client.published if t == topic]
    assert published and all(p == b"" for p, _r in published)
    assert _payload_for(client, "homeassistant/event/tesserae/dev_kiosk_input/config")
    assert _payload_for(client, "homeassistant/event/tesserae/dev_lounge_input/config")


def test_input_event_entity_publishes_non_retained_event(tmp_path: Path) -> None:
    svc = _FakeButtonService()
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(button_service=svc),
    )
    ha.start()
    cfg = json.loads(_payload_for(client, "homeassistant/event/tesserae/dev_lounge_input/config"))
    assert set(cfg["event_types"]) == {"button", "tap", "swipe", "slide"}
    assert len(svc.listeners) == 1
    svc.listeners[0](
        {
            "device_id": "lounge",
            "kind": "button",
            "name": "left",
            "outcome": "pushed",
            "page_id": "weather",
        }
    )
    rows = [
        (p, retain)
        for t, p, _q, retain in client.published
        if t == "tesserae/ha/dev/lounge/state/event"
    ]
    assert len(rows) == 1
    payload, retain = rows[0]
    assert retain is False
    body = json.loads(payload)
    assert body["event_type"] == "button"
    assert body["name"] == "left"
    assert body["page_name"] == "Weather"
    ha.stop()
    assert svc.listeners == []


def test_notify_commands_call_hook(tmp_path: Path) -> None:
    calls: list[tuple[str | None, str]] = []
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(notify_fn=lambda device_id, msg: calls.append((device_id, msg))),
    )
    ha.start()
    assert "homeassistant/notify/tesserae/notify_all/config" in _topics(client)
    assert "homeassistant/notify/tesserae/dev_lounge_notify/config" in _topics(client)
    ha._on_notify_all_cmd(CMD_TOPIC_NOTIFY, b"Dinner is ready")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/notify", b"Bins tonight")
    assert ha._wait_commands_idle()
    assert calls == [(None, "Dinner is ready"), ("lounge", "Bins tonight")]


def test_refresh_action_pushes_the_page_on_glass(tmp_path: Path) -> None:
    ha, _client, pm, _ = _wire(tmp_path, devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]))
    pm.latest_render_for.return_value = {"page_id": "weather", "digest": "abc"}
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"refresh")
    assert ha._wait_commands_idle()
    pm.push.assert_called_once_with(
        "weather",
        device_ids={"lounge"},
        force_publish=True,
        bypass_coalesce=True,
        source="home_assistant",
        lock_timeout_s=60.0,
        settle_max_s=30.0,
    )


def test_online_and_low_battery_binary_sensors(tmp_path: Path) -> None:
    now = datetime.now(UTC).timestamp()
    status = {
        "lounge": {"received_at": now - 60, "parsed": {"battery_pct": 9}},
        "hall": {"received_at": now - 86400, "parsed": {"battery_pct": 80}},
    }
    _decks, _nav, settings = _stores(tmp_path)
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge"), _FakeDevice("hall", "Hall")]),
        device_status=status,
        hooks=HaHooks(settings_store=settings, expected_interval_fn=lambda _id: 900),
    )
    ha.start()
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/online") == b"ON"
    assert _payload_for(client, "tesserae/ha/dev/hall/state/online") == b"OFF"
    assert "homeassistant/binary_sensor/tesserae/dev_lounge_low_battery/config" in _topics(client)
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/low_battery") == b"ON"
    assert _payload_for(client, "tesserae/ha/dev/hall/state/low_battery") == b"OFF"


def test_extra_heartbeat_sensors_are_lazy(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(tmp_path, devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]))
    ha.start()
    assert "homeassistant/sensor/tesserae/dev_lounge_firmware_version/config" not in _topics(client)
    ha.note_device_heartbeat(
        "lounge", {"fw_version": "1.30.1", "temperature_c": 21.5, "uptime_s": 42}
    )
    topics = _topics(client)
    assert "homeassistant/sensor/tesserae/dev_lounge_firmware_version/config" in topics
    assert "homeassistant/sensor/tesserae/dev_lounge_temperature/config" in topics
    assert "homeassistant/sensor/tesserae/dev_lounge_uptime/config" in topics
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/firmware_version") == b"1.30.1"


# ---------- teardown ----------


def test_stop_blanks_every_published_config(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    ha.start()
    configs = {t for t in _topics(client) if t.startswith("homeassistant/")}
    client.published.clear()
    ha.stop()
    blanked = {t for t, p, *_ in client.published if t.startswith("homeassistant/") and p == b""}
    assert configs <= blanked


def test_sweep_blanks_stale_lineup_configs(tmp_path: Path) -> None:
    decks, _nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    ha, client, _pm, _ = _wire(tmp_path, hooks=HaHooks(deck_store=decks, settings_store=settings))
    hub = {"identifiers": ["tesserae"]}
    stale = json.dumps({"object_id": "tesserae_lineup_gone_push", "device": hub}).encode()
    live = json.dumps({"object_id": "tesserae_lineup_morning_push", "device": hub}).encode()
    fixed = json.dumps({"object_id": "tesserae_automation", "device": hub}).encode()
    original_subscribe = ha._transport.subscribe

    def subscribe(topic, cb, *, qos=1):
        original_subscribe(topic, cb, qos=qos)
        if topic.startswith("homeassistant/+/"):
            cb("homeassistant/button/tesserae/lineup_gone_push/config", stale)
            cb("homeassistant/button/tesserae/lineup_morning_push/config", live)
            cb("homeassistant/switch/tesserae/automation/config", fixed)

    ha._transport.subscribe = subscribe  # type: ignore[method-assign]
    ha._sweep_stale_retained()
    blanked = {t for t, p, *_ in client.published if p == b""}
    assert "homeassistant/button/tesserae/lineup_gone_push/config" in blanked
    assert "homeassistant/button/tesserae/lineup_morning_push/config" not in blanked
    assert "homeassistant/switch/tesserae/automation/config" not in blanked


# ---------- publish toggles ----------


def _blanked(client: _FakeMqttClient) -> set[str]:
    return {t for t, p, *_ in client.published if p == b""}


_LINEUP_TOPICS = (
    "homeassistant/switch/tesserae/lineup_morning/config",
    "homeassistant/button/tesserae/lineup_morning_push/config",
    "homeassistant/button/tesserae/lineup_morning_next/config",
    "homeassistant/button/tesserae/lineup_morning_prev/config",
    "homeassistant/select/tesserae/dev_lounge_lineup/config",
)
_PAGE_BUTTON_TOPICS = (
    "homeassistant/button/tesserae/page_home/config",
    "homeassistant/button/tesserae/page_weather/config",
)
_KEPT_TOPICS = (
    "homeassistant/select/tesserae/active_page/config",
    "homeassistant/select/tesserae/dev_lounge_dashboard/config",
    "homeassistant/button/tesserae/dev_lounge_refresh/config",
    "homeassistant/number/tesserae/dev_lounge_hold/config",
    "homeassistant/switch/tesserae/automation/config",
)


def _toggle_rig(tmp_path: Path):
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(
            deck_store=decks,
            deck_nav_store=nav,
            settings_store=settings,
            rotation_state_store=DeviceRotationStateStore(tmp_path / "rot.json"),
        ),
    )
    return ha, client, settings


def test_both_groups_publish_by_default(tmp_path: Path) -> None:
    ha, client, _settings = _toggle_rig(tmp_path)
    ha.start()
    topics = set(_topics(client))
    assert set(_LINEUP_TOPICS) <= topics
    assert set(_PAGE_BUTTON_TOPICS) <= topics
    assert set(_KEPT_TOPICS) <= topics


def test_lineup_controls_off_blanks_them_live_and_back_on_republishes(tmp_path: Path) -> None:
    ha, client, settings = _toggle_rig(tmp_path)
    ha.start()
    client.published.clear()

    settings.patch_section("app", {"ha_discovery_lineup_controls": False})
    ha.refresh_entity_configs()
    assert set(_LINEUP_TOPICS) <= _blanked(client)
    # Everything else stays: dashboard buttons, selects, hold.
    assert not (set(_PAGE_BUTTON_TOPICS) | set(_KEPT_TOPICS)) & _blanked(client)
    for topic in _PAGE_BUTTON_TOPICS + _KEPT_TOPICS:
        assert _payload_for(client, topic) not in (None, b"")
    # Nobody reads the lineup states now; they stop being published.
    client.published.clear()
    ha.refresh_state()
    topics = _topics(client)
    assert "tesserae/ha/lineup/morning/state/enabled" not in topics
    assert "tesserae/ha/dev/lounge/state/lineup" not in topics

    client.published.clear()
    settings.patch_section("app", {"ha_discovery_lineup_controls": True})
    ha.refresh_entity_configs()
    for topic in _LINEUP_TOPICS:
        assert _payload_for(client, topic) not in (None, b"")


def test_dashboard_buttons_off_blanks_them_but_keeps_the_selects(tmp_path: Path) -> None:
    ha, client, settings = _toggle_rig(tmp_path)
    ha.start()
    client.published.clear()

    settings.patch_section("app", {"ha_discovery_dashboard_buttons": False})
    ha.refresh_entity_configs()
    assert set(_PAGE_BUTTON_TOPICS) <= _blanked(client)
    assert not (set(_LINEUP_TOPICS) | set(_KEPT_TOPICS)) & _blanked(client)
    select = json.loads(_payload_for(client, "homeassistant/select/tesserae/active_page/config"))
    assert select["options"] == ["Home", "Weather"]

    client.published.clear()
    settings.patch_section("app", {"ha_discovery_dashboard_buttons": True})
    ha.refresh_entity_configs()
    for topic in _PAGE_BUTTON_TOPICS:
        assert _payload_for(client, topic) not in (None, b"")


def test_a_new_dashboard_gets_no_button_while_they_are_off(tmp_path: Path) -> None:
    ha, client, settings = _toggle_rig(tmp_path)
    settings.patch_section("app", {"ha_discovery_dashboard_buttons": False})
    ha.start()
    ha._page_store.save(Page(id="news", name="News", panel=Panel(w=800, h=480)))
    assert "homeassistant/button/tesserae/page_news/config" not in _topics(client)


def test_starting_with_both_off_publishes_neither_group(tmp_path: Path) -> None:
    ha, client, settings = _toggle_rig(tmp_path)
    settings.patch_section(
        "app", {"ha_discovery_lineup_controls": False, "ha_discovery_dashboard_buttons": False}
    )
    ha.start()
    topics = set(_topics(client))
    assert not (set(_LINEUP_TOPICS) | set(_PAGE_BUTTON_TOPICS)) & topics
    assert set(_KEPT_TOPICS) <= topics


def test_settings_save_rebuild_honours_the_toggles(tmp_path: Path) -> None:
    # A Settings -> Server save rebuilds the transport: the old publisher
    # stops (blanking everything) and a fresh one starts from the saved
    # values, which is how ha_discovery_enabled takes effect live too.
    ha, client, settings = _toggle_rig(tmp_path)
    ha.start()
    settings.patch_section("app", {"ha_discovery_lineup_controls": False})
    ha.stop()
    assert set(_LINEUP_TOPICS) <= _blanked(client)
    client.published.clear()
    fresh = HomeAssistantDiscovery(
        transport=ha._transport,
        push_manager=ha._push_manager,
        page_store=ha._page_store,
        base_url_fn=lambda: "http://lan.test:8000",
        device_registry=ha._devices,
        hooks=ha._hooks,
        state_refresh_s=0,
    )
    fresh.start()
    topics = set(_topics(client))
    assert not set(_LINEUP_TOPICS) & topics
    assert set(_KEPT_TOPICS) <= topics


def test_missing_or_unreadable_settings_publish_everything(tmp_path: Path) -> None:
    decks, nav, _settings = _stores(tmp_path)
    decks.upsert(_deck())
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav),  # no settings store
    )
    ha.start()
    assert set(_LINEUP_TOPICS) | set(_PAGE_BUTTON_TOPICS) <= set(_topics(client))

    broken = MagicMock()
    broken.get_section.side_effect = RuntimeError("settings unreadable")
    ha2, client2, _pm2, _ = _wire(
        tmp_path / "b",
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=broken),
    )
    ha2.start()
    assert set(_LINEUP_TOPICS) | set(_PAGE_BUTTON_TOPICS) <= set(_topics(client2))


def test_toggle_values_parse_like_switches(tmp_path: Path) -> None:
    from app.ha_discovery import _switch_on

    assert _switch_on({}, "k") is True
    assert _switch_on({"k": None}, "k") is True
    assert _switch_on({"k": False}, "k") is False
    assert _switch_on({"k": 0}, "k") is False
    assert _switch_on({"k": "off"}, "k") is False
    assert _switch_on({"k": "false"}, "k") is False
    assert _switch_on({"k": ""}, "k") is False
    assert _switch_on({"k": "on"}, "k") is True
    assert _switch_on({"k": "TRUE"}, "k") is True
    assert _switch_on({"k": 1}, "k") is True


def _feed_retained(ha: HomeAssistantDiscovery, retained: dict[str, dict]) -> None:
    original_subscribe = ha._transport.subscribe

    def subscribe(topic, cb, *, qos=1):
        original_subscribe(topic, cb, qos=qos)
        if topic.startswith("homeassistant/+/"):
            for t, cfg in retained.items():
                cb(t, json.dumps(cfg).encode())

    ha._transport.subscribe = subscribe  # type: ignore[method-assign]


def test_sweep_blanks_retained_entities_of_groups_switched_off(tmp_path: Path) -> None:
    ha, client, settings = _toggle_rig(tmp_path)
    settings.patch_section(
        "app", {"ha_discovery_lineup_controls": False, "ha_discovery_dashboard_buttons": False}
    )
    hub = {"identifiers": ["tesserae"]}
    lounge = {"identifiers": ["tesserae_dev_lounge"]}
    _feed_retained(
        ha,
        {
            "homeassistant/button/tesserae/page_home/config": {
                "object_id": "tesserae_page_home",
                "device": hub,
            },
            "homeassistant/switch/tesserae/lineup_morning/config": {
                "object_id": "tesserae_lineup_morning",
                "device": hub,
            },
            "homeassistant/select/tesserae/dev_lounge_lineup/config": {
                "object_id": "tesserae_dev_lounge_lineup",
                "device": lounge,
            },
            "homeassistant/select/tesserae/active_page/config": {
                "object_id": "tesserae_active_page",
                "device": hub,
            },
            "homeassistant/image/tesserae/dev_lounge_frame/config": {
                "object_id": "tesserae_dev_lounge_frame",
                "device": lounge,
            },
            "homeassistant/sensor/tesserae/dev_lounge_battery/config": {
                "object_id": "tesserae_dev_lounge_battery",
                "device": lounge,
            },
        },
    )
    ha._sweep_stale_retained()
    blanked = _blanked(client)
    assert "homeassistant/button/tesserae/page_home/config" in blanked
    assert "homeassistant/switch/tesserae/lineup_morning/config" in blanked
    assert "homeassistant/select/tesserae/dev_lounge_lineup/config" in blanked
    assert "homeassistant/select/tesserae/active_page/config" not in blanked
    assert "homeassistant/image/tesserae/dev_lounge_frame/config" not in blanked
    assert "homeassistant/sensor/tesserae/dev_lounge_battery/config" not in blanked


def test_sweep_keeps_lineup_buttons_only_while_they_can_act(tmp_path: Path) -> None:
    decks, _nav, settings = _stores(tmp_path)
    decks.upsert(_deck(device_ids=[]))  # no displays: switch only, no buttons
    ha, client, _pm, _ = _wire(tmp_path, hooks=HaHooks(deck_store=decks, settings_store=settings))
    hub = {"identifiers": ["tesserae"]}
    _feed_retained(
        ha,
        {
            "homeassistant/switch/tesserae/lineup_morning/config": {
                "object_id": "tesserae_lineup_morning",
                "device": hub,
            },
            "homeassistant/button/tesserae/lineup_morning_push/config": {
                "object_id": "tesserae_lineup_morning_push",
                "device": hub,
            },
        },
    )
    ha._sweep_stale_retained()
    blanked = _blanked(client)
    assert "homeassistant/button/tesserae/lineup_morning_push/config" in blanked
    assert "homeassistant/switch/tesserae/lineup_morning/config" not in blanked


# ---------- teardown with overlapping ids ----------


def test_removing_a_display_spares_one_whose_id_extends_it(tmp_path: Path) -> None:
    registry = _FakeRegistry([_FakeDevice("a", "A"), _FakeDevice("a_b", "A B")])
    ha, client, _pm, _ = _wire(tmp_path, devices=registry)
    ha.start()
    ha.note_device_heartbeat("a_b", {"battery_pct": 50})
    del registry._d["a"]
    client.published.clear()
    ha.refresh_entity_configs()
    blanked = _blanked(client)
    assert "homeassistant/image/tesserae/dev_a_frame/config" in blanked
    assert not {t for t in blanked if "/dev_a_b_" in t}
    assert ("sensor", "dev_a_b_battery") in ha._published_configs


def test_deleting_a_lineup_spares_one_whose_id_extends_it(tmp_path: Path) -> None:
    decks, _nav, settings = _stores(tmp_path)
    decks.upsert(_deck(id="a", name="A"))
    decks.upsert(_deck(id="a_b", name="A B"))
    ha, client, _pm, _ = _wire(tmp_path, hooks=HaHooks(deck_store=decks, settings_store=settings))
    ha.start()
    client.published.clear()
    decks.delete("a")
    blanked = _blanked(client)
    assert "homeassistant/switch/tesserae/lineup_a/config" in blanked
    assert not {t for t in blanked if "/lineup_a_b" in t}


def test_teardown_uses_recorded_ownership_not_object_ids(tmp_path: Path) -> None:
    # ``a``'s Next change sensor is ``dev_a_next_change``, which reads like
    # an entity of a display called ``a_next``; removing either display must
    # take exactly its own entities.
    scheduler = MagicMock()
    scheduler.upcoming_for_device.return_value = []
    registry = _FakeRegistry([_FakeDevice("a", "A"), _FakeDevice("a_next", "A next")])
    ha, client, _pm, _ = _wire(tmp_path, devices=registry, hooks=HaHooks(scheduler=scheduler))
    ha.start()
    ha.note_device_heartbeat("a", {"battery_pct": 50})
    ha.note_device_heartbeat("a_next", {"battery_pct": 60})
    assert ha._config_owner[("sensor", "dev_a_next_change")] == "a"
    assert ha._config_owner[("sensor", "dev_a_next_battery")] == "a_next"

    del registry._d["a"]
    client.published.clear()
    ha.refresh_entity_configs()
    blanked = _blanked(client)
    assert "homeassistant/sensor/tesserae/dev_a_next_change/config" in blanked
    assert "homeassistant/sensor/tesserae/dev_a_battery/config" in blanked
    assert "homeassistant/image/tesserae/dev_a_next_frame/config" not in blanked
    assert "homeassistant/sensor/tesserae/dev_a_next_battery/config" not in blanked
    assert set(ha._config_owner.values()) == {"a_next"}


def test_removing_the_longer_id_spares_the_shorter_ones_lookalikes(tmp_path: Path) -> None:
    scheduler = MagicMock()
    scheduler.upcoming_for_device.return_value = []
    registry = _FakeRegistry([_FakeDevice("a", "A"), _FakeDevice("a_next", "A next")])
    ha, client, _pm, _ = _wire(tmp_path, devices=registry, hooks=HaHooks(scheduler=scheduler))
    ha.start()
    del registry._d["a_next"]
    client.published.clear()
    ha.refresh_entity_configs()
    blanked = _blanked(client)
    assert "homeassistant/image/tesserae/dev_a_next_frame/config" in blanked
    # ``a``'s own sensor survives although its id starts with ``dev_a_next_``.
    assert "homeassistant/sensor/tesserae/dev_a_next_change/config" not in blanked
    assert ("sensor", "dev_a_next_change") in ha._published_configs


# ---------- per-display next / previous ----------


def _steps_hook(calls: list[tuple[str, int]], err: str | None = None):
    def step(device_id: str, delta: int) -> str | None:
        calls.append((device_id, delta))
        return err

    return step


def test_next_prev_buttons_published_on_the_display_with_a_step_hook(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(step_device_fn=_steps_hook([])),
    )
    ha.start()
    nxt = json.loads(_payload_for(client, "homeassistant/button/tesserae/dev_lounge_next/config"))
    prv = json.loads(_payload_for(client, "homeassistant/button/tesserae/dev_lounge_prev/config"))
    assert nxt["name"] == "Next" and prv["name"] == "Previous"
    assert nxt["command_topic"] == prv["command_topic"] == "tesserae/ha/dev/lounge/cmd/action"
    assert (nxt["payload_press"], prv["payload_press"]) == ("next", "prev")
    assert nxt["unique_id"] == "tesserae_dev_lounge_next"
    assert nxt["device"]["identifiers"] == ["tesserae_dev_lounge"]


def test_next_prev_buttons_absent_without_the_hook(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(tmp_path, devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]))
    ha.start()
    topics = _topics(client)
    assert "homeassistant/button/tesserae/dev_lounge_next/config" not in topics
    assert "homeassistant/button/tesserae/dev_lounge_prev/config" not in topics
    # The refresh button needs no hook and is still there.
    assert "homeassistant/button/tesserae/dev_lounge_refresh/config" in topics


def test_next_prev_commands_route_to_the_hook(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(step_device_fn=_steps_hook(calls)),
    )
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"next")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b" prev\n")
    assert ha._wait_commands_idle()
    assert calls == [("lounge", 1), ("lounge", -1)]
    # Busy is cleared even when nothing pushed (a promoted warm frame).
    assert _payload_for(client, "tesserae/ha/state/busy") == b"0"


def test_next_prev_ignore_junk_and_unknown_displays(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []
    ha, _client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(step_device_fn=_steps_hook(calls)),
    )
    ha.start()
    for junk in (b"", b"NEXT", b"next please", b"\xff\xfenext", b"nex", b"previous", b"1"):
        ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", junk)
    ha._on_device_cmd("tesserae/ha/dev/ghost/cmd/action", b"next")  # unknown / deleted
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/next", b"next")  # wrong leaf
    ha._on_device_cmd("tesserae/ha/devlounge/cmd/action", b"next")  # malformed topic
    assert ha._wait_commands_idle()
    assert calls == []
    pm.push.assert_not_called()


def test_step_error_and_exception_land_on_last_error(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(step_device_fn=_steps_hook(calls, err="Lounge: render failed")),
    )
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"next")
    assert ha._wait_commands_idle()
    assert _payload_for(client, "tesserae/ha/state/last_error") == b"Lounge: render failed"

    def boom(_device_id: str, _delta: int) -> str | None:
        raise RuntimeError("nope")

    ha._hooks.step_device_fn = boom
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"prev")  # must not raise
    assert ha._wait_commands_idle()
    assert _payload_for(client, "tesserae/ha/state/last_error") == b"Lounge: failed"
    assert _payload_for(client, "tesserae/ha/state/busy") == b"0"


def test_next_steps_only_the_pressed_display_through_the_button_service(
    tmp_path: Path,
) -> None:
    from app.button_service import ButtonService
    from app.state.legacy_projections import RotationProjection

    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck(device_ids=["lounge", "hall"]))
    nav.set("lounge", "morning", "home")
    nav.set("hall", "morning", "home")
    holder: dict[str, ButtonService] = {}

    def step(device_id: str, delta: int) -> str | None:
        holder["svc"].step(device_id=device_id, forward=delta > 0)
        return None

    ha, client, pm, page_store = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge"), _FakeDevice("hall", "Hall")]),
        hooks=HaHooks(
            deck_store=decks, deck_nav_store=nav, settings_store=settings, step_device_fn=step
        ),
    )
    holder["svc"] = ButtonService(
        rotation_store=RotationProjection(decks),
        state_store=DeviceRotationStateStore(tmp_path / "rot.json"),
        settings_store=settings,
        page_store=page_store,
        push_manager=pm,
        deck_store=decks,
        deck_nav_store=nav,
    )
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"next")
    assert ha._wait_commands_idle()
    assert pm.push.call_args.args[0] == "weather"
    assert pm.push.call_args.kwargs["device_ids"] == {"lounge"}
    assert pm.push.call_args.kwargs["source"] == "home_assistant"
    assert nav.current_page("lounge", "morning") == "weather"
    assert nav.current_page("hall", "morning") == "home"
    # The pressed display's state was republished; it still follows Morning.
    assert _payload_for(client, "tesserae/ha/dev/lounge/state/lineup") == b"Morning"


# ---------- publish toggles ----------


def test_lineup_select_and_next_prev_agree_on_the_followed_lineup(tmp_path: Path) -> None:
    from app.button_service import ButtonService
    from app.state.legacy_projections import RotationProjection

    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck(id="breakfast", name="Breakfast"))
    decks.upsert(_deck(id="dinner", name="Dinner"))
    decks.upsert(_deck(id="late", name="Late", enabled=False))
    ha, _client, pm, page_store = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    svc = ButtonService(
        rotation_store=RotationProjection(decks),
        state_store=DeviceRotationStateStore(tmp_path / "rot.json"),
        settings_store=settings,
        page_store=page_store,
        push_manager=pm,
        deck_store=decks,
        deck_nav_store=nav,
    )

    def both() -> tuple[str | None, str | None]:
        a, b = ha._lineup_for_device("lounge"), svc.following_deck("lounge")
        return (a.id if a else None, b.id if b else None)

    assert both() == ("breakfast", "breakfast")  # no nav record: first enabled
    nav.set("lounge", "dinner", "home")
    assert both() == ("dinner", "dinner")  # the one on the glass
    nav.set("lounge", "late", "home")  # picked through the Lineup select
    assert both() == ("late", "late")
    decks.upsert(_deck(id="late", name="Late", enabled=False, device_ids=[]))
    assert both() == ("breakfast", "breakfast")  # no longer binds the display


def test_sweep_blanks_retained_next_prev_when_the_hook_is_gone(tmp_path: Path) -> None:
    ha, client, _pm, _ = _wire(tmp_path, devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]))
    lounge = {"identifiers": ["tesserae_dev_lounge"]}
    _feed_retained(
        ha,
        {
            "homeassistant/button/tesserae/dev_lounge_next/config": {
                "object_id": "tesserae_dev_lounge_next",
                "device": lounge,
            },
            "homeassistant/button/tesserae/dev_lounge_refresh/config": {
                "object_id": "tesserae_dev_lounge_refresh",
                "device": lounge,
            },
            # Its scheduler hook is missing too, but at boot that only means
            # the scheduler isn't built yet: the entity must survive.
            "homeassistant/sensor/tesserae/dev_lounge_next_change/config": {
                "object_id": "tesserae_dev_lounge_next_change",
                "device": lounge,
            },
        },
    )
    ha._sweep_stale_retained()
    blanked = {t for t, p, *_ in client.published if p == b""}
    assert "homeassistant/button/tesserae/dev_lounge_next/config" in blanked
    assert "homeassistant/button/tesserae/dev_lounge_refresh/config" not in blanked
    assert "homeassistant/sensor/tesserae/dev_lounge_next_change/config" not in blanked


def test_next_returns_from_the_mqtt_callback_at_once(tmp_path: Path) -> None:
    # A step can wait behind a render of the same display (the navigation
    # lock); it must not wait inside Paho's callback, which would stall
    # every other command, heartbeat and keepalive meanwhile.
    import threading
    import time

    gate = threading.Event()
    calls: list[tuple[str, int]] = []

    def slow_step(device_id: str, delta: int) -> str | None:
        calls.append((device_id, delta))
        assert gate.wait(5)
        return "Lounge: b superseded"

    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(step_device_fn=slow_step, rotation_state_store=rot_state),
    )
    ha.start()
    started = time.monotonic()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"next")
    # Another command is handled while the step is still waiting.
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/hold", b"30")
    assert time.monotonic() - started < 1.0
    assert rot_state.get("lounge").override_until is not None
    gate.set()
    assert ha._wait_commands_idle()
    assert calls == [("lounge", 1)]
    # The outcome still reaches Home Assistant.
    assert _payload_for(client, "tesserae/ha/state/last_error") == b"Lounge: b superseded"
    worker = ha._command_worker
    assert worker is not None
    ha.stop()
    worker.join(5)  # stop() doesn't wait; the idle worker exits on its own
    assert not worker.is_alive() and ha._command_worker is None


def test_a_full_step_queue_refuses_with_last_error(tmp_path: Path) -> None:
    import threading

    from app import ha_discovery as ha_module

    gate = threading.Event()

    def slow_step(_device_id: str, _delta: int) -> str | None:
        gate.wait(5)
        return None

    ha, client, _pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(step_device_fn=slow_step),
    )
    ha.start()
    try:
        for _ in range(ha_module.COMMAND_QUEUE_MAX + 2):
            ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"next")
        assert b"too many" in (_payload_for(client, "tesserae/ha/state/last_error") or b"")
    finally:
        gate.set()
    assert ha._wait_commands_idle()


def test_a_step_for_a_display_removed_while_queued_is_dropped(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []
    registry = _FakeRegistry([_FakeDevice("lounge", "Lounge")])
    ha, _client, _pm, _ = _wire(
        tmp_path, devices=registry, hooks=HaHooks(step_device_fn=_steps_hook(calls))
    )
    ha.start()
    ha._run_step("ghost", 1)
    del registry._d["lounge"]
    ha._run_step("lounge", 1)
    assert calls == []


# ---------- slow commands run on the command worker ----------


def _blocking_push(pm: MagicMock):
    """Make ``pm.push`` wait for a gate on its first call; record the rest."""
    import threading

    gate, started = threading.Event(), threading.Event()
    order: list[tuple[str, frozenset[str] | None]] = []

    def push(page_id: str, **kw):
        ids = kw.get("device_ids")
        order.append((page_id, frozenset(ids) if ids is not None else None))
        started.set()
        assert gate.wait(5)
        return PushResult(status="sent", page_id=page_id)

    pm.push.side_effect = push
    return gate, started, order


def test_every_slow_command_returns_from_the_mqtt_callback_at_once(tmp_path: Path) -> None:
    import time

    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    notes: list[tuple[str | None, str]] = []
    installs: list[str] = []
    steps: list[tuple[str, int]] = []
    rot_state = DeviceRotationStateStore(tmp_path / "rot.json")
    ha, _client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(
            deck_store=decks,
            deck_nav_store=nav,
            settings_store=settings,
            rotation_state_store=rot_state,
            notify_fn=lambda d, m: notes.append((d, m)),
            firmware_install_fn=lambda d: installs.append(d),
            step_device_fn=lambda d, delta: steps.append((d, delta)),
        ),
    )
    gate, started, _order = _blocking_push(pm)
    ha.start()
    t0 = time.monotonic()
    ha._on_push_page_cmd("tesserae/ha/cmd/push_page", b"home")  # blocks the worker
    assert started.wait(5)
    ha._on_active_page_cmd("tesserae/ha/cmd/active_page", b"Weather")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/push", b"Weather")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"refresh")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"next")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/lineup", b"Morning")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/notify", b"Bins tonight")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/firmware_install", b"install")
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"push")
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"next")
    ha._on_notify_all_cmd(CMD_TOPIC_NOTIFY, b"Dinner is ready")
    # Cheap toggles still apply inline, while the worker is busy.
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/hold", b"30")
    ha._on_automation_cmd(CMD_TOPIC_AUTOMATION, b"OFF")
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/enabled", b"OFF")  # queued too
    assert time.monotonic() - t0 < 1.0
    assert rot_state.get("lounge").override_until is not None
    assert settings.get_section("app")["automation_paused"] is True
    assert decks.get("morning").enabled is True
    assert notes == [] and installs == [] and steps == []
    gate.set()
    assert ha._wait_commands_idle()
    assert notes == [("lounge", "Bins tonight"), (None, "Dinner is ready")]
    assert installs == ["lounge"]
    assert steps == [("lounge", 1)]
    assert decks.get("morning").enabled is False


def test_commands_for_a_display_run_in_the_order_sent(tmp_path: Path) -> None:
    events: list[str] = []
    ha, _client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(
            notify_fn=lambda _d, m: events.append(f"notify {m}"),
            step_device_fn=lambda _d, delta: events.append(f"step {delta}"),
        ),
    )
    pm.latest_render_for.return_value = {"page_id": "home"}

    def push(page_id: str, **kw):
        events.append(f"push {page_id}{' refresh' if kw.get('force_publish') else ''}")
        return PushResult(status="sent", page_id=page_id)

    pm.push.side_effect = push
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/push", b"Weather")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"next")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/notify", b"hi")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"refresh")
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"prev")
    assert ha._wait_commands_idle()
    assert events == ["push weather", "step 1", "notify hi", "push home refresh", "step -1"]


@pytest.mark.parametrize("notify_all", [False, True])
def test_notify_deadline_frees_command_worker_and_reports_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notify_all: bool
) -> None:
    devices = _FakeRegistry([_FakeDevice("lounge", "Lounge")])
    app = Flask(__name__)
    ha, client, _, pages = _wire(
        tmp_path,
        devices=devices,
        hooks=HaHooks(notify_fn=lambda device_id, msg: ha_hooks._notify(app, device_id, msg)),
    )
    manager = PushManager(
        registry=MagicMock(),
        page_store=pages,
        transport=MagicMock(),
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=MagicMock(),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://lan.test:8000",
    )
    app.config.update(DEVICE_REGISTRY=devices, PUSH_MANAGER=manager)
    monkeypatch.setattr(ha_hooks, "HA_PUSH_LOCK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(ha_hooks, "render_notice", lambda *_args: b"notice")
    ha.start()
    manager._lock.acquire()
    try:
        if notify_all:
            ha._on_notify_all_cmd(CMD_TOPIC_NOTIFY, b"Hello")
        else:
            ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/notify", b"Hello")
        assert ha._wait_commands_idle(timeout=1)
        error = _payload_for(client, "tesserae/ha/state/last_error")
        assert error and error.startswith(b"Notify: Lounge:") and b"renderer busy" in error
        assert _payload_for(client, "tesserae/ha/state/busy") == b"0"
        assert manager._lock.locked()
    finally:
        manager._lock.release()
        ha.stop()


def test_queued_command_failures_reach_last_error(tmp_path: Path) -> None:
    ha, client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(notify_fn=lambda _d, _m: "Lounge: render timed out"),
    )
    ha.start()
    last = "tesserae/ha/state/last_error"
    pm.push.return_value = PushResult(
        status="superseded", page_id="weather", error="newer push for the same device took priority"
    )
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/push", b"Weather")
    assert ha._wait_commands_idle()
    assert _payload_for(client, last) == b"Lounge: newer push for the same device took priority"
    pm.push.return_value = PushResult(status="not_found", page_id="home", error="page not found")
    ha._on_push_page_cmd("tesserae/ha/cmd/push_page", b"home")
    assert ha._wait_commands_idle()
    assert _payload_for(client, last) == b"Home: page not found"
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/notify", b"hi")
    assert ha._wait_commands_idle()
    assert _payload_for(client, last) == b"Notify: Lounge: render timed out"
    pm.push.side_effect = RuntimeError("broker gone")
    pm.latest_render_for.return_value = {"page_id": "home"}
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"refresh")
    assert ha._wait_commands_idle()
    assert _payload_for(client, last) == b"Lounge: failed"
    assert _payload_for(client, "tesserae/ha/state/busy") == b"0"


def test_queued_commands_skip_a_display_or_lineup_removed_meanwhile(tmp_path: Path) -> None:
    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    registry = _FakeRegistry([_FakeDevice("lounge", "Lounge")])
    ha, _client, pm, _ = _wire(
        tmp_path,
        devices=registry,
        hooks=HaHooks(deck_store=decks, deck_nav_store=nav, settings_store=settings),
    )
    gate, started, order = _blocking_push(pm)
    pm.latest_render_for.return_value = {"page_id": "home"}
    ha.start()
    ha._on_push_page_cmd("tesserae/ha/cmd/push_page", b"home")  # holds the worker
    assert started.wait(5)
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/action", b"refresh")
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"push")
    del registry._d["lounge"]
    decks.delete("morning")
    gate.set()
    assert ha._wait_commands_idle()
    assert order == [("home", None)]  # nothing ran for the removed targets


# ---------- command worker lifecycle and limits ----------


def test_lineup_toggle_returns_at_once_while_invalidation_waits(tmp_path: Path) -> None:
    # The reproduced stall: invalidating the lineup's warmed frames takes the
    # push manager's render lock, so the OFF callback waited out a render.
    import threading
    import time

    decks, _nav, settings = _stores(tmp_path)
    decks.upsert(_deck())
    gate = threading.Event()
    ha, _client, _pm, _ = _wire(
        tmp_path,
        hooks=HaHooks(
            deck_store=decks, settings_store=settings, invalidate_deck_fn=lambda _d: gate.wait(5)
        ),
    )
    ha.start()
    t0 = time.monotonic()
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/enabled", b"OFF")
    assert time.monotonic() - t0 < 0.5
    gate.set()
    assert ha._wait_commands_idle()
    assert decks.get("morning").enabled is False


def _gated_jobs(ha, labels: list[str], gate, done: list[str]) -> None:
    for label in labels:

        def job(label: str = label) -> str | None:
            if label == labels[0]:
                assert gate.wait(5)
            done.append(label)
            return None

        ha._enqueue(label, job)


def _worker_threads(before: set | None = None) -> list:
    """Live command workers, minus those that existed at ``before``
    (other tests' instances in this process)."""
    import threading

    return [
        t
        for t in threading.enumerate()
        if t.name == "ha-discovery-commands" and t not in (before or set())
    ]


def test_stop_during_a_long_command_cancels_and_reports_the_queue(tmp_path: Path) -> None:
    # A stop while a command overran used to strand the queued ones. Now
    # they're cancelled explicitly and named on Last error, the stop does
    # not wait for the running one, and the worker exits once it's done.
    import threading

    from app import ha_discovery as ha_module

    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    ha._command_time_limit_s = 0.1
    gate, started, done = threading.Event(), threading.Event(), []

    def first() -> str | None:
        started.set()
        assert gate.wait(5)
        done.append("first")
        return None

    ha._enqueue("first", first)
    assert started.wait(5)
    for i in range(ha_module.COMMAND_QUEUE_MAX):
        ha._enqueue(f"j{i}", lambda i=i: done.append(f"j{i}"))
    worker = ha._command_worker
    assert worker is not None and ha._commands.full()
    threading.Event().wait(0.3)  # "first" is now over its time limit
    ha._stop_command_worker()  # returns although "first" still runs
    error = _payload_for(client, "tesserae/ha/state/last_error") or b""
    assert error.startswith(b"Cancelled when Home Assistant discovery stopped: j0, j1")
    gate.set()
    worker.join(5)
    assert not worker.is_alive() and ha._command_worker is None
    assert done == ["first"]  # cancelled commands never ran
    assert ha._commands.unfinished_tasks == 0


def test_restart_during_a_stop_takes_the_worker_back(tmp_path: Path) -> None:
    import threading

    before = set(_worker_threads())
    ha, _client, _pm, _ = _wire(tmp_path)
    ha.start()
    gate, done = threading.Event(), []
    _gated_jobs(ha, ["first", "second"], gate, done)
    for _ in range(50):  # until the worker is running "first"
        if ha._running_job is not None:
            break
        threading.Event().wait(0.05)
    old = ha._command_worker
    ha._stop_command_worker()  # "first" still running; "second" cancelled
    _gated_jobs(ha, ["third"], gate, done)  # started again before it exited
    assert ha._command_worker is old
    assert len(_worker_threads(before)) == 1
    gate.set()
    assert ha._wait_commands_idle()
    assert done == ["first", "third"]


def test_a_long_command_is_reported_without_spawning_threads(tmp_path: Path) -> None:
    # Sol's case: every overrun used to spawn a replacement worker, and the
    # stuck ones piled up (5 blocked threads). Now the watchdog only reports;
    # one worker, later commands wait their turn.
    import threading

    before = set(_worker_threads())
    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    ha._command_time_limit_s = 0.1
    gates = [threading.Event() for _ in range(5)]
    done: list[int] = []
    for i, gate in enumerate(gates):
        ha._enqueue(f"Job {i}", lambda i=i, gate=gate: (gate.wait(5), done.append(i))[0] and None)
    threading.Event().wait(0.5)
    assert len(_worker_threads(before)) == 1
    assert done == []
    assert (_payload_for(client, "tesserae/ha/state/last_error") or b"").startswith(
        b"Job 0: still running after"
    )
    for gate in gates:
        gate.set()
    assert ha._wait_commands_idle()
    assert done == [0, 1, 2, 3, 4]
    assert len(_worker_threads(before)) == 1


def test_a_late_watchdog_never_reports_the_next_command(tmp_path: Path) -> None:
    # Sol's case: the timer checked the worker, not the job, so one firing
    # late spoke for (and handed off) the command after it.
    import threading

    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    tokens: list[int | None] = []
    gate = threading.Event()
    ha._enqueue("First", lambda: tokens.append(ha._running_job) and None)
    ha._enqueue("Second", lambda: (gate.wait(5), None)[1])
    threading.Event().wait(0.3)  # "Second" is running now
    before = _payload_for(client, "tesserae/ha/state/last_error")
    ha._command_overran(tokens[0], "First")  # First's timer, firing late
    assert _payload_for(client, "tesserae/ha/state/last_error") == before
    worker = ha._command_worker
    gate.set()
    assert ha._wait_commands_idle()
    assert ha._command_worker is worker


def test_a_watchdog_firing_after_a_stop_leaves_last_error_alone(tmp_path: Path) -> None:
    # Once discovery is stopped a newer publisher owns Last error: the
    # overrun report of the command that is still finishing must only log.
    import threading

    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    gate, started = threading.Event(), threading.Event()
    tokens: list[int | None] = []

    def job() -> str | None:
        tokens.append(ha._running_job)
        started.set()
        assert gate.wait(5)
        return None

    ha._enqueue("Slow", job)
    assert started.wait(5)
    ha._command_overran(tokens[0], "Slow")  # not stopping: reported
    assert (_payload_for(client, "tesserae/ha/state/last_error") or b"").startswith(
        b"Slow: still running after"
    )
    client.published.clear()
    ha._stop_command_worker()  # "Slow" is still running
    ha._command_overran(tokens[0], "Slow")  # its timer fires after the stop
    assert _payload_for(client, "tesserae/ha/state/last_error") is None
    gate.set()
    worker = ha._command_worker
    if worker is not None:
        worker.join(5)


def test_the_command_time_limit_covers_a_slow_installs_push_now() -> None:
    # Push now can legitimately wait out the warm budget, then the settle
    # wait and the render lock for the entry page, then render: a limit
    # below that trips the watchdog on a push that is only slow.
    from app import ha_discovery as ha_module

    assert (
        ha_module.COMMAND_TIME_LIMIT_S
        > ha_module.HA_WARM_BUDGET_S + ha_module.HA_SETTLE_MAX_S + ha_module.HA_PUSH_LOCK_TIMEOUT_S
    )
    assert ha_module.COMMAND_TIME_LIMIT_S >= 180.0


def test_home_assistant_pushes_carry_deadlines(tmp_path: Path) -> None:
    ha, client, pm, _ = _wire(tmp_path, devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]))
    pm.push.return_value = PushResult(
        status="busy", page_id="weather", error="another push kept the renderer busy for over 60s"
    )
    ha.start()
    ha._on_device_cmd("tesserae/ha/dev/lounge/cmd/push", b"Weather")
    assert ha._wait_commands_idle()
    kwargs = pm.push.call_args.kwargs
    assert kwargs["lock_timeout_s"] == 60.0 and kwargs["settle_max_s"] == 30.0
    assert _payload_for(client, "tesserae/ha/state/last_error") == (
        b"Lounge: another push kept the renderer busy for over 60s"
    )


def test_lineup_actions_bound_their_renderer_waits(tmp_path: Path) -> None:
    import time

    from app import ha_discovery as ha_module

    decks, nav, settings = _stores(tmp_path)
    decks.upsert(_deck(pages=[DeckPage(page_id=p) for p in ("home", "weather", "a", "b")]))
    nav.set("lounge", "morning", "home")
    ha, client, pm, _ = _wire(
        tmp_path,
        devices=_FakeRegistry([_FakeDevice("lounge", "Lounge")]),
        hooks=HaHooks(
            deck_store=decks,
            deck_nav_store=nav,
            settings_store=settings,
            invalidate_deck_fn=lambda _d: "renderer busy; warmed frames for lounge not cleared",
        ),
    )

    def slow_warm(_page: str, _device: str, *, lock_timeout_s: float) -> bool:
        time.sleep(min(lock_timeout_s, 0.15))  # waits its whole share
        return False

    pm.warm_deck_page.side_effect = slow_warm
    ha.start()
    original = ha_module.HA_WARM_BUDGET_S
    ha_module.HA_WARM_BUDGET_S = 0.2
    try:
        ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"push")
        assert ha._wait_commands_idle()
    finally:
        ha_module.HA_WARM_BUDGET_S = original
    # The budget ran out after two warms; the entry page still went out.
    assert pm.warm_deck_page.call_count == 2
    assert all(c.kwargs["lock_timeout_s"] <= 0.2 for c in pm.warm_deck_page.call_args_list)
    assert pm.push.call_args.kwargs["lock_timeout_s"] == 60.0
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/action", b"next")
    assert ha._wait_commands_idle()
    assert pm.promote_deck_page.call_args.kwargs == {"lock_timeout_s": 60.0}
    ha._on_lineup_cmd("tesserae/ha/lineup/morning/cmd/enabled", b"OFF")
    assert ha._wait_commands_idle()
    assert _payload_for(client, "tesserae/ha/state/last_error") == (
        b"Morning: renderer busy; warmed frames for lounge not cleared"
    )


def test_stop_does_not_wait_for_a_command_in_progress(tmp_path: Path) -> None:
    # stop() runs on a Settings save: a render that takes seconds must not
    # hold the save up.
    import threading
    import time

    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    gate, started = threading.Event(), threading.Event()

    def slow() -> str | None:
        started.set()
        assert gate.wait(10)
        return "render failed"

    ha._enqueue("Lounge", slow)
    assert started.wait(5)
    worker = ha._command_worker
    assert worker is not None
    t0 = time.monotonic()
    ha.stop()
    assert time.monotonic() - t0 < 1.0
    assert worker.is_alive()  # still finishing its command in the background
    gate.set()
    worker.join(5)
    assert not worker.is_alive() and ha._command_worker is None
    # A publisher that has stopped leaves Busy / Last error to its successor.
    assert b"render failed" not in (_payload_for(client, "tesserae/ha/state/last_error") or b"")


def test_a_command_finishing_after_stop_is_logged_not_published(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import threading

    ha, client, _pm, _ = _wire(tmp_path)
    ha.start()
    gate, started = threading.Event(), threading.Event()

    def slow() -> str | None:
        started.set()
        assert gate.wait(10)
        return "Lounge: b superseded"

    ha._enqueue("Lounge", slow)
    assert started.wait(5)
    worker = ha._command_worker
    assert worker is not None
    ha.stop()
    published = len(client.published)
    with caplog.at_level(logging.WARNING, logger="app.ha_discovery"):
        gate.set()
        worker.join(5)
    assert not worker.is_alive()
    assert len(client.published) == published  # nothing published after the stop
    assert "Lounge: b superseded" in caplog.text
