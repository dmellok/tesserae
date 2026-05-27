"""calendar_day — today's agenda."""

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
        return {"error": "calendar_core plugin not installed.", "events": []}

    hours_ahead = int(options.get("hours_ahead") or 24)
    max_events = int(options.get("max_events") or 0)
    feeds_filter = _parse_feeds_filter(options.get("feeds_filter") or "")

    now = datetime.now(UTC)
    end = now + timedelta(hours=hours_ahead)
    try:
        events = core.server_module.load_events(
            feeds_filter, now, end, data_dir=Path(core.data_dir),
        )
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "events": []}

    slim = [
        {
            "summary":  e["summary"],
            "location": e.get("location") or "",
            "start":    e["start"],
            "end":      e.get("end"),
            "all_day":  e.get("all_day", False),
            "colour":   e.get("feed_colour"),
            "feed":     e.get("feed_name"),
        } for e in events
    ]
    if max_events > 0:
        slim = slim[:max_events]
    return {
        "now":    now.isoformat(),
        "date":   now.date().isoformat(),
        "events": slim,
        "count":  len(slim),
    }
