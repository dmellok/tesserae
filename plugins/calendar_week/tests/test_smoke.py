"""Smoke test for calendar_week's range_mode cell option.

Same stub-current_app setup as the other calendar_* widgets: patch
calendar_core's load_events so the test exercises the date-window logic
without reaching real ICS feeds.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server


def _stub_app(events: list[dict[str, Any]] | None = None) -> MagicMock:
    core = MagicMock()
    core.server_module.load_events.return_value = events or []
    core.data_dir = "/tmp/calendar_core_unused"
    registry = MagicMock()
    registry.get.return_value = core
    app = MagicMock()
    app.config = {"PLUGIN_REGISTRY": registry}
    return app


def test_rolling_7_always_starts_today() -> None:
    app = _stub_app()
    with patch.object(server, "current_app", app):
        out = server.fetch(options={"range_mode": "rolling_7"}, settings={}, ctx={})
    today = datetime.now(UTC).date()
    assert out["start"] == today.isoformat()
    assert out["end"] == (today + timedelta(days=6)).isoformat()
    assert out["days"][0]["is_today"] is True


def test_calendar_week_default_unchanged() -> None:
    app = _stub_app()
    with patch.object(server, "current_app", app):
        out = server.fetch(options={}, settings={}, ctx={})
    today = datetime.now(UTC).date()
    start = datetime.strptime(out["start"], "%Y-%m-%d").date()
    assert start <= today <= start + timedelta(days=6)
    assert any(d["is_today"] for d in out["days"])


def test_multi_day_all_day_event_appears_on_every_covered_day() -> None:
    app = _stub_app()
    with patch.object(server, "current_app", app):
        first = server.fetch(options={"range_mode": "rolling_7"}, settings={}, ctx={})
    start = datetime.strptime(first["start"], "%Y-%m-%d").date()
    # 3-day conference starting on the window's first day.
    events = [
        {
            "summary": "Conference",
            "start": start.isoformat(),
            "end": (start + timedelta(days=3)).isoformat(),
            "all_day": True,
            "feed_colour": "#3366CC",
        }
    ]
    app = _stub_app(events)
    with patch.object(server, "current_app", app):
        out = server.fetch(options={"range_mode": "rolling_7"}, settings={}, ctx={})
    covered = out["days"][:3]
    for day in covered:
        summaries = [e["summary"] for e in day["events"]]
        assert summaries == ["Conference"], day["date"]
    assert out["days"][3]["events"] == []


def test_multi_day_timed_event_appears_on_every_covered_day() -> None:
    # Regression: a *timed* (non-all-day) event spanning several days used
    # to be bucketed only on its start day and silently vanish from every
    # day after that, unlike all-day events.
    app = _stub_app()
    with patch.object(server, "current_app", app):
        first = server.fetch(options={"range_mode": "rolling_7"}, settings={}, ctx={})
    start = datetime.strptime(first["start"], "%Y-%m-%d").date()
    # A 4-calendar-day trip: starts 16:00 on day 0, ends 10:00 on day 3.
    events = [
        {
            "summary": "Trip",
            "start": f"{start.isoformat()}T16:00:00",
            "end": f"{(start + timedelta(days=3)).isoformat()}T10:00:00",
            "all_day": False,
            "feed_colour": "#3366CC",
        }
    ]
    app = _stub_app(events)
    with patch.object(server, "current_app", app):
        out = server.fetch(options={"range_mode": "rolling_7"}, settings={}, ctx={})
    covered = out["days"][:4]
    for day in covered:
        summaries = [e["summary"] for e in day["events"]]
        assert summaries == ["Trip"], day["date"]
    assert out["days"][4]["events"] == []


if __name__ == "__main__":
    test_rolling_7_always_starts_today()
    test_calendar_week_default_unchanged()
    test_multi_day_all_day_event_appears_on_every_covered_day()
    test_multi_day_timed_event_appears_on_every_covered_day()
    print("test_smoke.py: all assertions passed")
