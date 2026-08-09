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
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


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
