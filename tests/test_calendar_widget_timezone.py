"""Calendar widgets should calculate visible dates in the configured app timezone."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from flask import Flask


def _freeze_datetime(instant: datetime) -> type[datetime]:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            if tz is None:
                return instant.replace(tzinfo=None)
            return instant.astimezone(tz)

    return FrozenDateTime


def _plugin(app: Flask, plugin_id: str) -> Any:
    plugin = app.config["PLUGIN_REGISTRY"].get(plugin_id)
    assert plugin is not None and plugin.server_module is not None
    return plugin.server_module


def _core_plugin(app: Flask) -> Any:
    core = app.config["PLUGIN_REGISTRY"].get("calendar_core")
    assert core is not None and core.server_module is not None
    return core


def test_calendar_day_uses_app_timezone_for_display_date(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = _plugin(app, "calendar_day")
    core = _core_plugin(app)
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
    frozen = datetime(2026, 7, 4, 18, 30, tzinfo=UTC)
    captured: dict[str, datetime] = {}

    def load_events(
        _feeds_filter: list[str] | None,
        start: datetime,
        end: datetime,
        *,
        data_dir: Any,
    ) -> list[dict[str, Any]]:
        del data_dir
        captured["start"] = start
        captured["end"] = end
        return []

    monkeypatch.setattr(day, "datetime", _freeze_datetime(frozen))
    monkeypatch.setattr(core.server_module, "load_events", load_events)

    with app.app_context():
        out = day.fetch({"hours_ahead": 24}, {}, ctx={})

    assert out["date"] == "2026-07-05"
    assert captured["start"] == frozen
    assert captured["end"] == datetime(2026, 7, 5, 18, 30, tzinfo=UTC)


def test_calendar_day_filters_all_day_events_to_the_local_date(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = _plugin(app, "calendar_day")
    core = _core_plugin(app)
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
    frozen = datetime(2026, 7, 4, 19, 10, tzinfo=UTC)

    def load_events(
        _feeds_filter: list[str] | None,
        _start: datetime,
        _end: datetime,
        *,
        data_dir: Any,
    ) -> list[dict[str, Any]]:
        del data_dir
        return [
            {
                "summary": "Yesterday",
                "start": "2026-07-04",
                "end": "2026-07-05",
                "all_day": True,
                "feed_colour": "#0d8c7e",
            },
            {
                "summary": "Today",
                "start": "2026-07-05",
                "end": "2026-07-06",
                "all_day": True,
                "feed_colour": "#0d8c7e",
            },
            {
                "summary": "Spanning",
                "start": "2026-07-04",
                "end": "2026-07-06",
                "all_day": True,
                "feed_colour": "#0d8c7e",
            },
        ]

    monkeypatch.setattr(day, "datetime", _freeze_datetime(frozen))
    monkeypatch.setattr(core.server_module, "load_events", load_events)

    with app.app_context():
        out = day.fetch({"hours_ahead": 24}, {}, ctx={})

    assert out["date"] == "2026-07-05"
    assert [event["summary"] for event in out["events"]] == ["Today", "Spanning"]


def test_calendar_week_uses_local_week_and_buckets_timed_events_by_local_date(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    week = _plugin(app, "calendar_week")
    core = _core_plugin(app)
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
    frozen = datetime(2026, 7, 4, 18, 30, tzinfo=UTC)
    captured: dict[str, datetime] = {}

    def load_events(
        _feeds_filter: list[str] | None,
        start: datetime,
        end: datetime,
        *,
        data_dir: Any,
    ) -> list[dict[str, Any]]:
        del data_dir
        captured["start"] = start
        captured["end"] = end
        return [
            {
                "summary": "Late event",
                "start": "2026-07-04T17:30:00+00:00",
                "end": "2026-07-04T18:30:00+00:00",
                "all_day": False,
                "feed_colour": "#0d8c7e",
            }
        ]

    monkeypatch.setattr(week, "datetime", _freeze_datetime(frozen))
    monkeypatch.setattr(core.server_module, "load_events", load_events)

    with app.app_context():
        out = week.fetch({"week_start": "sunday"}, {}, ctx={})

    assert out["start"] == "2026-07-05"
    assert out["end"] == "2026-07-11"
    assert captured["start"] == datetime(2026, 7, 4, 16, 0, tzinfo=UTC)
    assert captured["end"] == datetime(2026, 7, 11, 16, 0, tzinfo=UTC)
    assert out["days"][0]["date"] == "2026-07-05"
    assert out["days"][0]["is_today"] is True
    assert out["days"][0]["events"][0]["summary"] == "Late event"


def test_calendar_month_uses_local_month(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    month = _plugin(app, "calendar_month")
    core = _core_plugin(app)
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
    frozen = datetime(2026, 6, 30, 17, 0, tzinfo=UTC)
    captured: dict[str, datetime] = {}

    def load_events(
        _feeds_filter: list[str] | None,
        start: datetime,
        end: datetime,
        *,
        data_dir: Any,
    ) -> list[dict[str, Any]]:
        del data_dir
        captured["start"] = start
        captured["end"] = end
        return []

    monkeypatch.setattr(month, "datetime", _freeze_datetime(frozen))
    monkeypatch.setattr(core.server_module, "load_events", load_events)

    with app.app_context():
        out = month.fetch({}, {}, ctx={})

    assert out["month"] == 7
    assert out["year"] == 2026
    assert out["month_name"] == "July"
    assert captured["start"] == datetime(2026, 6, 28, 16, 0, tzinfo=UTC)
    assert captured["end"] == datetime(2026, 8, 9, 16, 0, tzinfo=UTC)


def test_calendar_month_expands_all_day_events_across_visible_dates(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    month = _plugin(app, "calendar_month")
    core = _core_plugin(app)
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
    frozen = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)

    def load_events(
        _feeds_filter: list[str] | None,
        _start: datetime,
        _end: datetime,
        *,
        data_dir: Any,
    ) -> list[dict[str, Any]]:
        del data_dir
        return [
            {
                "summary": "Long weekend",
                "start": "2026-07-04",
                "end": "2026-07-07",
                "all_day": True,
                "feed_colour": "#0d8c7e",
            }
        ]

    monkeypatch.setattr(month, "datetime", _freeze_datetime(frozen))
    monkeypatch.setattr(core.server_module, "load_events", load_events)

    with app.app_context():
        out = month.fetch({}, {}, ctx={})

    events_by_date = {
        day["date"]: [event["summary"] for event in day["events"]] for day in out["days"]
    }
    assert events_by_date["2026-07-04"] == ["Long weekend"]
    assert events_by_date["2026-07-05"] == ["Long weekend"]
    assert events_by_date["2026-07-06"] == ["Long weekend"]
    assert events_by_date["2026-07-07"] == []


def test_calendar_month_spreads_multi_day_timed_events_across_covered_dates(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timed span occupies every day it runs through, not just its start.

    The week view already spreads these (#210); bucketing them on the start
    day alone made a trip vanish from every later month cell.
    """
    month = _plugin(app, "calendar_month")
    core = _core_plugin(app)
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
    frozen = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)

    def load_events(
        _feeds_filter: list[str] | None,
        _start: datetime,
        _end: datetime,
        *,
        data_dir: Any,
    ) -> list[dict[str, Any]]:
        del data_dir
        return [
            {
                # Friday 16:00 -> Monday 10:00, Hong Kong local.
                "summary": "Trip",
                "start": "2026-07-03T08:00:00+00:00",
                "end": "2026-07-06T02:00:00+00:00",
                "all_day": False,
                "feed_colour": "#0d8c7e",
            }
        ]

    monkeypatch.setattr(month, "datetime", _freeze_datetime(frozen))
    monkeypatch.setattr(core.server_module, "load_events", load_events)

    with app.app_context():
        out = month.fetch({}, {}, ctx={})

    by_date = {day["date"]: [e["summary"] for e in day["events"]] for day in out["days"]}
    # The bug: only 07-03 carried the trip, every later cell of the span
    # was empty while the week view spanned all four columns.
    assert by_date["2026-07-03"] == ["Trip"]
    assert by_date["2026-07-04"] == ["Trip"]
    assert by_date["2026-07-05"] == ["Trip"]
    assert by_date["2026-07-06"] == ["Trip"]
    assert by_date["2026-07-07"] == []

    # Only the start day is a fresh occurrence; the rest are pass-throughs,
    # so the cell can mark them instead of repeating the title.
    continued = {day["date"]: [e["continued"] for e in day["events"]] for day in out["days"]}
    assert continued["2026-07-03"] == [False]
    assert continued["2026-07-04"] == [True]
    assert continued["2026-07-05"] == [True]
    assert continued["2026-07-06"] == [True]


def test_calendar_month_timed_event_ending_at_midnight_does_not_spill(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End is exclusive: a block ending at local midnight stops the day before."""
    month = _plugin(app, "calendar_month")
    core = _core_plugin(app)
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
    frozen = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)

    def load_events(
        _feeds_filter: list[str] | None,
        _start: datetime,
        _end: datetime,
        *,
        data_dir: Any,
    ) -> list[dict[str, Any]]:
        del data_dir
        return [
            {
                # 22:00 Friday -> 00:00 Saturday, Hong Kong local.
                "summary": "Night shift",
                "start": "2026-07-03T14:00:00+00:00",
                "end": "2026-07-03T16:00:00+00:00",
                "all_day": False,
                "feed_colour": "#0d8c7e",
            }
        ]

    monkeypatch.setattr(month, "datetime", _freeze_datetime(frozen))
    monkeypatch.setattr(core.server_module, "load_events", load_events)

    with app.app_context():
        out = month.fetch({}, {}, ctx={})

    by_date = {day["date"]: [e["summary"] for e in day["events"]] for day in out["days"]}
    assert by_date["2026-07-03"] == ["Night shift"]
    assert by_date["2026-07-04"] == []
