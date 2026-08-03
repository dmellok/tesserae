"""Rotation / Schedule store APIs as views over plain timer decks (#167).

Rotations and schedules are decommissioned as stored concepts: a rotation IS
a deck with ``advance == "timer"`` on the ``cycle`` trigger and no
navigation, and a schedule IS a one-page timer deck on the ``interval`` or
``daily`` trigger. These classes keep the old four-method store contract
(``all`` / ``get`` / ``upsert`` / ``delete``) alive for the cycle and timed
forms, the deprecated MCP tools, the ButtonService, the device timetable,
and the scheduler's view-engines, all reading and writing plain decks.

Ownership is by SHAPE, not provenance: ``legacy_kind`` survives only as a
migration marker (it keeps re-migrating a restored backup idempotent) and no
surface distinguishes migrated records from deck-authored ones. ``both``
mode decks are deliberately outside the rotation view: they carry link
graphs a Rotation cannot represent, so they are edited as decks.

Upserts MERGE onto an existing deck rather than rebuilding it, so fields the
legacy shapes cannot express (background warm cadence, the provenance
marker, an explicit binding the legacy form doesn't collect) survive an edit
through the old forms.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

from typing import Any

from app.state.deck_migration import (
    deck_to_rotation,
    deck_to_schedule,
    rotation_to_deck,
    schedule_to_deck,
)
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.rotation_model import Rotation
from app.state.schedule_model import Schedule


def is_cycle_view_deck(deck: Deck) -> bool:
    """Whether a deck belongs to the rotation-shaped view: a pure timer
    cycle (no navigation semantics, so ``both`` mode is excluded)."""
    return deck.advance == "timer" and deck.advance_trigger == "cycle"


def is_timed_view_deck(deck: Deck) -> bool:
    """Whether a deck belongs to the schedule-shaped view: a timer deck on
    the ``interval`` or ``daily`` trigger."""
    return deck.advance == "timer" and deck.advance_trigger != "cycle"


class RotationProjection:
    """RotationStore-compatible view over pure cycle timer decks."""

    def __init__(self, deck_store: DeckStore) -> None:
        self._decks = deck_store

    def all(self) -> list[Rotation]:
        return [deck_to_rotation(d) for d in self._decks.all() if is_cycle_view_deck(d)]

    def get(self, rotation_id: str) -> Rotation | None:
        deck = self._decks.get(rotation_id)
        if deck is None or not is_cycle_view_deck(deck):
            return None
        return deck_to_rotation(deck)

    def upsert(self, rotation: Rotation) -> None:
        existing = self._decks.get(rotation.id)
        if existing is not None and not is_cycle_view_deck(existing):
            raise ValueError(f"id {rotation.id!r} already belongs to a deck; pick another id")
        if existing is None:
            self._decks.upsert(rotation_to_deck(rotation, legacy=False))
            return
        update: dict[str, Any] = {
            "name": rotation.name,
            "enabled": rotation.enabled,
            "pages": [
                DeckPage(
                    page_id=step.page_id,
                    dwell_minutes=step.dwell_minutes,
                    conditions=list(step.conditions),
                )
                for step in rotation.steps
            ],
            "advance_anchor": rotation.anchor,
            "advance_end_at": rotation.end_at,
            "advance_days_of_week": list(rotation.days_of_week),
            "advance_priority": rotation.priority,
            "advance_smart_sync": rotation.smart_sync,
            "advance_smart_sync_lead_s": rotation.smart_sync_lead_s,
            "advance_mode": rotation.mode,
            "advance_min_hold_minutes": rotation.min_hold_minutes,
        }
        # The cycle form always submits empty bindings (delivery falls
        # through to the step pages); don't let that unbind a deck the user
        # explicitly bound elsewhere.
        if rotation.device_ids:
            update["device_ids"] = list(rotation.device_ids)
        self._decks.upsert(existing.model_copy(update=update))

    def delete(self, rotation_id: str) -> bool:
        deck = self._decks.get(rotation_id)
        if deck is None or not is_cycle_view_deck(deck):
            return False
        return self._decks.delete(rotation_id)


class ScheduleProjection:
    """ScheduleStore-compatible view over interval / daily trigger decks."""

    def __init__(self, deck_store: DeckStore) -> None:
        self._decks = deck_store

    def all(self) -> list[Schedule]:
        return [deck_to_schedule(d) for d in self._decks.all() if is_timed_view_deck(d)]

    def get(self, schedule_id: str) -> Schedule | None:
        deck = self._decks.get(schedule_id)
        if deck is None or not is_timed_view_deck(deck):
            return None
        return deck_to_schedule(deck)

    def upsert(self, schedule: Schedule) -> None:
        existing = self._decks.get(schedule.id)
        if existing is not None and not is_timed_view_deck(existing):
            raise ValueError(f"id {schedule.id!r} already belongs to a deck; pick another id")
        if existing is None:
            self._decks.upsert(schedule_to_deck(schedule, legacy=False))
            return
        update: dict[str, Any] = {
            "name": schedule.name,
            "enabled": schedule.enabled,
            "pages": [DeckPage(page_id=schedule.page_id, conditions=list(schedule.conditions))],
            "advance_trigger": "interval" if schedule.type == "interval" else "daily",
            "advance_interval_minutes": schedule.interval_minutes or 30,
            "advance_fires_at": (
                schedule.fires_at.strftime("%H:%M") if schedule.fires_at is not None else None
            ),
            "advance_window_start": schedule.time_of_day_start,
            "advance_window_end": schedule.time_of_day_end,
            "advance_days_of_week": list(schedule.days_of_week),
            "advance_priority": schedule.priority,
            "advance_smart_sync": schedule.smart_sync,
            "advance_smart_sync_lead_s": schedule.smart_sync_lead_s,
            "advance_fallback_page_id": schedule.fallback_page_id,
        }
        self._decks.upsert(existing.model_copy(update=update))

    def delete(self, schedule_id: str) -> bool:
        deck = self._decks.get(schedule_id)
        if deck is None or not is_timed_view_deck(deck):
            return False
        return self._decks.delete(schedule_id)
