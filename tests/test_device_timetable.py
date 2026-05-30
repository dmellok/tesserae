"""Per-device rotation view tests.

Rotation in v0.6.3 is pure presentation — a join of pages → schedules
keyed by which device(s) each page is bound to. These tests pin the
join + the day-label compression so the device-card UI doesn't drift
silently when the schedule model evolves."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import device_loader, device_service, renderer_loader
from app.device_timetable import _days_label, timetable_for_device
from app.main import REPO_ROOT
from app.state.page_store import Page, PageStore
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore


@pytest.fixture
def wiring(tmp_path: Path):
    """Discover the bundled devices + renderers + register two
    instances so the join has more than one device to bind to."""
    data_root = tmp_path / "data"
    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=data_root,
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path / "rdata",
    )
    for dev_id in ("hallway", "kitchen"):
        device_service.create_instance(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id=dev_id,
            kind_id="esp32_client",
        )
    pages = PageStore(tmp_path / "pages.json")
    schedules = ScheduleStore(tmp_path / "schedules.json")
    return devices, pages, schedules


def test_rotation_picks_up_schedules_targeting_pages_bound_to_device(wiring) -> None:
    """A schedule's page binds to hallway only → rotation shows up on
    hallway, not on kitchen."""
    devices, pages, schedules = wiring
    pages.save(Page(id="morning", name="Morning", device_ids=["hallway"], cells=[]))
    schedules.upsert(
        Schedule(
            id="morning_sched",
            name="Morning fire",
            page_id="morning",
            type="interval",
            interval_minutes=15,
            time_of_day_start="06:00",
            time_of_day_end="12:00",
            days_of_week=[0, 1, 2, 3, 4],
        )
    )

    hall = timetable_for_device("hallway", devices=devices, pages=pages, schedules=schedules)
    assert len(hall) == 1
    assert hall[0].schedule_id == "morning_sched"
    assert hall[0].page_id == "morning"
    assert hall[0].page_name == "Morning"
    assert hall[0].window_start == "06:00"
    assert hall[0].window_end == "12:00"
    assert hall[0].interval_minutes == 15
    assert hall[0].days_label == "Weekdays"

    # Kitchen isn't bound to the morning page → no row.
    kit = timetable_for_device("kitchen", devices=devices, pages=pages, schedules=schedules)
    assert kit == []


def test_rotation_includes_pages_bound_to_no_device(wiring) -> None:
    """An unbound page (device_ids=[]) fires everywhere on every
    renderer, so it appears on every device's rotation."""
    devices, pages, schedules = wiring
    pages.save(Page(id="global", name="Global", device_ids=[], cells=[]))
    from datetime import UTC, datetime

    schedules.upsert(
        Schedule(
            id="global_sched",
            name="Everywhere",
            page_id="global",
            type="daily",
            fires_at=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        )
    )

    hall = timetable_for_device("hallway", devices=devices, pages=pages, schedules=schedules)
    kit = timetable_for_device("kitchen", devices=devices, pages=pages, schedules=schedules)
    assert [r.schedule_id for r in hall] == ["global_sched"]
    assert [r.schedule_id for r in kit] == ["global_sched"]


def test_rotation_sorted_by_window_start(wiring) -> None:
    """Three schedules, windows 18:00, 06:00, 12:00 → rotation lists
    them as 06:00, 12:00, 18:00."""
    devices, pages, schedules = wiring
    for pid in ("a", "b", "c"):
        pages.save(Page(id=pid, name=pid.upper(), device_ids=["hallway"], cells=[]))
    for pid, start in (("a", "18:00"), ("b", "06:00"), ("c", "12:00")):
        schedules.upsert(
            Schedule(
                id=f"sched_{pid}",
                name=pid,
                page_id=pid,
                type="interval",
                interval_minutes=30,
                time_of_day_start=start,
                time_of_day_end="22:00",
            )
        )
    rows = timetable_for_device("hallway", devices=devices, pages=pages, schedules=schedules)
    assert [r.window_start for r in rows] == ["06:00", "12:00", "18:00"]


def test_rotation_empty_for_kind_not_an_instance(wiring) -> None:
    """Built-in kinds (e.g. ``esp32_client`` itself, not an instance)
    return an empty rotation — kinds aren't bindable, so they can't
    appear on any page's device list."""
    devices, pages, schedules = wiring
    rows = timetable_for_device("esp32_client", devices=devices, pages=pages, schedules=schedules)
    assert rows == []


def test_rotation_empty_for_unknown_device(wiring) -> None:
    devices, pages, schedules = wiring
    rows = timetable_for_device("no_such_device", devices=devices, pages=pages, schedules=schedules)
    assert rows == []


# ----- day-of-week label compression --------------------------------


def test_days_label_groups() -> None:
    """The friendly labels are what users actually expect to read on
    a daily timetable."""
    assert _days_label([0, 1, 2, 3, 4, 5, 6]) == "Every day"
    assert _days_label([0, 1, 2, 3, 4]) == "Weekdays"
    assert _days_label([5, 6]) == "Weekends"
    assert _days_label([0, 2, 4]) == "Mon, Wed, Fri"
    assert _days_label([0, 1, 4, 5, 6]) == "Mon-Tue, Fri-Sun"
    assert _days_label([3]) == "Thu"
    assert _days_label([]) == "(no days)"
