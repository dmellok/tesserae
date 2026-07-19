"""OTA staging store + `python -m app.ota.stage` CLI (issue #121)."""

from __future__ import annotations

import json
from pathlib import Path

from app.ota import build_manifest, load_private_key, sign_manifest
from app.ota import stage as stage_cli
from app.state.ota_staging import OtaStagingStore

FIX = Path(__file__).parent / "fixtures" / "ota"
SEED = bytes.fromhex((FIX / "test_signing_key.hex").read_text().strip())


def _descriptor(kind: str = "esp32_client", fw: str = "1.4.0") -> dict[str, str]:
    manifest = build_manifest(
        key_id="test-ed25519-1",
        device_kind=kind,
        fw_version=fw,
        image_url="https://cdn.example.test/app.bin",
        image=b"firmware-bytes",
    )
    return sign_manifest(manifest, load_private_key(SEED))


def test_store_stage_get_clear(tmp_path: Path) -> None:
    store = OtaStagingStore(tmp_path / "ota_pending.json")
    assert store.get("d1") is None

    desc = _descriptor()
    store.stage("d1", desc, device_kind="esp32_client", fw_version="1.4.0", schema_version=1)
    entry = store.get("d1")
    assert entry is not None
    assert entry["descriptor"] == desc
    assert entry["device_kind"] == "esp32_client"
    assert entry["fw_version"] == "1.4.0"
    assert entry["schema_version"] == 1
    assert "d1" in store.all()

    assert store.clear("d1") is True
    assert store.get("d1") is None
    assert store.clear("d1") is False


def test_store_replaces_on_restage(tmp_path: Path) -> None:
    store = OtaStagingStore(tmp_path / "ota_pending.json")
    store.stage(
        "d1",
        _descriptor(fw="1.4.0"),
        device_kind="esp32_client",
        fw_version="1.4.0",
        schema_version=1,
    )
    store.stage(
        "d1",
        _descriptor(fw="1.5.0"),
        device_kind="esp32_client",
        fw_version="1.5.0",
        schema_version=1,
    )
    entry = store.get("d1")
    assert entry is not None and entry["fw_version"] == "1.5.0"


def test_stage_cli_stages_then_clears(tmp_path: Path) -> None:
    dpath = tmp_path / "descriptor.json"
    dpath.write_text(json.dumps(_descriptor()), encoding="utf-8")

    rc = stage_cli.main(
        ["--data-root", str(tmp_path), "--device-id", "hall_esp", "--descriptor", str(dpath)]
    )
    assert rc == 0
    store = OtaStagingStore(tmp_path / "core" / "ota_pending.json")
    entry = store.get("hall_esp")
    assert entry is not None
    assert entry["device_kind"] == "esp32_client"
    assert entry["fw_version"] == "1.4.0"
    assert entry["schema_version"] == 1

    rc = stage_cli.main(["--data-root", str(tmp_path), "--device-id", "hall_esp", "--clear"])
    assert rc == 0
    assert store.get("hall_esp") is None


def test_stage_cli_rejects_malformed_descriptor(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not": "a descriptor"}), encoding="utf-8")
    rc = stage_cli.main(
        ["--data-root", str(tmp_path), "--device-id", "x", "--descriptor", str(bad)]
    )
    assert rc == 2
