"""Quiet hours: per-app + per-device window during which automated
pushes (scheduler firings, webhook calls) are suppressed.

Two layers:

* **App-level** (Settings → Server → App): a single window for the
  whole install, *"the house goes quiet 22:00 → 07:00"*.
* **Per-device override** (Settings → Devices, panel block): a
  device can declare its own window, useful when a panel lives in
  a kid's room or a workshop with different rhythms.

Manual pushes, the Send page, the Push-now buttons, **bypass** quiet
hours by design. Quiet hours filter *automation*, not deliberate user
intent. The relevant callers (``Scheduler._fire``, the webhook route)
pass ``respect_quiet_hours=True`` into :func:`app.push.PushManager.push`,
which filters the bound-device set against this module before render.

Windows that wrap midnight (start ≥ end) are treated as one logical
window crossing the date boundary, same semantics the scheduler's
``_matches_window`` already uses for time-of-day schedule windows.

Both layers can also say which weekdays the window applies on and
which weekdays are quiet all day (#299, an office that is empty
overnight on weekdays and closed at the weekend). A day is evaluated
by its own clock: on a listed day a wrap-around window covers that
day's early morning up to ``end`` and its evening from ``start``, and
an all-day day is quiet from midnight to midnight. A layer can ask
for the device to *sleep through* its quiet window: the REST status
response then stretches ``next_poll_s`` to the end of the window
instead of letting the panel wake for nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# Weekday keys in ``datetime.weekday()`` order (Monday = 0). This is the
# vocabulary settings.json and the device manifest store, and what the
# settings forms submit.
DAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ALL_DAYS: frozenset[int] = frozenset(range(7))
NO_DAYS: frozenset[int] = frozenset()

# How far ``quiet_ends_at`` looks for the window to open. A week covers any
# weekly pattern; past that the window never opens and callers treat the
# device as quiet indefinitely.
_ENDS_SEARCH_DAYS = 8


class QuietHoursWindow(NamedTuple):
    """Resolved, parsed quiet-hours window. ``start == end`` is treated
    as "never" rather than "the whole day" so a misconfiguration
    (both fields blank → both parse to 00:00) doesn't accidentally
    silence every device.

    ``days`` are the weekdays the start/end window applies on and
    ``all_day`` the weekdays that are quiet from midnight to midnight;
    both are ``datetime.weekday()`` numbers. ``sleep_through`` asks the
    device REST path to sleep until the window ends rather than waking
    on its usual interval inside it."""

    start: time
    end: time
    days: frozenset[int] = ALL_DAYS
    all_day: frozenset[int] = NO_DAYS
    sleep_through: bool = False


def _parse_hhmm(value: str | None) -> time | None:
    """Parse ``'HH:MM'`` to a :class:`datetime.time`, or ``None`` if
    malformed. Returning ``None`` (rather than raising) means a typo
    in settings.json never crashes the scheduler thread."""
    if not value:
        return None
    try:
        h_str, m_str = value.split(":", 1)
        return time(int(h_str), int(m_str))
    except (ValueError, TypeError):
        return None


def parse_days(raw: object, default: frozenset[int]) -> frozenset[int]:
    """Weekday set from a stored or submitted value: a list of ``mon``..
    ``sun`` keys (or weekday numbers), or one comma-separated string.
    ``None`` means "not configured" and yields ``default``; anything
    present, including an empty list, is taken literally so unticking
    every day means what it says."""
    if raw is None:
        return default
    items: Iterable[object]
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = raw
    else:
        return default
    out: set[int] = set()
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and 0 <= item < 7:
            out.add(item)
            continue
        key = str(item).strip().lower()[:3]
        if key in DAY_KEYS:
            out.add(DAY_KEYS.index(key))
    return frozenset(out)


def days_to_keys(days: Iterable[int]) -> list[str]:
    """Weekday numbers back to the stored ``mon``..``sun`` keys, in week order."""
    return [DAY_KEYS[d] for d in sorted(set(days)) if 0 <= d < 7]


def _build_window(
    start: time | None,
    end: time | None,
    days: frozenset[int],
    all_day: frozenset[int],
    sleep_through: bool,
) -> QuietHoursWindow | None:
    """Assemble a window, or ``None`` when it could never be quiet. A
    missing or degenerate start/end pair disables the timed part but
    keeps any all-day days: a weekend-only office wants Saturday and
    Sunday quiet even with the nightly window left blank."""
    if start is None or end is None or start == end:
        start, end, days = time(0, 0), time(0, 0), NO_DAYS
    if not days and not all_day:
        return None
    return QuietHoursWindow(start, end, days, all_day, bool(sleep_through))


def _device_override(device: Any) -> dict[str, Any] | None:
    """Pull a ``quiet_hours`` dict off a Device-like object. Devices
    expose the parsed manifest under ``.manifest`` for instances and
    panel info under ``.panel`` for both kinds; we look at both so a
    panel-block-housed override works too."""
    if device is None:
        return None
    manifest = getattr(device, "manifest", None)
    if isinstance(manifest, dict):
        qh = manifest.get("quiet_hours")
        if isinstance(qh, dict):
            return qh
    return None


def resolve_quiet_hours(
    app_settings: dict[str, Any],
    device: Any | None,
) -> QuietHoursWindow | None:
    """Effective quiet-hours window for ``device``, or ``None`` if
    quiet hours are disabled at every applicable layer.

    Resolution order:

    1. Per-device override (when present and ``enabled``).
    2. App-level setting (when ``quiet_hours_enabled``).
    3. ``None`` (quiet hours off).
    """
    override = _device_override(device)
    if override and override.get("enabled"):
        window = _build_window(
            _parse_hhmm(str(override.get("start") or "")),
            _parse_hhmm(str(override.get("end") or "")),
            parse_days(override.get("days"), ALL_DAYS),
            parse_days(override.get("all_day"), NO_DAYS),
            bool(override.get("sleep")),
        )
        if window is not None:
            return window

    if not app_settings.get("quiet_hours_enabled"):
        return None
    return _build_window(
        _parse_hhmm(str(app_settings.get("quiet_hours_start") or "")),
        _parse_hhmm(str(app_settings.get("quiet_hours_end") or "")),
        parse_days(app_settings.get("quiet_hours_days"), ALL_DAYS),
        parse_days(app_settings.get("quiet_hours_all_day"), NO_DAYS),
        bool(app_settings.get("quiet_hours_sleep")),
    )


def is_in_window(window: QuietHoursWindow, now: datetime, tz: tzinfo | None) -> bool:
    """``True`` iff ``now`` (interpreted in ``tz``) falls inside
    ``window``. Handles midnight wrap: a window with ``start=22:00,
    end=07:00`` means *from 22:00 today through 07:00 tomorrow*."""
    local = now.astimezone(tz) if tz else now
    weekday = local.weekday()
    if weekday in window.all_day:
        return True
    if weekday not in window.days:
        return False
    current = local.time()
    if window.start <= window.end:
        return window.start <= current <= window.end
    # Wrap-around window, by this day's clock: the early morning up to
    # ``end`` and the evening from ``start``.
    return current >= window.start or current <= window.end


def quiet_ends_at(window: QuietHoursWindow, now: datetime, tz: tzinfo | None) -> datetime | None:
    """The first instant at or after ``now`` that is outside ``window``,
    or ``None`` when the window never opens within the next week (a
    device configured quiet every day, all day).

    Quietness only changes at three kinds of boundary: a minute past the
    window's end (the test is inclusive of its end minute), the window's
    start, and midnight, where the day's own rule takes over. Walking
    those boundaries in order and returning the first that is not quiet
    is exact, and cheap. ``now`` itself is returned unchanged when it is
    already outside the window. Aware input yields UTC; naive stays naive."""
    if not is_in_window(window, now, tz):
        return now
    local = now.astimezone(tz) if tz else now
    day0 = local.date()
    end_plus = (
        datetime.combine(datetime(2000, 1, 1).date(), window.end) + timedelta(minutes=1)
    ).time()
    marks = (time(0, 0), end_plus, window.start)
    candidates: list[datetime] = []
    for offset in range(_ENDS_SEARCH_DAYS + 1):
        day = day0 + timedelta(days=offset)
        for mark in marks:
            # Built from date + wall time so a zone's DST fold or gap
            # resolves through the tzinfo rather than by arithmetic.
            candidates.append(datetime.combine(day, mark, tzinfo=local.tzinfo))
    for candidate in sorted(candidates):
        if candidate <= local:
            continue
        if not is_in_window(window, candidate, tz):
            return candidate.astimezone(UTC) if candidate.tzinfo is not None else candidate
    return None


def device_is_quiet(
    app_settings: dict[str, Any],
    device: Any,
    now: datetime,
    tz: tzinfo | None,
) -> bool:
    """Convenience: ``True`` iff ``device`` is currently within its
    effective quiet-hours window. The most common form of the check
    used by callers."""
    window = resolve_quiet_hours(app_settings, device)
    if window is None:
        return False
    return is_in_window(window, now, tz)
