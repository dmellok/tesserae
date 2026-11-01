"""Lineups redesign: the view-model (``app.lineups_view``) and the JSON view +
inline-edit endpoints in ``app.deck_routes``.

The pure half runs the approved mockup's demo data (Wednesday 18:12: a
connected Visionect frame in sync and a sleeping kitchen display with a
queued step) through the real builder and checks every state the mockup
draws. The route half drives ``/decks/view.json``, ``/decks/<id>/window``,
``/decks/<id>/days``, ``/decks/edits`` and ``/decks/<id>/push-step``.
"""

from __future__ import annotations

import itertools
import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from flask import Flask

from app.lineups_view import (
    SAMPLE_NOW,
    SCALE_TIERS,
    DashboardInfo,
    DisplayView,
    LineupInput,
    LineupsView,
    LineupView,
    PauseInput,
    StepInput,
    build_view,
    clock,
    clock_range,
    days_label,
    find_clash,
    resolve_day,
    sample_inputs,
    sample_view_model,
    start_labels,
    window_overlaps_pause,
)

# ---------------------------------------------------------------------------
# Pure view-model
# ---------------------------------------------------------------------------


def _display(view: Any, display_id: str) -> DisplayView:
    return cast(DisplayView, next(d for d in view.displays if d.id == display_id))


def _lineup(d: DisplayView, lineup_id: str) -> LineupView:
    return next(lu for lu in d.lineups if lu.id == lineup_id)


def test_sample_is_json_serialisable_and_mirrors_the_mockup_now() -> None:
    view = sample_view_model()
    data = view.to_dict()
    json.dumps(data)  # no dataclass / datetime left behind
    assert data["now"]["weekday"] == 2 and data["now"]["hhmm"] == "18:12"
    assert [d["id"] for d in data["displays"]] == ["frame", "kitchen"]
    assert data["lineup_count"] == 6
    # Two displays have multi-step lineups, so later-today rows fold too.
    assert data["fold_default"] is True


def test_connected_frame_in_sync() -> None:
    frame = _display(sample_view_model(), "frame")
    assert frame.link.label == "Online" and frame.link.connected and frame.link.live
    assert frame.size_label == "1440×2560" and frame.orientation == "p"
    assert frame.pause is not None and frame.pause.label == "23:00–06:30"
    assert frame.refreshes_today == 19  # dinner 6 + night 1 + breakfast 12
    assert frame.on_screen is not None and frame.on_screen.name == "Family"
    assert frame.on_screen.aspect == "1440/2560"
    assert frame.in_sync is True and frame.queued is None
    assert frame.scheduled is not None
    assert (frame.scheduled.lineup_id, frame.scheduled.step_index) == ("dinner", 2)
    assert frame.scheduled.since == "18:00"
    nu = frame.next_update
    assert nu is not None
    assert (nu.name, nu.at_label, nu.from_label, nu.pct, nu.in_label) == (
        "AI News",
        "18:30",
        "18:00",
        40,
        "in 18 min",
    )
    assert nu.text == "Frame is connected, so it lands right away."
    assert (frame.next_line.before, frame.next_line.at) == ("AI News next at", "18:30")
    assert frame.push_lands == "lands now"
    assert frame.show_title == "Dinner (Mon–Wed), step 3 of 6, on screen since 18:00"


def test_sleeping_display_with_queued_step() -> None:
    kitchen = _display(sample_view_model(), "kitchen")
    assert kitchen.link.label == "Wakes at 18:18" and not kitchen.link.connected
    assert kitchen.on_screen is not None and kitchen.on_screen.name == "Bus times"
    assert kitchen.in_sync is False
    assert kitchen.queued is not None
    assert (kitchen.queued.name, kitchen.queued.sent_at, kitchen.queued.lands) == (
        "Weather",
        "18:00",
        "~18:18",
    )
    nu = kitchen.next_update
    assert nu is not None and nu.warn
    assert (nu.at_label, nu.from_label, nu.pct, nu.in_label) == (
        "~18:18",
        "17:58 check-in",
        70,
        "in ~6 min",
    )
    assert nu.then_text == "Then Calendar at 18:30, lands ~18:38"
    line = kitchen.next_line
    assert (line.before, line.at, line.after) == ("Weather next, lands", "~18:18", "when it wakes")
    assert kitchen.push_lands == "lands ~18:18"

    row = _lineup(kitchen, "kitchen")
    pills = [(s.name, s.pill.kind if s.pill else None) for s in row.steps]
    assert pills == [
        ("Weather", "queued"),
        ("Calendar", "next"),
        ("Recipes", None),
        ("Bus times", "now"),
    ]
    weather = row.steps[0]
    assert weather.queued and weather.current and not weather.on_screen
    assert weather.pill is not None and weather.pill.label == "Queued · ~18:18"
    assert row.steps[1].pill is not None and row.steps[1].pill.label == "Next 18:30"
    assert row.steps[3].on_screen
    assert row.live_now is not None and row.live_now.in_sync is False
    assert row.live_now.lands == "~18:18"
    assert row.steps[-1].separator == "loop" and row.steps[0].separator == "arrow"


def test_status_tags_ordering_and_day_sets() -> None:
    frame = _display(sample_view_model(), "frame")
    got = [(lu.id, lu.status, lu.status_label) for lu in frame.lineups]
    assert got == [
        ("dinner", "live", "Live"),
        ("night", "later_today", "Later today"),
        ("breakfast", "not_today", "Tue–Fri"),
        ("weekend", "not_today", "Weekends"),
        ("monday", "not_today", "Mondays"),
    ]
    dinner = _lineup(frame, "dinner")
    assert dinner.days == [True, True, True, False, False, False, False]
    assert dinner.today_index == 2
    assert (dinner.step_count, dinner.step_label, dinner.cycle_label) == (6, "30m", "3h")
    assert dinner.progress is not None
    assert (dinner.progress.window_pct, dinner.progress.ends_in_label) == (40, "1h 48m")
    assert dinner.progress.refreshes_today == 6
    assert dinner.actions.primary == "skip"
    assert not dinner.folded

    night = _lineup(frame, "night")
    assert night.window is not None and night.window.label == "at 22:00"
    assert night.progress is not None and night.progress.starts_in_label == "3h 48m"
    assert night.actions.primary == "push" and night.folded  # fold_default

    breakfast = _lineup(frame, "breakfast")
    assert breakfast.next_run is not None
    assert breakfast.next_run.day_label == "tomorrow"
    assert breakfast.next_run.text == "next Thu 06:30"
    assert _lineup(frame, "weekend").next_run.day_label == "Sat"  # type: ignore[union-attr]
    assert _lineup(frame, "monday").next_run.day_label == "Mon"  # type: ignore[union-attr]
    assert all(lu.folded for lu in frame.lineups if lu.status == "not_today")


def test_pause_overlap_flag_and_held_next_run() -> None:
    frame = _display(sample_view_model(), "frame")
    monday = _lineup(frame, "monday")
    assert monday.window is not None and monday.window.overlaps_pause
    assert monday.window.pause_title and "23:00–06:30" in monday.window.pause_title
    assert monday.next_run is not None and monday.next_run.held
    # Quiet hours include their end minute (quiet_hours.is_in_window), so a
    # run at 06:30 under 23:00-06:30 is held to 06:31.
    assert monday.next_run.lands == "Mon 06:31"
    assert "Held until Mon 06:31 by Quiet hours" in monday.next_run.note
    # The flag ignores that one inclusive minute: starting on the minute
    # quiet hours end is not an overlap on the page.
    for lid in ("dinner", "night", "breakfast", "weekend"):
        win = _lineup(frame, lid).window
        assert win is not None and not win.overlaps_pause, lid


def test_pause_parts_match_quiet_hours_predicate() -> None:
    """``_pause_parts`` and ``in_pause`` agree with ``quiet_hours.is_in_window``
    minute by minute, for every weekday. The display spans drawn on the bar
    are the same minus exactly the inclusive end minute (not on all-day
    days)."""
    from datetime import datetime, timedelta

    from app.lineups_view import _pause_display_parts, _pause_parts, _quiet_window, in_pause
    from app.quiet_hours import is_in_window

    monday = datetime(2024, 1, 1)
    for pause in (
        PauseInput(23 * 60, 6 * 60 + 30),
        PauseInput(22 * 60, 6 * 60, days=(0,)),
        PauseInput(1 * 60, 5 * 60, days=(2, 3), all_day=(6,)),
    ):
        window = _quiet_window(pause)
        for m in range(7 * 1440):
            expected = is_in_window(window, monday + timedelta(minutes=m), None)
            day, dm = divmod(m, 1440)
            parts = any(s <= dm < e for s, e in _pause_parts(pause, day))
            assert parts == expected == in_pause(pause, m), (pause, m)
            trimmed = dm == pause.end_min and day not in pause.all_day
            shown = any(s <= dm < e for s, e in _pause_display_parts(pause, day))
            assert shown == (expected and not trimmed), (pause, m)


def test_window_overlaps_pause_judges_each_day_on_its_own() -> None:
    # Friday-only 23:00-06:30 covers Friday 00:00-06:30 and 23:00-24:00, the
    # way quiet_hours.is_in_window reads it; Saturday morning is not paused.
    pause = PauseInput(23 * 60, 6 * 60 + 30, days=(4,))
    early = LineupInput(
        "early", "Early", "cycle", (StepInput("a", 30),), days=(5,), start="06:00", end="08:00"
    )
    assert not window_overlaps_pause(pause, early)
    assert window_overlaps_pause(pause, replace(early, days=(4,)))
    push = LineupInput("p", "P", "daily", (StepInput("a"),), days=(2,), start="23:30")
    assert not window_overlaps_pause(pause, push)  # not on Fri
    assert window_overlaps_pause(pause, replace(push, days=(4,)))


def test_overnight_interval_runs_only_on_its_own_days() -> None:
    """A Monday-only 22:00-06:00 interval window runs Monday 00:00-06:00 and
    22:00-24:00 (the scheduler's per-day window gate) and nothing Tuesday."""
    from app.lineups_view import day_spans

    night = LineupInput(
        "n",
        "N",
        "interval",
        (StepInput("x"),),
        days=(0,),
        start="22:00",
        end="06:00",
        interval_minutes=60,
    )
    assert day_spans(night) == [(0, 361), (1320, 1440)]
    tue = LineupInput(
        "t", "T", "cycle", (StepInput("x", 30),), days=(1,), start="05:00", end="07:00"
    )
    assert find_clash(night, [tue]) is None
    clash = find_clash(night, [replace(tue, days=(0,))])
    assert clash is not None and clash.day == 0


def test_multi_item_steps() -> None:
    frame = _display(sample_view_model(), "frame")
    dinner = _lineup(frame, "dinner")
    art = next(s for s in dinner.steps if s.dashboard_id == "art")
    quotes = next(s for s in dinner.steps if s.dashboard_id == "quotes")
    assert art.item_count == 12 and len(art.items) == 12
    assert art.items[0].label == "Art 1 of 12"
    assert quotes.item_count == 20
    assert art.conditional is True
    single = next(s for s in dinner.steps if s.dashboard_id == "family")
    assert single.item_count == 1 and single.items == []
    assert single.position == "3/6" and single.on_screen and single.pill is not None
    assert single.pill.kind == "now"


def test_day_bar_today() -> None:
    frame = _display(sample_view_model(), "frame")
    day = frame.day
    assert day.weekday == 2 and day.is_today and day.relative == "today"
    assert day.now_min == 18 * 60 + 12
    segs = [(s.lineup_id, s.kind, s.start_min, s.end_min) for s in day.segments]
    assert segs == [
        ("night", "hold", 0, 390),
        ("breakfast", "window", 390, 570),
        ("breakfast", "hold", 570, 1020),
        ("dinner", "window", 1020, 1200),
        ("dinner", "hold", 1200, 1320),
        ("night", "push", 1320, 1320),
        ("night", "hold", 1320, 1440),
    ]
    dinner = day.segments[3]
    assert dinner.live and not dinner.planned
    assert [t.minute for t in dinner.ticks if t.next] == [18 * 60 + 30]
    assert len(dinner.ticks) == 5  # six advances, the first sits on the edge
    roles = {p.minute: p.role for p in dinner.points if p.role}
    assert roles == {18 * 60: "now", 18 * 60 + 30: "next"}
    # Drawn end-exclusive, so Breakfast at 06:30 meets it exactly.
    assert [(p.start_min, p.end_min) for p in day.pause] == [(0, 390), (1380, 1440)]
    assert day.absent == ["Morning (weekend), runs Sat + Sun", "Morning (Mon), runs Mon"]


def _seg(kind: str, s: int, e: int) -> Any:
    return replace(
        _display(sample_view_model(), "frame").day.segments[1], kind=kind, start_min=s, end_min=e
    )


def test_start_labels_need_more_than_two_blocks() -> None:
    # Holds are filler, not blocks; one or two blocks keep the plain axis.
    two = [
        _seg("hold", 0, 360),
        _seg("window", 360, 540),
        _seg("hold", 540, 1320),
        _seg("push", 1320, 1320),
    ]
    assert start_labels(two) is None
    assert start_labels(two[1:2]) is None
    three = [*two, _seg("window", 1020, 1200)]
    got = start_labels(three)
    assert got is not None
    assert [(t.minute, t.label, t.pct, t.n) for t in got] == [
        (360, "06:00", 25.0, 5),
        (1020, "17:00", 70.8333, 5),
        (1320, "22:00", 91.6667, 5),
    ]
    # Two blocks starting together are one label.
    assert [t.minute for t in start_labels([*three, _seg("push", 360, 360)]) or []] == [
        360,
        1020,
        1320,
    ]


def test_start_labels_drop_the_shorter_of_two_close_starts() -> None:
    # 06:00 (15 min) and 06:15 (2 h) collide at every width; the longer
    # block keeps its label. 11:40 and 13:00 only fit on wide bars.
    segs = [
        _seg("window", 360, 375),
        _seg("window", 375, 495),
        _seg("window", 700, 760),
        _seg("window", 780, 1000),
    ]
    got = {t.minute: t.hide.split() for t in start_labels(segs) or []}
    every = [f"h{i}" for i in range(len(SCALE_TIERS))]
    assert got[360] == every and got[375] == [] and got[780] == []
    # 80 min apart is a 5ch label plus its gap only from a 99ch bar up.
    assert got[700] == every[:16]
    # Deterministic: the same input lays out the same way.
    assert start_labels(segs) == start_labels(list(segs))


def test_start_labels_follow_the_clock_style() -> None:
    # 12-hour labels are longer and vary in length; the layout uses each
    # label's own width, so they still never touch or leave the bar.
    import random

    from app.lineups_view import using_time_cycle

    segs = [_seg("window", 390, 480), _seg("window", 1020, 1200), _seg("push", 1320, 1320)]
    with using_time_cycle("h12"):
        got = start_labels(segs) or []
    assert [(t.label, t.n) for t in got] == [
        ("6:30\u202fAM", 7),
        ("5\u202fPM", 4),
        ("10\u202fPM", 5),
    ]
    rnd = random.Random(12)
    for _ in range(300):
        starts = sorted(rnd.sample(range(0, 1440, 5), rnd.randint(3, 12)))
        segs = [_seg("window", m, min(m + rnd.randint(0, 300), 1440)) for m in starts]
        with using_time_cycle("h12"):
            labels = start_labels(segs) or []
        for i, w in enumerate(SCALE_TIERS):
            shown = sorted(
                (min(t.minute / 1440 * w, w - t.n), t.n)
                for t in labels
                if f"h{i}" not in t.hide.split()
            )
            assert shown
            assert all(x >= 0 and x + n <= w for x, n in shown)
            assert all(b - (a + n) >= 0.5 - 1e-9 for (a, n), (b, _) in itertools.pairwise(shown))


def test_start_labels_never_overlap_or_leave_the_bar() -> None:
    import random

    rnd = random.Random(7)
    for _ in range(300):
        starts = sorted(rnd.sample(range(0, 1440, 5), rnd.randint(3, 12)))
        segs = [_seg("window", m, min(m + rnd.randint(0, 300), 1440)) for m in starts]
        labels = start_labels(segs) or []
        for i, w in enumerate(SCALE_TIERS):
            shown = [
                min(t.minute / 1440 * w, w - t.n) for t in labels if f"h{i}" not in t.hide.split()
            ]
            assert shown, "the longest block always keeps its label"
            assert all(0 <= x <= w - 5 for x in shown)
            assert all(b - a >= 5.5 - 1e-9 for a, b in itertools.pairwise(sorted(shown)))


def test_day_bar_tomorrow_and_queued_lane_role() -> None:
    view = sample_view_model(selected_day=resolve_day("+1", 2))
    frame = _display(view, "frame")
    assert view.selected_day == 3 and frame.day.weekday == 3
    day = frame.day
    assert day.relative == "tomorrow" and day.now_min is None and day.now_pct is None
    kinds = [(s.lineup_id, s.kind) for s in day.segments]
    assert ("dinner", "window") not in kinds  # Mon–Wed only
    breakfast = next(s for s in day.segments if s.lineup_id == "breakfast")
    assert breakfast.planned and not breakfast.live
    assert all(not p.role for s in day.segments for p in s.points)
    assert len(frame.week) == 7 and frame.week[2].is_today

    kitchen = _display(sample_view_model(), "kitchen")
    window = next(s for s in kitchen.day.segments if s.kind == "window")
    roles = {p.minute: p.role for p in window.points if p.role}
    assert roles == {18 * 60: "waiting", 18 * 60 + 30: "next"}


def _with_missing_art() -> LineupsView:
    displays, dashboards = sample_inputs()
    dashboards["art"] = DashboardInfo(id="art", name="art", missing=True)
    return build_view(displays, dashboards, now=SAMPLE_NOW, tz_name="UTC")


def test_step_on_a_deleted_dashboard_is_flagged_and_not_counted() -> None:
    real = _lineup(_display(sample_view_model(), "frame"), "dinner")
    dinner = _lineup(_display(_with_missing_art(), "frame"), "dinner")
    gone = next(s for s in dinner.steps if s.dashboard_id == "art")
    assert gone.missing and gone.name == "Missing dashboard"
    assert gone.push_title == "This dashboard was deleted; remove it from the lineup"
    assert not any(s.missing for s in dinner.steps if s.dashboard_id != "art")
    assert (dinner.step_count, dinner.missing_count) == (real.step_count - 1, 1)
    assert real.missing_count == 0 and not any(s.missing for s in real.steps)
    # Positions keep counting every step: the scheduler still walks it.
    assert gone.position == f"{gone.index + 1}/{real.step_count}"


def test_next_copy_never_names_a_deleted_dashboard_as_real() -> None:
    displays, dashboards = sample_inputs()
    # AI News is next on the frame; delete it.
    dashboards["ainews"] = DashboardInfo(id="ainews", name="ainews", missing=True)
    frame = _display(build_view(displays, dashboards, now=SAMPLE_NOW, tz_name="UTC"), "frame")
    assert frame.next_update is not None
    assert frame.next_update.name == "Missing dashboard (can't be shown)"
    assert frame.next_line.before == "Missing dashboard (can't be shown) next at"
    assert "ainews" not in json.dumps(frame.to_dict()["next_line"])


def test_holding_after_a_daily_push() -> None:
    displays, dashboards = sample_inputs()
    late = SAMPLE_NOW.replace(hour=22, minute=40)
    frame = _display(build_view(displays, dashboards, now=late), "frame")
    assert frame.scheduled is not None and frame.scheduled.holding
    assert frame.scheduled.name == "Night sky"
    night = _lineup(frame, "night")
    assert night.holding and night.status == "not_today"
    assert frame.lineups[0].id == "night"  # holding sorts with live
    # Next update: tomorrow's breakfast at 06:30, held to 06:31 because pause
    # hours include their end minute.
    assert frame.next_update is not None and frame.next_update.name == "Family"
    assert frame.next_update.at_label == "Thu 06:31" and frame.next_update.held


def test_disabled_lineup_is_off_and_last() -> None:
    displays, dashboards = sample_inputs()
    frame_in = displays[0]
    lineups = tuple(
        replace(lu, enabled=False) if lu.id == "night" else lu for lu in frame_in.lineups
    )
    displays[0] = replace(frame_in, lineups=lineups)
    frame = _display(build_view(displays, dashboards, now=SAMPLE_NOW), "frame")
    assert frame.lineups[-1].id == "night"
    night = frame.lineups[-1]
    assert (night.status, night.status_label, night.folded) == ("off", "Off", True)
    assert night.next_run is None
    assert frame.refreshes_today == 18
    assert all(s.lineup_id != "night" for s in frame.day.segments if s.kind != "hold")


def test_days_label_and_resolve_day() -> None:
    assert days_label(range(7)) == "Daily"
    assert days_label([5, 6]) == "Weekends"
    assert days_label([0, 1, 2, 3, 4]) == "Weekdays"
    assert days_label([0]) == "Mondays"
    assert days_label([1, 2, 3, 4]) == "Tue–Fri"
    assert days_label([0, 2]) == "Mon Wed"
    assert resolve_day("4", 2) == 4
    assert resolve_day("tomorrow", 6) == 0
    assert resolve_day("-1", 0) == 6
    assert resolve_day("nonsense", 2) == 2
    assert resolve_day(None, 5) == 5


def test_find_clash() -> None:
    a = LineupInput(
        "a", "A", "cycle", (StepInput("x", 30),), days=(0, 1), start="06:00", end="09:00"
    )
    b = LineupInput("b", "B", "cycle", (StepInput("x", 30),), days=(1,), start="08:00", end="10:00")
    clash = find_clash(a, [b])
    assert clash is not None and clash.other.id == "b" and clash.day == 1
    assert clash.message_for_window() == "Overlaps B on Tue (08:00–10:00)."
    assert find_clash(replace(a, days=(0,)), [b]) is None
    assert find_clash(a, [replace(b, enabled=False)]) is None
    # A window that wraps past midnight runs on its own day's clock only (the
    # scheduler's per-day gate): Monday's 22:00-07:00 does not reach Tuesday.
    night = LineupInput(
        "n",
        "N",
        "interval",
        (StepInput("x"),),
        days=(0,),
        start="22:00",
        end="07:00",
        interval_minutes=60,
    )
    assert find_clash(night, [replace(b, days=(1,), start="06:30", end="08:00")]) is None
    assert find_clash(night, [replace(b, days=(0,), start="06:30", end="08:00")]) is not None
    # A push at the very end of a window does not clash with it.
    push = LineupInput("p", "P", "daily", (StepInput("x"),), days=(0,), start="09:00")
    assert find_clash(push, [a]) is None
    assert find_clash(replace(push, start="08:59"), [a]) is not None


def test_mixed_dwells_and_interval_steps() -> None:
    mixed = LineupInput(
        "m",
        "Mixed",
        "cycle",
        (StepInput("x", 10), StepInput("y", 50)),
        start="18:00",
        end="20:00",
    )
    every = LineupInput(
        "i",
        "Every 15",
        "interval",
        (StepInput("y"),),
        start="18:00",
        end="19:00",
        interval_minutes=15,
        days=(3,),
    )
    displays, dashboards = sample_inputs()
    d = replace(displays[1], id="other", lineups=(mixed, every))
    view = build_view([d], dashboards, now=SAMPLE_NOW)
    out = view.displays[0]
    m = _lineup(out, "m")
    assert (m.step_minutes, m.step_label, m.cycle_label) == (None, "10m–50m", "1h")
    assert m.status == "live"
    # 18:00 x (10m), 18:10 y (50m), 19:00 x, 19:10 y: at 18:12 y is up, x next at 19:00.
    assert [s.current for s in m.steps] == [False, True]
    assert m.steps[0].next and m.steps[0].pill is not None
    assert m.steps[0].pill.label == "Next 19:00"
    i = _lineup(out, "i")
    assert (i.step_label, i.status, i.status_label) == ("15m", "not_today", "Thursdays")


# ---------------------------------------------------------------------------
# Clock style
# ---------------------------------------------------------------------------

NNBSP = "\u202f"


@pytest.mark.parametrize(
    ("minutes", "end", "h12", "h23"),
    [
        (0, False, "12 AM", "00:00"),
        (0, True, "12 AM", "00:00"),
        (5, False, "12:05 AM", "00:05"),
        (390, False, "6:30 AM", "06:30"),
        (660, False, "11 AM", "11:00"),
        (720, False, "12 PM", "12:00"),
        (735, False, "12:15 PM", "12:15"),
        (1080, False, "6 PM", "18:00"),
        (1380, False, "11 PM", "23:00"),
        (1439, True, "11:59 PM", "23:59"),
        (1440, True, "12 AM", "24:00"),
        (1440, False, "12 AM", "00:00"),
        (1830, True, "6:30 AM", "06:30"),  # a window wrapping past midnight
        (-30, False, "11:30 PM", "23:30"),
    ],
)
def test_clock_labels(minutes: int, end: bool, h12: str, h23: str) -> None:
    assert clock(minutes, end=end, cycle="h12") == h12.replace(" ", NNBSP)
    assert clock(minutes, end=end, cycle="h23") == h23
    assert clock(minutes, end=end) == h23  # the default outside a build


def test_clock_range() -> None:
    from app.lineups_view import using_time_cycle

    assert clock_range(1380, 390) == "23:00–06:30"
    assert clock_range(360, 1440) == "06:00–24:00"
    with using_time_cycle("h12"):
        assert clock_range(1380, 390) == f"11{NNBSP}PM–6:30{NNBSP}AM"
        assert clock_range(360, 1440) == f"6{NNBSP}AM–12{NNBSP}AM"
    assert clock_range(1380, 390) == "23:00–06:30"  # reset after the block


# Fields that carry canonical 24-hour data, not labels.
_DATA_KEYS = {"start", "end", "hhmm", "iso", "tz"}
_HHMM = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?!\u202f[AP]M)")  # a 24-hour time


def _labels(node: Any, key: str = "") -> list[str]:
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in _labels(v, k)]
    if isinstance(node, list):
        return [s for v in node for s in _labels(v, key)]
    if isinstance(node, str) and key not in _DATA_KEYS and not key.endswith("_url"):
        return [node]
    return []


def test_a_12_hour_view_has_no_24_hour_labels_and_keeps_its_data() -> None:
    data = sample_view_model(time_cycle="h12").to_dict()
    assert data["time_cycle"] == "h12" and data["schema_version"] == 1
    assert data["now"]["hhmm"] == "18:12"  # data stays 24-hour
    leaks = [s for s in _labels(data) if _HHMM.search(s)]
    assert leaks == []
    frame = next(d for d in data["displays"] if d["id"] == "frame")
    windows = [lu["window"] for lu in frame["lineups"] if lu["window"]]
    assert all(re.fullmatch(r"\d{2}:\d{2}", w["start"]) for w in windows)
    assert any(NNBSP + "AM" in w["label"] or NNBSP + "PM" in w["label"] for w in windows)
    assert frame["day"]["now_label"] == f"6:12{NNBSP}PM"
    assert frame["next_update"]["at_label"] == f"6:30{NNBSP}PM"


def test_a_24_hour_view_is_unchanged() -> None:
    plain = sample_view_model().to_dict()
    assert plain == sample_view_model(time_cycle="h23").to_dict()
    assert plain["time_cycle"] == "h23"
    frame = next(d for d in plain["displays"] if d["id"] == "frame")
    assert frame["day"]["now_label"] == "18:12"
    assert frame["next_update"]["at_label"] == "18:30"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    from app.main import REPO_ROOT, create_app
    from app.state.page_store import Page

    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    # Labels follow the app locale (the host's when unset); pin a 24-hour one.
    a.config["SETTINGS_STORE"].patch_section("app", {"locale": "en-GB"})
    for pid in ("news", "art", "sky"):
        a.config["PAGE_STORE"].save(Page(id=pid, name=pid.title(), device_ids=["panel"]))
    return a


@pytest.fixture
def client(app: Flask) -> Any:
    c = app.test_client()
    c.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = c.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "panel", "kind": "esp32_client"}),
    )
    assert resp.status_code == 201
    _seed(app)
    return c


def _seed(app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    weekdays = [0, 1, 2, 3, 4]

    def cycle(id_: str, name: str, start: str, end: str, days: list[int]) -> Deck:
        return Deck(
            id=id_,
            name=name,
            device_ids=["panel"],
            pages=[DeckPage(page_id="news"), DeckPage(page_id="art")],
            advance="timer",
            advance_interval_minutes=30,
            advance_anchor=start,
            advance_end_at=end,
            advance_days_of_week=days,
        )

    store = app.config["DECK_STORE"]
    store.upsert(cycle("morning", "Morning", "06:00", "09:00", weekdays))
    store.upsert(cycle("evening", "Evening", "17:00", "20:00", weekdays))
    store.upsert(cycle("weekend", "Weekend", "17:00", "20:00", [5]))
    store.upsert(
        Deck(
            id="night",
            name="Night",
            device_ids=["panel"],
            pages=[DeckPage(page_id="sky")],
            advance="timer",
            advance_trigger="daily",
            advance_fires_at="22:00",
        )
    )


def _deck(app: Flask, deck_id: str) -> Any:
    return app.config["DECK_STORE"].get(deck_id)


def test_index_still_renders(client: Any) -> None:
    assert client.get("/decks").status_code == 200


def test_view_json(client: Any) -> None:
    resp = client.get("/decks/view.json?day=%2B1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["selected_day"] == (data["now"]["weekday"] + 1) % 7
    panel = next(d for d in data["displays"] if d["id"] == "panel")
    assert {lu["id"] for lu in panel["lineups"]} == {"morning", "evening", "weekend", "night"}
    assert panel["day"]["weekday"] == data["selected_day"]
    morning = next(lu for lu in panel["lineups"] if lu["id"] == "morning")
    assert morning["window"]["label"] == "06:00–09:00"
    assert morning["actions"]["window_url"] == "/decks/morning/window"
    assert morning["actions"]["push_step_url"] == "/decks/morning/push-step"
    assert [s["dashboard_id"] for s in morning["steps"]] == ["news", "art"]
    only = client.get("/decks/view.json?display=nope").get_json()
    assert only["displays"] == []


@pytest.mark.parametrize(
    ("locale", "cycle", "label", "clash"),
    [
        ("en-US", "h12", f"6{NNBSP}AM–9{NNBSP}AM", f"at 10{NNBSP}PM"),
        ("de", "h23", "06:00–09:00", "at 22:00"),
    ],
)
def test_page_labels_follow_the_app_locale(
    client: Any, app: Flask, locale: str, cycle: str, label: str, clash: str
) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": locale})
    data = client.get("/decks/view.json").get_json()
    assert data["time_cycle"] == cycle
    panel = next(d for d in data["displays"] if d["id"] == "panel")
    morning = next(lu for lu in panel["lineups"] if lu["id"] == "morning")
    assert morning["window"]["label"] == label
    assert (morning["window"]["start"], morning["window"]["end"]) == ("06:00", "09:00")
    page = client.get("/decks").get_data(as_text=True)
    assert re.search(r'"time_cycle": ?"' + cycle, page)  # in the page's vm JSON
    resp = client.post("/decks/evening/window", json={"start": "17:00", "end": "24:00"})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == f"Overlaps Night on Mon ({clash})."
    # The edit response's own window field stays the 24-hour data form.
    ok = client.post("/decks/morning/window", json={"start": "07:00", "end": "10:00"})
    assert ok.get_json()["lineups"][0]["window"] == "07:00–10:00"


def test_edit_window_valid(client: Any, app: Flask) -> None:
    resp = client.post("/decks/morning/window", json={"start": "07:00", "end": "10:00"})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["ok"] and data["warnings"] == [] and data["pause_overlap"] is False
    deck = _deck(app, "morning")
    assert (deck.advance_anchor, deck.advance_end_at) == ("07:00", "10:00")
    assert data["displays"] and data["displays"][0]["id"] == "panel"
    # Until midnight meets Night's 22:00 push...
    resp = client.post("/decks/evening/window", json={"start": "17:00", "end": "24:00"})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "Overlaps Night on Mon (at 22:00)."
    # ...and once Night is off it saves, stored as no end.
    store = app.config["DECK_STORE"]
    store.upsert(_deck(app, "night").model_copy(update={"enabled": False}))
    resp = client.post("/decks/evening/window", json={"start": "17:00", "end": "24:00"})
    assert resp.status_code == 200
    assert _deck(app, "evening").advance_end_at is None


def test_edit_window_rejects_overlap(client: Any, app: Flask) -> None:
    resp = client.post("/decks/morning/window", json={"start": "16:00", "end": "18:00"})
    assert resp.status_code == 409
    assert resp.get_json()["error"].startswith("Overlaps Evening")
    assert _deck(app, "morning").advance_anchor == "06:00"


def test_edit_window_rejects_bad_input(client: Any) -> None:
    assert client.post("/decks/morning/window", json={"start": "9am"}).status_code == 400
    resp = client.post("/decks/morning/window", json={"start": "10:00", "end": "09:00"})
    assert resp.status_code == 400 and "after start" in resp.get_json()["error"]
    assert client.post("/decks/nope/window", json={"start": "10:00"}).status_code == 404


def test_daily_push_window_moves_fire_time(client: Any, app: Flask) -> None:
    resp = client.post("/decks/night/window", json={"start": "21:30"})
    assert resp.status_code == 200
    assert _deck(app, "night").advance_fires_at == "21:30"
    # A push inside another lineup's window clashes.
    resp = client.post("/decks/night/window", json={"start": "18:00"})
    assert resp.status_code == 409


def test_pause_overlap_is_a_warning(client: Any, app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "06:30",
        },
    )
    resp = client.post("/decks/morning/window", json={"start": "05:00", "end": "08:00"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pause_overlap"] is True
    assert "Quiet hours (23:00–06:30)" in data["warnings"][0]
    assert _deck(app, "morning").advance_anchor == "05:00"


def test_days_toggle_last_day_and_clash(client: Any, app: Flask) -> None:
    resp = client.post("/decks/weekend/days", json={"day": 6})
    assert resp.status_code == 200
    assert _deck(app, "weekend").advance_days_of_week == [5, 6]
    resp = client.post("/decks/weekend/days", json={"days": [6]})
    assert resp.status_code == 200
    resp = client.post("/decks/weekend/days", json={"day": 6, "on": False})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Weekend needs at least one day."
    assert _deck(app, "weekend").advance_days_of_week == [6]
    resp = client.post("/decks/weekend/days", json={"day": 0})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "Mon clashes with Evening (17:00–20:00)."
    assert _deck(app, "weekend").advance_days_of_week == [6]


def test_batch_save_is_atomic(client: Any, app: Flask) -> None:
    # Swapping two windows is only valid when both moves are checked together.
    resp = client.post(
        "/decks/edits",
        json={
            "edits": [
                {"id": "morning", "start": "17:00", "end": "20:00"},
                {"id": "evening", "start": "06:00", "end": "09:00"},
            ]
        },
    )
    assert resp.status_code == 200, resp.get_json()
    assert _deck(app, "morning").advance_anchor == "17:00"
    assert _deck(app, "evening").advance_anchor == "06:00"
    # One bad edit and nothing saves.
    resp = client.post(
        "/decks/edits",
        json={
            "edits": [
                {"id": "night", "start": "21:00"},
                {"id": "weekend", "days": [0]},
            ]
        },
    )
    assert resp.status_code == 409
    assert resp.get_json()["id"] == "weekend"
    assert _deck(app, "night").advance_fires_at == "22:00"
    assert _deck(app, "weekend").advance_days_of_week == [5]
    assert client.post("/decks/edits", json={"edits": []}).status_code == 400


class _FakePush:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stored_calls: list[tuple[bytes, dict[str, Any]]] = []

    def republish_composition(self, png: bytes, **kw: Any) -> Any:
        self.stored_calls.append((png, kw))
        return type("R", (), {"status": "sent"})()

    def push(self, page_id: str, **kw: Any) -> Any:
        self.calls.append((page_id, kw))
        return type("R", (), {"status": "sent"})()


def test_push_step(client: Any, app: Flask) -> None:
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    resp = client.post("/decks/morning/push-step", json={"index": 1})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["ok"] and data["page_id"] == "art" and data["name"] == "Art"
    assert data["displays"][0]["display_id"] == "panel"
    assert data["lands"].startswith("lands ")
    assert data["fresh"] is True
    assert fake.calls == [
        ("art", {"device_ids": {"panel"}, "bypass_coalesce": True, "force_publish": True})
    ]
    assert client.post("/decks/morning/push-step", json={"index": 9}).status_code == 400
    assert client.post("/decks/morning/push-step", json={"page_id": "sky"}).status_code == 400
    resp = client.post("/decks/morning/push-step", json={"page_id": "news", "display_id": "x"})
    assert resp.status_code == 400
    assert len(fake.calls) == 1


def test_existing_overlap_does_not_block_other_edits(client: Any, app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    # An all-day interval lineup already overlaps Night's 22:00 push on Sunday
    # (the scheduler settles that by priority). Editing it stays possible...
    app.config["DECK_STORE"].upsert(
        Deck(
            id="sunday",
            name="Sunday",
            device_ids=["panel"],
            pages=[DeckPage(page_id="sky")],
            advance="timer",
            advance_trigger="interval",
            advance_interval_minutes=60,
            advance_days_of_week=[6],
        )
    )
    resp = client.post("/decks/sunday/days", json={"days": [6]})
    assert resp.status_code == 200
    resp = client.post("/decks/night/window", json={"start": "21:45"})
    assert resp.status_code == 200
    # ...but a clash the edit introduces is still refused.
    resp = client.post("/decks/sunday/days", json={"day": 0})
    assert resp.status_code == 409
    assert resp.get_json()["error"].startswith("Mon clashes with")


# ---------------------------------------------------------------------------
# Audit regressions
# ---------------------------------------------------------------------------


def _one_display(*lineups: LineupInput, **kw: Any) -> Any:
    """The sample's connected frame carrying just ``lineups``."""
    displays, dashboards = sample_inputs()
    kw.setdefault("published", None)
    d = replace(displays[0], lineups=tuple(lineups), on_screen=None, **kw)
    return d, dashboards, None


def test_identical_start_times_do_not_crash() -> None:
    """Two lineups starting at the same minute used to make the next-update
    sort compare blocks and raise TypeError."""
    a = LineupInput(
        "a", "A", "cycle", (StepInput("art", 30),), days=(2,), start="20:00", end="21:00"
    )
    b = LineupInput("b", "B", "daily", (StepInput("comics"),), days=(2,), start="20:00")
    d, dashboards, _ = _one_display(a, b)
    view = build_view([d], dashboards, now=SAMPLE_NOW)
    nu = view.displays[0].next_update
    # A tie goes the scheduler's way: a timed record over a cycle...
    assert nu is not None and nu.lineup_id == "b" and nu.push_at_label == "20:00"
    # ...unless the cycle has the higher priority.
    d2 = replace(d, lineups=(replace(a, priority=5), b))
    nu2 = build_view([d2], dashboards, now=SAMPLE_NOW).displays[0].next_update
    assert nu2 is not None and nu2.lineup_id == "a"
    json.dumps(view.to_dict())


def test_interval_prediction_follows_the_last_fire() -> None:
    """A 30-minute interval that last fired at 06:07 fires next at 06:37,
    not on a grid from its 06:00 window start."""
    now = SAMPLE_NOW.replace(hour=6, minute=20)
    every = LineupInput(
        "i",
        "Every 30",
        "interval",
        (StepInput("weather"),),
        start="06:00",
        end="09:00",
        interval_minutes=30,
        last_fired=now.replace(minute=7).timestamp(),
    )
    d, dashboards, _ = _one_display(every)
    out = build_view([d], dashboards, now=now).displays[0]
    assert out.next_update is not None and out.next_update.push_at_label == "06:37"
    row = _lineup(out, "i")
    assert row.status == "live"
    window = next(s for s in out.day.segments if s.kind == "window")
    assert [p.minute for p in window.points][:3] == [367, 397, 427]


def test_overlapping_cycles_show_the_latest_fire() -> None:
    """With two windows running, the panel holds whichever fired last, not
    the one that started first."""
    early = LineupInput(
        "early", "Early", "cycle", (StepInput("art", 30),), days=(2,), start="17:00", end="20:00"
    )
    late = LineupInput(
        "late", "Late", "cycle", (StepInput("comics", 60),), days=(2,), start="18:05", end="20:00"
    )
    d, dashboards, _ = _one_display(early, late)
    out = build_view([d], dashboards, now=SAMPLE_NOW).displays[0]
    assert out.scheduled is not None and out.scheduled.lineup_id == "late"
    assert out.on_screen is not None and out.on_screen.dashboard_id == "comics"
    # The next fire is Early's 18:30 step.
    assert out.next_update is not None and out.next_update.push_at_label == "18:30"


def test_scheduler_projection_drives_the_next_update() -> None:
    from app.lineups_view import UpcomingInput

    every = LineupInput(
        "i",
        "Every 30",
        "interval",
        (StepInput("weather"),),
        start="06:00",
        end="22:00",
        interval_minutes=30,
    )
    at = SAMPLE_NOW.replace(hour=18, minute=41).timestamp()
    d, dashboards, _ = _one_display(
        every, upcoming=(UpcomingInput(at=at, lineup_id="i", dashboard_id="weather"),)
    )
    out = build_view([d], dashboards, now=SAMPLE_NOW).displays[0]
    assert out.next_update is not None and out.next_update.at_label == "18:41"
    # An unknown dashboard (conditions with a fallback) names the lineup.
    d2 = replace(d, upcoming=(UpcomingInput(at=at, lineup_id="i", dashboard_id=None),))
    nu = build_view([d2], dashboards, now=SAMPLE_NOW).displays[0].next_update
    assert nu is not None and nu.name == "Every 30"


def test_dst_rollback_next_run_is_never_in_the_past() -> None:
    """At New York's second 01:15 on 1 Nov 2026 a daily 01:30 run is 15
    minutes ahead, not 45 minutes behind."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 1, 1, 15, fold=1, tzinfo=tz)
    push = LineupInput("p", "P", "daily", (StepInput("art"),), start="01:30")
    d, dashboards, _ = _one_display(push, pause=None)
    out = build_view([d], dashboards, now=now).displays[0]
    row = _lineup(out, "p")
    assert row.next_run is not None
    assert row.next_run.epoch == datetime(2026, 11, 1, 6, 30, tzinfo=UTC).timestamp()
    assert row.next_run.epoch > now.timestamp()
    assert out.next_update is not None and out.next_update.at_epoch is not None
    assert out.next_update.at_epoch >= now.timestamp()


def test_unknown_glass_on_a_sleeping_display_is_queued_not_in_sync() -> None:
    displays, dashboards = sample_inputs()
    kitchen = replace(displays[1], on_screen=None)
    out = build_view([kitchen], dashboards, now=SAMPLE_NOW).displays[0]
    assert out.on_screen is None and out.on_screen_known is False
    assert out.in_sync is False
    assert out.queued is not None and out.queued.name == "Weather"
    assert out.next_line.after == "when it wakes"
    row = _lineup(out, "kitchen")
    assert row.steps[0].pill is not None and row.steps[0].pill.kind == "queued"
    # The sample's known glass still reads as known.
    assert _display(sample_view_model(), "kitchen").on_screen_known is True


def test_batch_cannot_move_two_lineups_into_each_other(client: Any, app: Flask) -> None:
    """Morning 06:00-09:00 and Evening 17:00-20:00 are disjoint; moving both
    to 08:00-19:00 in one batch creates a clash and must be refused."""
    resp = client.post(
        "/decks/edits",
        json={
            "edits": [
                {"id": "morning", "start": "08:00", "end": "19:00"},
                {"id": "evening", "start": "08:00", "end": "19:00"},
            ]
        },
    )
    assert resp.status_code == 409
    assert resp.get_json()["id"] == "morning"
    assert _deck(app, "morning").advance_anchor == "06:00"
    assert _deck(app, "evening").advance_anchor == "17:00"


def _second_client(app: Flask) -> Any:
    from app.auth import SESSION_KEY

    c = app.test_client()
    with c.session_transaction() as sess:
        sess[SESSION_KEY] = True
    return c


def test_concurrent_edits_are_one_transaction_each(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read, validate and write happen under the deck store's lock: two
    concurrent edits to one lineup both land, and two edits that only clash
    together cannot both pass their checks."""
    import threading
    import time as _time

    import app.deck_routes as routes

    real = routes._check_batch

    def slow(*args: Any, **kw: Any) -> None:
        _time.sleep(0.3)  # widen the read-validate-write window
        real(*args, **kw)

    monkeypatch.setattr(routes, "_check_batch", slow)
    other = _second_client(app)

    def run(fn: Any, out: list[Any]) -> None:
        out.append(fn())

    results: list[Any] = []
    threads = [
        threading.Thread(
            target=run,
            args=(lambda: client.post("/decks/weekend/window", json={"start": "18:00"}), results),
        ),
        threading.Thread(
            target=run,
            args=(lambda: other.post("/decks/weekend/days", json={"day": 6}), results),
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status_code for r in results) == [200, 200]
    deck = _deck(app, "weekend")
    assert deck.advance_anchor == "18:00" and deck.advance_days_of_week == [5, 6]

    results.clear()
    threads = [
        threading.Thread(
            target=run,
            args=(
                lambda: client.post(
                    "/decks/morning/window", json={"start": "10:00", "end": "12:00"}
                ),
                results,
            ),
        ),
        threading.Thread(
            target=run,
            args=(
                lambda: other.post(
                    "/decks/evening/window", json={"start": "11:00", "end": "13:00"}
                ),
                results,
            ),
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status_code for r in results) == [200, 409]


def test_view_json_with_two_lineups_starting_together(client: Any, app: Flask) -> None:
    store = app.config["DECK_STORE"]
    store.upsert(_deck(app, "weekend").model_copy(update={"advance_days_of_week": [0, 1, 2, 3, 4]}))
    assert client.get("/decks/view.json").status_code == 200


def test_unknown_fetched_frame_is_not_replaced_by_nav_state(client: Any, app: Flask) -> None:
    """A display that fetches its frames, with nothing fetched yet, must not
    borrow the nav record as what it shows."""
    app.config["DECK_NAV_STORE"].set("panel", "morning", "news")
    panel = client.get("/decks/view.json?display=panel").get_json()["displays"][0]
    assert panel["on_screen"] is None and panel["on_screen_known"] is False


def test_display_filter_builds_only_that_display(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.deck_routes as routes

    seen: list[list[str]] = []
    real = routes._panel_states

    def spy(devices: list[Any]) -> dict[str, Any]:
        seen.append([d.id for d in devices])
        return real(devices)

    monkeypatch.setattr(routes, "_panel_states", spy)
    data = client.get("/decks/view.json?display=nope").get_json()
    assert data["displays"] == [] and data["unassigned"] == []
    assert seen == [[]]


def test_gallery_items_are_cached_by_folder_mtime(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.deck_routes as routes
    from app.state.page_store import Cell, Page

    module = app.config["PLUGIN_REGISTRY"].get("picture_gallery").server_module
    folder = tmp_path / "photos"
    folder.mkdir()
    calls: list[str] = []

    def lister(name: str) -> list[str]:
        calls.append(name)
        return [f"{i:03d}.jpg" for i in range(500)]

    monkeypatch.setattr(module, "list_folder_files", lister)
    monkeypatch.setattr(module, "_folder_path", lambda name, data_dir: folder)
    monkeypatch.setattr(routes, "_GALLERY_CACHE", {})
    page = Page(
        id="g",
        name="G",
        cells=[
            Cell(
                id="c", plugin="picture_gallery", x=0, y=0, w=10, h=10, options={"folder": "photos"}
            )
        ],
    )
    with app.test_request_context():
        count, items = routes._step_items(page)
        assert count == 500 and len(items) == routes.MAX_STEP_ITEMS
        routes._step_items(page)
        assert calls == ["photos"]
        (folder / "new.jpg").write_bytes(b"x")
        import os

        st = folder.stat()
        os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        routes._step_items(page)
        assert calls == ["photos", "photos"]


def test_pause_flag_needs_a_real_overlap() -> None:
    """A window or push starting on the minute quiet hours end is not
    flagged; one reaching a full minute into them is. Delivery predictions
    still use the scheduler's inclusive end (held to 06:31)."""
    pause = PauseInput(23 * 60, 6 * 60 + 30)
    at_end = LineupInput("a", "A", "cycle", (StepInput("x", 30),), start="06:30", end="09:30")
    assert not window_overlaps_pause(pause, at_end)
    assert window_overlaps_pause(pause, replace(at_end, start="06:29"))
    assert window_overlaps_pause(pause, replace(at_end, start="06:00", end="09:00"))
    push = LineupInput("p", "P", "daily", (StepInput("x"),), start="06:30")
    assert not window_overlaps_pause(pause, push)
    assert window_overlaps_pause(pause, replace(push, start="06:29"))
    kitchen = _lineup(_display(sample_view_model(), "kitchen"), "kitchen")
    assert kitchen.window is not None and not kitchen.window.overlaps_pause


# ---------------------------------------------------------------------------
# Re-audit regressions
# ---------------------------------------------------------------------------


def test_projected_epochs_are_kept_exactly() -> None:
    """A projected update in New York's repeated 01:00 hour keeps its exact
    instant (second pass, seconds included) instead of being rebuilt from a
    wall-clock minute."""
    from zoneinfo import ZoneInfo

    from app.lineups_view import UpcomingInput

    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 1, 1, 20, fold=1, tzinfo=tz)  # 06:20 UTC
    at = datetime(2026, 11, 1, 6, 30, 15, tzinfo=UTC).timestamp()  # second 01:30:15
    every = LineupInput("i", "Every 30", "interval", (StepInput("art"),), interval_minutes=30)
    d, dashboards, _ = _one_display(
        every, pause=None, upcoming=(UpcomingInput(at=at, lineup_id="i", dashboard_id="art"),)
    )
    nu = build_view([d], dashboards, now=now).displays[0].next_update
    assert nu is not None and nu.at_epoch == at
    assert nu.push_at_label == "01:30" and nu.in_label == "in 11 min"


def _noon_override(**kw: Any) -> Any:
    from app.lineups_view import OnScreenInput, PublishedInput

    hourly = LineupInput(
        "hourly",
        "Hourly",
        "cycle",
        (StepInput("art", 60), StepInput("quotes", 60)),
        start="06:00",
        end="22:00",
    )
    noon = LineupInput("noon", "Noon", "daily", (StepInput("comics"),), start="12:00")
    displays, dashboards = sample_inputs()
    now = SAMPLE_NOW.replace(hour=12, minute=5)
    kitchen = replace(
        displays[1],
        lineups=(hourly, noon),
        reach=replace(displays[1].reach, next_checkin_at=now.replace(minute=20).timestamp()),
        on_screen=OnScreenInput("comics", now.replace(minute=0).timestamp(), "fetched"),
        **{"published": PublishedInput("comics", now.replace(minute=0).timestamp()), **kw},
    )
    return build_view([kitchen], dashboards, now=now).displays[0]


def test_daily_override_inside_a_rotation_is_not_queued() -> None:
    """Hourly rotation plus a noon daily push: at 12:05 a sleeping display
    showing the noon page is in sync, nothing is queued, and the next push
    is the rotation's 13:00 step."""
    for out in (_noon_override(), _noon_override(published=None)):
        assert out.in_sync is True and out.queued is None
        assert out.scheduled is not None and out.scheduled.lineup_id == "noon"
        assert out.next_update is not None and out.next_update.push_at_label == "13:00"
        hourly = _lineup(out, "hourly")
        assert not any(s.queued for s in hourly.steps)
        assert all(s.pill is None or s.pill.kind != "queued" for s in hourly.steps)


def test_queued_comes_from_the_published_frame() -> None:
    from app.lineups_view import PublishedInput

    displays, dashboards = sample_inputs()
    # Published Recipes (say, a manual push), fetched Bus times: Recipes is
    # what is queued, whatever the rotation's window says.
    kitchen = replace(displays[1], published=PublishedInput("recipes", SAMPLE_NOW.timestamp()))
    out = build_view([kitchen], dashboards, now=SAMPLE_NOW).displays[0]
    assert out.queued is not None and out.queued.name == "Recipes"
    assert out.next_line.before == "Recipes next, lands"
    row = _lineup(out, "kitchen")
    assert [s.dashboard_id for s in row.steps if s.queued] == []


def test_projection_decides_and_the_model_only_fills_in_when_it_is_missing() -> None:
    """On a Thursday a Monday-only lineup: with no projection available the
    model names its next run; a projection that ran and found nothing is
    believed ('No update scheduled'), while the row keeps its scheduled
    next run."""
    monday = LineupInput(
        "m", "Monday", "cycle", (StepInput("art", 60),), days=(0,), start="06:00", end="09:00"
    )
    thursday = SAMPLE_NOW.replace(day=29)
    d, dashboards, _ = _one_display(monday, pause=None, upcoming=None)
    out = build_view([d], dashboards, now=thursday).displays[0]
    assert out.next_update is not None and out.next_update.at_label == "Mon 06:00"
    d2 = replace(d, upcoming=())
    out2 = build_view([d2], dashboards, now=thursday).displays[0]
    assert out2.next_update is None and out2.next_line.before == "No update scheduled"
    row = _lineup(out2, "m")
    assert row.next_run is not None and row.next_run.text == "scheduled Mon 06:00"
    assert row.next_run.lands == ""
    assert "no delivery projected" in row.next_run.note


def test_daily_push_inside_pause_hours_shows_no_update(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real scheduler skips a daily fire inside quiet hours, rather
    than delivering it after the pause ends."""
    from app.state.deck_model import Deck, DeckPage

    sched = app.config["SCHEDULER"]
    real = sched.upcoming_for_device
    projections: list[list[Any]] = []

    def project(device_id: str, **kw: Any) -> list[Any]:
        events = cast(list[Any], real(device_id, **kw))
        projections.append(events)
        return events

    monkeypatch.setattr(sched, "_tz_provider", lambda: UTC)
    monkeypatch.setattr(sched, "upcoming_for_device", project)
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {
            "timezone": "UTC",
            "quiet_hours_enabled": True,
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "06:00",
        },
    )
    for d in ("morning", "evening", "weekend", "night"):
        app.config["DECK_STORE"].delete(d)
    app.config["DECK_STORE"].upsert(
        Deck(
            id="small_hours",
            name="Small hours",
            device_ids=["panel"],
            pages=[DeckPage(page_id="sky")],
            advance="timer",
            advance_trigger="daily",
            advance_fires_at="01:00",
        )
    )
    panel = client.get("/decks/view.json?display=panel").get_json()["displays"][0]
    assert panel["next_update"] is None
    assert panel["next_line"]["before"] == "No update scheduled"
    row = panel["lineups"][0]
    assert row["next_run"]["hhmm"] == "01:00"  # the schedule row still says when it runs
    assert row["next_run"]["text"].startswith("scheduled ")
    assert row["next_run"]["lands"] == ""
    assert projections == [[]]


def test_projection_searches_a_week_and_a_day(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    sched = app.config["SCHEDULER"]
    real = sched.upcoming_for_device

    def spy(device_id: str, **kw: Any) -> Any:
        calls.append(kw)
        return real(device_id, **kw)

    monkeypatch.setattr(sched, "upcoming_for_device", spy)
    assert client.get("/decks/view.json?display=panel").status_code == 200
    assert calls and calls[0]["hours"] >= 8 * 24


@pytest.mark.parametrize(
    "edit",
    [
        {"id": "weekend", "toggle_day": "1"},
        {"id": "weekend", "toggle_day": []},
        {"id": "weekend", "toggle_day": {}},
        {"id": "weekend", "toggle_day": True},
        {"id": "weekend", "toggle_day": 7},
        {"id": "weekend", "toggle_day": 6, "on": "false"},
        {"id": "weekend", "days": ["1"]},
        {"id": "weekend", "days": [True]},
        {"id": "weekend", "days": "0,1"},
        {"id": "weekend", "start": 630},
        {"id": "weekend", "end": ["20:00"]},
        {"id": 5, "start": "18:00"},
        {"id": "weekend", "bogus": 1},
    ],
)
def test_edits_type_check_every_field(client: Any, app: Flask, edit: dict[str, Any]) -> None:
    before = _deck(app, "weekend")
    resp = client.post("/decks/edits", json={"edits": [edit]})
    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["ok"] is False and resp.get_json()["error"]
    assert _deck(app, "weekend") == before


def test_day_toggle_and_push_step_type_checks(client: Any, app: Flask) -> None:
    before = _deck(app, "weekend")
    for body in (
        {"day": 6, "on": "false"},
        {"day": "6"},
        {"day": True},
        {"days": [5, "6"]},
        {"days": [5], "view_day": True},
    ):
        resp = client.post("/decks/weekend/days", json=body)
        assert resp.status_code == 400, (body, resp.get_json())
    assert _deck(app, "weekend") == before
    resp = client.post("/decks/weekend/days", json={"day": 6, "on": True})
    assert resp.status_code == 200
    assert client.post("/decks/morning/window", json={"start": 630}).status_code == 400
    assert (
        client.post("/decks/morning/window", json={"start": "07:00", "day": [1]}).status_code == 400
    )
    app.config["PUSH_MANAGER"] = _FakePush()
    push_bodies: tuple[dict[str, Any], ...] = (
        {"index": "1"},
        {"index": True},
        {"index": 1.0},
        {"page_id": 5},
        {"index": 0, "display_id": 3},
    )
    for push_body in push_bodies:
        resp = client.post("/decks/morning/push-step", json=push_body)
        assert resp.status_code == 400, (push_body, resp.get_json())
    assert app.config["PUSH_MANAGER"].calls == []


# ---------------------------------------------------------------------------
# Second review
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [("22:00", "06:00"), ("06:00", "09:00"), (None, "07:30"), ("20:00", None), (None, None)],
)
def test_interval_spans_match_the_scheduler_window_gate(start: Any, end: Any) -> None:
    """``day_spans`` agrees minute by minute with the scheduler's own
    interval window test (``device_upcoming._window_contains``)."""
    from datetime import time

    from app.device_upcoming import _window_contains
    from app.lineups_view import day_spans

    lu = LineupInput("i", "I", "interval", (StepInput("x"),), start=start, end=end)
    spans = day_spans(lu)
    for m in range(1440):
        inside = any(s <= m < e for s, e in spans)
        assert inside == _window_contains(start, end, time(m // 60, m % 60)), (start, end, m)


@pytest.mark.parametrize(
    ("anchor", "end_at", "days"),
    [
        ("06:00", "09:00", [2]),
        ("17:00", None, [2]),
        ("20:00", "08:00", [2]),
        ("06:00", "09:00", [3]),
    ],
)
def test_cycle_spans_match_compute_step_window(anchor: str, end_at: Any, days: list[int]) -> None:
    """A cycle's span on a day matches when ``scheduler.compute_step_window``
    has a step for it (Wednesday 28 Oct 2026, UTC)."""
    from datetime import timedelta

    from app.lineups_view import day_spans
    from app.scheduler import compute_step_window
    from app.state.rotation_model import Rotation, RotationStep

    rotation = Rotation(
        id="r",
        name="R",
        steps=[RotationStep(page_id="x", dwell_minutes=30)],
        anchor=anchor,
        end_at=end_at,
        days_of_week=days,
    )
    lu = LineupInput(
        "r", "R", "cycle", (StepInput("x", 30),), days=tuple(days), start=anchor, end=end_at
    )
    spans = day_spans(lu) if 2 in days else []
    midnight = SAMPLE_NOW.replace(hour=0, minute=0)
    for m in range(1440):
        state = compute_step_window(rotation, midnight + timedelta(minutes=m), UTC)
        inside = any(s <= m < e for s, e in spans)
        assert inside == (state is not None), (anchor, end_at, days, m)


def test_a_pause_that_ends_first_does_not_hold_later_runs() -> None:
    from app.next_update import Hold

    displays, dashboards = sample_inputs()
    paused = replace(
        displays[0], hold=Hold(SAMPLE_NOW.replace(hour=18, minute=20).timestamp(), "a pause")
    )
    frame = build_view([paused], dashboards, now=SAMPLE_NOW).displays[0]
    nu = frame.next_update
    assert nu is not None and nu.at_label == "18:30" and not nu.held  # pause over by 18:30
    still = replace(
        paused, hold=Hold(SAMPLE_NOW.replace(hour=18, minute=45).timestamp(), "a pause")
    )
    nu2 = build_view([still], dashboards, now=SAMPLE_NOW).displays[0].next_update
    assert nu2 is not None and nu2.held and nu2.at_label == "18:45"


def test_all_day_pause_label() -> None:
    from app.lineups_view import pause_label

    assert pause_label(PauseInput(0, 0, days=(), all_day=(5, 6))) == "All day Sat, Sun"
    assert pause_label(PauseInput(0, 0, days=(), all_day=tuple(range(7)))) == "All day"
    assert pause_label(PauseInput(23 * 60, 390)) == "23:00–06:30"


def test_parse_hhmm_is_ascii_only() -> None:
    from app.lineups_view import parse_hhmm

    assert parse_hhmm("06:30") == 390 and parse_hhmm("6:30") == 390
    assert parse_hhmm("٠٦:٣٠") is None  # Arabic-Indic digits
    assert parse_hhmm("06:3") is None and parse_hhmm("24:01") is None


def test_unencoded_plus_offset_and_schema_version(client: Any) -> None:
    assert resolve_day(" 1", 2) == 3  # "+1" decoded from a query string
    data = client.get("/decks/view.json?day=+1").get_json()
    assert data["selected_day"] == (data["now"]["weekday"] + 1) % 7
    data = client.get("/decks/view.json?offset=2").get_json()
    assert data["selected_day"] == (data["now"]["weekday"] + 2) % 7
    assert data["schema_version"] == 1
    assert client.get("/decks/view.json?offset=x").status_code == 400


def test_interval_end_2400_is_no_end(client: Any, app: Flask) -> None:
    """For an interval window 24:00 means to the end of the day (stored as
    no end), not 00:00, which would wrap and clash with Sunday's early run."""
    from app.state.deck_model import Deck, DeckPage

    store = app.config["DECK_STORE"]
    store.upsert(
        Deck(
            id="late",
            name="Late",
            device_ids=["panel"],
            pages=[DeckPage(page_id="sky")],
            advance="timer",
            advance_trigger="interval",
            advance_interval_minutes=60,
            advance_window_start="23:00",
            advance_window_end="23:30",
            advance_days_of_week=[6],
        )
    )
    store.upsert(_deck(app, "weekend").model_copy(update={"advance_days_of_week": [6]}))
    store.upsert(
        _deck(app, "morning").model_copy(
            update={
                "advance_anchor": "00:30",
                "advance_end_at": "02:00",
                "advance_days_of_week": [6],
            }
        )
    )
    store.upsert(_deck(app, "night").model_copy(update={"enabled": False}))
    resp = client.post("/decks/late/window", json={"start": "23:00", "end": "24:00"})
    assert resp.status_code == 200, resp.get_json()
    late = _deck(app, "late")
    assert (late.advance_window_start, late.advance_window_end) == ("23:00", None)
    resp = client.post("/decks/late/window", json={"start": "23:00", "end": "23:00"})
    assert resp.status_code == 400 and resp.get_json()["id"] == "late"


def test_time_format_errors_name_the_lineup(client: Any) -> None:
    resp = client.post("/decks/morning/window", json={"start": "7am"})
    assert resp.status_code == 400 and resp.get_json()["id"] == "morning"


def test_edit_saves_even_if_the_refresh_fails(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.deck_routes as routes

    def boom(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("view broke")

    monkeypatch.setattr(routes, "_build_lineups_view", boom)
    resp = client.post("/decks/weekend/days", json={"day": 6, "on": True})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] and data["displays"] == [] and "Reload" in data["warning"]
    assert _deck(app, "weekend").advance_days_of_week == [5, 6]
    resp = client.get("/decks/view.json")
    assert resp.status_code == 500 and resp.get_json() == {
        "ok": False,
        "error": "The Lineups view could not be built.",
    }
    assert client.get("/decks").status_code == 200  # page still renders


def test_batch_toggle_needs_on(client: Any, app: Flask) -> None:
    resp = client.post("/decks/edits", json={"edits": [{"id": "weekend", "toggle_day": 6}]})
    assert resp.status_code == 400 and "'on'" in resp.get_json()["error"]
    body = {"edits": [{"id": "weekend", "toggle_day": 6, "on": True}]}
    assert client.post("/decks/edits", json=body).status_code == 200
    assert client.post("/decks/edits", json=body).status_code == 200  # retry is a no-op
    assert _deck(app, "weekend").advance_days_of_week == [5, 6]


def test_schedule_edits_keep_warm_frames(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.deck_routes as routes

    calls: list[str] = []
    monkeypatch.setattr(routes, "_invalidate", lambda deck: calls.append(deck.id))
    assert client.post("/decks/morning/window", json={"start": "07:00"}).status_code == 200
    assert client.post("/decks/weekend/days", json={"day": 6, "on": True}).status_code == 200
    assert calls == []


def test_view_model_is_built_once_per_request(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.deck_routes as routes

    calls: list[Any] = []
    real = routes._build_lineups_view

    def spy(*a: Any) -> Any:
        calls.append(a)
        return real(*a)

    monkeypatch.setattr(routes, "_build_lineups_view", spy)
    with app.test_request_context("/decks"):
        routes.build_lineups_view("today")
        routes.build_lineups_view("today")
    assert len(calls) == 1


def test_scheduler_status_feeds_interval_last_fire(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter reads the last fire through ``Scheduler.status()``."""
    from app.state.deck_model import Deck, DeckPage
    from app.tz_resolve import app_timezone

    now = datetime.now(app_timezone())
    if now.hour == 0 and now.minute < 8:
        pytest.skip("needs a last fire earlier today")
    for d in ("morning", "evening", "weekend", "night"):
        app.config["DECK_STORE"].delete(d)
    app.config["DECK_STORE"].upsert(
        Deck(
            id="tick",
            name="Tick",
            device_ids=["panel"],
            pages=[DeckPage(page_id="news")],
            advance="timer",
            advance_trigger="interval",
            advance_interval_minutes=600,
        )
    )
    fired = now.replace(second=0, microsecond=0) - __import__("datetime").timedelta(minutes=7)
    sched = app.config["SCHEDULER"]
    monkeypatch.setattr(sched, "status", lambda: {"tick": {"last_fired": fired.timestamp()}})
    panel = client.get("/decks/view.json?display=panel").get_json()["displays"][0]
    window = next(s for s in panel["day"]["segments"] if s["kind"] == "window")
    assert fired.hour * 60 + fired.minute in [p["minute"] for p in window["points"]]


def test_projection_failure_is_logged(
    client: Any, app: Flask, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*a: Any, **kw: Any) -> Any:
        raise RuntimeError("projection broke")

    monkeypatch.setattr(app.config["SCHEDULER"], "upcoming_for_device", broken)
    with caplog.at_level("ERROR"):
        resp = client.get("/decks/view.json?display=panel")
    assert resp.status_code == 200
    assert "projection failed for panel" in caplog.text
    assert resp.get_json()["displays"][0]["next_update"] is not None  # model fallback


def test_next_update_comes_from_the_real_projection(client: Any, app: Flask) -> None:
    """Through the real ``Scheduler.upcoming_for_device`` (not the fallback):
    the header's next update is the projection's next event."""
    from app.quiet_hours import resolve_quiet_hours

    sched = app.config["SCHEDULER"]
    device = next(d for d in app.config["DEVICE_REGISTRY"].all() if d.id == "panel")
    before = datetime.now(UTC)
    panel = client.get("/decks/view.json?display=panel").get_json()["displays"][0]
    events = sched.upcoming_for_device(
        "panel",
        now=before,
        hours=8 * 24,
        limit=12,
        quiet_window=resolve_quiet_hours({}, device),
    )
    assert events, "the seeded lineups project at least one update"
    first = events[0]
    nu = panel["next_update"]
    assert nu["lineup_id"] == first.lineup_id
    assert abs(nu["at_epoch"] - max(first.scheduled_at.timestamp(), before.timestamp())) < 120


def test_deck_store_transaction(tmp_path: Path) -> None:
    from app.state.deck_model import Deck, DeckPage
    from app.state.deck_store import DeckStore

    store = DeckStore(tmp_path / "decks.json")
    deck = Deck(id="a", name="A", pages=[DeckPage(page_id="p")])
    store.upsert(deck)
    notes: list[int] = []
    store.add_listener(lambda: notes.append(1))
    raw = (tmp_path / "decks.json").read_text()

    def failing(decks: dict[str, Deck]) -> tuple[list[Deck], None]:
        decks["a"] = decks["a"].model_copy(update={"name": "changed"})
        raise ValueError("refused")

    with pytest.raises(ValueError):
        store.transaction(failing)
    assert (tmp_path / "decks.json").read_text() == raw and notes == []
    assert store.transaction(lambda decks: ([], "nothing")) == "nothing"
    assert notes == []
    renamed = deck.model_copy(update={"name": "B"})
    assert store.transaction(lambda decks: ([renamed], len(decks))) == 1
    assert store.get("a").name == "B" and notes == [1]  # type: ignore[union-attr]


def test_new_posts_need_a_session_and_same_origin(app: Flask, client: Any) -> None:
    anon = app.test_client()
    for url, body in (
        ("/decks/morning/window", {"start": "07:00"}),
        ("/decks/morning/days", {"days": [0]}),
        ("/decks/edits", {"edits": [{"id": "morning", "days": [0]}]}),
        ("/decks/morning/push-step", {"index": 0}),
    ):
        resp = anon.post(url, json=body)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], url
        cross = client.post(url, json=body, headers={"Sec-Fetch-Site": "cross-site"})
        assert cross.status_code == 403, url
    assert _deck(app, "morning").advance_days_of_week == [0, 1, 2, 3, 4]


def test_route_4xx_branches(client: Any) -> None:
    assert client.post("/decks/morning/window", json={}).status_code == 400
    assert client.post("/decks/morning/days", json={}).status_code == 400
    assert client.post("/decks/nope/days", json={"days": [0]}).status_code == 404
    assert (
        client.post("/decks/edits", json={"edits": [{"id": "nope", "days": [0]}]}).status_code
        == 404
    )
    assert client.post("/decks/edits", json={"edits": "x"}).status_code == 400
    assert client.post("/decks/edits", data="not json").status_code == 400
    assert client.post("/decks/nope/push-step", json={"index": 0}).status_code == 404
    assert client.post("/decks/morning/push-step", json={}).status_code == 400


def test_nothing_published_means_nothing_queued() -> None:
    """When the server knows nothing was published to a display, the model's
    scheduled step is not reported as queued (it was never sent)."""
    displays, dashboards = sample_inputs()
    kitchen = replace(displays[1], on_screen=None, published=None, published_known=True)
    out = build_view([kitchen], dashboards, now=SAMPLE_NOW).displays[0]
    assert out.queued is None and out.on_screen_known is False and out.in_sync is False
    assert not any(s.queued for s in _lineup(out, "kitchen").steps)
    # Unknown publish state keeps the model's stand-in.
    legacy = replace(kitchen, published_known=False)
    assert build_view([legacy], dashboards, now=SAMPLE_NOW).displays[0].queued is not None


def test_timeline_hold_carries_the_last_rotation_step() -> None:
    view = sample_view_model()
    frame = _display(view, "frame")
    day = frame.week[view.now.weekday]
    dinner = _lineup(frame, "dinner")
    hold = next(s for s in day.segments if s.kind == "hold" and s.lineup_id == dinner.id)
    last = dinner.steps[-1]
    assert hold.carried is not None
    assert hold.carried.to_dict() == {
        "dashboard_id": last.dashboard_id,
        "name": last.name,
        "thumb_url": last.thumb_url,
        "aspect": last.aspect,
        "width": last.width,
        "height": last.height,
        "orientation": last.orientation,
        "since": None,
        "missing": False,
    }
    assert hold.from_lineup_id == dinner.id
    assert hold.from_prev_day is False and hold.from_day_label is None
    assert hold.predicted is True and hold.is_current is False
    assert hold.steps == [] and hold.steps_total == 0


def test_timeline_hold_carries_across_midnight_and_week_boundary() -> None:
    view = sample_view_model()
    frame = _display(view, "frame")
    for day_index in (view.now.weekday, 0):
        day = frame.week[day_index]
        hold = next(s for s in day.segments if s.kind == "hold" and s.start_min == 0)
        previous = frame.week[(day_index - 1) % 7]
        previous_block = [s for s in previous.segments if s.kind != "hold"][-1]
        assert hold.from_prev_day is True and hold.from_day_label == previous.label
        assert hold.from_lineup_id == previous_block.lineup_id
        assert hold.carried is not None
        assert hold.carried.dashboard_id == previous_block.points[-1].dashboard_id
        assert hold.predicted is False and hold.is_current is False


def test_timeline_current_and_future_holds() -> None:
    displays, dashboards = sample_inputs()
    frame = _display(sample_view_model(), "frame")
    hold = next(s for s in frame.day.segments if s.kind == "hold" and s.lineup_id == "dinner")
    # Observe halfway through the hold derived from the sample's window.
    now = SAMPLE_NOW.replace(hour=(hold.start_min + hold.end_min) // 2 // 60, minute=0)
    current = build_view(displays, dashboards, now=now).displays[0]
    holds = [s for s in current.day.segments if s.kind == "hold"]
    active = next(s for s in holds if s.lineup_id == hold.lineup_id)
    assert active.is_current is True and active.predicted is False and active.live is True
    assert all(s.predicted == (s.start_min > now.hour * 60 + now.minute) for s in holds)
    assert sum(s.is_current for s in holds) == 1


def test_timeline_steps_follow_rotation_advances_and_roles() -> None:
    view = sample_view_model()
    for display in view.displays:
        for day in display.week:
            for segment in day.segments:
                if segment.kind == "hold":
                    continue
                lineup = _lineup(display, segment.lineup_id)
                assert segment.steps_total == len(segment.points)
                for step, point in zip(segment.steps, segment.points, strict=True):
                    source = lineup.steps[point.step_index]
                    assert step.index == point.step_index
                    assert (step.dashboard_id, step.name, step.thumb_url, step.aspect) == (
                        source.dashboard_id,
                        source.name,
                        source.thumb_url,
                        source.aspect,
                    )
                    assert step.at_min == point.minute
                    assert step.at_label == f"{point.minute // 60:02d}:{point.minute % 60:02d}"
                    assert step.current == (point.role in ("now", "waiting"))
                    assert step.next == (point.role == "next")
    dinner = next(
        s for s in view.displays[0].day.segments if s.lineup_id == "dinner" and s.kind == "window"
    )
    row = _lineup(view.displays[0], dinner.lineup_id)
    assert row.step_minutes is not None
    assert [s.at_min for s in dinner.steps] == [
        dinner.start_min + i * row.step_minutes for i in range(row.step_count)
    ]
    assert sum(s.current for s in dinner.steps) == sum(s.next for s in dinner.steps) == 1


def test_timeline_steps_cap_occurrences_without_losing_total() -> None:
    displays, dashboards = sample_inputs()
    frame = displays[0]
    dinner = frame.lineups[0]
    fast = replace(dinner, steps=tuple(replace(s, dwell_minutes=1) for s in dinner.steps))
    out = build_view([replace(frame, lineups=(fast,))], dashboards, now=SAMPLE_NOW).displays[0]
    block = next(s for s in out.day.segments if s.kind == "window")
    assert len(block.steps) == 60
    assert block.steps_total == block.end_min - block.start_min
    assert [s.at_min for s in block.steps] == list(range(block.start_min, block.start_min + 60))
    assert [s.dashboard_id for s in block.steps] == [
        fast.steps[i % len(fast.steps)].dashboard_id for i in range(60)
    ]


def test_timeline_interval_steps_keep_the_weekdays_cooldown_grid() -> None:
    displays, dashboards = sample_inputs()
    interval = replace(
        displays[0].lineups[0],
        kind="interval",
        interval_minutes=17,
        last_fired=SAMPLE_NOW.replace(hour=18, minute=7).timestamp(),
    )
    out = build_view(
        [replace(displays[0], lineups=(interval,))], dashboards, now=SAMPLE_NOW
    ).displays[0]
    today = next(s for s in out.day.segments if s.kind == "window")
    earlier = next(s for s in out.week[0].segments if s.kind == "window")
    assert 18 * 60 + 7 in [s.at_min for s in today.steps]
    assert earlier.steps[0].at_min == earlier.start_min
    assert [s.at_min for s in today.steps] == [p.minute for p in today.points]


def test_empty_projection_does_not_invent_a_queued_model_frame() -> None:
    displays, dashboards = sample_inputs()
    kitchen = replace(displays[1], upcoming=(), on_screen=None, published=None)
    out = build_view([kitchen], dashboards, now=SAMPLE_NOW).displays[0]
    assert out.queued is None
    assert out.next_update is None and out.next_line.before == "No update scheduled"


def test_connected_display_without_a_published_frame_stays_unknown() -> None:
    displays, dashboards = sample_inputs()
    frame = replace(displays[0], on_screen=None, published=None, published_known=True)
    out = build_view([frame], dashboards, now=SAMPLE_NOW).displays[0]
    assert out.on_screen is None and out.on_screen_known is False
    assert out.in_sync is False and out.queued is None


def test_timeline_reuses_dashboard_metadata_for_the_whole_week(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.deck_routes as routes

    real = routes._step_items
    lookups: list[str] = []

    def lookup(page: Any) -> Any:
        lookups.append(page.id)
        return real(page)

    monkeypatch.setattr(routes, "_step_items", lookup)
    panel = client.get("/decks/view.json?display=panel").get_json()["displays"][0]
    assert sorted(lookups) == ["art", "news", "sky"]
    assert all(s["steps"] for day in panel["week"] for s in day["segments"] if s["kind"] != "hold")


def test_timeline_mixed_dwells_repeat_at_the_real_advance_times() -> None:
    displays, dashboards = sample_inputs()
    frame = displays[0]
    dinner = frame.lineups[0]
    mixed = replace(
        dinner,
        steps=(
            replace(dinner.steps[0], dwell_minutes=10),
            replace(dinner.steps[1], dwell_minutes=50),
        ),
    )
    out = build_view([replace(frame, lineups=(mixed,))], dashboards, now=SAMPLE_NOW).displays[0]
    block = next(s for s in out.day.segments if s.kind == "window")
    assert [s.at_min - block.start_min for s in block.steps] == [0, 10, 60, 70, 120, 130]
    assert [s.index for s in block.steps] == [0, 1, 0, 1, 0, 1]
    assert [s.current for s in block.steps] == [False, False, False, True, False, False]
    assert [s.next for s in block.steps] == [False, False, False, False, True, False]


def test_plain_form_push_redirects_with_feedback(client: Any, app: Flask) -> None:
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    response = client.post(
        "/decks/morning/push-step",
        data={"index": "1", "display_id": "panel"},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/decks?hl=morning")
    assert fake.calls[0][0] == "art"
    with client.session_transaction() as session:
        assert any("Pushed Art to" in text for _, text in session["_flashes"])
    response = client.post("/decks/morning/push-step", data={"index": "99"})
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert any("doesn't exist" in text for _, text in session["_flashes"])


def test_deleted_display_is_named_in_view(client: Any, app: Flask) -> None:
    deck = _deck(app, "morning")
    app.config["DECK_STORE"].upsert(deck.model_copy(update={"device_ids": ["deleted"]}))
    view = client.get("/decks/view.json").get_json()
    orphan = next(row for row in view["unassigned"] if row["id"] == "morning")
    assert orphan["display_removed"] is True
    assert view["schema_version"] == 1


def test_quiet_hours_under_an_all_day_block_get_an_edge() -> None:
    """A lineup that runs all day covers the quiet-hours rod, so the stretch
    carries where it lies under the block (the hatched edge)."""
    displays, dashboards = sample_inputs()
    d = displays[0]
    all_day = replace(d.lineups[0], kind="cycle", start="00:00", end="23:59", days=tuple(range(7)))
    view = build_view([replace(d, lineups=(all_day,))], dashboards, now=SAMPLE_NOW).displays[0]
    assert view.day.pause, "the sample display has quiet hours"
    assert any(p.over for p in view.day.pause)
    for p in view.day.pause:
        for o in p.over:
            assert 0 <= o["left_pct"] <= 100 and o["width_pct"] > 0


@pytest.fixture
def sent_composition(app: Flask) -> tuple[str, bytes]:
    import hashlib
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 80), "red").save(buf, format="PNG")
    png = buf.getvalue()
    digest = hashlib.sha256(png).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(png)
    # The legacy extra field must win over a device artifact's digest.
    app.config["EVENT_LOG"].record(
        type="push",
        source="page",
        target="news",
        status="sent",
        digest="a" * 16,
        extra={"composition_digest": digest, "device_ids": ["old-panel"]},
    )
    return digest, png


def test_step_digest_matches_immutable_thumbnail(
    client: Any, sent_composition: tuple[str, bytes]
) -> None:
    digest, _ = sent_composition
    data = client.get("/decks/view.json").get_json()
    step = next(lu for d in data["displays"] for lu in d["lineups"] if lu["id"] == "morning")[
        "steps"
    ][0]
    assert data["schema_version"] == 1
    assert step["composition_digest"] == digest
    assert digest in step["thumb_url"]
    with client.get(step["thumb_url"]) as response:
        assert response.status_code == 200


def test_push_step_resends_shown_composition(
    client: Any, app: Flask, sent_composition: tuple[str, bytes]
) -> None:
    digest, png = sent_composition
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    resp = client.post("/decks/morning/push-step", json={"index": 0, "composition_digest": digest})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["fresh"] is False
    assert fake.calls == []
    assert fake.stored_calls == [(png, {"page_id": "news", "device_ids": {"panel"}})]


@pytest.mark.parametrize("digest", ["../escape", "abc.png", "xyz", 123, True, []])
def test_push_step_rejects_invalid_digest(client: Any, app: Flask, digest: Any) -> None:
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    resp = client.post("/decks/morning/push-step", json={"index": 0, "composition_digest": digest})
    assert resp.status_code == 400
    assert "digest" in resp.get_json()["error"]
    assert not fake.calls and not fake.stored_calls


def test_push_step_rejects_foreign_or_unrecorded_composition(
    client: Any, app: Flask, sent_composition: tuple[str, bytes]
) -> None:
    digest, _ = sent_composition
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    for page_id, candidate in [("art", digest), ("news", "b" * 16)]:
        resp = client.post(
            "/decks/morning/push-step", json={"page_id": page_id, "composition_digest": candidate}
        )
        assert resp.status_code == 409
        assert "dashboard" in resp.get_json()["error"]
    assert not fake.calls and not fake.stored_calls


def test_push_step_culled_composition_is_explicitly_fresh(
    client: Any, app: Flask, sent_composition: tuple[str, bytes]
) -> None:
    digest, _ = sent_composition
    (app.config["RENDERS_DIR"] / f"{digest}.png").unlink()
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    resp = client.post("/decks/morning/push-step", json={"index": 0, "composition_digest": digest})
    assert resp.status_code == 200
    assert resp.get_json()["fresh"] is True
    assert (
        resp.get_json()["message"] == "Sent a fresh render, the earlier picture is no longer stored"
    )
    assert len(fake.calls) == 1 and not fake.stored_calls


def test_push_step_no_js_resends_composition(
    client: Any, app: Flask, sent_composition: tuple[str, bytes]
) -> None:
    digest, png = sent_composition
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    resp = client.post(
        "/decks/morning/push-step", data={"index": "0", "composition_digest": digest}
    )
    assert resp.status_code == 302
    assert fake.stored_calls[0][0] == png


def test_push_step_accepts_older_owned_composition(
    client: Any, app: Flask, sent_composition: tuple[str, bytes]
) -> None:
    digest, png = sent_composition
    app.config["EVENT_LOG"].record(
        type="push", source="page", target="news", status="sent", digest="f" * 16
    )
    fake = _FakePush()
    app.config["PUSH_MANAGER"] = fake
    resp = client.post(
        "/decks/morning/push-step", json={"page_id": "news", "composition_digest": digest}
    )
    assert resp.status_code == 200 and resp.get_json()["fresh"] is False
    assert fake.stored_calls[0][0] == png


def test_push_step_logs_same_composition_and_keeps_thumbnail(
    client: Any, app: Flask, sent_composition: tuple[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import patch

    digest, _ = sent_composition
    pusher = app.config["PUSH_MANAGER"]
    monkeypatch.setattr(pusher._transport, "publish", lambda *args, **kwargs: None)
    before = client.get("/decks/view.json").get_json()
    with patch("app.push.capture_composed", side_effect=AssertionError("must not re-render")):
        response = client.post(
            "/decks/morning/push-step", json={"page_id": "news", "composition_digest": digest}
        )
    assert response.status_code == 200 and response.get_json()["fresh"] is False
    row = app.config["EVENT_LOG"].list(type="push", target="news", limit=1)[0]
    assert row.status == "sent", row.error
    assert row.source == "resend" and row.digest == digest
    after = client.get("/decks/view.json").get_json()

    def step(data: Any) -> Any:
        return next(lu for d in data["displays"] for lu in d["lineups"] if lu["id"] == "morning")[
            "steps"
        ][0]

    assert step(after)["composition_digest"] == digest
    assert step(after)["thumb_url"] == step(before)["thumb_url"]


# ---------------------------------------------------------------------------
# Header: what is on the frame when no dashboard explains it
# ---------------------------------------------------------------------------


def _unexplained(**kw: Any) -> DisplayView:
    displays, dashboards = sample_inputs()
    kitchen = replace(displays[1], on_screen=None, published=None, published_known=True, **kw)
    return _display(build_view([kitchen], dashboards, now=SAMPLE_NOW), "kitchen")


def test_frame_without_a_dashboard_replaces_the_unknown_header() -> None:
    from app.lineups_view import FrameInput

    at = SAMPLE_NOW.replace(hour=17, minute=58).timestamp()
    d = _unexplained(
        frame=FrameInput("a frame sent by hand", "/compose/thumbs/v1/sent/abc123.webp", at)
    )
    assert d.on_screen is None and d.frame is not None
    assert d.frame.label == "a frame sent by hand"
    assert d.frame.thumb_url == "/compose/thumbs/v1/sent/abc123.webp"
    assert d.frame.since == "17:58"
    assert d.show_title == "A frame sent by hand, on screen since 17:58"
    assert d.show_note == "" and not d.nothing_sent


def test_a_dashboard_on_screen_wins_over_a_frame() -> None:
    from app.lineups_view import FrameInput

    displays, dashboards = sample_inputs()
    kitchen = replace(displays[1], frame=FrameInput("a frame sent by hand", "/x.webp"))
    d = _display(build_view([kitchen], dashboards, now=SAMPLE_NOW), "kitchen")
    assert d.on_screen is not None and d.frame is None


def test_nothing_rendered_says_so_instead_of_unknown() -> None:
    d = _unexplained(nothing_sent=True)
    assert d.frame is None and d.nothing_sent
    assert d.show_note == "" and d.show_title == "Nothing has been sent to this display yet"


def test_waiting_for_check_in_only_when_the_display_is_silent() -> None:
    silent = _unexplained()
    assert silent.show_note == ", waiting for check-in"
    seen = SAMPLE_NOW.replace(hour=18, minute=10).timestamp()
    heard = _unexplained(heard=True, last_seen=seen)
    assert heard.show_note == ", checked in 18:10"
    assert _unexplained(heard=True).show_note == ", checked in recently"
    # An awake display that is simply connected never waits.
    from app.next_update import MODE_CONNECTED, Reach

    assert _unexplained(reach=Reach(MODE_CONNECTED)).show_note == ""


def _png(app: Flask, colour: str) -> str:
    import hashlib
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 80), colour).save(buf, format="PNG")
    digest = hashlib.sha256(buf.getvalue()).hexdigest()[:16]
    (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(buf.getvalue())
    return digest


def _render_record(
    app: Flask, comp: str, *, digest: str = "d" * 16, at: float = 1000.0, **extra: Any
) -> None:
    app.config["PUSH_MANAGER"]._latest_renders["panel"] = {
        "digest": digest,
        "ext": "bin",
        "composition_digest": comp,
        "preview_digest": None,
        "page_id": None,
        "timestamp": at,
        **extra,
    }


def _frame(app: Flask, transport: str) -> Any:
    from types import SimpleNamespace

    from app.deck_routes import _frame_state

    with app.test_request_context("/decks"):
        return _frame_state(SimpleNamespace(id="panel", transport=transport))


def test_pull_display_shows_the_served_frame_not_the_latest(app: Flask) -> None:
    served, latest = _png(app, "red"), _png(app, "blue")
    push = app.config["PUSH_MANAGER"]
    _render_record(app, served, digest="s" * 16)
    push._retire_live_locked("panel", "n" * 16)  # the served frame is now history
    _render_record(
        app,
        latest,
        digest="n" * 16,
        last_served_digest="s" * 16,
        last_served_at=2000.0,
    )
    frame, nothing = _frame(app, "rest")
    assert not nothing and frame is not None
    assert frame.thumb_url.endswith(f"/{served}.webp") or frame.thumb_url.endswith(f"/{served}.png")
    assert frame.at == 2000.0
    # A push display shows its latest push instead.
    pushed, _ = _frame(app, "mqtt")
    assert pushed is not None and f"/{latest}." in pushed.thumb_url


def test_pull_display_that_never_fetched_shows_the_latest(app: Flask) -> None:
    comp = _png(app, "green")
    _render_record(app, comp)
    frame, nothing = _frame(app, "rest")
    assert not nothing and frame is not None
    assert f"/{comp}." in frame.thumb_url and frame.at == 1000.0
    assert frame.label == "a frame sent by hand"


def test_pull_display_serving_the_latest_uses_its_composition(app: Flask) -> None:
    comp = _png(app, "green")
    _render_record(app, comp, last_served_digest="d" * 16, last_served_at=3000.0)
    frame, _ = _frame(app, "rest")
    assert frame is not None and f"/{comp}." in frame.thumb_url and frame.at == 3000.0


def test_no_render_at_all_is_nothing_sent(app: Flask) -> None:
    assert _frame(app, "rest") == (None, True)


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("file", "an uploaded file"),
        ("webpage", "a webpage"),
        ("url", "an image from a link"),
        ("manual", "a frame sent by hand"),
        ("scheduler", "a frame sent by hand"),
    ],
)
def test_frame_label_follows_the_push_source(app: Flask, source: str, label: str) -> None:
    comp = _png(app, "green")
    _render_record(app, comp, at=1000.0)
    app.config["EVENT_LOG"].record(
        type="push",
        source=source,
        target="x",
        status="sent",
        digest=comp,
        extra={"device_ids": ["panel"]},
    )
    row = app.config["EVENT_LOG"].list(type="push", limit=1)[0]
    _render_record(app, comp, at=row.timestamp + 5)
    frame, _ = _frame(app, "rest")
    assert frame is not None and frame.label == label


def test_an_old_push_row_does_not_name_a_newer_hand_sent_frame(app: Flask) -> None:
    comp = _png(app, "green")
    app.config["EVENT_LOG"].record(
        type="push",
        source="file",
        target="x",
        status="sent",
        digest=comp,
        extra={"device_ids": ["panel"]},
    )
    _render_record(app, comp, at=time.time() + 86400)
    frame, _ = _frame(app, "rest")
    assert frame is not None and frame.label == "a frame sent by hand"


def test_view_json_carries_the_header_frame(client: Any, app: Flask) -> None:
    comp = _png(app, "green")
    _render_record(app, comp, at=time.time() - 240)
    panel = client.get("/decks/view.json?display=panel").get_json()["displays"][0]
    assert panel["on_screen"] is None
    assert f"/{comp}." in panel["frame"]["thumb_url"]
    assert panel["frame"]["label"] == "a frame sent by hand"
    assert "unknown" not in panel["show_title"].lower()
    html = client.get("/decks?display=panel").get_data(as_text=True)
    assert "a frame sent by hand" in html and "waiting for check-in" not in html


def test_heard_display_with_unknown_cadence_is_not_offline() -> None:
    from app.next_update import MODE_UNKNOWN, Reach

    assert _unexplained(reach=Reach(MODE_UNKNOWN)).link.label == "Offline"
    heard = _unexplained(reach=Reach(MODE_UNKNOWN), heard=True)
    assert heard.link.label == "Checked in" and heard.link.mode == MODE_UNKNOWN


# ---------------------------------------------------------------------------
# Header placeholder states and the queued badge
# ---------------------------------------------------------------------------


def test_queued_delivery_placeholder_names_the_next_check_in() -> None:
    d = _unexplained(pending=True)
    assert d.placeholder is not None and d.queued_badge is None
    assert d.placeholder.state == "queued" and d.placeholder.label == "Queued"
    assert d.placeholder.sub.startswith("Frame picks it up at its next check-in ~")
    assert d.placeholder.sub.split("check-in ")[1] in d.placeholder.title
    assert "?" not in d.placeholder.label + d.placeholder.sub


def test_queued_placeholder_with_unknown_reach_still_estimates() -> None:
    from app.next_update import Reach

    d = _unexplained(pending=True, reach=Reach("unknown"))
    assert d.placeholder is not None and d.placeholder.state == "queued"
    assert d.placeholder.sub.startswith("Frame picks it up at its next check-in")
    assert d.placeholder.title.endswith(".")


def test_no_render_is_nothing_sent_placeholder() -> None:
    d = _unexplained(nothing_sent=True)
    assert d.placeholder is not None
    assert (d.placeholder.state, d.placeholder.label) == ("nothing", "Nothing sent yet")


def test_missing_image_file_is_image_unavailable() -> None:
    from app.lineups_view import FrameInput

    d = _unexplained(frame=FrameInput("a frame sent by hand", ""))
    assert d.placeholder is not None
    assert (d.placeholder.state, d.placeholder.label) == ("unavailable", "Image unavailable")


def test_live_device_being_delivered_says_delivering() -> None:
    from app.lineups_view import OnScreenInput, PublishedInput
    from app.next_update import MODE_CONNECTED, Reach

    displays, dashboards = sample_inputs()
    kitchen = replace(
        displays[1],
        on_screen=OnScreenInput("family", SAMPLE_NOW.timestamp() - 600),
        reach=Reach(MODE_CONNECTED, live=True),
        published=PublishedInput("weather", SAMPLE_NOW.timestamp()),
        published_known=True,
    )
    d = _display(build_view([kitchen], dashboards, now=SAMPLE_NOW), "kitchen")
    assert d.placeholder is not None and d.placeholder.label == "Delivering"
    assert d.queued_badge is None


def test_served_image_stays_and_a_queued_badge_appears() -> None:
    from app.lineups_view import FrameInput

    d = _unexplained(frame=FrameInput("a frame sent by hand", "/x.webp"), pending=True)
    assert d.frame is not None and d.frame.thumb_url == "/x.webp"
    assert d.placeholder is None
    assert d.queued_badge is not None and d.queued_badge.label == "Queued"
    assert "next check-in" in d.queued_badge.title
    calm = _unexplained(frame=FrameInput("a frame sent by hand", "/x.webp"))
    assert calm.queued_badge is None and calm.placeholder is None


def test_pending_needs_a_render_the_display_has_not_fetched(app: Flask) -> None:
    from types import SimpleNamespace

    from app.deck_routes import _frame_pending

    def pending(transport: str = "rest") -> bool:
        with app.test_request_context("/decks"):
            return _frame_pending(SimpleNamespace(id="panel", transport=transport))

    assert pending() is False  # nothing rendered
    _render_record(app, "c" * 16, digest="n" * 16)
    assert pending() is True  # never served
    assert pending("mqtt") is False  # push displays have no pull queue
    _render_record(app, "c" * 16, digest="n" * 16, last_served_digest="o" * 16)
    assert pending() is True
    _render_record(app, "c" * 16, digest="n" * 16, last_served_digest="n" * 16)
    assert pending() is False


def _link(**reach: Any) -> Any:
    from app.next_update import MODE_SLEEPS, Reach

    displays, dashboards = sample_inputs()
    kitchen = replace(displays[1], reach=Reach(MODE_SLEEPS, **reach))
    return _display(build_view([kitchen], dashboards, now=SAMPLE_NOW), "kitchen").link


def test_link_label_names_the_actual_wake_time() -> None:
    now = SAMPLE_NOW.timestamp()
    soon = _link(next_checkin_at=now + 36 * 60, gap_s=60.0)  # the 1 min heartbeat gap is ignored
    assert soon.label == "Wakes at 18:48" and "18:48" in soon.title
    tomorrow = _link(next_checkin_at=now + 14 * 3600)
    assert tomorrow.label.startswith("Wakes ") and tomorrow.label != "Wakes at 08:12"
    assert not tomorrow.label.startswith("Wakes at")


def test_link_label_without_a_future_wake_time() -> None:
    now = SAMPLE_NOW.timestamp()
    # Past prediction and an unstable gap: no cadence is claimed.
    assert _link(next_checkin_at=now - 60, gap_s=70.0).label == "Sleeps"
    assert _link(gap_s=70.0).label == "Sleeps"
    assert _link().label == "Sleeps"
    # A stable cadence (set by the operator or stated by the firmware) is named.
    assert _link(gap_s=1200.0, cadence_stable=True).label == "Wakes every 20 min"
    assert _link(next_checkin_at=now - 60, gap_s=1200.0, cadence_stable=True).label == (
        "Wakes every 20 min"
    )
