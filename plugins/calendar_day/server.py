"""calendar_day, today's agenda."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app

from app.calendar_time import all_day_event_overlaps_date, timed_event_date_keys
from app.tz_resolve import app_timezone


def _parse_feeds_filter(s: str) -> list[str] | None:
    s = (s or "").strip()
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


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

    zone = app_timezone()
    now_local = datetime.now(zone)
    # Start at LOCAL MIDNIGHT, not at `now`.
    #
    # This widget draws a single day on a 0-24 axis and filters everything to that day
    # (see below), so it is a day view. Starting the fetch at `now` meant it quietly lost
    # the morning as the day went on: at 14:00 a 10:00-11:00 meeting was not in the
    # payload at all, and the auto-fitted range then shrank around whatever was left. On
    # a widget that draws a day's timeline that reads as missing data rather than as an
    # agenda (#252).
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day_start_local.astimezone(UTC)
    # `hours_ahead` keeps its meaning: a FORWARD bound, measured from now. It no longer
    # decides where the window begins, only how far past this moment to look.
    end = now_local.astimezone(UTC) + timedelta(hours=hours_ahead)
    try:
        events = core.server_module.load_events(
            feeds_filter,
            start,
            end,
            data_dir=Path(core.data_dir),
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "events": []}

    # Both kinds are filtered to the day being drawn (#248). ``hours_ahead``
    # bounds how far PAST NOW to look, so a 24 h default reaches into tomorrow;
    # a timed event living entirely on tomorrow used to survive into today's
    # list. The client then drew it against today's 0-24 axis, where neither
    # edge is today, and clampToDay's multi-day rule painted it across the
    # whole column while the label still read its real 10:00-11:00. Moving an
    # event to tomorrow therefore looked like today's copy had gone stale.
    #
    # Overnight and multi-day events still belong here: they genuinely occupy
    # part of today, and clamping them to the day is exactly what that rule is
    # for.
    target_date = now_local.date()
    target_key = target_date.isoformat()
    window_end = target_date + timedelta(days=1)

    def _occupies_today(event: dict[str, Any]) -> bool:
        if event.get("all_day"):
            return all_day_event_overlaps_date(event, target_date)
        return target_key in timed_event_date_keys(event, zone, target_date, window_end)

    visible_events = [e for e in events if _occupies_today(e)]
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
