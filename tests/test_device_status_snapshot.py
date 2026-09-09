"""DeviceStatusSnapshotStore: the last heartbeat per device persists across a
server restart, so the status cache is seeded with real readings instead of
sitting empty until the device next reports."""

from __future__ import annotations

import json
from pathlib import Path

from app.state.device_status_snapshot import REFRESH_AFTER_S, DeviceStatusSnapshotStore


def test_record_and_get_roundtrip(tmp_path: Path) -> None:
    store = DeviceStatusSnapshotStore(tmp_path / "status.json")
    assert store.get("dev_a") is None
    store.record(
        "dev_a",
        received_at=1_700_000_000.0,
        parsed={"battery_pct": 81, "rssi": -61, "temperature_c": 22.4, "humidity_pct": 48.0},
    )
    entry = store.get("dev_a")
    assert entry is not None
    assert entry["received_at"] == 1_700_000_000.0
    assert entry["parsed"]["temperature_c"] == 22.4
    assert entry["parsed"]["rssi"] == -61


def test_all_returns_seedable_entries_only(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "good": {"received_at": 1.0, "parsed": {"rssi": -50}},
                "no_parsed": {"received_at": 1.0},
                "no_time": {"parsed": {"rssi": -50}},
                "junk": "nope",
            }
        ),
        encoding="utf-8",
    )
    assert DeviceStatusSnapshotStore(path).all() == {
        "good": {"received_at": 1.0, "parsed": {"rssi": -50}}
    }


def test_steady_beat_skips_the_write_until_refresh_window(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    store = DeviceStatusSnapshotStore(path)
    parsed = {"battery_pct": 80, "rssi": -60}
    store.record("dev_a", received_at=1000.0, parsed=parsed)
    before = path.read_bytes()
    store.record("dev_a", received_at=1030.0, parsed=dict(parsed))  # same readings, 30 s on
    assert path.read_bytes() == before
    store.record("dev_a", received_at=1000.0 + REFRESH_AFTER_S, parsed=dict(parsed))
    entry = store.get("dev_a")
    assert entry is not None and entry["received_at"] == 1000.0 + REFRESH_AFTER_S
    store.record("dev_a", received_at=1310.0, parsed={"battery_pct": 79, "rssi": -60})
    entry = store.get("dev_a")
    assert entry is not None and entry["parsed"]["battery_pct"] == 79


def test_unencodable_values_are_dropped_not_fatal(tmp_path: Path) -> None:
    store = DeviceStatusSnapshotStore(tmp_path / "status.json")
    store.record(
        "dev_a",
        received_at=1.0,
        parsed={"rssi": -60, "temperature_c": float("nan"), "weird": object(), "ok": [1, "x"]},
    )
    entry = store.get("dev_a")
    assert entry is not None
    assert entry["parsed"] == {"rssi": -60, "ok": [1, "x"]}


def test_forget(tmp_path: Path) -> None:
    store = DeviceStatusSnapshotStore(tmp_path / "status.json")
    store.record("dev_a", received_at=1.0, parsed={"rssi": -60})
    store.record("dev_b", received_at=1.0, parsed={"rssi": -70})
    store.forget("dev_a")
    store.forget("missing")
    assert store.get("dev_a") is None
    assert store.get("dev_b") is not None


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text("{not json", encoding="utf-8")
    store = DeviceStatusSnapshotStore(path)
    assert store.all() == {}
    store.record("dev_a", received_at=1.0, parsed={"rssi": -60})
    assert store.get("dev_a") is not None


def test_app_seeds_status_cache_from_snapshot(tmp_path: Path) -> None:
    from app.app_factory import create_app

    core = tmp_path / "core"
    core.mkdir(parents=True)
    (core / "device_status.json").write_text(
        json.dumps(
            {
                "panel_1": {
                    "received_at": 1_700_000_000.0,
                    "parsed": {"battery_pct": 77, "rssi": -58, "temperature_c": 21.5},
                }
            }
        ),
        encoding="utf-8",
    )
    app = create_app(testing=True, data_root=tmp_path)
    cached = app.config["DEVICE_STATUS"].get("panel_1")
    assert cached is not None
    assert cached["received_at"] == 1_700_000_000.0
    assert cached["parsed"]["temperature_c"] == 21.5
    assert cached["parsed"]["battery_pct"] == 77
