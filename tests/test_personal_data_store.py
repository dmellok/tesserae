"""Publisher isolation and legacy migration for personal-data snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from app.state.personal_data_store import PersonalDataSnapshotStore


def _snapshot(title: str) -> dict:
    return {"data": {"lists": [{"id": title.lower(), "title": title, "items": []}]}}


def test_reads_v1_single_source_file_and_claims_it_on_first_publisher_put(
    tmp_path: Path,
) -> None:
    path = tmp_path / "personal.json"
    path.write_text(
        json.dumps(
            {
                "reminders": {
                    "snapshot": _snapshot("Legacy"),
                    "generated_epoch": 1.0,
                    "expires_epoch": 9_999_999_999.0,
                    "stored_at": 2.0,
                }
            }
        )
    )
    store = PersonalDataSnapshotStore(path)

    assert store.get("reminders")["snapshot"] == _snapshot("Legacy")
    assert store.publications("reminders")[0]["publisher_name"] == "Previously synced Companion"

    store.put(
        "reminders",
        snapshot=_snapshot("Alice"),
        generated_epoch=3.0,
        expires_epoch=9_999_999_999.0,
        publisher_id="companion_alice",
        publisher_name="Alice iPhone",
    )

    publications = store.publications("reminders")
    assert len(publications) == 1
    assert publications[0]["publisher_id"] == "companion_alice"
    assert publications[0]["snapshot"] == _snapshot("Alice")


def test_delete_is_scoped_to_one_publisher(tmp_path: Path) -> None:
    store = PersonalDataSnapshotStore(tmp_path / "personal.json")
    for publisher_id, name in (("companion_a", "Alice"), ("companion_b", "Bob")):
        store.put(
            "reminders",
            snapshot=_snapshot(name),
            generated_epoch=1.0,
            expires_epoch=9_999_999_999.0,
            publisher_id=publisher_id,
            publisher_name=name,
        )

    assert store.delete("reminders", publisher_id="companion_a") is True
    assert store.get("reminders", publisher_id="companion_a") is None
    assert store.get("reminders", publisher_id="companion_b") is not None
    assert [item["publisher_name"] for item in store.publications("reminders")] == ["Bob"]


def test_legacy_read_fallback_prefers_newest_generated_snapshot(tmp_path: Path) -> None:
    store = PersonalDataSnapshotStore(tmp_path / "personal.json")
    store.put(
        "reminders",
        snapshot=_snapshot("Semantically newest"),
        generated_epoch=200.0,
        expires_epoch=9_999_999_999.0,
        publisher_id="companion_first",
        publisher_name="First",
    )
    # Stored second, but generated earlier: delayed upload must not win the
    # compatibility fallback used by widgets that predate publications().
    store.put(
        "reminders",
        snapshot=_snapshot("Uploaded later"),
        generated_epoch=100.0,
        expires_epoch=9_999_999_999.0,
        publisher_id="companion_second",
        publisher_name="Second",
    )

    assert store.get("reminders")["snapshot"] == _snapshot("Semantically newest")
