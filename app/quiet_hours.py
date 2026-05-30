"""Quiet hours: per-app + per-device window during which automated
pushes (scheduler firings, webhook calls) are suppressed.

Two layers:

* **App-level** (Settings → Server → App): a single window for the
  whole install — *"the house goes quiet 22:00 → 07:00"*.
* **Per-device override** (Settings → Devices, panel block): a
  device can declare its own window — useful when a panel lives in
  a kid's room or a workshop with different rhythms.

Manual pushes — the Send page, the Push-now buttons — **bypass** quiet
hours by design. Quiet hours filter *automation*, not deliberate user
intent. The relevant callers (``Scheduler._fire``, the webhook route)
pass ``respect_quiet_hours=True`` into :func:`app.push.PushManager.push`,
which filters the bound-device set against this module before render.

Windows that wrap midnight (start ≥ end) are treated as one logical
window crossing the date boundary — same semantics the scheduler's
``_matches_window`` already uses for time-of-day schedule windows.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, tzinfo
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class QuietHoursWindow(NamedTuple):
    """Resolved, parsed quiet-hours window. ``start == end`` is treated
    as "never" rather than "the whole day" so a misconfiguration
    (both fields blank → both parse to 00:00) doesn't accidentally
    silence every device."""

    start: time
    end: time


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
        start = _parse_hhmm(str(override.get("start") or ""))
        end = _parse_hhmm(str(override.get("end") or ""))
        if start is not None and end is not None and start != end:
            return QuietHoursWindow(start, end)

    if not app_settings.get("quiet_hours_enabled"):
        return None
    start = _parse_hhmm(str(app_settings.get("quiet_hours_start") or ""))
    end = _parse_hhmm(str(app_settings.get("quiet_hours_end") or ""))
    if start is None or end is None or start == end:
        return None
    return QuietHoursWindow(start, end)


def is_in_window(window: QuietHoursWindow, now: datetime, tz: tzinfo | None) -> bool:
    """``True`` iff ``now`` (interpreted in ``tz``) falls inside
    ``window``. Handles midnight wrap: a window with ``start=22:00,
    end=07:00`` means *from 22:00 today through 07:00 tomorrow*."""
    local = now.astimezone(tz) if tz else now
    current = local.time()
    if window.start <= window.end:
        return window.start <= current <= window.end
    # Wrap-around window.
    return current >= window.start or current <= window.end


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
