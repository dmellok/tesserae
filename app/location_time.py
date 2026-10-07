"""Wall-clock time at a widget's location rather than the server's.

Open-Meteo calls made with ``timezone=auto`` return sunrise / sunset /
daily dates in the location's own zone, and the response carries the
zone as ``timezone`` (IANA name) plus ``utc_offset_seconds``. A widget
that compares those values against "now" has to take "now" in the same
zone, otherwise a server in Berlin showing Melbourne weather is hours
out (issue #351).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def is_iana_zone(name: Any) -> bool:
    """True when ``name`` is a zone name ``zoneinfo`` can load.

    Rejects non-strings, empty strings, absolute paths and anything with
    ``..`` before asking ``zoneinfo`` so a saved option can never be used
    to probe the filesystem.
    """
    if not isinstance(name, str):
        return False
    name = name.strip()
    if not name or len(name) > 64 or name.startswith("/") or ".." in name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def location_tz(tz_name: Any = None, utc_offset_seconds: Any = None) -> tzinfo | None:
    """Resolve a location's zone from an IANA name, else a fixed offset.

    Returns ``None`` when neither is usable so the caller can fall back
    to the server's local clock.
    """
    if is_iana_zone(tz_name):
        return ZoneInfo(str(tz_name).strip())
    if isinstance(utc_offset_seconds, (int, float)) and not isinstance(utc_offset_seconds, bool):
        try:
            return timezone(timedelta(seconds=int(utc_offset_seconds)))
        except (OverflowError, ValueError):
            return None
    return None


def location_now(
    tz_name: Any = None,
    utc_offset_seconds: Any = None,
    *,
    now: datetime | None = None,
) -> datetime:
    """Current time at the location, falling back to server local time.

    ``now`` is an aware instant to convert (tests pass a fixed one);
    defaults to the current time.
    """
    zone = location_tz(tz_name, utc_offset_seconds)
    instant = now if now is not None else datetime.now().astimezone()
    if instant.tzinfo is None:
        instant = instant.astimezone()
    if zone is None:
        return instant.astimezone()
    return instant.astimezone(zone)


def payload_now(payload: dict[str, Any], *, now: datetime | None = None) -> datetime:
    """``location_now`` fed from an Open-Meteo response body."""
    return location_now(payload.get("timezone"), payload.get("utc_offset_seconds"), now=now)
