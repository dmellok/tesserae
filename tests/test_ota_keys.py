"""Trusted OTA key registry (``app/ota/keys.py``)."""

from __future__ import annotations

from pathlib import Path

from app.ota import verify
from app.ota.keys import default_keys_dir, load_trusted_keys


def test_published_test_key_is_trusted() -> None:
    keys = load_trusted_keys()
    assert "test-ed25519-1" in keys


def test_default_keys_dir_points_at_repo_ota_keys() -> None:
    d = default_keys_dir()
    assert d.name == "keys" and d.parent.name == "ota"
    assert (d / "test-ed25519-1.pub").exists()


def test_fixture_descriptor_verifies_against_trusted_key() -> None:
    import json

    fixture = Path(__file__).parent / "fixtures" / "ota" / "valid.json"
    descriptor = json.loads(fixture.read_text())
    key = load_trusted_keys()["test-ed25519-1"]
    manifest = verify(descriptor, key)
    assert manifest["key_id"] == "test-ed25519-1"


def test_malformed_key_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "good.pub").write_text(
        "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8\n"
    )
    (tmp_path / "bad.pub").write_text("not-hex\n")
    keys = load_trusted_keys(tmp_path)
    assert "good" in keys and "bad" not in keys
