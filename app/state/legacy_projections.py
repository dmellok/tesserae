"""Rotation / Schedule store APIs as projections over the DeckStore (#167).

Phase 2b of the scheduling unification: ``decks.json`` is the one store for
timed content, and records migrated from the legacy ``rotations.json`` /
``schedules.json`` live there tagged ``legacy_kind``. These classes keep the
old four-method store contract (``all`` / ``get`` / ``upsert`` / ``delete``)
so every existing consumer, the Rotations and Schedules UIs, the MCP tools,
the ButtonService, the scheduler's rotation/schedule passes, and the device
timetable, keeps working unchanged against projected objects.

Writes round-trip through the pure mappings in :mod:`app.state.deck_migration`,
so a rotation edited in the Rotations UI is stored as a deck and projected
back identically. Ids are shared with real decks; a projection never touches
a deck it does not own (wrong or absent ``legacy_kind``).

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

from app.state.deck_migration import (
    deck_to_rotation,
    deck_to_schedule,
    rotation_to_deck,
    schedule_to_deck,
)
from app.state.deck_store import DeckStore
from app.state.rotation_model import Rotation
from app.state.schedule_model import Schedule


class RotationProjection:
    """RotationStore-compatible view over ``legacy_kind == "rotation"`` decks."""

    def __init__(self, deck_store: DeckStore) -> None:
        self._decks = deck_store

    def all(self) -> list[Rotation]:
        return [
            deck_to_rotation(deck) for deck in self._decks.all() if deck.legacy_kind == "rotation"
        ]

    def get(self, rotation_id: str) -> Rotation | None:
        deck = self._decks.get(rotation_id)
        if deck is None or deck.legacy_kind != "rotation":
            return None
        return deck_to_rotation(deck)

    def upsert(self, rotation: Rotation) -> None:
        existing = self._decks.get(rotation.id)
        if existing is not None and existing.legacy_kind != "rotation":
            raise ValueError(
                f"id {rotation.id!r} already belongs to a deck; pick another rotation id"
            )
        self._decks.upsert(rotation_to_deck(rotation))

    def delete(self, rotation_id: str) -> bool:
        deck = self._decks.get(rotation_id)
        if deck is None or deck.legacy_kind != "rotation":
            return False
        return self._decks.delete(rotation_id)


class ScheduleProjection:
    """ScheduleStore-compatible view over ``legacy_kind == "schedule"`` decks."""

    def __init__(self, deck_store: DeckStore) -> None:
        self._decks = deck_store

    def all(self) -> list[Schedule]:
        return [
            deck_to_schedule(deck) for deck in self._decks.all() if deck.legacy_kind == "schedule"
        ]

    def get(self, schedule_id: str) -> Schedule | None:
        deck = self._decks.get(schedule_id)
        if deck is None or deck.legacy_kind != "schedule":
            return None
        return deck_to_schedule(deck)

    def upsert(self, schedule: Schedule) -> None:
        existing = self._decks.get(schedule.id)
        if existing is not None and existing.legacy_kind != "schedule":
            raise ValueError(
                f"id {schedule.id!r} already belongs to a deck; pick another schedule id"
            )
        self._decks.upsert(schedule_to_deck(schedule))

    def delete(self, schedule_id: str) -> bool:
        deck = self._decks.get(schedule_id)
        if deck is None or deck.legacy_kind != "schedule":
            return False
        return self._decks.delete(schedule_id)
