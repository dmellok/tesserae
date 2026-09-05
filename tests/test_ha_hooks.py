"""App-side glue for HA discovery: notice rendering, device config
patching, firmware state / install, the scheduler pause gate, the
button-service listener, and the DeckStore change listener."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from flask import Flask
from PIL import Image

from app import ha_hooks
from app.button_service import ButtonHandleResult, ButtonService, TouchHandleResult
from app.scheduler import Scheduler
from app.settings.field_defs import APP_FIELD_GROUPS, APP_FIELDS
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.schedule_store import ScheduleStore
from app.state.settings_store import SettingsStore

# ---------- render_notice ----------


def test_render_notice_is_panel_sized_png() -> None:
    png = ha_hooks.render_notice("Dinner is ready", 800, 480)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"
    assert img.size == (800, 480)
    # Something dark got drawn (text), the card isn't blank white.
    assert img.convert("L").getextrema()[0] < 128


def test_render_notice_shrinks_long_text_to_fit() -> None:
    long = " ".join(["notification"] * 60)
    png = ha_hooks.render_notice(long, 296, 128)
    assert Image.open(io.BytesIO(png)).size == (296, 128)


# ---------- device config / interval / firmware ----------


class _Device:
    def __init__(self, id: str, *, transport: str = "mqtt", config_topic: str | None = None):
        self.id = id
        self.display_name = id.title()
        self.name = id.title()
        self.kind_of = "esp32"
        self.panel = {"w": 800, "h": 480}
        self.manifest: dict = {}
        self.transport = transport
        self.config_topic = config_topic
        self.config_schema = {
            "sleep_interval_s": {"type": "int", "default": 900, "min": 60, "max": 86400},
            "label": {"type": "string", "default": "x"},
        }
        self.validated: list[dict] = []

    def validate_config(self, payload: dict) -> tuple[bool, str | None]:
        self.validated.append(dict(payload))
        if payload.get("sleep_interval_s", 0) < 60:
            return False, "too fast"
        return True, None


class _Registry:
    def __init__(self, *devices: _Device) -> None:
        self._d = {d.id: d for d in devices}

    def get(self, device_id: str):
        return self._d.get(device_id)

    def all(self):
        return list(self._d.values())


def _app(tmp_path: Path, *devices: _Device) -> Flask:
    app = Flask(__name__)
    app.config["DEVICE_REGISTRY"] = _Registry(*devices)
    app.config["SETTINGS_STORE"] = SettingsStore(tmp_path / "settings.json")
    app.config["DEVICE_STATUS"] = {}
    app.config["MQTT_TRANSPORT"] = MagicMock()
    app.config["DEVICE_TELEMETRY"] = MagicMock()
    return app


def test_set_device_config_validates_persists_publishes_and_reprojects(tmp_path: Path) -> None:
    device = _Device("lounge", config_topic="tesserae/lounge/config")
    app = _app(tmp_path, device)
    assert ha_hooks._set_device_config(app, "lounge", {"sleep_interval_s": 1800}) is None
    # Merged doc (schema defaults + patch) was validated and stored.
    assert device.validated[-1]["sleep_interval_s"] == 1800
    assert device.validated[-1]["label"] == "x"
    stored = app.config["SETTINGS_STORE"].get_section("devices")["lounge"]
    assert stored["sleep_interval_s"] == 1800
    # MQTT devices get the config republished on their config topic.
    topic, payload = app.config["MQTT_TRANSPORT"].publish.call_args.args[:2]
    assert topic == "tesserae/lounge/config"
    assert json.loads(payload)["sleep_interval_s"] == 1800
    app.config["DEVICE_TELEMETRY"].reproject.assert_called_once_with("lounge", 1800)
    assert ha_hooks._device_config(app, "lounge")["sleep_interval_s"] == 1800
    assert ha_hooks._expected_interval(app, "lounge") == 1800


def test_set_device_config_rejects_invalid_and_unknown(tmp_path: Path) -> None:
    device = _Device("lounge")
    app = _app(tmp_path, device)
    err = ha_hooks._set_device_config(app, "lounge", {"sleep_interval_s": 5})
    assert err and "too fast" in err
    assert "devices" not in app.config["SETTINGS_STORE"].raw_section("devices") or (
        "lounge" not in app.config["SETTINGS_STORE"].get_section("devices")
    )
    assert ha_hooks._set_device_config(app, "nope", {"sleep_interval_s": 900})


def test_expected_interval_falls_back_to_schema_default(tmp_path: Path) -> None:
    app = _app(tmp_path, _Device("lounge"))
    assert ha_hooks._expected_interval(app, "lounge") == 900
    assert ha_hooks._expected_interval(app, "ghost") is None


def test_firmware_state_reports_capability_versions_and_progress(tmp_path: Path) -> None:
    app = _app(tmp_path, _Device("lounge"))
    app.config["SETTINGS_STORE"].update_section("app", {"online_features": False})
    release = MagicMock()
    release.get.return_value = {"fw_version": "1.30.1", "state": "canary"}
    app.config["OTA_RELEASE"] = release
    app.config["DEVICE_STATUS"]["lounge"] = {
        "received_at": 1.0,
        "parsed": {"fw_version": "1.29.0"},
        "ota_schema": 1,
        "ota": {"phase": "downloading"},
    }
    state = ha_hooks._firmware_state(app, "lounge")
    assert state == {
        "capable": True,
        "installed_version": "1.29.0",
        "latest_version": "1.30.1",
        "in_progress": True,
        "release_url": None,
    }


def test_firmware_state_uses_persisted_facts_when_cache_is_cold(tmp_path: Path) -> None:
    app = _app(tmp_path, _Device("lounge"))
    app.config["SETTINGS_STORE"].update_section("app", {"online_features": False})
    app.config["OTA_RELEASE"] = MagicMock(get=MagicMock(return_value=None))
    facts = MagicMock()
    facts.get.return_value = {"ota_schema": 1, "fw_version": "1.28.0"}
    app.config["DEVICE_FACTS"] = facts
    state = ha_hooks._firmware_state(app, "lounge")
    assert state is not None
    assert state["capable"] is True
    assert state["installed_version"] == "1.28.0"
    assert state["latest_version"] is None
    assert state["in_progress"] is False


def test_firmware_install_queues_device_on_kind_release(tmp_path: Path) -> None:
    app = _app(tmp_path, _Device("lounge"))
    app.config["SETTINGS_STORE"].update_section("app", {"online_features": False})
    release = MagicMock()
    release.get.return_value = {
        "fw_version": "1.30.1",
        "state": "canary",
        "descriptor": {"payload": "p", "signature": "s"},
        "canary_device_ids": ["other"],
    }
    app.config["OTA_RELEASE"] = release
    app.config["DEVICE_STATUS"]["lounge"] = {"ota_schema": 1, "parsed": {}}
    assert ha_hooks._firmware_install(app, "lounge") is None
    release.set_target.assert_called_once_with(
        "esp32",
        {"payload": "p", "signature": "s"},
        fw_version="1.30.1",
        canary_device_ids=["lounge", "other"],
    )


def test_firmware_install_refuses_non_ota_device(tmp_path: Path) -> None:
    app = _app(tmp_path, _Device("lounge"))
    app.config["OTA_RELEASE"] = MagicMock()
    err = ha_hooks._firmware_install(app, "lounge")
    assert err and "OTA" in err
    app.config["OTA_RELEASE"].set_target.assert_not_called()


def test_notify_renders_and_pushes_per_display(tmp_path: Path) -> None:
    app = _app(tmp_path, _Device("lounge"), _Device("hall"))
    push = MagicMock()
    push.push_image.return_value = MagicMock(status="sent", error=None)
    app.config["PUSH_MANAGER"] = push
    assert ha_hooks._notify(app, None, "Hello") is None
    assert push.push_image.call_count == 2
    kwargs = push.push_image.call_args.kwargs
    assert kwargs["source"] == "home_assistant"
    assert kwargs["device_id"] in ("lounge", "hall")
    assert Image.open(io.BytesIO(push.push_image.call_args.args[0])).size == (800, 480)
    push.push_image.reset_mock()
    assert ha_hooks._notify(app, "lounge", "Just you") is None
    assert push.push_image.call_args.kwargs["device_id"] == "lounge"
    assert ha_hooks._notify(app, "ghost", "x")


def test_build_ha_hooks_gates_firmware_on_release_store(tmp_path: Path) -> None:
    app = _app(tmp_path, _Device("lounge"))
    hooks = ha_hooks.build_ha_hooks(app)
    assert hooks.firmware_state_fn is None and hooks.firmware_install_fn is None
    assert hooks.settings_store is app.config["SETTINGS_STORE"]
    assert callable(hooks.notify_fn) and callable(hooks.set_device_config_fn)
    app.config["OTA_RELEASE"] = MagicMock()
    assert callable(ha_hooks.build_ha_hooks(app).firmware_install_fn)


# ---------- scheduler pause gate ----------


def _timer_deck() -> Deck:
    return Deck(
        id="d1",
        name="D",
        device_ids=["panel"],
        advance="timer",
        advance_interval_minutes=30,
        advance_anchor="00:00",
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
    )


def test_scheduler_paused_provider_skips_every_fire(tmp_path: Path) -> None:
    push = MagicMock()
    push.push.return_value = MagicMock(status="sent")
    push.device_in_quiet_hours.return_value = False
    push.warm_deck_page.return_value = True
    push.promote_deck_page.return_value = False
    deck_store = DeckStore(tmp_path / "decks.json")
    deck_store.upsert(_timer_deck())
    paused = {"value": True}
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=deck_store,
        deck_nav_store=MagicMock(get=MagicMock(return_value=None)),
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
        paused_provider=lambda: paused["value"],
    )
    assert scheduler.is_paused() is True
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    push.push.assert_not_called()
    paused["value"] = False
    assert scheduler.is_paused() is False
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    assert push.push.call_args[0][0] == "a"


def test_automation_pause_is_a_settings_field() -> None:
    field = next(f for f in APP_FIELDS if f["name"] == "automation_paused")
    assert field["type"] == "switch" and field["default"] is False
    group = next(g for g in APP_FIELD_GROUPS if g["id"] == "automation")
    assert group["master"] == "automation_paused"


# ---------- button service listener ----------


def _button_service() -> ButtonService:
    return ButtonService(
        rotation_store=MagicMock(all=MagicMock(return_value=[])),
        state_store=MagicMock(),
        settings_store=MagicMock(),
        page_store=MagicMock(),
        push_manager=None,
    )


def _result(**kw) -> ButtonHandleResult:
    base = dict(
        device_id="lounge",
        dedup=False,
        unmapped=False,
        action_spec="rotate_next",
        action_description=None,
        rotation_id=None,
        step_index=0,
        step_page_id="home",
        step_count=1,
        manual_override=False,
        override_until=None,
        pushed_page_id="weather",
        push_result=None,
    )
    base.update(kw)
    return ButtonHandleResult(**base)


def test_button_listener_receives_button_and_touch_events() -> None:
    svc = _button_service()
    seen: list[dict] = []
    svc.add_listener(seen.append)
    svc._emit_button("lounge", "left", _result())
    svc._emit_button("lounge", "left", _result(dedup=True))  # dropped
    svc._emit_button("lounge", "zz", _result(unmapped=True, pushed_page_id=None))
    svc._emit_touch(
        "lounge",
        TouchHandleResult(
            outcome="dispatched",
            gesture="swipe_left",
            base=_result(),
            action_spec="rotate_next",
        ),
    )
    svc._emit_touch(
        "lounge",
        TouchHandleResult(outcome="dispatched", gesture="slide", base=_result(), value=40),
    )
    assert [e["kind"] for e in seen] == ["button", "button", "swipe", "slide"]
    assert seen[0] == {
        "device_id": "lounge",
        "kind": "button",
        "name": "left",
        "outcome": "pushed",
        "action": "rotate_next",
        "page_id": "weather",
    }
    assert seen[1]["outcome"] == "unmapped"
    assert seen[3]["value"] == 40
    svc.remove_listener(seen.append)
    svc._emit_button("lounge", "left", _result())
    assert len(seen) == 4


def test_button_listener_failure_is_swallowed() -> None:
    svc = _button_service()

    def boom(_event: dict) -> None:
        raise RuntimeError("nope")

    svc.add_listener(boom)
    svc._emit_button("lounge", "left", _result())  # must not raise


# ---------- deck store listener ----------


def test_deck_store_notifies_listeners_on_upsert_and_delete(tmp_path: Path) -> None:
    store = DeckStore(tmp_path / "decks.json")
    calls: list[str] = []
    store.add_listener(lambda: calls.append("hit"))
    store.upsert(_timer_deck())
    assert calls == ["hit"]
    assert store.delete("d1") is True
    assert calls == ["hit", "hit"]
    assert store.delete("d1") is False
    assert calls == ["hit", "hit"]
    store.remove_listener(calls.append)  # unknown callback is a no-op
