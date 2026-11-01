"""The one "next update" a display shows: when the server pushes, weighed
against when the display can receive (connected now, or its next check-in),
and held by quiet hours / pauses."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import next_update as nu
from app import visionect
from app.main import REPO_ROOT, create_app
from app.state.page_store import Page
from app.state.rotation_model import Rotation, RotationStep

NOW = 1_900_000_000.0
MIN = 60.0


def _compute(push: float | None, reach: nu.Reach, hold: nu.Hold | None = None) -> nu.NextUpdate:
    return nu.compute_next_update(push, reach, now=NOW, hold=hold)


# -- the computation ------------------------------------------------------


def test_connected_frame_gets_the_push_the_moment_it_happens() -> None:
    reach = nu.Reach(nu.MODE_CONNECTED, live=True)
    r = _compute(NOW + 20 * MIN, reach)
    assert r.at == NOW + 20 * MIN


def test_sleeping_frame_waits_for_the_first_checkin_after_the_push() -> None:
    # Check-ins every 15 min starting in 24: 24, 39, 54...
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 24 * MIN, gap_s=15 * MIN)
    # Push before the next check-in: it waits for that check-in.
    assert _compute(NOW + 1 * MIN, reach).at == NOW + 24 * MIN
    # Push between two check-ins: it waits for the later one.
    assert _compute(NOW + 30 * MIN, reach).at == NOW + 39 * MIN
    # Push exactly on a check-in: that one.
    assert _compute(NOW + 39 * MIN, reach).at == NOW + 39 * MIN


def test_an_overdue_checkin_is_rolled_forward_by_the_gap() -> None:
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW - 5 * MIN, gap_s=15 * MIN)
    assert _compute(None, reach).checkin_at == NOW + 10 * MIN


def test_sleeping_frame_without_a_checkin_estimate_gets_the_push_time() -> None:
    assert _compute(NOW + 10 * MIN, nu.Reach(nu.MODE_SLEEPS)).at == NOW + 10 * MIN
    assert _compute(NOW + 10 * MIN, nu.Reach(nu.MODE_UNKNOWN)).at == NOW + 10 * MIN


def test_nothing_scheduled_still_reports_the_next_checkin() -> None:
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 24 * MIN, gap_s=15 * MIN)
    r = _compute(None, reach)
    assert r.at is None and r.checkin_at == NOW + 24 * MIN


def test_a_push_inside_a_hold_waits_for_its_end_and_then_the_checkin() -> None:
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 10 * MIN, gap_s=30 * MIN)
    hold = nu.Hold(NOW + 100 * MIN, "quiet hours")
    r = _compute(NOW + 20 * MIN, reach, hold)
    assert r.held_until == NOW + 100 * MIN
    assert r.push_at == NOW + 100 * MIN
    assert r.at == NOW + 100 * MIN  # 10, 40, 70, 100: a check-in lands on the end
    connected = _compute(NOW + 20 * MIN, nu.Reach(nu.MODE_CONNECTED, live=True), hold)
    assert connected.at == NOW + 100 * MIN


def test_a_hold_with_no_end_means_no_automatic_update() -> None:
    r = _compute(NOW + 20 * MIN, nu.Reach(nu.MODE_CONNECTED), nu.Hold(None, "a pause"))
    assert r.at is None and r.held_indefinitely


def test_a_lapsed_hold_is_ignored() -> None:
    r = _compute(NOW + 20 * MIN, nu.Reach(nu.MODE_CONNECTED), nu.Hold(NOW - 5, "quiet hours"))
    assert r.at == NOW + 20 * MIN and r.held_until is None


def test_checkins_on_a_wake_alignment_grid_use_the_grid_not_a_fixed_gap() -> None:
    grid = [NOW + 10 * MIN, NOW + 20 * MIN, NOW + 50 * MIN, NOW + 60 * MIN]

    def step(after: float) -> float | None:
        return next((g for g in grid if g > after), None)

    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=grid[0], gap_s=10 * MIN, step=step)
    assert _compute(NOW + 25 * MIN, reach).at == NOW + 50 * MIN


def _tz() -> tzinfo:
    return UTC


def _say(push: float | None, reach: nu.Reach, hold: nu.Hold | None = None) -> dict[str, Any]:
    r = _compute(push, reach, hold)
    return nu.describe(r, reach, tz=_tz(), now=NOW)


def test_wording_for_connected_and_sleeping_frames() -> None:
    now = datetime.fromtimestamp(NOW, UTC)
    push = NOW + 30 * MIN
    hhmm = datetime.fromtimestamp(push, UTC).strftime("%H:%M")
    same_day = datetime.fromtimestamp(push, UTC).date() == now.date()
    prefix = "" if same_day else datetime.fromtimestamp(push, UTC).strftime("%a ")
    said = _say(push, nu.Reach(nu.MODE_CONNECTED, live=True))
    assert said["text"] == f"Next update ~{prefix}{hhmm} (frame connected, arrives right away)"
    reach = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 40 * MIN, gap_s=15 * MIN)
    said = _say(push, reach)
    at = datetime.fromtimestamp(NOW + 40 * MIN, UTC)
    stamp = at.strftime("%H:%M") if at.date() == now.date() else at.strftime("%a %H:%M")
    assert said["text"] == f"Next update ~{stamp} (frame sleeps; next check-in ~{stamp})"
    assert said["when"] == f"~{stamp}"


def test_wording_when_held_and_when_nothing_is_scheduled() -> None:
    reach = nu.Reach(nu.MODE_CONNECTED, live=True)
    said = _say(NOW + 5 * MIN, reach, nu.Hold(NOW + 60 * MIN, "quiet hours"))
    assert "held until " in said["detail"] and "by quiet hours" in said["detail"]
    assert said["held"] is True
    said = _say(None, nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 9 * MIN, gap_s=600))
    assert said["text"].startswith("No update scheduled (frame sleeps; next check-in ~")
    assert said["when"] == ""
    said = _say(NOW + 5 * MIN, reach, nu.Hold(None, "a pause"))
    assert said["text"] == (
        "No update scheduled (automation is paused · frame connected, arrives right away)"
    )
    said = _say(NOW + 5 * MIN, reach, nu.Hold(None, "quiet hours"))
    assert said["detail"].startswith("quiet hours all week")


# -- Visionect: the observed check-in gap ---------------------------------


def test_observed_status_gap_is_the_median_of_the_recent_gaps() -> None:
    t = 1_000.0
    assert visionect.observed_status_gap_s([]) is None
    assert visionect.observed_status_gap_s([t]) is None
    # A real 32" configured for 30 min that reports every ~15.1 min.
    times = [t, t + 905, t + 1815, t + 2720]
    assert visionect.observed_status_gap_s(times) == pytest.approx(905, abs=10)
    # A reconnect's second status (a few seconds apart) is not a cadence.
    assert visionect.observed_status_gap_s([t, t + 4]) is None
    assert visionect.observed_status_gap_s([t, t + 4, t + 910]) == pytest.approx(906)


def test_status_heartbeat_prefers_the_observed_gap_to_the_configured_interval() -> None:
    from pyvisionect.packets import StatusPacket

    fields = StatusPacket(records=[(27, 30)], sentinel=0).fields()  # NextStatus 30 min
    beat = visionect.status_heartbeat(
        fields, uuid="u", connect_reason="heartbeat", interval_min=30, observed_gap_s=905.0
    )
    assert beat["next_sleep_s"] == 905  # not the configured 1800
    # Nothing observed yet: the configured interval stands.
    beat = visionect.status_heartbeat(fields, uuid="u", connect_reason="heartbeat", interval_min=30)
    assert beat["next_sleep_s"] == 1800
    # A sleep Tesserae ordered, or a frame that stays awake, still outrank it.
    ordered = visionect.status_heartbeat(
        fields, uuid="u", connect_reason="x", sleep_until=1.9e9, observed_gap_s=905.0
    )
    assert ordered["sleep_until"] == 1.9e9 and "next_sleep_s" not in ordered


# -- UI: Devices page, device page, Lineups page ---------------------------


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
    # 24-hour labels, whatever the host's locale (the Lineups page follows it).
    a.config["SETTINGS_STORE"].patch_section("app", {"locale": "en-GB"})
    for pid in ("kitchen", "hall"):
        a.config["PAGE_STORE"].save(Page(id=pid, name=pid.title()))
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _register(app: Flask, client: Any, device_id: str) -> None:
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": "esp32_client"}),
    )
    assert resp.status_code == 201


def _beat(app: Flask, device_id: str, *, wake_in_s: float, sleep_s: int = 900) -> None:
    now = time.time()
    app.config["DEVICE_TELEMETRY"].record_heartbeat(
        device_id,
        received_at=now,
        parsed={"sleep_until": now + wake_in_s},
        configured_sleep_s=sleep_s,
    )


def _seed_loop(app: Flask, device_id: str) -> None:
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="loop",
            name="Kitchen loop",
            device_ids=[device_id],
            steps=[
                RotationStep(page_id="kitchen", dwell_minutes=15),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )


def _device_page(client: Any, device_id: str) -> str:
    resp = client.get(f"/settings/devices/{device_id}")
    assert resp.status_code == 200
    return str(resp.get_data(as_text=True))


@pytest.mark.parametrize("design", ["classic", "paper"])
def test_device_page_says_when_a_sleeping_frame_gets_its_next_update(
    app: Flask, design: str
) -> None:
    app.config["SETTINGS_STORE"].update_section("app", {"ui_design": design})
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=40 * 60)  # next check-in far past the next step
    body = _device_page(client, "panel")
    assert "Next update ~" in body  # the Smart sync card's sentence
    assert "frame sleeps; next check-in ~" in body
    assert '<span class="dx-qtile-label">Next update</span>' in body  # the overview tile


def test_device_page_says_connected_for_a_frame_with_a_live_connection(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=24 * 60)
    monkeypatch.setattr(
        nu, "reach_for_device", lambda d, now=None: nu.Reach(nu.MODE_CONNECTED, live=True)
    )
    body = _device_page(client, "panel")
    assert "frame connected, arrives right away" in body
    assert "in 24 min" not in body  # no bogus countdown to a wake
    assert "frame sleeps" not in body


def test_lineups_page_adds_the_next_update_to_the_next_advance(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=3 * 3600)
    body = client.get("/decks").get_data(as_text=True)
    # The display header's next line says when the queued step lands: at the
    # next check-in, approximately, with the reason on hover.
    line = re.search(r'<p class="lu-nextl" title="([^"]*)">(.*?)</p>', body)
    assert line
    assert "sleeps" in line.group(1) and "check-in" in line.group(1)
    # Three hours can cross midnight. The weekday is part of that label.
    assert re.search(r"<b>~(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) )?\d\d:\d\d</b>", line.group(2))
    embedded = re.search(r'<script type="application/json" data-lu-vm>(.*?)</script>', body, re.S)
    assert embedded
    label = json.loads(embedded.group(1))["displays"][0]["next_line"]["at"]
    assert f"<b>{label}</b>" in line.group(2)
    assert re.search(r"<span>Wakes (?:at|\w{3}) ", body)


def test_lineups_card_for_a_connected_frame_arrives_right_away(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=24 * 60)
    device = app.config["DEVICE_REGISTRY"].devices["panel"]
    monkeypatch.setattr(
        nu,
        "reach_for_device",
        lambda d, now=None: (
            nu.Reach(nu.MODE_CONNECTED, live=True) if d is device else nu.Reach(nu.MODE_UNKNOWN)
        ),
    )
    body = client.get("/decks").get_data(as_text=True)
    line = re.search(r'<p class="lu-nextl" title="([^"]*)">(.*?)</p>', body)
    assert line and "connected, so it lands right away" in line.group(1)
    assert re.search(r"next at <b>\d\d:\d\d</b>", line.group(2))  # no ~: lands on time
    assert "<span>Online</span>" in body


def test_quiet_hours_hold_the_lineups_card_until_they_end(app: Flask) -> None:
    from datetime import datetime, timedelta

    now = datetime.now()
    app.config["SETTINGS_STORE"].update_section(
        "app",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "quiet_hours_end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "timezone": "system",
        },
    )
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=5 * 60)
    device = app.config["DEVICE_REGISTRY"].devices["panel"]
    with app.test_request_context("/"):
        timing = nu.DeviceTiming(device)
        assert timing.hold is not None and timing.hold.reason == "quiet hours"
        said = timing.view(time.time() + 5 * 60)
    assert said["held"] is True
    assert "held until " in said["text"] and "by quiet hours" in said["text"]
    # The update lands no earlier than the end of quiet hours.
    assert said["at"] >= timing.hold.until


def test_scheduler_reports_a_display_pause_and_the_global_pause() -> None:
    from datetime import timedelta
    from types import SimpleNamespace

    from app.scheduler import Scheduler

    now = datetime.now(UTC)
    until = now + timedelta(minutes=30)
    sched = Scheduler.__new__(Scheduler)
    sched._paused_provider = None
    sched._rotation_state_store = SimpleNamespace(
        all=lambda: {
            "a": SimpleNamespace(device_id="a", automation_pause_until=until),
            "b": SimpleNamespace(device_id="b", automation_pause_until=now - timedelta(hours=1)),
        }
    )
    assert sched.pause_hold_for_device("a", now) == (True, until)
    assert sched.pause_hold_for_device("b", now) == (False, None)
    assert sched.pause_hold_for_device("c", now) == (False, None)
    sched._paused_provider = lambda: True
    assert sched.pause_hold_for_device("c", now) == (True, None)


# -- Home Assistant: Next update + Next wake sensors -------------------------


def test_reason_phrases() -> None:
    sleepy = nu.Reach(nu.MODE_SLEEPS, next_checkin_at=NOW + 5 * MIN, gap_s=15 * MIN)
    live = nu.Reach(nu.MODE_CONNECTED, live=True)
    assert nu.reason_for(_compute(NOW + MIN, sleepy), sleepy) == "check-in"
    assert nu.reason_for(_compute(NOW + MIN, live), live) == "connected"
    held = _compute(NOW + MIN, live, nu.Hold(NOW + 30 * MIN, "quiet hours"))
    assert nu.reason_for(held, live) == "held by quiet hours"
    paused = _compute(NOW + MIN, live, nu.Hold(None, "a pause"))
    assert nu.reason_for(paused, live) == "held by the automation pause"
    quiet_week = _compute(NOW + MIN, live, nu.Hold(None, "quiet hours"))
    assert nu.reason_for(quiet_week, live) == "held by quiet hours"


def test_ha_state_for_a_sleeping_frame_has_a_checkin_and_a_check_in_reason(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=40 * 60)
    from app.ha_hooks import build_ha_hooks

    hooks = build_ha_hooks(app)
    assert hooks.next_update_fn is not None
    state = hooks.next_update_fn("panel")
    assert state is not None
    assert state["reason"] == "check-in" and state["mode"] == nu.MODE_SLEEPS
    assert state["checkin_at"] == pytest.approx(time.time() + 40 * 60, abs=5)
    assert state["at"] >= state["checkin_at"]  # arrives on a check-in


def test_ha_state_for_a_connected_visionect_sign_has_no_wake(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=24 * 60)  # the bogus telemetry prediction
    monkeypatch.setattr(
        nu, "reach_for_device", lambda d, now=None: nu.Reach(nu.MODE_CONNECTED, live=True)
    )
    from app.ha_hooks import build_ha_hooks

    hooks = build_ha_hooks(app)
    assert hooks.next_update_fn is not None
    state = hooks.next_update_fn("panel")
    assert state is not None
    assert state["checkin_at"] is None
    assert state["reason"] == "connected"
    assert state["at"] is not None  # the scheduled push time, not a wake


def test_ha_state_is_held_by_pause_hours(app: Flask) -> None:
    from datetime import datetime, timedelta

    now = datetime.now()
    app.config["SETTINGS_STORE"].update_section(
        "app",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "quiet_hours_end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "timezone": "system",
        },
    )
    client = app.test_client()
    _sign_in(client)
    _register(app, client, "panel")
    _seed_loop(app, "panel")
    _beat(app, "panel", wake_in_s=5 * 60)
    from app.ha_hooks import build_ha_hooks

    hooks = build_ha_hooks(app)
    assert hooks.next_update_fn is not None
    state = hooks.next_update_fn("panel")
    assert state is not None
    assert state["reason"] == "held by quiet hours" and state["held"] is True


def test_ha_hook_is_none_for_an_unknown_display(app: Flask) -> None:
    from app.ha_hooks import build_ha_hooks

    hooks = build_ha_hooks(app)
    assert hooks.next_update_fn is not None
    assert hooks.next_update_fn("ghost") is None
