"""Tests for the per-device rotation state store."""

from __future__ import annotations

import json
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


def test_patch_changes_only_the_given_fields(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    store.upsert(_sample())
    until = datetime(2026, 7, 5, tzinfo=UTC)
    store.patch("kitchen", {"automation_pause_until": until})
    got = store.get("kitchen")
    assert got is not None
    assert got.automation_pause_until == until
    assert got.override_until == datetime(2026, 7, 4, tzinfo=UTC)
    assert (got.rotation_id, got.step_index, got.last_button_event_id) == ("kitchen_rot", 2, 42)


def test_patch_creates_a_missing_record(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    until = datetime(2026, 7, 5, tzinfo=UTC)
    created = store.patch("hall", {"automation_pause_until": until})
    assert created is not None and created.device_id == "hall"
    assert store.get("hall") == created


def test_patch_with_expect_is_a_compare_and_set(tmp_path: Path) -> None:
    store = DeviceRotationStateStore(tmp_path / "state.json")
    old = datetime(2026, 7, 5, tzinfo=UTC)
    new = datetime(2026, 7, 6, tzinfo=UTC)
    store.patch("kitchen", {"automation_pause_until": new})
    # A release of the lapsed value must not clear the newer pause.
    assert (
        store.patch(
            "kitchen",
            {"automation_pause_until": None},
            expect={"automation_pause_until": old},
        )
        is None
    )
    got = store.get("kitchen")
    assert got is not None and got.automation_pause_until == new
    cleared = store.patch(
        "kitchen", {"automation_pause_until": None}, expect={"automation_pause_until": new}
    )
    assert cleared is not None and cleared.automation_pause_until is None


def test_a_record_with_a_field_this_version_does_not_know_still_loads(tmp_path: Path) -> None:
    # Written by a newer server, read after a downgrade: the hold and position
    # beside the unknown field must not read as missing.
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "kitchen": {
                    "device_id": "kitchen",
                    "rotation_id": "kitchen_rot",
                    "step_index": 3,
                    "from_the_future": True,
                }
            }
        ),
        encoding="utf-8",
    )
    got = DeviceRotationStateStore(path).get("kitchen")
    assert got is not None and got.rotation_id == "kitchen_rot" and got.step_index == 3
