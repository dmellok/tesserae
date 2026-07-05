"""Timezone helpers shared by the calendar widgets."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any


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


def _all_day_bounds(event: dict[str, Any]) -> tuple[date, date] | None:
    start_raw = str(event.get("start") or "")
    if not start_raw:
        return None
    try:
        start = date.fromisoformat(start_raw.split("T")[0])
    except ValueError:
        return None

    end_raw = str(event.get("end") or "")
    if end_raw:
        try:
            end = date.fromisoformat(end_raw.split("T")[0])
        except ValueError:
            end = start + timedelta(days=1)
    else:
        end = start + timedelta(days=1)

    if end <= start:
        end = start + timedelta(days=1)
    return start, end


def all_day_event_overlaps_date(event: dict[str, Any], target: date) -> bool:
    """Return whether an all-day event covers ``target``.

    iCalendar date-only ``DTEND`` values are exclusive, so an event with
    start=2026-07-04 and end=2026-07-05 belongs only to July 4.
    """
    bounds = _all_day_bounds(event)
    if bounds is None:
        return False
    start, end = bounds
    return start <= target < end


def all_day_event_date_keys(
    event: dict[str, Any], window_start: date, window_end: date
) -> list[str]:
    """Return local date keys an all-day event occupies inside ``[start, end)``."""
    bounds = _all_day_bounds(event)
    if bounds is None:
        return []
    start, end = bounds
    cur = max(start, window_start)
    stop = min(end, window_end)
    keys: list[str] = []
    while cur < stop:
        keys.append(cur.isoformat())
        cur += timedelta(days=1)
    return keys
