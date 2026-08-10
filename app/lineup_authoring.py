"""One write path for creating a Lineup (issue #204).

The Lineups surface has been unified since #167, and the scheduler already
runs decks natively: ``Scheduler._cycle_records`` and ``_timed_records``
pick up any deck the legacy stores don't already represent and adapt it on
the fly. Creation was the piece still fanned out, with the setup wizard
posting to three different routes backed by three different stores, so a
record's shape depended on which button made it.

This module is the single place an authoring intent becomes a ``Deck``.
The four intents are the wizard's own four, and the same vocabulary the
Companion API exposes, so a Lineup created from the app and one created
from the web are the same record made the same way.

mypy --strict does not apply here (it isn't in the strict list), but the
mapping is small enough to keep exhaustive.
"""

from __future__ import annotations

from typing import Any

from app.state.deck_model import Deck, DeckPage

# daily / interval keep one dashboard fresh or show it at a time; cycle and
# manual move through several, on a timer or by hand.
INTENTS = ("daily", "interval", "cycle", "manual")

_SINGLE_PAGE_INTENTS = ("daily", "interval")


def build_lineup(
    *,
    intent: str,
    lineup_id: str,
    name: str,
    page_ids: list[str],
    device_ids: list[str] | None = None,
    dwell_minutes: dict[str, int] | None = None,
    interval_minutes: int = 30,
    fires_at: str | None = None,
    anchor: str = "00:00",
) -> Deck:
    """Turn an authoring intent into a Deck. Raises ValueError on bad input.

    Everything the four intents don't mention is left at the model's
    defaults, which is what makes a record built here round-trip through
    the app's native editor: no conditions, no fallback, no windows, no
    priority mode. A user who wants those adds them in the web editor
    afterwards, and the record then reports itself as web-only.
    """
    if intent not in INTENTS:
        raise ValueError(f"unknown intent {intent!r}")
    pages = [p for p in page_ids if p]
    if not pages:
        raise ValueError("a lineup needs at least one dashboard")
    if intent in _SINGLE_PAGE_INTENTS and len(pages) != 1:
        raise ValueError(f"the {intent} intent takes exactly one dashboard")
    if intent == "daily" and not fires_at:
        raise ValueError("the daily intent needs a time of day")

    dwell = dwell_minutes or {}
    deck_pages = [
        DeckPage(page_id=page_id, dwell_minutes=dwell.get(page_id) or None) for page_id in pages
    ]
    fields: dict[str, Any] = {
        "id": lineup_id,
        "name": name,
        "device_ids": list(device_ids or []),
        "pages": deck_pages,
    }
    if intent == "manual":
        # Nothing timed: the panel moves on a tap, button, or swipe.
        fields["advance"] = "manual"
        return Deck(**fields)

    fields["advance"] = "timer"
    fields["advance_anchor"] = anchor
    if intent == "daily":
        fields["advance_trigger"] = "daily"
        fields["advance_fires_at"] = fires_at
    elif intent == "interval":
        fields["advance_trigger"] = "interval"
        fields["advance_interval_minutes"] = max(1, int(interval_minutes))
    else:
        fields["advance_trigger"] = "cycle"
        fields["advance_interval_minutes"] = max(1, int(interval_minutes))
    return Deck(**fields)
