"""DeviceFactsStore: persisted last-known per-device facts (fw version, OTA
capability) that keep the Firmware page truthful across server restarts."""

from __future__ import annotations

from pathlib import Path

from app.state.device_facts import DeviceFactsStore


def test_record_and_get_roundtrip(tmp_path: Path) -> None:
    store = DeviceFactsStore(tmp_path / "facts.json")
    assert store.get("dev_a") is None
    store.record("dev_a", fw_version="1.6.2", ota_schema=1)
    entry = store.get("dev_a")
    assert entry is not None
    assert entry["fw_version"] == "1.6.2"
    assert entry["ota_schema"] == 1
    assert entry["updated_at"] > 0


def test_none_means_no_new_information(tmp_path: Path) -> None:
    store = DeviceFactsStore(tmp_path / "facts.json")
    store.record("dev_a", fw_version="1.6.2", ota_schema=1)
    store.record("dev_a", fw_version=None, ota_schema=None)  # partial beat
    entry = store.get("dev_a")
    assert entry is not None and entry["fw_version"] == "1.6.2" and entry["ota_schema"] == 1


def test_write_only_on_change(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    store = DeviceFactsStore(path)
    store.record("dev_a", fw_version="1.6.2", ota_schema=1)
    before = path.read_bytes()
    store.record("dev_a", fw_version="1.6.2", ota_schema=1)  # steady-state beat
    assert path.read_bytes() == before  # no rewrite (updated_at untouched)
    store.record("dev_a", fw_version="1.6.3")
    assert path.read_bytes() != before


def test_survives_new_store_instance(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    DeviceFactsStore(path).record("dev_a", fw_version="1.6.2", ota_schema=1)
    entry = DeviceFactsStore(path).get("dev_a")  # fresh instance = restart
    assert entry is not None and entry["ota_schema"] == 1


def test_forget(tmp_path: Path) -> None:
    store = DeviceFactsStore(tmp_path / "facts.json")
    store.record("dev_a", fw_version="1.6.2", ota_schema=1)
    store.forget("dev_a")
    assert store.get("dev_a") is None
    store.forget("dev_a")  # idempotent


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    path.write_text("{not json", encoding="utf-8")
    store = DeviceFactsStore(path)
    assert store.get("dev_a") is None
    store.record("dev_a", ota_schema=1)  # recovers by rewriting
    assert store.get("dev_a") == {
        "ota_schema": 1,
        "updated_at": store.get("dev_a")["updated_at"],  # type: ignore[index]
    }
