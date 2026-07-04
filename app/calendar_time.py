"""Timezone helpers shared by the calendar widgets."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from app.tz_resolve import _resolve_iana_timezone


def calendar_timezone() -> tzinfo:
    """Return the timezone calendar widgets should use for date boundaries.

    Rendering already forwards ``settings.app.timezone`` to Chromium so
    client-side ``new Date()`` uses the configured zone. Calendar server-side
    fetchers also need the same zone for "today", visible week/month windows,
    and event date buckets.
    """
    raw = "system"
    try:
        store = current_app.config.get("SETTINGS_STORE")
        if store is not None:
            raw = str((store.get_section("app") or {}).get("timezone") or "system").strip()
    except Exception:
        raw = "system"

    if raw and raw.lower() != "system":
        try:
            return ZoneInfo(raw)
        except ZoneInfoNotFoundError:
            pass

    resolved = _resolve_iana_timezone("system")
    if resolved:
        return ZoneInfo(resolved)

    return datetime.now().astimezone().tzinfo or UTC


def local_midnight_utc(day: date, zone: tzinfo) -> datetime:
    """Return local midnight for ``day`` converted to UTC."""
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)


def event_local_date_key(event: dict[str, Any], zone: tzinfo) -> str:
    """Bucket a calendar event by the date a user sees in ``zone``."""
    start = str(event.get("start") or "")
    if not start:
        return ""
    if event.get("all_day") or "T" not in start:
        return start.split("T")[0]
    try:
        parsed = datetime.fromisoformat(start)
    except ValueError:
        return start.split("T")[0]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(zone).date().isoformat()
