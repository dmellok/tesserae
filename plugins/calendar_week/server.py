"""calendar_week, fetch events for the current 7-day window."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app

from app.calendar_time import (
    all_day_event_date_keys,
    event_local_date_key,
    local_midnight_utc,
)
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
        return {"error": "calendar_core plugin not installed.", "days": []}

    zone = app_timezone()
    today = datetime.now(zone).date()
    week_start = options.get("week_start", "monday")
    # range_mode: "calendar_week" (default) anchors the 7-day window to the
    # chosen week_start, same as before; "rolling_7" ignores week_start and
    # always starts today, so the widget shows "today + the next 6 days"
    # instead of jumping back to Sunday/Monday once today isn't the first
    # day of the calendar week.
    range_mode = options.get("range_mode", "calendar_week")
    if range_mode == "rolling_7":
        start_date = today
    else:
        first_weekday = 6 if week_start == "sunday" else 0
        # Start the week on the chosen weekday at-or-before today.
        offset = (today.weekday() - first_weekday) % 7
        start_date = today - timedelta(days=offset)
    end_date = start_date + timedelta(days=7)

    start_dt = local_midnight_utc(start_date, zone)
    end_dt = local_midnight_utc(end_date, zone)

    feeds_filter = _parse_feeds_filter(options.get("feeds_filter") or "")
    try:
        events = core.server_module.load_events(
            feeds_filter,
            start_dt,
            end_dt,
            data_dir=Path(core.data_dir),
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "days": []}

    # Bucket timed events by their local start date. All-day event DTEND
    # values are exclusive, so expand them across each covered visible date
    # (same split as calendar_month) rather than only their start day.
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev.get("all_day"):
            day_keys = all_day_event_date_keys(ev, start_date, end_date)
        else:
            day_keys = [event_local_date_key(ev, zone)]
        for day_key in day_keys:
            buckets.setdefault(day_key, []).append(ev)

    days = []
    cur = start_date
    while cur < end_date:
        all_evs = buckets.get(cur.isoformat(), [])
        days.append(
            {
                "date": cur.isoformat(),
                "day": cur.day,
                "is_today": cur == today,
                "weekday": cur.weekday(),  # 0=Mon
                "events": [
                    {
                        "summary": e["summary"],
                        "start": e["start"],
                        # The client's height calculation falls back to a
                        # 1-hour duration when end is missing. Forward it
                        # here so a real 2-hour event renders at its true
                        # height instead of every event collapsing to the
                        # same 1-hour block.
                        "end": e.get("end"),
                        "all_day": e.get("all_day", False),
                        "colour": e.get("feed_colour"),
                        "location": e.get("location") or "",
                    }
                    for e in all_evs
                ],
            }
        )
        cur += timedelta(days=1)

    return {
        "start": start_date.isoformat(),
        "end": (end_date - timedelta(days=1)).isoformat(),
        "week_start": week_start,
        "days": days,
    }
