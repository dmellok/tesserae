"""Tests for the per-device rotation state store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.state.device_rotation_state_model import DeviceRotationState
from app.state.device_rotation_state_store import DeviceRotationStateStore


def _sample(device_id: str = "kitchen") -> DeviceRotationState:
    return DeviceRotationState(
        device_id=device_id,
        rotation_id="kitchen_rot",
        step_index=2,
        override_until=datetime(2026, 7, 4, tzinfo=UTC),
        last_button="right",
        last_button_event_id=42,
        last_button_at=datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
    )


def test_get_missing_returns_none(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    assert store.get("does_not_exist") is None


def test_upsert_then_get_roundtrips(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    state = _sample()
    store.upsert(state)

    loaded = store.get("kitchen")
    assert loaded is not None
    assert loaded.device_id == "kitchen"
    assert loaded.rotation_id == "kitchen_rot"
    assert loaded.step_index == 2
    assert loaded.last_button_event_id == 42
    assert loaded.override_until == datetime(2026, 7, 4, tzinfo=UTC)


def test_upsert_overwrites_existing(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    store.upsert(_sample())
    updated = _sample().model_copy(update={"step_index": 5})
    store.upsert(updated)

    loaded = store.get("kitchen")
    assert loaded is not None
    assert loaded.step_index == 5


def test_delete_returns_true_when_removed(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    store.upsert(_sample())

    assert store.delete("kitchen") is True
    assert store.get("kitchen") is None


def test_delete_returns_false_when_absent(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    assert store.delete("nope") is False


def test_all_returns_every_record(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    store.upsert(_sample("kitchen"))
    store.upsert(_sample("bedroom"))

    all_records = store.all()
    assert set(all_records.keys()) == {"kitchen", "bedroom"}


def test_corrupt_json_reads_as_empty(tmp_path: Path) -> None:
    """A garbled state file shouldn't crash the process; a fresh boot
    from a corrupt state just resets rotation position to time-based
    defaults, no worse than a first-time boot."""
    path = tmp_path / "state.json"
    path.write_text("not valid json {", encoding="utf-8")

    store = DeviceRotationStateStore(path)
    assert store.get("kitchen") is None
    assert store.all() == {}
