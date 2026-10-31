"""One place a clock time becomes words, in the app's clock style.

The style (12-hour ``3:48 PM`` or 24-hour ``15:48``) follows the resolved
locale (:func:`app.locale_resolve.time_cycle`), so the Devices page, the device
page, History, schedules and every Jinja ``clock_time`` read the same. This is
display text only:
persisted settings, ``<input type="time">`` values, ISO timestamps, log lines
and anything a Home Assistant sensor or the REST/companion API carries stay the
canonical 24-hour data form. Mirrored in the browser by ``TesseraeClock`` in
``static/components.js``; change both together.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, tzinfo
from datetime import time as dt_time
from typing import Any

_NARROW_NBSP = "\u202f"
_DAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def app_cycle() -> str:
    """``h12`` / ``h23`` from the app-wide locale (Settings, else the host's).
    ``h23`` outside an app context, and when no locale is set anywhere: the
    bare ``en`` fallback a container (``C.UTF-8``) lands on would otherwise
    read 12-hour, and installs kept 24-hour times before locales chose."""
    from flask import current_app, has_app_context

    from app.locale_resolve import resolve_locale, time_cycle

    if not has_app_context():
        return "h23"
    store = current_app.config.get("SETTINGS_STORE")
    app_section = (store.get_section("app") if store is not None else None) or {}
    raw = str(app_section.get("locale") or "system").strip().lower()
    if raw == "system" and not _host_locale():
        return "h23"
    return time_cycle(resolve_locale(app_section))


def _host_locale() -> str | None:
    from app.locale_resolve import _system_locale

    return _system_locale()


def clock_of(moment: datetime | dt_time, cycle: str | None = None, *, seconds: bool = False) -> str:
    """``3:48 PM`` / ``15:48`` for ``moment``. 12-hour drops ``:00`` from whole
    hours (``11 PM``) unless ``seconds`` is set, which appends ``:SS``
    (``3:48:09 PM``, ``15:48:09``). A narrow no-break space sits before AM/PM."""
    cycle = cycle or app_cycle()
    if cycle != "h12":
        label = f"{moment.hour:02d}:{moment.minute:02d}"
        return f"{label}:{moment.second:02d}" if seconds else label
    hour = moment.hour % 12 or 12
    clock = f"{hour}:{moment.minute:02d}" if moment.minute or seconds else str(hour)
    if seconds:
        clock += f":{moment.second:02d}"
    return f"{clock}{_NARROW_NBSP}{'AM' if moment.hour < 12 else 'PM'}"


def epoch_label(
    epoch: float, *, tz: tzinfo | None = None, now: float | None = None, cycle: str | None = None
) -> str:
    """``3:48 PM`` today, ``Tue 7 AM`` another day (``now`` defaults to the
    present). The day-aware form every "when will it land" line uses."""
    moment = datetime.fromtimestamp(epoch, tz=tz)
    label = clock_of(moment, cycle)
    today = datetime.fromtimestamp(time.time() if now is None else now, tz=tz).date()
    if moment.date() == today:
        return label
    return f"{_DAY_ABBR[moment.weekday()]} {label}"


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, dt_time):
        return datetime(2000, 1, 1, value.hour, value.minute, tzinfo=UTC)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        try:
            hh, mm = text.split(":")[:2]
            if len(text.split(":")) == 2 and 0 <= int(hh) < 24 and 0 <= int(mm) < 60:
                return datetime(2000, 1, 1, int(hh), int(mm), tzinfo=UTC)
        except ValueError:
            pass
    return None


def clock_time(
    value: Any, cycle: str | None = None, *, seconds: bool = False, date: bool = False
) -> str:
    """The ``clock_time`` Jinja filter: an epoch, a ``datetime`` or a
    ``"HH:MM"`` string as a clock time in the app's style. ``date`` puts
    ``Jun 1`` in front (an epoch / datetime only). Unreadable input comes
    back as an empty string, as ``fmt_time`` does."""
    from app.tz_resolve import app_timezone

    moment = _moment(value)
    if moment is None:
        return ""
    if isinstance(value, (int, float)):
        moment = moment.astimezone(app_timezone())
    label = clock_of(moment, cycle, seconds=seconds)
    if date and not isinstance(value, str):
        return f"{_MONTH_ABBR[moment.month - 1]} {moment.day} {label}"
    return label


def register(app: Any) -> None:
    """Add the ``clock_time`` Jinja filter."""
    app.jinja_env.filters["clock_time"] = clock_time
