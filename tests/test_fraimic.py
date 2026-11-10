"""Fraimic publisher: wake-window probing, schedule sync, upload, sleep.

The frame's REST API is replaced by a fake that records calls; the clock is
a variable the test moves, so wake windows can be stepped through.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import device_service, fraimic
from app.fraimic import FraimicClient, FraimicError, FraimicPublisher
from app.main import REPO_ROOT, create_app

T0 = datetime(2026, 11, 10, 5, 59, 0, tzinfo=UTC).timestamp()


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).astimezone().isoformat()


class FakeFrame:
    def __init__(self) -> None:
        self.awake = True
        self.charging = False
        self.keep_awake = False
        self.calls: list[tuple[Any, ...]] = []
        self.schedule: dict[str, Any] = {
            "enabled": False,
            "times": [],
            "interval_minutes": None,
            "weekdays": list(fraimic.ALL_DAYS),
            "max_idle": 120,
            "time_valid": True,
            "next_wakes": [],
        }
        self.next_wakes: list[float] = []
        self.uploads: list[bytes] = []
        self.upload_error: FraimicError | None = None
        self.has_schedule_api = True

    def info(self) -> dict[str, Any]:
        return {
            "firmware_version": "0.2.31",
            "wifi": {"rssi": -52, "ip": "192.0.2.42"},
            "battery": {"percent": 78, "voltage_mv": 7948, "charging": self.charging},
            "settings": {"keep_awake": self.keep_awake},
        }


class FakeClient(FraimicClient):
    def __init__(self, frame: FakeFrame, host: str) -> None:
        self.frame, self.host = frame, host

    def info(self) -> dict[str, Any] | None:
        self.frame.calls.append(("info",))
        return self.frame.info() if self.frame.awake else None

    def get_schedule(self) -> dict[str, Any]:
        self.frame.calls.append(("get_schedule",))
        if not self.frame.has_schedule_api:
            raise FraimicError(404, "not_found")
        return {**self.frame.schedule, "next_wakes": [_iso(w) for w in self.frame.next_wakes]}

    def set_schedule(self, schedule: dict[str, Any]) -> dict[str, Any]:
        self.frame.calls.append(("set_schedule", schedule))
        mode = (
            {"times": schedule["times"], "interval_minutes": None}
            if "times" in schedule
            else {
                "times": [],
                "interval_minutes": schedule["interval_minutes"],
            }
        )
        self.frame.schedule.update({**schedule, **mode})
        return {**self.frame.schedule, "next_wakes": [_iso(w) for w in self.frame.next_wakes]}

    def upload(self, data: bytes) -> dict[str, Any]:
        self.frame.calls.append(("upload", len(data)))
        if self.frame.upload_error is not None:
            raise self.frame.upload_error
        self.frame.uploads.append(data)
        return {"status": "rendering", "bytes_received": len(data)}

    def sleep(self) -> dict[str, Any]:
        self.frame.calls.append(("sleep",))
        self.frame.awake = False
        return {"status": "sleeping"}


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _make_frame(app: Flask, device_id: str = "hall", **cfg: Any) -> Any:
    with app.app_context():
        result = device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id=device_id,
            kind_id="fraimic",
            name="Hall Fraimic",
        )
    assert result.ok, result.error
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    entry = dict(section.get(device_id) or {})
    entry.update({"host": "192.0.2.42", **cfg})
    app.config["SETTINGS_STORE"].patch_section("devices", {device_id: entry})
    return result.device


def _plant(app: Flask, device_id: str, digest: str) -> None:
    (app.config["RENDERS_DIR"] / f"{digest}.bin").write_bytes(b"\x11" * 64)
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = {"digest": digest, "ext": "bin"}


def _publisher(app: Flask, frame: FakeFrame, clock: Clock) -> FraimicPublisher:
    return FraimicPublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=app.config["PUSH_MANAGER"].latest_render_for,
        client_factory=lambda host: FakeClient(frame, host),
        clock=clock,
    )


def _names(frame: FakeFrame) -> list[str]:
    return [c[0] for c in frame.calls]


def test_asleep_frame_only_probed(app: Flask) -> None:
    _make_frame(app)
    _plant(app, "hall", "d1")
    frame, clock = FakeFrame(), Clock()
    frame.awake = False
    _publisher(app, frame, clock).tick()
    assert _names(frame) == ["info"]


def test_tapped_awake_frame_gets_schedule_and_frame_but_stays_awake(app: Flask) -> None:
    _make_frame(app)
    _plant(app, "hall", "d1")
    frame, clock = FakeFrame(), Clock()
    pub = _publisher(app, frame, clock)
    pub.tick()
    assert _names(frame) == ["info", "get_schedule", "set_schedule", "upload"]
    sent = frame.calls[2][1]
    assert sent["interval_minutes"] == 60 and sent["max_idle"] == 120 and sent["enabled"]
    status = app.config["DEVICE_STATUS"]["hall"]["parsed"]
    assert (
        status["battery_pct"] == 78 and status["rssi"] == -52 and status["fw_version"] == "0.2.31"
    )
    # Same frame again: no second upload, and the schedule already matches.
    frame.calls.clear()
    clock.now += fraimic.IDLE_POLL_S
    pub.tick()
    assert _names(frame) == ["info", "get_schedule"]


def test_probe_cadence_idle_then_hunting(app: Flask) -> None:
    device = _make_frame(app)
    frame, clock = FakeFrame(), Clock()
    frame.awake = False
    pub = _publisher(app, frame, clock)
    wake = T0 + 600
    pub.update_state(device, {"next_wakes": [wake]})
    pub.tick()
    clock.now += 30
    pub.tick()  # idle: 60 s between probes
    assert _names(frame) == ["info"]
    clock.now = wake - fraimic.HUNT_LEAD_S
    pub.tick()
    clock.now += fraimic.HUNT_POLL_S
    pub.tick()  # hunting: every few seconds
    assert _names(frame) == ["info", "info", "info"]


def test_scheduled_wake_uploads_then_sleeps(app: Flask) -> None:
    device = _make_frame(app)
    _plant(app, "hall", "d1")
    frame, clock = FakeFrame(), Clock()
    wake = T0 + 60
    frame.schedule.update({"enabled": True, "interval_minutes": 60})
    frame.next_wakes = [wake, wake + 3600]
    pub = _publisher(app, frame, clock)
    pub.update_state(device, {"next_wakes": [wake, wake + 3600]})
    clock.now = wake + 2
    pub.tick()
    assert _names(frame) == ["info", "get_schedule", "upload", "sleep"]
    state = pub.load_state(device)
    assert state["last_digest"] == "d1" and wake in state["handled_wakes"]
    assert app.config["DEVICE_STATUS"]["hall"]["parsed"]["awake"] is False


def test_scheduled_wake_with_nothing_new_sleeps_at_once(app: Flask) -> None:
    device = _make_frame(app)
    frame, clock = FakeFrame(), Clock()
    frame.schedule.update({"enabled": True, "interval_minutes": 60})
    pub = _publisher(app, frame, clock)
    pub.update_state(device, {"next_wakes": [T0]})
    pub.tick()
    assert _names(frame) == ["info", "get_schedule", "sleep"]


@pytest.mark.parametrize("attr", ["charging", "keep_awake"])
def test_frame_kept_awake_is_not_slept(app: Flask, attr: str) -> None:
    device = _make_frame(app)
    _plant(app, "hall", "d1")
    frame, clock = FakeFrame(), Clock()
    setattr(frame, attr, True)
    pub = _publisher(app, frame, clock)
    pub.update_state(device, {"next_wakes": [T0]})
    pub.tick()
    assert "sleep" not in _names(frame)


def test_sleep_after_upload_off(app: Flask) -> None:
    device = _make_frame(app, sleep_after_upload=False)
    frame, clock = FakeFrame(), Clock()
    pub = _publisher(app, frame, clock)
    pub.update_state(device, {"next_wakes": [T0]})
    pub.tick()
    assert "sleep" not in _names(frame)


def test_times_mode_and_manual_mode(app: Flask) -> None:
    _make_frame(app, "hall_a", wake_mode="times", wake_times="18:00, 06:00")
    _make_frame(app, "hall_b", wake_mode="manual")
    frame, clock = FakeFrame(), Clock()
    _publisher(app, frame, clock).tick()
    sets = [c[1] for c in frame.calls if c[0] == "set_schedule"]
    assert len(sets) == 1 and sets[0]["times"] == ["06:00", "18:00"]
    assert "interval_minutes" not in sets[0]


def test_push_reaches_an_awake_frame_straight_away(app: Flask) -> None:
    _make_frame(app)
    frame, clock = FakeFrame(), Clock()
    pub = _publisher(app, frame, clock)
    pub.tick()
    _plant(app, "hall", "d2")
    clock.now += 5  # well inside the idle gap
    pub.on_push()
    assert pub.wakeup.is_set()
    pub.tick()
    assert ("upload", 64) in frame.calls


def test_busy_frame_retried_next_tick(app: Flask) -> None:
    device = _make_frame(app)
    _plant(app, "hall", "d1")
    frame, clock = FakeFrame(), Clock()
    frame.upload_error = FraimicError(503, "buffer_not_ready")
    pub = _publisher(app, frame, clock)
    pub.tick()
    assert "last_digest" not in pub.load_state(device)
    frame.upload_error = None
    clock.now += fraimic.TICK_S
    pub.tick()
    assert pub.load_state(device)["last_digest"] == "d1"


def test_old_firmware_without_schedule_still_gets_frames(app: Flask) -> None:
    device = _make_frame(app)
    _plant(app, "hall", "d1")
    frame, clock = FakeFrame(), Clock()
    frame.has_schedule_api = False
    pub = _publisher(app, frame, clock)
    pub.tick()
    assert pub.load_state(device)["last_digest"] == "d1"


def test_schedule_helpers() -> None:
    current = {
        "enabled": True,
        "times": [],
        "interval_minutes": 60,
        "weekdays": list(fraimic.ALL_DAYS),
        "max_idle": 120,
    }
    desired = {
        "enabled": True,
        "interval_minutes": 60,
        "weekdays": fraimic.ALL_DAYS,
        "max_idle": 120,
    }
    assert fraimic.schedule_matches(current, desired)
    assert not fraimic.schedule_matches({**current, "interval_minutes": 120}, desired)
    assert not fraimic.schedule_matches({**current, "enabled": False}, desired)
    wakes = fraimic.wake_epochs(
        {"next_wakes": ["2026-11-03T06:00:00-05:00", "junk", "2026-11-03T06:00:00"]}
    )
    assert wakes == [datetime(2026, 11, 3, 11, 0, tzinfo=UTC).timestamp()]


def test_client_reports_frame_errors() -> None:
    def opener(req: Any, timeout: float) -> Any:
        assert req.full_url == "http://192.0.2.42/api/image"
        assert req.get_header("Content-type") == "application/octet-stream"
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error": "invalid_image_size"}'),  # type: ignore[arg-type]
        )

    with pytest.raises(FraimicError) as exc:
        FraimicClient("192.0.2.42", opener=opener).upload(b"x")
    assert exc.value.code == "invalid_image_size" and exc.value.status == 400

    def asleep(req: Any, timeout: float) -> Any:
        raise urllib.error.URLError("timed out")

    assert FraimicClient("192.0.2.42", opener=asleep).info() is None


def test_device_contract(app: Flask) -> None:
    device = _make_frame(app)
    ok, _ = device.validate_config({"host": "192.168.50.42"})
    assert ok
    assert not device.validate_config({"wake_interval_minutes": "100"})[0]
    assert not device.validate_config({"wake_mode": "times", "wake_times": "6am"})[0]
    assert not device.validate_config({"wake_times": "01:00,02:00,03:00,04:00,05:00"})[0]
    assert device.validate_config({"wake_mode": "times", "wake_times": "06:00, 18:30"})[0]
    parsed = device.parse_status(json.dumps(FakeFrame().info()).encode())
    assert parsed["battery_pct"] == 78 and "battery_mv" not in parsed


def test_badge_reads_lan(app: Flask) -> None:
    from app.settings.index_routes import _transport_badge

    assert _transport_badge(_make_frame(app)) == "LAN"


def test_next_wake_in_status(app: Flask) -> None:
    _make_frame(app)
    frame, clock = FakeFrame(), Clock()
    frame.next_wakes = [T0 + timedelta(hours=1).total_seconds()]
    _publisher(app, frame, clock).tick()
    assert app.config["DEVICE_STATUS"]["hall"]["parsed"]["next_wake"].startswith("2026-11-10T06:59")
