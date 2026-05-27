"""calendar_week — fetch events for the current 7-day window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app


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

    today = datetime.now(UTC).date()
    week_start = options.get("week_start", "monday")
    first_weekday = 6 if week_start == "sunday" else 0
    # Start the week on the chosen weekday at-or-before today.
    offset = (today.weekday() - first_weekday) % 7
    start_date = today - timedelta(days=offset)
    end_date = start_date + timedelta(days=7)

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(end_date, datetime.min.time(), tzinfo=UTC)

    feeds_filter = _parse_feeds_filter(options.get("feeds_filter") or "")
    try:
        events = core.server_module.load_events(
            feeds_filter, start_dt, end_dt, data_dir=Path(core.data_dir),
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "days": []}

    buckets: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        s = ev["start"]
        day_key = s.split("T")[0] if "T" in s else s
        buckets.setdefault(day_key, []).append(ev)

    days = []
    cur = start_date
    while cur < end_date:
        all_evs = buckets.get(cur.isoformat(), [])
        days.append({
            "date":     cur.isoformat(),
            "day":      cur.day,
            "is_today": cur == today,
            "weekday":  cur.weekday(),  # 0=Mon
            "events":   [
                {
                    "summary": e["summary"],
                    "start":   e["start"],
                    "all_day": e.get("all_day", False),
                    "colour":  e.get("feed_colour"),
                } for e in all_evs
            ],
        })
        cur += timedelta(days=1)

    return {
        "start":      start_date.isoformat(),
        "end":        (end_date - timedelta(days=1)).isoformat(),
        "week_start": week_start,
        "days":       days,
    }
