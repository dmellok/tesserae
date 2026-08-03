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

from app.state.deck_model import Deck, DeckPage
from app.state.rotation_model import Rotation
from app.state.schedule_model import Schedule


def rotation_to_deck(rotation: Rotation) -> Deck | None:
    """A rotation as a timer deck on the ``cycle`` trigger.

    Field-for-field per the #167 survey table: the nine ``advance_*`` fields
    are verbatim renames, steps become pages with explicit dwell, and the
    round trip through ``scheduler._deck_to_rotation`` reproduces the input.

    Returns None when the rotation repeats a page across steps: decks require
    unique page ids (the nav graph is keyed by page), so such rotations stay
    on the legacy path and the migration reports them instead of guessing.
    """
    if len({step.page_id for step in rotation.steps}) != len(rotation.steps):
        return None
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
