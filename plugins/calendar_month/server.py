"""calendar_month, fetch events for the current month."""

from __future__ import annotations

import calendar as cal_mod
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
    feeds_filter = _parse_feeds_filter(options.get("feeds_filter") or "")
    max_per_day = int(options.get("max_events_per_day") or 3)

    # Compute the 6-week visible grid that covers `today`'s month.
    first_of_month = today.replace(day=1)
    first_weekday = 6 if week_start == "sunday" else 0  # 0=Mon, 6=Sun
    # Days from grid-start to first of month
    offset = (first_of_month.weekday() - first_weekday) % 7
    grid_start = first_of_month - timedelta(days=offset)
    grid_end = grid_start + timedelta(days=42)

    start_dt = local_midnight_utc(grid_start, zone)
    end_dt = local_midnight_utc(grid_end, zone)
    try:
        events = core.server_module.load_events(
            feeds_filter,
            start_dt,
            end_dt,
            data_dir=Path(core.data_dir),
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "days": []}

    # Bucket timed events by their local start date. All-day event DTEND values
    # are exclusive, so expand them across each covered visible date.
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev.get("all_day"):
            day_keys = all_day_event_date_keys(ev, grid_start, grid_end)
        else:
            day_keys = [event_local_date_key(ev, zone)]
        for day_key in day_keys:
            buckets.setdefault(day_key, []).append(ev)

    days = []
    cur = grid_start
    while cur < grid_end:
        key = cur.isoformat()
        all_evs = buckets.get(key, [])
        # All-day events first, then timed; cap to max_per_day for the
        # cell, but keep total for the "+N more" badge.
        all_day = [e for e in all_evs if e.get("all_day")]
        timed = [e for e in all_evs if not e.get("all_day")]
        visible = (all_day + timed)[:max_per_day]
        days.append(
            {
                "date": cur.isoformat(),
                "day": cur.day,
                "month": cur.month,
                "in_month": cur.month == first_of_month.month,
                "is_today": cur == today,
                "events": [
                    {
                        "summary": e["summary"],
                        "start": e["start"],
                        # Forward end so a future client variant that wants
                        # to time-slot events in the day cell can; the
                        # current month grid only shows summaries but
                        # keeping the field consistent across calendar_*
                        # avoids drift if/when the month cell grows a
                        # mini-timeline.
                        "end": e.get("end"),
                        "all_day": e.get("all_day", False),
                        "colour": e.get("feed_colour"),
                    }
                    for e in visible
                ],
                "total": len(all_evs),
                "extra": max(0, len(all_evs) - max_per_day),
            }
        )
        cur += timedelta(days=1)

    return {
        "month": first_of_month.month,
        "year": first_of_month.year,
        "month_name": cal_mod.month_name[first_of_month.month],
        "week_start": week_start,
        "days": days,
    }
