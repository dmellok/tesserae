"""Fraimic publisher: wake-window probing, schedule sync, upload, sleep,
chained wakes, telemetry, taps.

The frame's REST API is replaced by a fake that records calls; the clock is
a variable the test moves, so wake windows can be stepped through. The fake
keeps its own clock an hour behind UTC+0... no: at ``OFFSET_S`` from UTC,
as a real frame keeps a fixed offset that need not be the app's timezone.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import device_service, fraimic, fraimic_wakes
from app.fraimic import FraimicClient, FraimicError, FraimicPublisher
from app.main import REPO_ROOT, create_app

T0 = datetime(2026, 11, 10, 5, 59, 0, tzinfo=UTC).timestamp()
OFFSET_S = 10 * 3600  # the fake frame's clock: UTC+10
FRAME_TZ = timezone(timedelta(seconds=OFFSET_S))


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


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, FRAME_TZ).isoformat()


class FakeFrame:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock
        self.boot = T0 - 5.0  # woke five seconds before the test starts
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

        self.percent = 78
        self.refreshes = 0

    def upcoming(self) -> list[float]:
        """What the frame reports as ``next_wakes``: for a schedule of
        times, each one's next occurrence on the frame's clock."""
        if not (self.clock is not None and self.schedule.get("times")):
            return self.next_wakes
        now = datetime.fromtimestamp(self.clock.now, FRAME_TZ)
        out = []
        for raw in self.schedule["times"]:
            hour, minute = map(int, raw.split(":"))
            at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            out.append((at if at > now else at + timedelta(days=1)).timestamp())
        return sorted(out)

    def wake(self, at: float) -> None:
        """The frame boots at ``at`` (a scheduled wake or a tap)."""
        self.boot, self.awake = at, True

    def info(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "firmware_version": "0.2.31",
            "wifi": {"rssi": -52, "ip": "192.0.2.42"},
            "battery": {"percent": self.percent, "voltage_mv": 7948, "charging": self.charging},
            "settings": {"keep_awake": self.keep_awake},
        }
        if self.clock is not None:
            local = datetime.fromtimestamp(self.clock.now, FRAME_TZ).replace(tzinfo=None)
            out["device"] = {
                "time_synced": True,
                "local_time": local.isoformat(timespec="seconds"),
                "uptime_s": int(self.clock.now - self.boot),
            }
        return out


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
        return {**self.frame.schedule, "next_wakes": [_iso(w) for w in self.frame.upcoming()]}

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
        return {**self.frame.schedule, "next_wakes": [_iso(w) for w in self.frame.upcoming()]}

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

    def refresh(self) -> dict[str, Any]:
        self.frame.calls.append(("refresh",))
        self.frame.refreshes += 1
        return {"status": "refresh_started"}


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


def _run(pub: FraimicPublisher, clock: Clock, seconds: float, step: float = 3.0) -> None:
    """Tick the publisher through ``seconds`` of clock time."""
    end = clock.now + seconds
    while clock.now <= end:
        pub.tick()
        clock.now += step


def _sets(frame: FakeFrame) -> list[dict[str, Any]]:
    return [c[1] for c in frame.calls if c[0] == "set_schedule"]


def _hhmm(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, FRAME_TZ).strftime("%H:%M")


def test_asleep_frame_only_probed(app: Flask) -> None:
    _make_frame(app)
    _plant(app, "hall", "d1")
    clock = Clock()
    frame = FakeFrame(clock)
    frame.awake = False
    _publisher(app, frame, clock).tick()
    assert _names(frame) == ["info"]


def test_tapped_awake_frame_gets_frame_and_schedule_but_stays_awake(app: Flask) -> None:
    _make_frame(app)
    _plant(app, "hall", "d1")
    clock = Clock()
    frame = FakeFrame(clock)
    pub = _publisher(app, frame, clock)
    _run(pub, clock, 30)
    assert _names(frame)[:3] == ["info", "get_schedule", "upload"]
    sent = _sets(frame)
    assert len(sent) == 1
    assert sent[0]["interval_minutes"] == 60 and sent[0]["max_idle"] == 120 and sent[0]["enabled"]
    assert "sleep" not in _names(frame)
    status = app.config["DEVICE_STATUS"]["hall"]["parsed"]
    assert (
        status["battery_pct"] == 78 and status["rssi"] == -52 and status["fw_version"] == "0.2.31"
    )
    # Settled: back to the idle probe, no second upload, schedule matches.
    frame.calls.clear()
    clock.now += fraimic.IDLE_POLL_S
    pub.tick()
    assert _names(frame) == ["info", "get_schedule"]


def test_probe_cadence_idle_then_hunting(app: Flask) -> None:
    device = _make_frame(app)
    clock = Clock()
    frame = FakeFrame(clock)
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


def _scheduled(app: Flask, **cfg: Any) -> tuple[Any, FakeFrame, Clock, FraimicPublisher, float]:
    """A frame on the hourly schedule that boots at a wake Tesserae has on
    record, first seen eight seconds later (as a real one is)."""
    device = _make_frame(app, **cfg)
    clock = Clock()
    frame = FakeFrame(clock)
    wake = T0 + 60
    frame.schedule.update({"enabled": True, "interval_minutes": 60})
    frame.next_wakes = [wake + 3600, wake + 7200]
    pub = _publisher(app, frame, clock)
    pub.update_state(device, {"next_wakes": [wake, wake + 3600], "boot": 1})
    frame.wake(wake + 1)
    clock.now = wake + 9
    return device, frame, clock, pub, wake


def test_scheduled_wake_uploads_then_sleeps_once_it_has_been_up_long_enough(app: Flask) -> None:
    device, frame, clock, pub, wake = _scheduled(app)
    _plant(app, "hall", "d1")
    pub.tick()
    assert _names(frame) == ["info", "get_schedule", "upload"]  # up 8 s: too early to sleep
    _run(pub, clock, 60)
    assert _names(frame).count("upload") == 1 and _names(frame)[-1] == "sleep"
    slept_at = next(i for i, c in enumerate(frame.calls) if c[0] == "sleep")
    assert _names(frame)[:slept_at].count("info") >= 2
    state = pub.load_state(device)
    assert state["last_digest"] == "d1" and wake in state["handled_wakes"]
    assert app.config["DEVICE_STATUS"]["hall"]["parsed"]["awake"] is False


def test_scheduled_wake_with_nothing_new_still_stays_up_before_sleeping(app: Flask) -> None:
    _device, frame, clock, pub, wake = _scheduled(app)
    while "sleep" not in _names(frame):
        pub.tick()
        clock.now += fraimic.HUNT_POLL_S
        assert clock.now < wake + 120
    assert clock.now - fraimic.HUNT_POLL_S - frame.boot >= fraimic.MIN_AWAKE_S
    assert "upload" not in _names(frame) and "set_schedule" not in _names(frame)


@pytest.mark.parametrize("attr", ["charging", "keep_awake"])
def test_frame_kept_awake_is_not_slept(app: Flask, attr: str) -> None:
    _device, frame, clock, pub, _wake = _scheduled(app)
    _plant(app, "hall", "d1")
    setattr(frame, attr, True)
    _run(pub, clock, 90)
    assert "sleep" not in _names(frame)
    assert app.config["DEVICE_TELEMETRY"].get("hall").always_on is True


def test_sleep_after_upload_off(app: Flask) -> None:
    _device, frame, clock, pub, _wake = _scheduled(app, sleep_after_upload=False)
    _run(pub, clock, 90)
    assert "sleep" not in _names(frame)


def test_times_mode_is_written_in_the_frames_clock_and_manual_is_left_alone(app: Flask) -> None:
    _make_frame(app, "hall_a", wake_mode="times", wake_times="18:00, 06:00")
    _make_frame(app, "hall_b", wake_mode="manual")
    clock = Clock()
    frame = FakeFrame(clock)
    pub = _publisher(app, frame, clock)
    _run(pub, clock, 30)
    sets = _sets(frame)
    assert len(sets) == 1 and "interval_minutes" not in sets[0]
    with app.app_context():
        from app.tz_resolve import app_timezone

        tz = app_timezone()
    expected = fraimic_wakes.to_frame_times(["06:00", "18:00"], now=T0, tz=tz, offset_s=OFFSET_S)
    assert sets[0]["times"] == expected


def test_push_reaches_an_awake_frame_straight_away(app: Flask) -> None:
    _make_frame(app)
    clock = Clock()
    frame = FakeFrame(clock)
    pub = _publisher(app, frame, clock)
    _run(pub, clock, 30)
    _plant(app, "hall", "d2")
    clock.now += 5  # well inside the idle gap
    pub.on_push()
    assert pub.wakeup.is_set()
    pub.tick()
    assert ("upload", 64) in frame.calls


def test_busy_frame_retried_next_tick(app: Flask) -> None:
    device = _make_frame(app)
    _plant(app, "hall", "d1")
    clock = Clock()
    frame = FakeFrame(clock)
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
    clock = Clock()
    frame = FakeFrame(clock)
    frame.has_schedule_api = False
    pub = _publisher(app, frame, clock)
    pub.tick()
    assert pub.load_state(device)["last_digest"] == "d1"


# -- chained wakes ---------------------------------------------------------


def test_short_interval_is_led_along_by_a_chain(app: Flask) -> None:
    """Every wake writes the next four times, then sleeps; the wake after
    lands on the first of them and does the same."""
    device = _make_frame(app, wake_interval_minutes="5")
    clock = Clock()
    frame = FakeFrame(clock)
    pub = _publisher(app, frame, clock)
    _run(pub, clock, 30)  # a tap: the first chain goes down, no sleep
    first = _sets(frame)[-1]
    assert len(first["times"]) == 4 and "interval_minutes" not in first
    assert "sleep" not in _names(frame)
    wakes = frame.upcoming()
    assert wakes[1] - wakes[0] == 300 and wakes[0] - clock.now >= 120
    frame.awake = False
    seen = []
    for _ in range(6):  # more wakes than the frame's four slots
        wake = frame.upcoming()[0]
        frame.calls.clear()
        clock.now = wake - 10
        pub.tick()
        frame.wake(wake + 2)
        clock.now = wake + 10
        _run(pub, clock, 80)
        names = [n for n in _names(frame) if n != "info"]
        # Nothing is written or slept until the frame has been up a while,
        # then the next chain goes down and the sleep follows it directly.
        assert names[-2:] == ["set_schedule", "sleep"] and names.count("set_schedule") == 1, names
        assert names.count("get_schedule") >= 5 and not frame.awake
        seen.append(wake)
        assert frame.upcoming()[0] == wake + 300
    assert len(seen) == 6
    assert pub.load_state(device)["next_wakes"][0] == frame.upcoming()[0]


def test_scheduled_wakes_earn_trust_and_a_tap_does_not_lose_it(app: Flask) -> None:
    _make_frame(app, wake_interval_minutes="5")
    clock = Clock(datetime.now(UTC).timestamp())
    frame = FakeFrame(clock)
    frame.boot = clock.now - 5
    pub = _publisher(app, frame, clock)
    telemetry = app.config["DEVICE_TELEMETRY"]
    _run(pub, clock, 30)
    entry = telemetry.get("hall")
    assert entry is not None and entry.prediction_source == "server"
    assert entry.predicted_next_wake_at == frame.upcoming()[0]
    assert entry.consecutive_on_time_wakes == 0
    frame.awake = False
    for expected in (1, 2, 3):
        wake = frame.upcoming()[0]
        pub.tick()
        # Telemetry stamps a heartbeat with the wall clock, not the test's:
        # replay the wake as happening now by moving the prediction onto it,
        # and age the last beat past the same-wake debounce.
        telemetry.note_server_directive("hall", wake_at=datetime.now(UTC).timestamp(), interval_s=1)
        telemetry._state.devices["hall"].last_heartbeat_at -= 300
        frame.wake(wake + 1)
        clock.now = wake + 9
        _run(pub, clock, 80)
        entry = telemetry.get("hall")
        assert not frame.awake and entry.consecutive_on_time_wakes == expected
        assert entry.predicted_next_wake_at == frame.upcoming()[0]
    assert telemetry.get("hall").is_trusted
    # A tap, nowhere near a predicted wake: the schedule moves on, trust stays.
    pub.tick()
    clock.now += 40
    frame.wake(clock.now - 6)
    _run(pub, clock, 30)
    entry = telemetry.get("hall")
    assert entry.consecutive_on_time_wakes == 3
    assert entry.predicted_next_wake_at == frame.upcoming()[0]


def test_chain_skips_quiet_hours(app: Flask) -> None:
    _make_frame(app, wake_interval_minutes="30")
    clock = Clock()
    frame = FakeFrame(clock)
    with app.app_context():
        from app.tz_resolve import app_timezone

        tz = app_timezone()
    local = datetime.fromtimestamp(T0, tz)
    start = (local + timedelta(minutes=45)).strftime("%H:%M")
    end = (local + timedelta(hours=3)).strftime("%H:%M")
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {"quiet_hours_enabled": True, "quiet_hours_start": start, "quiet_hours_end": end},
    )
    pub = _publisher(app, frame, clock)
    _run(pub, clock, 30)
    wakes = frame.upcoming()
    quiet_from = T0 + 45 * 60
    quiet_to = T0 + 3 * 3600
    assert wakes and all(not (quiet_from <= w <= quiet_to) for w in wakes)
    assert any(w > quiet_to for w in wakes)


def test_tap_steps_the_display_once_when_asked(app: Flask) -> None:
    _device, frame, clock, pub, wake = _scheduled(app, tap_action="next")
    steps: list[dict[str, Any]] = []

    class Buttons:
        def step(self, **kw: Any) -> None:
            steps.append(kw)

    app.config["BUTTON_SERVICE"] = Buttons()
    _run(pub, clock, 60)  # the scheduled wake: not a tap
    assert steps == [] and not frame.awake
    pub.tick()
    frame.calls.clear()
    clock.now = wake + 900
    frame.wake(clock.now - 7)
    _run(pub, clock, 40)
    assert len(steps) == 1
    assert steps[0]["device_id"] == "hall" and steps[0]["forward"] and "sleep" not in _names(frame)


def test_tap_does_nothing_extra_by_default(app: Flask) -> None:
    _device, frame, clock, pub, wake = _scheduled(app)
    steps: list[Any] = []

    class Buttons:
        def step(self, **kw: Any) -> None:
            steps.append(kw)

    app.config["BUTTON_SERVICE"] = Buttons()
    _run(pub, clock, 60)
    pub.tick()
    clock.now = wake + 900
    frame.wake(clock.now - 7)
    _run(pub, clock, 40)
    assert steps == []


def test_unchanged_picture_is_redrawn_after_the_period(app: Flask) -> None:
    device, frame, clock, pub, _wake = _scheduled(app, redraw="daily")
    pub.update_state(device, {"last_upload": int(clock.now - 90000), "last_digest": "d1"})
    _plant(app, "hall", "d1")
    _run(pub, clock, 60)
    assert frame.refreshes == 1 and _names(frame)[-1] == "sleep"
    assert pub.load_state(device)["last_redraw"] >= int(clock.now - 80)


def test_missed_wake_is_counted_and_reported(app: Flask) -> None:
    device = _make_frame(app)
    clock = Clock()
    frame = FakeFrame(clock)
    frame.awake = False
    pub = _publisher(app, frame, clock)
    pub.update_state(device, {"next_wakes": [T0 + 60], "boot": 1})
    clock.now = T0 + 60 + 400
    pub.tick()
    assert pub.load_state(device)["missed_wakes"] == 1
    pub.tick()
    assert pub.load_state(device)["missed_wakes"] == 1  # counted once
    clock.now += 600
    frame.wake(clock.now - 6)
    pub.tick()
    assert app.config["DEVICE_STATUS"]["hall"]["parsed"]["missed_wakes"] == 1
    assert "missed_wakes" not in pub.load_state(device)


# -- planning (pure) ---------------------------------------------------------


def _plan(cfg: dict[str, Any], **kw: Any) -> fraimic_wakes.WakePlan:
    kw.setdefault("now", T0)
    kw.setdefault("tz", UTC)
    kw.setdefault("offset_s", 0)
    return fraimic_wakes.plan_wakes(cfg, **kw)


def test_plan_uses_the_frames_own_schedule_where_it_fits() -> None:
    hourly = _plan({})
    assert hourly.body is not None and hourly.body["interval_minutes"] == 60
    assert not hourly.chained
    # An hour's skew between the clocks leaves an hourly grid where it was...
    assert not _plan({}, offset_s=-3600).chained
    # ...but moves a six-hourly one, so that is chained onto the app's clock.
    six = _plan({"wake_interval_minutes": "360"}, offset_s=-3600)
    assert six.chained and six.body is not None
    assert six.body["times"] == sorted(fraimic_wakes.frame_hhmm(w, -3600) for w in six.wakes)
    assert (
        datetime.fromtimestamp(six.wakes[0], UTC).strftime("%H:%M") == "12:00"
    )  # 06:00 is a minute off: too soon
    four = _plan({"wake_mode": "times", "wake_times": "06:00,12:00"}, offset_s=7200)
    assert not four.chained and four.body is not None and four.body["times"] == ["08:00", "14:00"]
    assert _plan({"wake_mode": "manual"}).body is None


def test_plan_chains_what_the_frame_cannot_express() -> None:
    quick = _plan({"wake_interval_minutes": "10"})
    assert quick.chained and len(quick.wakes) == 4
    assert quick.wakes[0] - T0 >= fraimic_wakes.MIN_FIRST_GAP_S
    assert [b - a for a, b in zip(quick.wakes, quick.wakes[1:], strict=False)] == [600] * 3
    anchored = _plan({"wake_interval_minutes": "60", "wake_anchor": "00:20"})
    assert anchored.chained
    assert datetime.fromtimestamp(anchored.wakes[0], UTC).minute == 20
    many = _plan({"wake_mode": "times", "wake_times": "06:10,06:20,06:30,06:40,06:50,07:00"})
    assert many.chained and many.body is not None
    assert many.body["times"] == ["06:10", "06:20", "06:30", "06:40"]
    waiting = _plan({"wake_interval_minutes": "10"}, offset_s=None)
    assert waiting.body is None and waiting.needs_clock


def _gap(plan: fraimic_wakes.WakePlan) -> float:
    return plan.wakes[1] - plan.wakes[0]


def test_plan_wakes_less_often_on_a_low_battery() -> None:
    normal = _plan({"wake_interval_minutes": "30"}, battery_pct=50)
    low = _plan({"wake_interval_minutes": "30"}, battery_pct=15)
    flat = _plan({"wake_interval_minutes": "30"}, battery_pct=5)
    off = _plan({"wake_interval_minutes": "30", "low_battery_stretch": False}, battery_pct=5)
    assert (_gap(normal), _gap(low), _gap(flat), _gap(off)) == (1800, 3600, 7200, 1800)
    assert _plan({}, battery_pct=15).chained  # hourly becomes two-hourly: no longer the frame's own


def test_plan_aims_at_the_next_changes() -> None:
    change = T0 + 47 * 60 + 20
    plan = _plan({"wake_mode": "changes"}, changes=[T0 + 30, change, change + 30])
    assert plan.chained
    assert plan.wakes[0] == T0 + 47 * 60  # the minute the change falls in; the one 30 s out is past
    assert T0 + 30 not in plan.wakes and len(plan.wakes) <= 4
    assert datetime.fromtimestamp(plan.wakes[1], UTC).strftime("%H:%M") == "12:00"  # the ceiling
    assert len(_plan({"wake_mode": "changes"}).wakes) >= 1


def test_plan_follows_a_page_that_declares_its_interval() -> None:
    plan = _plan({}, page_interval_s=60)
    assert plan.chained and plan.wakes[1] - plan.wakes[0] == fraimic_wakes.PAGE_MIN_INTERVAL_S


def test_schedule_clock_tells_a_scheduled_wake_from_a_tap() -> None:
    held = {"enabled": True, "times": ["06:05", "06:10"], "interval_minutes": None}
    at = datetime(2026, 11, 10, 6, 5, 2, tzinfo=UTC).timestamp()
    assert fraimic_wakes.on_schedule_clock(held, at, 0)
    assert fraimic_wakes.on_schedule_clock(held, at - 3600, 3600)
    assert not fraimic_wakes.on_schedule_clock(held, at + 180, 0)
    assert not fraimic_wakes.on_schedule_clock(held, at, None)
    hourly = {"enabled": True, "times": [], "interval_minutes": 60}
    assert fraimic_wakes.on_schedule_clock(hourly, at - 5 * 60 + 1, 0)
    assert not fraimic_wakes.on_schedule_clock(hourly, at + 600, 0)


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
    assert device.validate_config({"wake_times": "01:00,02:00,03:00,04:00,05:00"})[0]
    assert device.validate_config({"wake_mode": "changes", "wake_ceiling_minutes": "360"})[0]
    assert device.validate_config({"wake_interval_minutes": "5", "wake_anchor": "00:20"})[0]
    assert not device.validate_config({"wake_anchor": "20 past"})[0]
    assert not device.validate_config({"tap_action": "explode"})[0]
    assert not device.validate_config({"redraw": "hourly"})[0]
    assert device.validate_config({"wake_mode": "times", "wake_times": "06:00, 18:30"})[0]
    parsed = device.parse_status(json.dumps(FakeFrame().info()).encode())
    assert parsed["battery_pct"] == 78 and "battery_mv" not in parsed
    assert parsed["stays_awake"] is False
    charging = FakeFrame()
    charging.charging = True
    assert device.parse_status(json.dumps(charging.info()).encode())["stays_awake"] is True


def test_badge_reads_lan(app: Flask) -> None:
    from app.settings.index_routes import _transport_badge

    assert _transport_badge(_make_frame(app)) == "LAN"


def test_next_wake_in_status(app: Flask) -> None:
    _make_frame(app)
    clock = Clock()
    frame = FakeFrame(clock)
    frame.next_wakes = [T0 + timedelta(hours=1).total_seconds()]
    _publisher(app, frame, clock).tick()
    assert app.config["DEVICE_STATUS"]["hall"]["parsed"]["next_wake"].startswith("2026-11-10T06:59")


def test_catalog_models_are_fraimic_frames_with_their_own_panel(app: Flask) -> None:
    """A frame added as a catalog model (hardware/fraimic/*.json) is driven
    like one added as the generic kind, and starts on its own panel size."""
    with app.app_context():
        result = device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id="study",
            kind_id="fraimic_standard_canvas",
            name="Study Fraimic",
        )
    assert result.ok, result.error
    device = result.device
    assert fraimic.is_fraimic_device(device)
    assert (device.panel["w"], device.panel["h"]) == (1200, 1600)
    clock = Clock()
    frame = FakeFrame(clock)
    pub = _publisher(app, frame, clock)
    assert [d.id for d in pub._frames()] == ["study"]
    kinds = [d.id for d in fraimic.fraimic_kinds(app.config["DEVICE_REGISTRY"])]
    assert kinds == ["fraimic", "fraimic_large_canvas", "fraimic_standard_canvas"]
    client = app.test_client()
    with client.session_transaction() as session:
        session["authed"] = True
    page = client.get("/settings/devices")
    if page.status_code == 200:
        assert b"fraimic_standard_canvas" in page.data
