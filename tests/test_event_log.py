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
