"""EventLog: append + list + delete + digest reference counting + cap eviction."""

from __future__ import annotations

from pathlib import Path

from app.state.event_log import EventLog


def test_round_trip(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    eid = log.record(type="push", source="page", target="home", status="sent", digest="abc")
    row = log.get(eid)
    assert row is not None
    assert row.type == "push"
    assert row.source == "page"
    assert row.target == "home"
    assert row.status == "sent"
    assert row.digest == "abc"


def test_extra_round_trips_through_json(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    eid = log.record(
        type="push",
        source="page",
        target="home",
        status="sent",
        extra={"renderers": [{"renderer_id": "pi_png", "topic": "x"}]},
    )
    row = log.get(eid)
    assert row is not None
    assert row.extra["renderers"][0]["renderer_id"] == "pi_png"


def test_list_orders_newest_first(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    for i in range(5):
        log.record(type="push", source="page", target=f"p{i}", status="sent")
    rows = log.list(limit=10)
    assert [r.target for r in rows] == ["p4", "p3", "p2", "p1", "p0"]


def test_list_filters_by_type(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    log.record(type="push", source="page", target="home", status="sent")
    log.record(type="renderer", source="pi_png", target="x", status="sent")
    log.record(type="push", source="file", target="img", status="sent")
    pushes = log.list(type="push", limit=10)
    assert [r.source for r in pushes] == ["file", "page"]
    assert log.count(type="push") == 2
    assert log.count(type="renderer") == 1


def test_delete_removes_row(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    eid = log.record(type="push", source="page", target="home", status="sent")
    assert log.delete(eid) is True
    assert log.get(eid) is None
    assert log.delete(eid) is False


def test_digest_in_use_only_when_referenced(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    log.record(type="push", source="page", target="a", status="sent", digest="shared")
    log.record(type="push", source="page", target="b", status="sent", digest="shared")
    assert log.digest_in_use("shared") is True
    assert log.digest_in_use("never") is False


def test_cap_evicts_oldest(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db", cap=3)
    ids = [log.record(type="push", source="page", target=str(i), status="sent") for i in range(5)]
    rows = log.list(limit=10)
    assert len(rows) == 3
    # Oldest two (ids[0], ids[1]) evicted; newest three remain.
    assert {r.id for r in rows} == set(ids[2:])


def test_device_cap_protects_other_events(tmp_path: Path) -> None:
    """A flood of device heartbeats evicts old heartbeats, not push history."""
    log = EventLog(tmp_path / "events.db", cap=1000, device_cap=10)
    push_ids = [
        log.record(type="push", source="page", target=f"p{i}", status="sent") for i in range(5)
    ]
    for i in range(50):  # way over the device sub-cap
        log.record(type="device", source="lounge", target="t", status="ok", extra={"i": i})
    assert log.count(type="device") == 10  # bounded by device_cap
    # All push rows survive (well under the global cap).
    assert all(log.get(pid) is not None for pid in push_ids)
    # The surviving device rows are the most recent ones.
    devs = log.list(type="device", limit=10)
    assert {d.extra["i"] for d in devs} == set(range(40, 50))


def test_status_changed_meaningfully() -> None:
    from app.main import status_changed_meaningfully

    # First sighting always counts.
    assert status_changed_meaningfully({}, {"state": "idle"}) is True
    # Volatile-only drift (battery / rssi) does not.
    prev = {"state": "idle", "battery_pct": 80, "rssi": -60}
    same = {"state": "idle", "battery_pct": 74, "rssi": -71}
    assert status_changed_meaningfully(prev, same) is False
    # A real field change does.
    assert status_changed_meaningfully(prev, {"state": "rendering", "battery_pct": 80}) is True


def test_list_exclude_statuses_filters_them_out(tmp_path: Path) -> None:
    """``exclude_statuses`` should skip those status values entirely so
    the History page can hide quiet-hours / held-by-conditions rows
    without losing them from the underlying log."""
    log = EventLog(tmp_path / "events.db")
    log.record(type="push", source="page", target="home", status="sent")
    log.record(type="push", source="scheduler", target="evening", status="quiet")
    log.record(type="push", source="scheduler", target="evening", status="held")
    log.record(type="push", source="webhook", target="home", status="failed")

    # Default: all rows visible.
    rows = log.list(type="push")
    assert {r.status for r in rows} == {"sent", "quiet", "held", "failed"}

    # With the exclude_statuses set the History route passes:
    rows = log.list(type="push", exclude_statuses=("quiet", "held"))
    assert {r.status for r in rows} == {"sent", "failed"}
