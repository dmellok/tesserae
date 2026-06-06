"""Per-device rotation view, UI sugar over existing Schedules.

Settings → Devices wants to answer "what plays on this panel, and when?"
without forcing the user to grep through the Schedules page and
mentally join schedule.page_id → page.device_ids back to a specific
device card.

This module computes that join: for each instance device, find every
Schedule whose target Page is bound to the device, and present the set
as an ordered timetable. The data underneath stays plain old
``Schedule`` rows, no new model, no new tick path, no two sources of
truth. The card on the device just deep-links each entry to the
Schedules editor.

mypy --strict-friendly. No state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.device_loader import DeviceRegistry
    from app.state.page_store import PageStore
    from app.state.schedule_store import ScheduleStore


# Mon-Sun lookup, matches Schedule.days_of_week values (0=Mon, 6=Sun)
# and the labels the existing schedules UI uses.
DAY_LABELS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class TimetableEntry:
    """One schedule's appearance on a device's rotation timetable.
    Keeps the fields the template needs to read without leaking the
    full Schedule object across the boundary."""

    schedule_id: str
    schedule_name: str
    page_id: str
    page_name: str
    type: str  # "interval" | "daily"
    window_start: str | None  # "HH:MM" or None (interval schedule with no window)
    window_end: str | None
    interval_minutes: int | None  # None for daily schedules
    days_of_week: list[int]  # 0=Mon..6=Sun
    days_label: str  # human-readable: "Mon-Fri", "Weekends", or "Mon, Wed, Fri"
    enabled: bool


def _days_label(dows: list[int]) -> str:
    """Render ``days_of_week`` as a friendly string. Compresses runs to
    "Mon-Fri", recognises common groupings, falls back to a comma list."""
    if not dows:
        return "(no days)"
    s = sorted(set(dows))
    if s == [0, 1, 2, 3, 4, 5, 6]:
        return "Every day"
    if s == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if s == [5, 6]:
        return "Weekends"
    # Compress consecutive runs.
    parts: list[str] = []
    start = s[0]
    prev = s[0]
    for d in s[1:]:
        if d == prev + 1:
            prev = d
            continue
        parts.append(
            DAY_LABELS[start] if start == prev else f"{DAY_LABELS[start]}-{DAY_LABELS[prev]}"
        )
        start = prev = d
    parts.append(DAY_LABELS[start] if start == prev else f"{DAY_LABELS[start]}-{DAY_LABELS[prev]}")
    return ", ".join(parts)


def _sort_key(entry: TimetableEntry) -> tuple[str, str]:
    """Sort by window start (interval schedules with no window come
    first), then by schedule_id for stability."""
    return (entry.window_start or "00:00", entry.schedule_id)


def timetable_for_device(
    device_id: str,
    *,
    devices: DeviceRegistry,
    pages: PageStore,
    schedules: ScheduleStore,
) -> list[TimetableEntry]:
    """Compute the rotation timetable for one device.

    The join is: pages whose ``device_ids`` include this device →
    schedules whose ``page_id`` matches one of those pages → one
    TimetableEntry per schedule. Pages bound to no device at all (the
    virtual-panel fan-out path) are also included on every device -
    they fire everywhere, so they belong on every device's timetable.

    Returns entries sorted by window start so a glance at the card
    reads as a daily timetable."""
    device = devices.get(device_id)
    if device is None or device.kind_of is None:
        return []

    pages_by_id = {p.id: p for p in pages.list()}
    matching_page_ids: set[str] = set()
    for page in pages_by_id.values():
        if not page.device_ids or device_id in page.device_ids:
            # Page is either bound to this device explicitly, or unbound
            # (renders to every device the schedule's renderers cover).
            matching_page_ids.add(page.id)

    entries: list[TimetableEntry] = []
    for schedule in schedules.all():
        if schedule.page_id not in matching_page_ids:
            continue
        bound_page = pages_by_id.get(schedule.page_id)
        page_name = bound_page.name if bound_page else schedule.page_id
        entries.append(
            TimetableEntry(
                schedule_id=schedule.id,
                schedule_name=schedule.name,
                page_id=schedule.page_id,
                page_name=page_name,
                type=schedule.type,
                window_start=schedule.time_of_day_start,
                window_end=schedule.time_of_day_end,
                interval_minutes=schedule.interval_minutes,
                days_of_week=list(schedule.days_of_week),
                days_label=_days_label(list(schedule.days_of_week)),
                enabled=schedule.enabled,
            )
        )

    entries.sort(key=_sort_key)
    return entries
