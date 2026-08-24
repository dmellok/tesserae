"""Smoke tests for the calendar_day widget.

Stubs ``current_app`` + the calendar_core plugin so the tests exercise
fetch()'s own logic (event slimming and cap) without reaching real ICS
feeds or the Tesserae core.

The event_title_scale / event_time_scale styling-slider clamp used to
live here too; it's moved to client.js's clampScale (see
tests/clamp_check.mjs) since ctx.cell.options already carries the raw
slider value to the browser unclamped.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


def _stub_calendar_core(events: list[dict[str, Any]]) -> MagicMock:
    core = MagicMock()
    core.server_module.load_events.return_value = events
    core.data_dir = "/tmp/calendar_core_unused"
    return core


def _stub_app(events: list[dict[str, Any]]) -> MagicMock:
    registry = MagicMock()
    registry.get.return_value = _stub_calendar_core(events)
    app = MagicMock()
    app.config = {"PLUGIN_REGISTRY": registry}
    return app


def _future_iso(hours: float) -> str:
    """An event time on TODAY, whatever time the suite runs.

    ``fetch`` keeps only events occupying the current date IN THE APP'S
    TIMEZONE, so offsets from "now" fall off the end of the day when the run
    happens late: CI at 22:44 UTC turned a five-event fixture into one. Noon
    in that same zone is safely mid-day whatever the hour, and spacing in
    minutes keeps every event inside it."""
    local_noon = datetime.now(server.app_timezone()).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    return (local_noon + timedelta(minutes=hours * 10)).astimezone(UTC).isoformat()


def test_missing_calendar_core_surfaces_error() -> None:
    registry = MagicMock()
    registry.get.return_value = None
    app = MagicMock()
    app.config = {"PLUGIN_REGISTRY": registry}
    with patch.object(server, "current_app", app):
        out = server.fetch(options={}, settings={}, ctx={})
    assert "error" in out
    assert out["events"] == []


def test_fetch_slims_events() -> None:
    app = _stub_app(
        [
            {
                "summary": "Standup",
                "start": _future_iso(1),
                "end": _future_iso(1.5),
                "all_day": False,
                "feed_colour": "#3366CC",
                "feed_name": "Work",
                "location": "Zoom",
            },
        ]
    )
    with patch.object(server, "current_app", app):
        out = server.fetch(options={}, settings={}, ctx={})
    assert out["count"] == 1
    assert out["events"][0]["summary"] == "Standup"
    assert "event_title_scale" not in out
    assert "event_time_scale" not in out


def test_max_events_truncates() -> None:
    events = [
        {
            "summary": f"event {i}",
            "start": _future_iso(1 + i),
            "end": _future_iso(1.5 + i),
            "all_day": False,
        }
        for i in range(5)
    ]
    app = _stub_app(events)
    with patch.object(server, "current_app", app):
        out = server.fetch(options={"max_events": 2}, settings={}, ctx={})
    assert out["count"] == 2


if __name__ == "__main__":
    test_missing_calendar_core_surfaces_error()
    test_fetch_slims_events()
    test_max_events_truncates()
    print("test_smoke.py: all assertions passed")


# -- #248: only events that actually occupy today ------------------------


class _FixedNow(datetime):
    """A clock pinned to 2026-08-21 09:00 UTC (a Friday), so "tomorrow"
    is unambiguous regardless of when the suite runs. The bug is a
    boundary bug; testing it against the wall clock would only reproduce
    near midnight."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return datetime(2026, 8, 21, 9, 0, tzinfo=tz or UTC)


def _fetch_with_events(events: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
    """Clock AND zone are pinned. The widget works in local time, so with the
    machine's own zone a UTC event time lands on a different date depending on
    where the suite runs, which is precisely the boundary under test."""
    app = _stub_app(events)
    with (
        patch.object(server, "current_app", app),
        patch.object(server, "datetime", _FixedNow),
        patch.object(server, "app_timezone", lambda: UTC),
    ):
        return server.fetch(options=options, settings={}, ctx={})


def _timed(summary: str, start: str, end: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "start": start,
        "end": end,
        "all_day": False,
        "feed_colour": "#3366CC",
        "feed_name": "Work",
        "location": "",
    }


def test_an_event_moved_to_tomorrow_leaves_todays_view() -> None:
    """The reported bug (#248). ``hours_ahead`` is a rolling window from now,
    so a 24 h default reaches into tomorrow and a timed event living wholly
    on tomorrow survived into today's list. The client drew it against
    today's 0-24 axis, where neither edge is today, so the multi-day rule
    painted it across the whole column while its label still read
    10:00-11:00. It looked like today's copy had gone stale."""
    out = _fetch_with_events(
        [_timed("Moved", "2026-08-22T10:00:00+00:00", "2026-08-22T11:00:00+00:00")]
    )
    assert out["count"] == 0
    assert out["events"] == []


def test_an_event_later_today_is_kept() -> None:
    out = _fetch_with_events(
        [_timed("Standup", "2026-08-21T10:00:00+00:00", "2026-08-21T11:00:00+00:00")]
    )
    assert [e["summary"] for e in out["events"]] == ["Standup"]


def test_an_overnight_event_starting_today_is_kept() -> None:
    """It genuinely occupies part of today; clamping it to the day is what
    the client's multi-day rule is for."""
    out = _fetch_with_events(
        [_timed("Night shift", "2026-08-21T22:00:00+00:00", "2026-08-22T06:00:00+00:00")]
    )
    assert [e["summary"] for e in out["events"]] == ["Night shift"]


def test_a_multi_day_event_spanning_today_is_kept() -> None:
    out = _fetch_with_events(
        [_timed("Conference", "2026-08-20T09:00:00+00:00", "2026-08-23T17:00:00+00:00")]
    )
    assert [e["summary"] for e in out["events"]] == ["Conference"]


def test_todays_date_is_reported() -> None:
    out = _fetch_with_events([])
    assert out["date"] == "2026-08-21"
