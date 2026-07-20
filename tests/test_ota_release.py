"""Per-kind OTA release store, CLI, and version gate (issue #121)."""

from __future__ import annotations

import json
from pathlib import Path

from app.ota import build_manifest, load_private_key, sign_manifest
from app.ota import release as release_cli
from app.ota.release import is_newer
from app.state.ota_release import OtaReleaseStore

FIX = Path(__file__).parent / "fixtures" / "ota"
SEED = bytes.fromhex((FIX / "test_signing_key.hex").read_text().strip())


def _descriptor(kind: str = "esp32_client", fw: str = "1.5.0") -> dict[str, str]:
    manifest = build_manifest(
        key_id="test-ed25519-1",
        device_kind=kind,
        fw_version=fw,
        image_url="https://cdn.example.test/app.bin",
        image=b"firmware-bytes",
    )
    return sign_manifest(manifest, load_private_key(SEED))


def test_is_newer_semver() -> None:
    assert is_newer("1.5.0", "1.4.0")
    assert not is_newer("1.4.0", "1.5.0")
    assert not is_newer("1.5.0", "1.5.0")
    # Unparseable falls back to "offer when different, never when equal".
    assert is_newer("beta2", "beta1")
    assert not is_newer("beta1", "beta1")


def test_store_canary_gate(tmp_path: Path) -> None:
    store = OtaReleaseStore(tmp_path / "r.json")
    store.set_target("k1", _descriptor(), fw_version="1.5.0", canary_device_ids=["dev_a"])
    # Canary: only listed devices are offered.
    assert store.descriptor_for("k1", "dev_a") is not None
    assert store.descriptor_for("k1", "dev_b") is None
    # Promote: everyone is offered.
    assert store.promote("k1") is True
    assert store.descriptor_for("k1", "dev_b") is not None
    # Pause: no one.
    assert store.pause("k1") is True
    assert store.descriptor_for("k1", "dev_a") is None


def test_store_clear_and_missing(tmp_path: Path) -> None:
    store = OtaReleaseStore(tmp_path / "r.json")
    assert store.get("nope") is None
    assert store.promote("nope") is False
    store.set_target("k1", _descriptor(), fw_version="1.5.0")
    assert store.clear("k1") is True
    assert store.clear("k1") is False


def test_cli_set_promote_pause_clear(tmp_path: Path) -> None:
    dpath = tmp_path / "d.json"
    dpath.write_text(json.dumps(_descriptor(kind="seeed_reterminal_e1001", fw="1.5.0")))
    rc = release_cli.main(
        ["--data-root", str(tmp_path), "set", "--descriptor", str(dpath), "--canary", "hall_esp"]
    )
    assert rc == 0
    store = OtaReleaseStore(tmp_path / "core" / "ota_releases.json")
    entry = store.get("seeed_reterminal_e1001")
    assert entry is not None
    assert entry["fw_version"] == "1.5.0"
    assert entry["state"] == "canary"
    assert entry["canary_device_ids"] == ["hall_esp"]

    assert (
        release_cli.main(
            ["--data-root", str(tmp_path), "promote", "--kind", "seeed_reterminal_e1001"]
        )
        == 0
    )
    assert store.get("seeed_reterminal_e1001")["state"] == "promoted"
    assert (
        release_cli.main(
            ["--data-root", str(tmp_path), "pause", "--kind", "seeed_reterminal_e1001"]
        )
        == 0
    )
    assert store.get("seeed_reterminal_e1001")["state"] == "paused"
    assert (
        release_cli.main(
            ["--data-root", str(tmp_path), "clear", "--kind", "seeed_reterminal_e1001"]
        )
        == 0
    )
    assert store.get("seeed_reterminal_e1001") is None


def test_cli_set_rejects_untrusted_key(tmp_path: Path) -> None:
    manifest = build_manifest(
        key_id="nope-1",
        device_kind="esp32_client",
        fw_version="1.5.0",
        image_url="https://x/app.bin",
        image=b"x",
    )
    dpath = tmp_path / "d.json"
    dpath.write_text(json.dumps(sign_manifest(manifest, load_private_key(SEED))))
    rc = release_cli.main(["--data-root", str(tmp_path), "set", "--descriptor", str(dpath)])
    assert rc == 2
