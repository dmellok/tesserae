"""One active producer per display (discussion #230).

A display plays back a single cached collection. Two enabled albums naming
the same display isn't a configuration with a winner, it's an ambiguity:
whichever the store returned first synced, and the other silently did
nothing. These pin the refusal, the explicit take-over, and the cases that
are deliberately not conflicts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.state.album_model import Album
from app.state.album_store import AlbumConflict, AlbumStore


def _album(album_id: str, *device_ids: str, enabled: bool = True) -> Album:
    return Album(
        id=album_id,
        name=album_id.replace("_", " ").title(),
        device_ids=list(device_ids),
        source_folder=album_id,
        enabled=enabled,
    )


def _store(tmp_path: Path) -> AlbumStore:
    return AlbumStore(tmp_path / "albums.json")


def test_binding_a_claimed_display_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_album("holidays", "kitchen"))

    with pytest.raises(AlbumConflict) as caught:
        store.upsert(_album("pets", "kitchen"))

    # The conflict names what has the display, so the caller can say so
    # rather than "something else is using this".
    assert caught.value.claims == {"kitchen": "holidays"}
    # Nothing was written: the refusal is not a partial save.
    assert [a.id for a in store.all()] == ["holidays"]
    assert [a.id for a in store.for_device("kitchen")] == ["holidays"]


def test_taking_over_a_display_has_to_be_asked_for(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_album("holidays", "kitchen", "hallway"))

    displaced = store.upsert(_album("pets", "kitchen"), replace=True)

    assert displaced == {"kitchen": "holidays"}
    assert [a.id for a in store.for_device("kitchen")] == ["pets"]
    # The displaced album survives with its other target rather than being
    # deleted out from under the operator.
    holidays = store.get("holidays")
    assert holidays is not None
    assert holidays.device_ids == ["hallway"]
    assert [a.id for a in store.for_device("hallway")] == ["holidays"]


def test_resaving_the_same_album_is_never_a_conflict(tmp_path: Path) -> None:
    """Editing an album's interval or membership must not trip over its own
    existing binding."""
    store = _store(tmp_path)
    store.upsert(_album("holidays", "kitchen"))
    assert store.conflicts_for(_album("holidays", "kitchen", "hallway")) == {}
    store.upsert(_album("holidays", "kitchen", "hallway"))
    assert store.get("holidays").device_ids == ["kitchen", "hallway"]


def test_a_disabled_album_neither_claims_nor_is_claimed(tmp_path: Path) -> None:
    """Disabled means not playing, so it holds no display and can be parked
    on one that's in use."""
    store = _store(tmp_path)
    store.upsert(_album("holidays", "kitchen", enabled=False))
    store.upsert(_album("pets", "kitchen"))  # no conflict: holidays is off

    assert [a.id for a in store.for_device("kitchen")] == ["pets"]
    assert store.conflicts_for(_album("archive", "kitchen", enabled=False)) == {}


def test_an_unbound_album_conflicts_with_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_album("holidays", "kitchen"))
    store.upsert(_album("drafts"))
    assert store.conflicts_for(_album("drafts")) == {}


def test_conflicts_are_reported_across_every_contested_display(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_album("holidays", "kitchen"))
    store.upsert(_album("pets", "hallway"))

    conflicts = store.conflicts_for(_album("mixed", "kitchen", "hallway", "studio"))
    assert conflicts == {"kitchen": "holidays", "hallway": "pets"}
    # studio is free, so it isn't in the report.
    assert "studio" not in conflicts


def test_a_legacy_double_binding_resolves_the_same_way_every_time(tmp_path: Path) -> None:
    """A file written before the store refused this can still hold two. The
    reader can't fix it, but it must not pick a different one per restart:
    a collection that changes on reboot is harder to diagnose than one that
    is consistently wrong."""
    path = tmp_path / "albums.json"
    path.write_text(
        '[{"id": "zebra", "name": "Z", "source_folder": "z", "device_ids": ["kitchen"]},'
        ' {"id": "alpha", "name": "A", "source_folder": "a", "device_ids": ["kitchen"]}]',
        encoding="utf-8",
    )
    store = AlbumStore(path)
    assert [a.id for a in store.for_device("kitchen")] == ["alpha", "zebra"]
    assert [a.id for a in AlbumStore(path).for_device("kitchen")] == ["alpha", "zebra"]
