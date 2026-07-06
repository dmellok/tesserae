"""Install identifier: v4 UUID generated on first startup, persisted at
``data/core/install_id.json``, regenerable via Settings, exposed to widgets
that opt in through their plugin manifest."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app import install_id as install_id_module


def test_load_or_create_generates_new_uuid_on_first_startup(tmp_path: Path) -> None:
    """A fresh install (no file) mints a v4 UUID and writes it to disk with
    metadata (``created_at`` + descriptive ``note``)."""
    install_id = install_id_module.load_or_create(tmp_path)
    assert uuid.UUID(install_id).version == 4
    path = install_id_module.install_id_path(tmp_path)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["id"] == install_id
    assert payload["created_at"].endswith("Z")
    assert "Regenerable" in payload["note"]


def test_load_or_create_returns_same_id_across_restarts(tmp_path: Path) -> None:
    """Subsequent calls read the persisted value rather than minting a new
    one, so the install identity is stable across process restarts."""
    first = install_id_module.load_or_create(tmp_path)
    second = install_id_module.load_or_create(tmp_path)
    third = install_id_module.load_or_create(tmp_path)
    assert first == second == third


def test_regenerate_rotates_the_id(tmp_path: Path) -> None:
    """Explicit regeneration produces a fresh UUID and overwrites the on-disk
    file, so a user pressing the Settings regenerate button drops the
    previous per-install state on the widget side (a pet's history, a
    traveler's home waypoint) by making the install look brand new."""
    original = install_id_module.load_or_create(tmp_path)
    rotated = install_id_module.regenerate(tmp_path)
    assert rotated != original
    on_disk = install_id_module.load_or_create(tmp_path)
    assert on_disk == rotated


def test_malformed_file_is_replaced_with_fresh_uuid(tmp_path: Path) -> None:
    """A corrupt or hand-edited file (non-JSON, bad payload, invalid UUID)
    is quietly replaced with a fresh UUID rather than crashing the boot
    sequence. Keeps ``data/core/install_id.json`` self-healing."""
    path = install_id_module.install_id_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for corruption in ("not json at all", '{"id": "not-a-uuid"}', "{}"):
        path.write_text(corruption, encoding="utf-8")
        new_id = install_id_module.load_or_create(tmp_path)
        assert uuid.UUID(new_id).version == 4


def test_read_metadata_returns_none_when_missing(tmp_path: Path) -> None:
    """The Settings UI reads metadata to show the current id + minted-at
    timestamp. Missing file returns ``None`` so the template can hide the
    section rather than error."""
    assert install_id_module.read_metadata(tmp_path) is None


def test_read_metadata_returns_id_created_at_and_note(tmp_path: Path) -> None:
    install_id_module.load_or_create(tmp_path)
    meta = install_id_module.read_metadata(tmp_path)
    assert meta is not None
    assert uuid.UUID(meta["id"]).version == 4
    assert meta["created_at"].endswith("Z")
    assert meta["note"]


def test_scoped_id_is_deterministic_per_install_and_scope(tmp_path: Path) -> None:
    """Different plugin ids produce different scoped ids for the same
    install, so widgets can't be correlated by an external service. Same
    plugin id + same install returns the same value across calls, so a
    widget sees stable identity across restarts."""
    install_id = install_id_module.load_or_create(tmp_path)
    scoped_a = install_id_module.scoped_id(install_id, "weather_now")
    scoped_b = install_id_module.scoped_id(install_id, "calendar_schedule")
    scoped_a_again = install_id_module.scoped_id(install_id, "weather_now")
    assert scoped_a != scoped_b
    assert scoped_a == scoped_a_again
    # Length matches the documented 128-bit hex derivation.
    assert len(scoped_a) == 32
    assert all(c in "0123456789abcdef" for c in scoped_a)


def test_scoped_id_changes_when_install_id_rotates(tmp_path: Path) -> None:
    """Regenerating the install id also rotates every widget's scoped id,
    so a user pressing "Regenerate" resets identity as expected."""
    install_id = install_id_module.load_or_create(tmp_path)
    scoped_before = install_id_module.scoped_id(install_id, "tesserae_status")
    rotated = install_id_module.regenerate(tmp_path)
    scoped_after = install_id_module.scoped_id(rotated, "tesserae_status")
    assert scoped_after != scoped_before


def test_scoped_id_ignores_scope_ordering(tmp_path: Path) -> None:
    """Order-independent: swapping which plugin asks for a scoped id doesn't
    leak the ordering of previous calls into the derivation."""
    install_id = install_id_module.load_or_create(tmp_path)
    a1 = install_id_module.scoped_id(install_id, "widget_a")
    b1 = install_id_module.scoped_id(install_id, "widget_b")
    b2 = install_id_module.scoped_id(install_id, "widget_b")
    a2 = install_id_module.scoped_id(install_id, "widget_a")
    assert a1 == a2
    assert b1 == b2


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "12345", None])
def test_scoped_id_still_derives_from_any_input(tmp_path: Path, bad: str | None) -> None:
    """``scoped_id`` doesn't validate the install id; it hashes whatever's
    handed in. Guarantees the function stays cheap and deterministic even
    when a caller hasn't run load_or_create first."""
    if bad is None:
        return  # scoped_id typed for str; None wouldn't be passed
    scoped = install_id_module.scoped_id(bad, "some_plugin")
    assert len(scoped) == 32
