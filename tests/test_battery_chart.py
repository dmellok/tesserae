"""Battery chart component (``app.battery_chart``): wake extraction, cause
attribution, thinning, wake metrics, gaps and the Visionect no-predictions rule."""

from __future__ import annotations

import contextlib
import sqlite3
import time
from datetime import UTC
from datetime import time as dtime
from pathlib import Path
from typing import Any

import pytest

from app import battery_chart as bc
from app.quiet_hours import QuietHoursWindow
from app.state.battery_history import BatteryHistory
from app.state.event_log import EventLog, EventRow

NOW = time.time()
NOW_MS = int(NOW * 1000)
DEV = "d1"
CHECK_IN = "Scheduled check-in"
DROPPED = "Reconnected after a dropped connection"


def _event(
    ts: float,
    extra: dict[str, Any],
    *,
    type: str = "device",
    source: str = DEV,
    target: str | None = None,
    status: str = "ok",
    id: int = 1,
) -> EventRow:
    return EventRow(
        id=id,
        type=type,
        timestamp=ts,
        source=source,
        target=target if target is not None else f"rest://{source}/status",
        status=status,
        digest=None,
        error=None,
        duration_s=0.0,
        extra=extra,
    )


def _push(ts: float, source: str, *, id: int = 1, status: str = "sent", **extra: Any) -> EventRow:
    return _event(
        ts,
        {"device_ids": [DEV], **extra},
        type="push",
        source=source,
        target="page_a",
        status=status,
        id=id,
    )


def _series(*offsets_s: float, pct: int = 80) -> list[dict[str, Any]]:
    return [{"t_ms": int((NOW + o) * 1000), "pct": pct - i} for i, o in enumerate(offsets_s)]


def _wakes(series: list[dict[str, Any]], status: list[EventRow] = (), pushes=(), **kw: Any):  # type: ignore[no-untyped-def]
    wakes = bc.extract_wakes(series, status)
    return bc.attribute_wakes(wakes, pushes, device_id=DEV, **kw)


# -- reasons -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("heartbeat", CHECK_IN),
        ("timer", CHECK_IN),
        ("wakeup", CHECK_IN),
        ("", CHECK_IN),
        (None, CHECK_IN),
        ("unknown", CHECK_IN),
        ("by server request", "Server request"),
        ("reboot", "Reboot"),
        ("connection error", DROPPED),
        ("connection driver error", DROPPED),
        ("protocol error", DROPPED),
        ("wifi-reconnect", "Wi-Fi reconnect"),
        ("ext0", "Button"),
        ("POWERON", "Power on"),
        ("brownout", "Brownout"),
        ("task_wdt", "Watchdog reset"),
        ("moon phase", "moon phase"),
    ],
)
def test_reason_label(raw: str | None, label: str) -> None:
    assert bc.reason_label(raw) == label


def test_unknown_reason_is_truncated_not_dropped() -> None:
    assert bc.reason_label("x" * 200) == "x" * bc.MAX_REASON_CHARS


def test_event_reason_reads_known_keys_in_priority_order() -> None:
    assert bc.event_reason({"parsed": {"connect_reason": "wakeup", "wake_reason": "timer"}}) == (
        "wakeup"
    )
    assert bc.event_reason({"parsed": {"wake_reason": "ext0"}}) == "ext0"
    assert bc.event_reason({"parsed": {"reset_reason": "poweron"}}) == "poweron"
    assert bc.event_reason({"diag": {"id": 1, "reset": "brownout"}}) == "brownout"


def test_event_reason_reads_only_the_reasons_that_beat_sent() -> None:
    # The merged status keeps a boot's reset_reason; a later row whose own beat
    # sent no reason must not read it again.
    stale = {"parsed": {"reset_reason": "poweron", "ip": "10.0.0.9"}, "reason_keys": []}
    assert bc.event_reason(stale) is None
    fresh = {"parsed": {"reset_reason": "poweron"}, "reason_keys": ["reset_reason"]}
    assert bc.event_reason(fresh) == "poweron"
    # a row from before reason_keys was written reads every key, as before
    assert bc.event_reason({"parsed": {"reset_reason": "poweron"}}) == "poweron"


def test_heartbeat_rows_name_only_their_own_reasons(tmp_path: Path) -> None:
    import json

    from app import device_service
    from app.main import REPO_ROOT, create_app
    from app.transport_wiring import record_status_heartbeat

    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="desk",
        kind_id="esp32_client",
        orientation="landscape",
    )
    device = app.config["DEVICE_REGISTRY"].get("desk")
    log = app.config["EVENT_LOG"]

    def beat(body: dict[str, object]) -> None:
        record_status_heartbeat(
            app=app,
            device=device,
            payload=json.dumps(body).encode(),
            status_cache=app.config["DEVICE_STATUS"],
            event_log=log,
            event_target="rest://desk/status",
        )

    beat({"battery_pct": 80, "ip": "10.0.0.2", "reset_reason": "poweron"})
    beat({"battery_pct": 80, "ip": "10.0.0.9"})  # a new IP is logged, no new reason
    rows = log.device_status_events(["desk"], since=0)["desk"]
    assert [bc.event_reason(r.extra) for r in rows] == ["poweron", None]


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"parsed": {"battery_pct": 50}},
        {"parsed": {"connect_reason": 3}},
        {"parsed": {"wake_reason": "  "}},
        {"diag": {"id": 1, "paint_error": "refresh_timeout"}},
        {"ota": {"state": "idle"}},
    ],
)
def test_event_reason_none_when_row_names_no_reason(extra: dict[str, Any]) -> None:
    assert bc.event_reason(extra) is None


# -- wake extraction -----------------------------------------------------


def test_every_sample_is_a_wake() -> None:
    series = _series(-300, -200, -100)
    wakes = bc.extract_wakes(series, [])
    assert [w["t_ms"] for w in wakes] == [p["t_ms"] for p in series]
    assert all(w["reason"] is None for w in wakes)


def test_samples_within_the_debounce_are_one_wake() -> None:
    wakes = bc.extract_wakes(_series(-300, -296, -100), [])
    assert len(wakes) == 2


def test_no_series_means_no_wakes() -> None:
    event = _event(NOW, {"parsed": {"connect_reason": "wakeup"}})
    assert bc.extract_wakes([], [event]) == []


def test_event_reason_labels_the_matching_sample() -> None:
    series = _series(-300, -200, -100)
    event = _event(NOW - 198, {"parsed": {"connect_reason": "by server request"}})
    wakes = bc.extract_wakes(series, [event])
    assert len(wakes) == 3
    assert [w["reason_label"] for w in wakes] == [CHECK_IN, "Server request", CHECK_IN]
    assert wakes[1]["reason"] == "by server request"


def test_event_without_nearby_sample_becomes_its_own_wake_on_the_line() -> None:
    series = [
        {"t_ms": int((NOW - 1000) * 1000), "pct": 80},
        {"t_ms": int((NOW - 200) * 1000), "pct": 60},
    ]
    event = _event(NOW - 600, {"diag": {"id": 7, "reset": "brownout"}})
    wakes = bc.extract_wakes(series, [event])
    assert [w["reason_label"] for w in wakes] == [CHECK_IN, "Brownout", CHECK_IN]
    assert wakes[1]["t_ms"] == int((NOW - 600) * 1000)
    assert wakes[1]["pct"] == 70  # halfway down the line between 80 and 60


def test_routine_event_does_not_overwrite_a_specific_reason() -> None:
    events = [
        _event(NOW - 99, {"parsed": {"connect_reason": "reboot"}}),
        _event(NOW - 98, {"parsed": {"connect_reason": "heartbeat"}}),
    ]
    assert bc.extract_wakes(_series(-100), events)[0]["reason_label"] == "Reboot"


# -- attribution ---------------------------------------------------------


def _cause(wakes: list[dict[str, Any]], i: int = 0) -> tuple[str, str, str | None]:
    w = wakes[i]
    return w["cat"], w["label"], w["detail"]


def test_a_wake_with_nothing_pushed_is_a_scheduled_check_in() -> None:
    assert _cause(_wakes(_series(-100))) == ("scheduled", CHECK_IN, None)


@pytest.mark.parametrize(
    ("reason", "cat", "label"),
    [
        ("connection error", "reconnect", DROPPED),
        ("protocol error", "reconnect", DROPPED),
        ("reboot", "problem", "Reboot"),
        ("poweron", "problem", "Power on"),
        ("brownout", "problem", "Brownout"),
        ("panic", "problem", "Crash"),
    ],
)
def test_the_devices_own_reconnect_or_restart_wins_over_a_push(
    reason: str, cat: str, label: str
) -> None:
    status = [_event(NOW - 100, {"parsed": {"connect_reason": reason}})]
    pushes = [_push(NOW - 105, "home_assistant")]
    got = _cause(_wakes(_series(-100), status, pushes))
    assert got == (cat, label, "Picked up Home Assistant")


@pytest.mark.parametrize(
    ("source", "extra", "cat", "label"),
    [
        ("home_assistant", {"button": "Next"}, "ha", "Home Assistant: Next"),
        ("home_assistant", {}, "ha", "Home Assistant"),
        ("button", {"button": "right"}, "device", "Button: right"),
        ("touch", {}, "device", "Touch"),
        ("page", {}, "manual", "Manual push"),
        ("resend", {}, "manual", "Manual push"),
        ("companion", {}, "manual", "Manual push"),
        ("webhook", {}, "webhook", "Webhook / API"),
        ("page_refresh", {}, "lineup", "Lineup update: page refresh"),
        ("mystery", {}, "other", "Push (mystery)"),
    ],
)
def test_a_push_just_before_the_wake_is_its_cause(
    source: str, extra: dict[str, Any], cat: str, label: str
) -> None:
    pushes = [_push(NOW - 110, source, **extra)]
    assert _cause(_wakes(_series(-100), pushes=pushes)) == (cat, label, None)


def test_a_push_logged_a_moment_after_the_wake_still_belongs_to_it() -> None:
    # A button press: the wake opens, then the press is logged.
    pushes = [_push(NOW - 95, "button", button="left")]
    assert _cause(_wakes(_series(-100), pushes=pushes))[:2] == ("device", "Button: left")


def test_non_deliveries_and_background_renders_are_not_causes() -> None:
    pushes = [
        _push(NOW - 110, "page", id=1, status="failed"),
        _push(NOW - 109, "page", id=2, status="busy"),
        _push(NOW - 108, "deck_warm", id=3),
        _push(NOW - 107, "page", id=4, status="warmed"),
    ]
    assert _cause(_wakes(_series(-100), pushes=pushes))[0] == "scheduled"


def test_a_press_counts_even_when_nothing_was_pushed() -> None:
    pushes = [_push(NOW - 105, "button", status="deduped", button="up")]
    assert _cause(_wakes(_series(-100), pushes=pushes))[0] == "device"


def test_sleeping_frame_picks_up_an_earlier_push_at_its_next_wake() -> None:
    # Pushed three hours before the wake the frame was asleep through.
    pushes = [_push(NOW - 3 * 3600, "scheduler", id=7)]
    links = [
        _event(
            NOW - 3 * 3600,
            {"schedule_name": "Morning", "push_event_id": 7},
            type="scheduler",
            source="scheduler",
            target="s1",
        )
    ]
    status = [_event(NOW - 100, {"parsed": {"connect_reason": "wakeup"}})]
    wakes = _wakes(_series(-100), status, pushes, links=links)
    assert _cause(wakes) == ("scheduled", CHECK_IN, "Picked up Lineup update: Morning")
    assert wakes[0]["reason"] == "wakeup"


def test_a_push_is_picked_up_once_by_the_first_wake_after_it() -> None:
    pushes = [_push(NOW - 3000, "page")]
    wakes = _wakes(_series(-2500, -1500, -500), pushes=pushes)
    assert [w["detail"] for w in wakes] == ["Picked up Manual push", None, None]


def test_a_push_older_than_the_pickup_limit_is_ignored() -> None:
    pushes = [_push(NOW - 2 * 86400, "page")]
    assert _cause(_wakes(_series(-100), pushes=pushes)) == ("scheduled", CHECK_IN, None)


def test_the_latest_push_before_the_wake_names_the_cause() -> None:
    pushes = [_push(NOW - 130, "page", id=1), _push(NOW - 110, "home_assistant", id=2)]
    assert _cause(_wakes(_series(-100), pushes=pushes))[:2] == ("ha", "Home Assistant")


def test_by_server_request_takes_the_cause_of_the_push_that_triggered_it() -> None:
    status = [_event(NOW - 100, {"parsed": {"connect_reason": "by server request"}})]
    pushes = [_push(NOW - 250, "home_assistant", button="Next")]
    cat, label, _ = _cause(_wakes(_series(-100), status, pushes))
    assert (cat, label) == ("ha", "Home Assistant: Next")


def test_by_server_request_with_no_push_is_the_server_asking_it_to_check_in() -> None:
    status = [_event(NOW - 100, {"parsed": {"connect_reason": "by server request"}})]
    wakes = _wakes(_series(-100), status)
    assert _cause(wakes) == ("scheduled", CHECK_IN, None)
    assert wakes[0]["reason"] == "by server request"


def test_by_server_request_ignores_a_push_too_old_to_have_caused_it() -> None:
    status = [_event(NOW - 100, {"parsed": {"connect_reason": "by server request"}})]
    pushes = [_push(NOW - 4000, "page")]
    assert _cause(_wakes(_series(-100), status, pushes)) == (
        "scheduled",
        CHECK_IN,
        "Picked up Manual push",
    )


def test_a_button_wake_from_the_device_is_a_device_cause() -> None:
    status = [_event(NOW - 100, {"parsed": {"wake_reason": "ext0"}})]
    assert _cause(_wakes(_series(-100), status))[:2] == ("device", "Button")


def test_an_unmapped_device_reason_is_shown_raw() -> None:
    status = [_event(NOW - 100, {"parsed": {"wake_reason": "moon phase"}})]
    assert _cause(_wakes(_series(-100), status))[:2] == ("other", "moon phase")


def test_lineup_pushes_are_named_from_their_schedule_rotation_or_lineup() -> None:
    names = {"deck1": "Morning (weekend)", "rot1": "Evening"}
    cases = [
        (
            _push(NOW - 110, "scheduler", id=5),
            [
                _event(
                    NOW - 110,
                    {"schedule_name": "Breakfast", "push_event_id": 5},
                    type="scheduler",
                    source="scheduler",
                    target="s",
                )
            ],
            "Lineup update: Breakfast",
        ),
        (
            _push(NOW - 110, "rotation", id=6),
            [
                _event(
                    NOW - 110,
                    {"rotation_name": "Evening", "push_event_id": 6},
                    type="rotation",
                    source="rotation",
                    target="rot1",
                )
            ],
            "Lineup update: Evening",
        ),
        (_push(NOW - 110, "deck", deck_id="deck1"), [], "Lineup update: Morning (weekend)"),
        (
            _push(NOW - 110, "deck"),
            [
                _event(
                    NOW - 108,
                    {"timer_advance_device": DEV, "page_id": "page_a"},
                    type="deck",
                    source="deck",
                    target="deck1",
                )
            ],
            "Lineup update: Morning (weekend)",
        ),
        (_push(NOW - 110, "deck"), [], "Lineup update"),
    ]
    for push, links, label in cases:
        got = _cause(_wakes(_series(-100), pushes=[push], links=links, names=names))
        assert got == ("lineup", label, None)


def test_a_link_for_another_push_does_not_name_this_one() -> None:
    links = [
        _event(
            NOW,
            {"schedule_name": "Other", "push_event_id": 99},
            type="scheduler",
            source="scheduler",
            target="s",
        )
    ]
    got = _cause(_wakes(_series(-100), pushes=[_push(NOW - 110, "scheduler", id=5)], links=links))
    assert got == ("lineup", "Lineup update", None)


# -- thinning ------------------------------------------------------------


def _wake(t_ms: int, cat: str = "scheduled") -> dict[str, Any]:
    return {"t_ms": t_ms, "pct": 50, "cat": cat, "label": cat, "detail": None, "reason": None}


def test_thin_passes_small_lists_through() -> None:
    wakes = [_wake(i * 1000) for i in range(10)]
    assert bc.thin_wakes(wakes, start_ms=0, end_ms=10_000, max_dots=20) == wakes


def test_thin_caps_dot_count_and_keeps_the_last_of_each_bucket() -> None:
    wakes = [_wake(i * 1000) for i in range(1000)]
    out = bc.thin_wakes(wakes, start_ms=0, end_ms=1_000_000, max_dots=50)
    assert len(out) == 50
    assert out[0]["t_ms"] == 19_000
    assert out[-1]["t_ms"] == 999_000
    assert [w["t_ms"] for w in out] == sorted(w["t_ms"] for w in out)


def test_thin_keeps_a_notable_wake_over_later_routine_ones() -> None:
    wakes = [_wake(i * 1000) for i in range(100)]
    wakes[3] = _wake(3000, "problem")
    out = bc.thin_wakes(wakes, start_ms=0, end_ms=100_000, max_dots=5)
    assert len(out) == 5
    assert "problem" in {w["cat"] for w in out}


def test_thin_clamps_wakes_outside_the_window() -> None:
    wakes = [_wake(-5000)] + [_wake(i * 1000) for i in range(100)] + [_wake(500_000)]
    assert len(bc.thin_wakes(wakes, start_ms=0, end_ms=100_000, max_dots=10)) <= 10


# -- wakes per day -------------------------------------------------------


def test_wake_stats_rate_and_breakdown_come_from_the_unthinned_wakes() -> None:
    day_ms = 86_400_000
    wakes = [_wake(NOW_MS - day_ms + i * 60_000) for i in range(34)]
    wakes += [_wake(NOW_MS - 1000 * (i + 1), "lineup") for i in range(6)]
    wakes += [_wake(NOW_MS - 100 * (i + 1), "ha") for i in range(2)]
    wakes += [_wake(NOW_MS - 10, "problem")]
    wakes.sort(key=lambda w: w["t_ms"])
    stats = bc.wake_stats(wakes, window_days=7, now_ms=NOW_MS)
    assert stats["total"] == 43
    assert stats["per_day"] == 43.0  # 43 wakes over the one day the device has reported
    assert stats["summary"] == "34 check-ins, 6 lineup, 2 HA, 1 more"
    assert stats["summary_full"] == "34 check-ins, 6 lineup, 2 HA, 1 restart"
    assert [b["cat"] for b in stats["breakdown"]] == ["scheduled", "lineup", "ha", "problem"]


def test_wake_stats_average_over_the_window_when_it_is_fully_covered() -> None:
    day_ms = 86_400_000
    wakes = [_wake(NOW_MS - 7 * day_ms + i * 3_600_000) for i in range(70)]
    stats = bc.wake_stats(wakes, window_days=7, now_ms=NOW_MS)
    assert stats["per_day"] == 10.0


def test_wake_stats_do_not_dilute_a_new_devices_rate_over_the_whole_window() -> None:
    wakes = [_wake(NOW_MS - 3_600_000 * 12 + i * 3_600_000) for i in range(12)]
    stats = bc.wake_stats(wakes, window_days=30, now_ms=NOW_MS)
    assert stats["per_day"] == 24.0


def test_wake_stats_need_two_wakes_for_a_rate() -> None:
    assert bc.wake_stats([_wake(NOW_MS)], window_days=7, now_ms=NOW_MS)["per_day"] is None
    empty = bc.wake_stats([], window_days=7, now_ms=NOW_MS)
    assert empty["total"] == 0 and empty["summary"] == "" and empty["per_day"] is None


# -- gaps ----------------------------------------------------------------

QUIET = QuietHoursWindow(start=dtime(23, 0), end=dtime(9, 30))


def _ms(hour: int, minute: int = 0, day: int = 1) -> int:
    from datetime import datetime

    return int(datetime(2026, 11, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _regular(start: int, end: int, step_min: int = 15) -> list[dict[str, Any]]:
    return [{"t_ms": t, "pct": 80} for t in range(start, end + 1, step_min * 60_000)]


def test_expected_interval_is_the_median_spacing() -> None:
    series = _regular(_ms(8), _ms(12)) + _regular(_ms(20), _ms(22))
    assert bc.expected_interval_s(series) == 15 * 60


def test_expected_interval_falls_back_to_the_hint_with_few_samples() -> None:
    assert bc.expected_interval_s(_regular(_ms(8), _ms(9)), None) == 15 * 60
    two = [{"t_ms": _ms(8), "pct": 1}, {"t_ms": _ms(9), "pct": 1}]
    assert bc.expected_interval_s(two, 1800) == 1800
    assert bc.expected_interval_s(two, None) is None


def test_a_too_small_hint_cannot_flag_every_interval() -> None:
    series = _regular(_ms(8), _ms(12), 30)
    assert bc.expected_interval_s(series, 60) == 30 * 60


def test_overnight_sleep_in_quiet_hours_is_an_asleep_gap() -> None:
    series = _regular(_ms(14), _ms(23, 0)) + _regular(_ms(9, 30, 2), _ms(12, 0, 2))
    gaps = bc.detect_gaps(series, now_ms=_ms(12, 5, 2), expected_s=900, quiet_window=QUIET, tz=UTC)
    assert gaps == [{"start_ms": _ms(23, 0), "end_ms": _ms(9, 30, 2), "kind": "asleep"}]


def test_a_daytime_outage_is_an_offline_gap() -> None:
    series = _regular(_ms(12), _ms(14, 10)) + _regular(_ms(16, 40), _ms(18))
    gaps = bc.detect_gaps(series, now_ms=_ms(18, 5), expected_s=900, quiet_window=QUIET, tz=UTC)
    assert gaps == [{"start_ms": _ms(14), "end_ms": _ms(16, 40), "kind": "offline"}]


def test_without_quiet_hours_every_gap_is_offline() -> None:
    series = _regular(_ms(14), _ms(23)) + _regular(_ms(9, 30, 2), _ms(10, 0, 2))
    gaps = bc.detect_gaps(series, now_ms=_ms(10, 5, 2), expected_s=900, quiet_window=None, tz=UTC)
    assert [g["kind"] for g in gaps] == ["offline"]


def test_spans_inside_the_margin_or_under_twenty_minutes_are_not_gaps() -> None:
    # 2.5 x 15 min = 37.5 min: a 35 minute miss is jitter, not a gap.
    series = [{"t_ms": _ms(8), "pct": 1}, {"t_ms": _ms(8, 35), "pct": 1}]
    assert bc.detect_gaps(series, now_ms=_ms(8, 36), expected_s=900, tz=UTC) == []
    # A 1 minute cadence: 2.5x is 2.5 min, but the 20 minute floor applies.
    fast = [{"t_ms": _ms(8), "pct": 1}, {"t_ms": _ms(8, 15), "pct": 1}]
    assert bc.detect_gaps(fast, now_ms=_ms(8, 16), expected_s=60, tz=UTC) == []
    slow = [{"t_ms": _ms(8), "pct": 1}, {"t_ms": _ms(8, 25), "pct": 1}]
    assert len(bc.detect_gaps(slow, now_ms=_ms(8, 26), expected_s=60, tz=UTC)) == 1


def test_silence_since_the_last_sample_is_a_trailing_gap() -> None:
    series = _regular(_ms(8), _ms(10))
    gaps = bc.detect_gaps(series, now_ms=_ms(13), expected_s=900, tz=UTC)
    assert gaps == [{"start_ms": _ms(10), "end_ms": _ms(13), "kind": "offline"}]
    assert bc.detect_gaps(series, now_ms=_ms(10, 10), expected_s=900, tz=UTC) == []


def test_no_expected_interval_means_no_gaps() -> None:
    assert bc.detect_gaps(_regular(_ms(8), _ms(9)), now_ms=_ms(20), expected_s=None) == []


# -- builder -------------------------------------------------------------


@pytest.fixture
def history(tmp_path: Path) -> BatteryHistory:
    return BatteryHistory(tmp_path / "battery.db")


def _fill(history: BatteryHistory, device_id: str, n: int = 12) -> None:
    for i in range(n):
        history.record(device_id, pct=100 - i, timestamp=NOW - (n - i) * 3600)


def test_builder_shape_for_a_normal_device(history: BatteryHistory) -> None:
    _fill(history, DEV)
    card = bc.build_battery_chart(
        DEV, history=history, window_days=7, name="Hall", current={"pct": 25}
    )
    assert card["id"] == DEV and card["name"] == "Hall"
    assert card["pct"] == 25 and card["tone"] == "low"
    assert card["samples"] == 12 == len(card["series"]) == len(card["wakes"])
    assert card["prediction"] is not None
    assert set(card["series"][0]) == {"t_ms", "pct"}
    assert set(card["wakes"][0]) == {"t_ms", "pct", "cat", "label", "detail", "reason"}
    assert card["wake_stats"]["total"] == 12
    assert card["legend"] == [{"cat": "scheduled", "name": "Scheduled check-in"}]
    assert card["gaps"] == []


def test_builder_applies_offset_to_series_and_wakes(history: BatteryHistory) -> None:
    history.record(DEV, pct=50, timestamp=NOW - 60)
    card = bc.build_battery_chart(DEV, history=history, window_days=1, pct_offset=10)
    assert card["series"][0]["pct"] == 60
    assert card["wakes"][0]["pct"] == 60


def test_builder_explains_wakes_from_the_event_log(history: BatteryHistory, tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    history.record(DEV, pct=70, timestamp=time.time())
    log.record(
        type="device",
        source=DEV,
        target=f"rest://{DEV}/status",
        status="ok",
        extra={"parsed": {"wake_reason": "ext0"}},
    )
    card = bc.build_battery_chart(DEV, history=history, window_days=1, event_log=log)
    assert [(w["cat"], w["label"]) for w in card["wakes"]] == [("device", "Button")]
    assert [e["cat"] for e in card["legend"]] == ["device"]


def test_builder_stats_count_every_wake_even_when_dots_are_thinned(
    history: BatteryHistory,
) -> None:
    for i in range(300):
        history.record(DEV, pct=90, timestamp=NOW - (300 - i) * 60)
    card = bc.build_battery_chart(DEV, history=history, window_days=1)
    assert len(card["wakes"]) <= bc.MAX_WAKE_DOTS
    assert card["wake_stats"]["total"] == 300


def test_builder_reports_gaps_and_keeps_the_series_unfilled(history: BatteryHistory) -> None:
    for i in range(10):
        history.record(DEV, pct=90 - i, timestamp=NOW - 20 * 3600 + i * 900)
    for i in range(6):
        history.record(DEV, pct=70 - i, timestamp=NOW - 5 * 900 + i * 900)
    card = bc.build_battery_chart(DEV, history=history, window_days=1)
    assert card["samples"] == 16 == len(card["series"])  # nothing invented
    assert [g["kind"] for g in card["gaps"]] == ["offline"]
    assert card["gaps"][0]["end_ms"] - card["gaps"][0]["start_ms"] > 10 * 3600 * 1000


def test_builder_with_no_samples(history: BatteryHistory) -> None:
    card = bc.build_battery_chart(DEV, history=history, window_days=7)
    assert card["series"] == [] and card["wakes"] == [] and card["samples"] == 0
    assert card["pct"] is None and card["tone"] == "ok"
    assert card["gaps"] == [] and card["wake_stats"]["total"] == 0


def test_any_device_gets_predictions(history: BatteryHistory) -> None:
    _fill(history, "frame")
    card = bc.build_battery_chart("frame", history=history, window_days=7)
    assert card["prediction"] is not None
    assert card["samples"] == 12 and len(card["series"]) == 12
    assert card["wake_stats"]["total"] == 12


def test_a_short_chart_window_still_predicts_from_a_week_of_readings(
    history: BatteryHistory,
) -> None:
    # A slow drain: a percent a day, so the last 12 hours alone look flat.
    for hour in range(0, 7 * 24, 6):
        history.record("slow", pct=83 + hour // 24, timestamp=NOW - hour * 3600)
    card = bc.build_battery_chart("slow", history=history, window_hours=12)
    assert card["prediction"]["slope_per_day"] < -0.5
    assert card["prediction"]["days_to_empty"] is not None


def test_load_wake_events_is_batched_and_window_bounded(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    for dev in ("a", "b", "c"):
        log.record(
            type="device",
            source=dev,
            target=f"rest://{dev}/status",
            status="ok",
            extra={"parsed": {"connect_reason": "wakeup"}},
        )
    log.record(
        type="device", source="a", target="client_log", status="info", extra={"client_log": True}
    )
    log.record(type="push", source="page", target="p", status="sent", extra={"device_ids": ["a"]})
    log.record(type="push", source="page", target="p", status="sent", extra={"device_ids": ["b"]})
    log.record(
        type="scheduler", source="scheduler", target="s", status="sent", extra={"push_event_id": 1}
    )
    out = bc.load_wake_events(log, ["a", "b"], window_days=1)
    assert set(out) == {"a", "b"}
    assert len(out["a"].status) == 1  # no client-log row, no push row
    assert [r.extra["device_ids"] for r in out["a"].pushes] == [["a"]]
    assert [r.extra["device_ids"] for r in out["b"].pushes] == [["b"]]
    assert [r.type for r in out["a"].links] == ["scheduler"]
    # Status rows are bounded by the chart window; age "c" past it.
    with contextlib.closing(sqlite3.connect(tmp_path / "events.db")) as conn, conn:
        conn.execute("UPDATE events SET timestamp = ? WHERE source = 'c'", (NOW - 10 * 86400,))
    assert bc.load_wake_events(log, ["c"], window_days=1)["c"].status == []
    assert len(bc.load_wake_events(log, ["c"], window_days=30)["c"].status) == 1
    assert bc.load_wake_events(None, ["a"], window_days=1) == {}
    assert bc.load_wake_events(log, [], window_days=1) == {}


def test_a_week_of_the_live_visionect_frames_reasons_reads_as_check_ins_and_reconnects() -> None:
    """connect_reason counts from the live frame over seven days."""
    counts = {
        "by server request": 81,
        "connection error": 24,
        "protocol error": 8,
        "reboot": 5,
        "wakeup": 3,
        "heartbeat": 2,
    }
    status, series, i = [], [], 0
    for reason, n in counts.items():
        for _ in range(n):
            ts = NOW - 7 * 86400 + i * 3000
            series.append({"t_ms": int(ts * 1000), "pct": 80})
            status.append(_event(ts + 1, {"parsed": {"connect_reason": reason}}))
            i += 1
    wakes = _wakes(series, status)
    stats = bc.wake_stats(wakes, window_days=7, now_ms=NOW_MS)
    assert stats["total"] == 123
    assert {b["cat"]: b["count"] for b in stats["breakdown"]} == {
        "scheduled": 86,  # 81 by server request + 3 wakeup + 2 heartbeat
        "reconnect": 32,  # 24 connection error + 8 protocol error
        "problem": 5,  # reboot
    }
    assert stats["summary"] == "86 check-ins, 32 reconnects, 5 restarts"
    assert not any(w["cat"] == "other" for w in wakes)


def test_an_unmapped_reason_is_labelled_with_its_own_text() -> None:
    status = [_event(NOW - 100, {"parsed": {"connect_reason": "solar flare"}})]
    wakes = _wakes(_series(-100), status)
    assert wakes[0]["label"] == "solar flare" and wakes[0]["cat"] == "other"


@pytest.mark.parametrize(
    ("hours", "label"),
    [
        (1, "1 hour"),
        (12, "12 hours"),
        (24, "24 hours"),
        (72, "3 days"),
        (168, "7 days"),
        (720, "30 days"),
    ],
)
def test_window_label(hours: float, label: str) -> None:
    assert bc.window_label(hours) == label


def test_builder_takes_the_window_in_hours_and_days(history: BatteryHistory) -> None:
    for i in range(48):
        history.record(DEV, pct=90, timestamp=NOW - (48 - i) * 1800)
    half = bc.build_battery_chart(DEV, history=history, window_hours=12)
    day = bc.build_battery_chart(DEV, history=history, window_days=1)
    default = bc.build_battery_chart(DEV, history=history)
    assert (half["window_hours"], half["window_days"]) == (12, 0.5)
    assert (day["window_hours"], day["window_days"]) == (24, 1)
    assert default["window_hours"] == 168
    assert 0 < half["samples"] < day["samples"]
    assert [r["hours"] for r in half["ranges"] if r["pressed"]] == [12]
    assert not any(r["pressed"] for r in default["ranges"][:2])
