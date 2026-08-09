"""calendar_day, today's agenda."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app


def _parse_feeds_filter(s: str) -> list[str] | None:
    s = (s or "").strip()
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


def _app_timezone() -> Any:
    """Resolve the app timezone via core's helper; UTC when it's not on
    the path (standalone tests outside a Tesserae install)."""
    try:
        from app.tz_resolve import app_timezone

        return app_timezone()
    except ImportError:
        return UTC


def _all_day_event_overlaps_date(event: dict[str, Any], target: date) -> bool:
    """Whether an all-day event covers ``target``. Delegates to core's
    helper when importable; falls back to the same exclusive-end-date
    logic standalone (mirrors app.calendar_time.all_day_event_overlaps_date)."""
    try:
        from app.calendar_time import all_day_event_overlaps_date

        return all_day_event_overlaps_date(event, target)
    except ImportError:
        start_raw = str(event.get("start") or "")
        if not start_raw:
            return False
        try:
            start = date.fromisoformat(start_raw.split("T")[0])
        except ValueError:
            return False
        end_raw = str(event.get("end") or "")
        try:
            end = date.fromisoformat(end_raw.split("T")[0]) if end_raw else start + timedelta(days=1)
        except ValueError:
            end = start + timedelta(days=1)
        if end <= start:
            end = start + timedelta(days=1)
        return start <= target < end


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    registry = current_app.config["PLUGIN_REGISTRY"]
    core = registry.get("calendar_core")
    if core is None or core.server_module is None:
        return {"error": "calendar_core plugin not installed.", "events": []}

    hours_ahead = int(options.get("hours_ahead") or 24)
    max_events = int(options.get("max_events") or 0)
    feeds_filter = _parse_feeds_filter(options.get("feeds_filter") or "")

    zone = _app_timezone()
    now_local = datetime.now(zone)
    now = now_local.astimezone(UTC)
    end = now + timedelta(hours=hours_ahead)
    try:
        events = core.server_module.load_events(
            feeds_filter,
            now,
            end,
            data_dir=Path(core.data_dir),
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "events": []}

    target_date = now_local.date()
    visible_events = [
        e for e in events if not e.get("all_day") or _all_day_event_overlaps_date(e, target_date)
    ]
    slim = [
        {
            "summary": e["summary"],
            "location": e.get("location") or "",
            "start": e["start"],
            "end": e.get("end"),
            "all_day": e.get("all_day", False),
            "colour": e.get("feed_colour"),
            "feed": e.get("feed_name"),
        }
        for e in visible_events
    ]
    if max_events > 0:
        slim = slim[:max_events]
    return {
        "now": now_local.isoformat(),
        "date": now_local.date().isoformat(),
        "events": slim,
        "count": len(slim),
    }
