"""Lineup projections for the Companion API (issue #205).

A Lineup is what the UI calls a ``Deck``: the one surface that owns what a
display shows over time. The internal model is deliberately broad (it
absorbed the old rotation and schedule stores in #167), so this module's
job is to hand the app a shape it can render without knowing any of that
history, and to be honest about the records it must not try to edit.

``native_editable`` is the load-bearing field. The app is allowed to author
the four intents the setup wizard offers; anything using conditions, a
fallback page, priority mode, smart sync, day or time windows, or a home
card is beyond that vocabulary. Deciding here rather than in the client
means a record can gain a field the app has never heard of and the app
still refuses to flatten it, which is the failure mode that matters: a
partial update that silently drops what it didn't understand.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from typing import Any

from app.state.deck_model import Deck

# The four authoring intents the setup wizard offers, which are also the
# only ones a native client can round-trip. Mapped from the internal
# advance/trigger pair rather than stored, so a record migrated from the
# legacy stores reports the intent a user would recognise.
INTENTS = ("daily", "interval", "cycle", "manual")


def lineup_intent(deck: Deck) -> str | None:
    """Which authoring intent this record reads as, or None when it doesn't
    match one (a timer deck with an unexpected trigger, say)."""
    if deck.advance == "manual":
        return "manual"
    if deck.advance_trigger == "daily":
        return "daily"
    if deck.advance_trigger == "interval":
        return "interval"
    if deck.advance_trigger == "cycle":
        return "cycle"
    return None


def _web_only_reason(deck: Deck) -> str | None:
    """Why the app must send this record to the web editor, or None when it
    can represent it completely.

    Ordered by how likely a user is to hit it, so the reason they see names
    the thing they actually configured."""
    if lineup_intent(deck) is None:
        return "uses a trigger the app doesn't know"
    if any(page.conditions for page in deck.pages):
        return "has conditions on one or more dashboards"
    if deck.advance_fallback_page_id:
        return "has a fallback dashboard"
    if deck.advance_mode != "scheduled":
        return "uses priority mode"
    if deck.advance_smart_sync:
        return "uses smart sync"
    if deck.advance_window_start or deck.advance_window_end:
        return "runs inside a time-of-day window"
    if sorted(deck.advance_days_of_week) != [0, 1, 2, 3, 4, 5, 6]:
        return "runs on selected days only"
    if deck.advance_end_at:
        return "stops at a set time each day"
    if deck.home_page_id or deck.home_timeout_minutes:
        return "has a home dashboard"
    if any(page.refresh_interval_minutes for page in deck.pages):
        return "has a per-dashboard refresh override"
    return None


def lineup_dict(
    deck: Deck,
    *,
    page_names: dict[str, str],
    current_pages: dict[str, str],
    web_url: str,
    next_advance_epoch: int | None = None,
) -> dict[str, Any]:
    """One Lineup as the app sees it.

    ``current_pages`` maps device id to the page that device is showing, so
    a Lineup bound to several displays can report each one rather than a
    single fictional "current" page.

    ``web_url`` is required rather than built here: this module has no app
    context, and the first version of it hardcoded a path that was never a
    route, so every "open in Tesserae" link the app advertised was a 404
    (#203). The caller resolves it with ``url_for`` so it can't drift from
    the routing table again.
    """
    reason = _web_only_reason(deck)
    return {
        "id": deck.id,
        "name": deck.name,
        "enabled": deck.enabled,
        "intent": lineup_intent(deck),
        "device_ids": list(deck.device_ids),
        "dashboards": [
            {
                "page_id": page.page_id,
                "name": page_names.get(page.page_id, page.page_id),
                "dwell_minutes": page.effective_dwell_minutes(deck.advance_interval_minutes),
                # A page can be listed in a Lineup and since deleted; the app
                # needs to render the row without pretending it's playable.
                "missing": page.page_id not in page_names,
                # The raw override next to the effective value above, so a
                # client can tell "inherits the Lineup's cadence" from "set to
                # the same number".
                "refresh_interval_minutes": page.refresh_interval_minutes,
                "links": [link.model_dump(exclude_none=True) for link in page.links],
                "conditions": [c.model_dump(exclude_none=True) for c in page.conditions],
            }
            for page in deck.pages
        ],
        "current": [
            {"device_id": device_id, "page_id": page_id}
            for device_id, page_id in sorted(current_pages.items())
        ],
        "next_advance_epoch": next_advance_epoch,
        # Advance shape, kept in the internal vocabulary rather than folded
        # into ``intent``: the four intents can't describe every stored
        # record, and an app that hides an advanced Lineup still has to
        # label it (#203).
        "advance": deck.advance,
        "trigger": deck.advance_trigger if deck.advance != "manual" else None,
        "interval_minutes": (deck.advance_interval_minutes if deck.advance != "manual" else None),
        "fires_at": deck.advance_fires_at,
        "anchor": deck.advance_anchor if deck.advance != "manual" else None,
        # Everything an advanced Lineup can carry, so a client that must not
        # edit one can still describe it completely (#203). Absent-means-
        # unknown is the contract the app relies on: reporting only the
        # fields it understands would let a partial update flatten the rest.
        "entry_page_id": deck.entry_page_id,
        "home_page_id": deck.home_page_id,
        "home_timeout_minutes": deck.home_timeout_minutes,
        "refresh_interval_minutes": deck.refresh_interval_minutes,
        "end_at": deck.advance_end_at,
        "days_of_week": list(deck.advance_days_of_week),
        "priority": deck.advance_priority,
        "smart_sync": deck.advance_smart_sync,
        "smart_sync_lead_seconds": deck.advance_smart_sync_lead_s,
        "mode": deck.advance_mode,
        "min_hold_minutes": deck.advance_min_hold_minutes,
        "window_start": deck.advance_window_start,
        "window_end": deck.advance_window_end,
        "fallback_page_id": deck.advance_fallback_page_id,
        "native_editable": reason is None,
        "requires_web_reason": reason,
        "web_url": web_url,
        # ``legacy_kind`` is deliberately absent: which store a record was
        # migrated from is ours, not something a client should branch on.
    }
