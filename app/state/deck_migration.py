"""Pure mappings from legacy Rotation / Schedule records to Decks (#167).

Phase 2 of the scheduling unification stores everything as a Deck. These
functions are the lossless field maps; they never touch a store. The startup
migration (Phase 2b) applies them, handles id collisions, and renames the
source files so deleted records can't resurrect on the next boot (the
canvas-phantom lesson, fixed in v0.242.1).

Mapped records carry ``legacy_kind`` so the compatibility projections know
which UI / API surface owns them.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore

logger = logging.getLogger(__name__)


def rotation_to_deck(rotation: Rotation) -> Deck:
    """A rotation as a timer deck on the ``cycle`` trigger.

    Field-for-field per the #167 survey table: the nine ``advance_*`` fields
    are verbatim renames, steps become pages with explicit dwell, and the
    round trip through ``scheduler._deck_to_rotation`` reproduces the input.
    Total: repeated step pages are legal on a linkless deck, so every
    rotation maps.
    """
    return Deck(
        id=rotation.id,
        name=rotation.name,
        enabled=rotation.enabled,
        device_ids=list(rotation.device_ids),
        pages=[
            DeckPage(
                page_id=step.page_id,
                dwell_minutes=step.dwell_minutes,
                conditions=list(step.conditions),
            )
            for step in rotation.steps
        ],
        advance="timer",
        advance_trigger="cycle",
        advance_anchor=rotation.anchor,
        advance_end_at=rotation.end_at,
        advance_days_of_week=list(rotation.days_of_week),
        advance_priority=rotation.priority,
        advance_smart_sync=rotation.smart_sync,
        advance_smart_sync_lead_s=rotation.smart_sync_lead_s,
        advance_mode=rotation.mode,
        advance_min_hold_minutes=rotation.min_hold_minutes,
        # Rotations never background-warmed; don't start on migration.
        refresh_interval_minutes=0,
        legacy_kind="rotation",
    )


def schedule_to_deck(schedule: Schedule) -> Deck:
    """A schedule as a one-page timer deck on the ``interval`` or ``daily``
    trigger.

    Cadence semantics carry over exactly: ``interval`` keeps the drifting
    last-fired cooldown and the wrap-around time-of-day window; ``daily``
    keeps the once-per-local-day fire with the backfill guard. Conditions
    move to the single page; ``fallback_page_id`` becomes the whole-deck
    fallback. ``device_ids`` stays empty, which the engine fires
    schedule-style (one push to the page's own bound devices).
    ``advance_min_hold_minutes`` is 0 because schedules never had a min-hold
    gate; a nonzero default would delay the fallback flip by that long.
    """
    return Deck(
        id=schedule.id,
        name=schedule.name,
        enabled=schedule.enabled,
        device_ids=[],
        pages=[DeckPage(page_id=schedule.page_id, conditions=list(schedule.conditions))],
        advance="timer",
        advance_trigger="interval" if schedule.type == "interval" else "daily",
        advance_interval_minutes=schedule.interval_minutes or 30,
        advance_fires_at=(
            schedule.fires_at.strftime("%H:%M") if schedule.fires_at is not None else None
        ),
        advance_window_start=schedule.time_of_day_start,
        advance_window_end=schedule.time_of_day_end,
        advance_days_of_week=list(schedule.days_of_week),
        advance_priority=schedule.priority,
        advance_smart_sync=schedule.smart_sync,
        advance_smart_sync_lead_s=schedule.smart_sync_lead_s,
        advance_fallback_page_id=schedule.fallback_page_id,
        advance_min_hold_minutes=0,
        # Schedules never background-warmed; don't start on migration.
        refresh_interval_minutes=0,
        legacy_kind="schedule",
    )


def deck_to_rotation(deck: Deck) -> Rotation:
    """The inverse of :func:`rotation_to_deck`: project a ``legacy_kind ==
    "rotation"`` deck back into the Rotation shape the rotations UI, MCP
    tools, ButtonService, and scheduler pass consume during the
    compatibility window. Same field map as the scheduler's engine adapter,
    so a projected rotation behaves identically to the record it came from."""
    return Rotation(
        id=deck.id,
        name=deck.name,
        enabled=deck.enabled,
        device_ids=list(deck.device_ids),
        steps=[
            RotationStep(
                page_id=page.page_id,
                dwell_minutes=page.effective_dwell_minutes(deck.advance_interval_minutes),
                conditions=list(page.conditions),
            )
            for page in deck.pages
        ],
        anchor=deck.advance_anchor,
        end_at=deck.advance_end_at,
        days_of_week=list(deck.advance_days_of_week),
        priority=deck.advance_priority,
        smart_sync=deck.advance_smart_sync,
        smart_sync_lead_s=deck.advance_smart_sync_lead_s,
        mode=deck.advance_mode,
        min_hold_minutes=deck.advance_min_hold_minutes,
    )


def deck_to_schedule(deck: Deck) -> Schedule:
    """The inverse of :func:`schedule_to_deck` for ``legacy_kind ==
    "schedule"`` decks. ``fires_at`` carries only a wall-clock time (the
    schedule model ignores the date part), so it is rebuilt on a fixed
    dummy date."""
    fires_at: datetime | None = None
    if deck.advance_fires_at is not None:
        hour, minute = (int(part) for part in deck.advance_fires_at.split(":"))
        fires_at = datetime(2000, 1, 1, hour, minute)
    return Schedule(
        id=deck.id,
        name=deck.name,
        enabled=deck.enabled,
        page_id=deck.pages[0].page_id,
        type="interval" if deck.advance_trigger == "interval" else "daily",
        interval_minutes=(
            deck.advance_interval_minutes if deck.advance_trigger == "interval" else None
        ),
        fires_at=fires_at,
        time_of_day_start=deck.advance_window_start,
        time_of_day_end=deck.advance_window_end,
        days_of_week=list(deck.advance_days_of_week),
        priority=deck.advance_priority,
        smart_sync=deck.advance_smart_sync,
        smart_sync_lead_s=deck.advance_smart_sync_lead_s,
        conditions=list(deck.pages[0].conditions),
        fallback_page_id=deck.advance_fallback_page_id,
    )


def migrate_legacy_stores(
    *,
    deck_store: DeckStore,
    schedules_path: Path,
    rotations_path: Path,
) -> dict[str, list[str]]:
    """One-time startup migration of ``rotations.json`` / ``schedules.json``
    into the deck store.

    Each source file is renamed to ``<name>.json.migrated`` after its records
    are copied in. The rename is the resurrection guard (a migration that
    re-runs every boot against a live source re-creates deleted records, the
    canvas-phantom bug fixed in v0.242.1) and the renamed file doubles as the
    rollback artifact. A missing source file is a no-op, so steady-state
    boots do nothing.

    Id collisions with an existing non-legacy deck get a ``-rotation`` /
    ``-schedule`` suffix (reported in the result); a collision with an
    already-migrated record of the same kind is overwritten in place, so
    re-migrating a restored backup stays idempotent instead of stacking
    suffixed duplicates. Records the legacy stores cannot validate were
    already invisible to the app and simply remain in the renamed file."""
    report: dict[str, list[str]] = {"migrated": [], "renamed_ids": []}
    sources: list[tuple[Path, str]] = [
        (rotations_path, "rotation"),
        (schedules_path, "schedule"),
    ]
    for path, kind in sources:
        if not path.exists():
            continue
        records: list[Rotation] | list[Schedule] = (
            RotationStore(path).all() if kind == "rotation" else ScheduleStore(path).all()
        )
        for record in records:
            deck = (
                rotation_to_deck(record)
                if isinstance(record, Rotation)
                else schedule_to_deck(record)
            )
            existing = deck_store.get(deck.id)
            if existing is not None and existing.legacy_kind != kind:
                new_id = f"{deck.id}-{kind}"
                n = 2
                while deck_store.get(new_id) is not None:
                    new_id = f"{deck.id}-{kind}{n}"
                    n += 1
                logger.warning(
                    "legacy migration: %s id %r collides with an existing deck; stored as %r",
                    kind,
                    deck.id,
                    new_id,
                )
                report["renamed_ids"].append(f"{deck.id} -> {new_id}")
                deck = deck.model_copy(update={"id": new_id})
            deck_store.upsert(deck)
            report["migrated"].append(f"{kind}:{deck.id}")
        path.rename(path.with_suffix(".json.migrated"))
        logger.info(
            "legacy migration: %d %s record(s) moved into the deck store; %s kept as rollback",
            len(records),
            kind,
            path.with_suffix(".json.migrated").name,
        )
    return report
