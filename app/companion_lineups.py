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

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.state.deck_model import Deck, DeckPage

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


def resolved_device_ids(deck: Deck, page_devices: Mapping[str, Sequence[str]]) -> list[str]:
    """Which displays this Lineup actually paints.

    Usually the authored binding. Schedule-style records (daily, interval)
    are the exception: they came from a store where a schedule carried a
    page and no display at all, so ``device_ids`` is empty and the engine
    fires them at whatever displays their member dashboards are bound to.

    Reading the authored list alone therefore makes a daily Lineup look
    unplayable when it isn't. The client is told the resolved set rather
    than deriving it from the dashboard bindings itself, and the action
    endpoint paints the same set, so what the app offers and what the
    server will do can't disagree (#203).
    """
    if deck.device_ids:
        return list(dict.fromkeys(deck.device_ids))
    out: list[str] = []
    for page in deck.pages:
        for device_id in page_devices.get(page.page_id, ()):
            if device_id not in out:
                out.append(device_id)
    return out


def lineup_etag(deck: Deck) -> str:
    """A version tag for one Lineup, for optimistic concurrency.

    Hashes the stored record, so any edit from any surface changes it. The
    app sends it back as ``If-Match`` and a stale one is refused rather
    than applied: two people editing the same Lineup is ordinary in a
    household, and the web editor is the other one (#206)."""
    payload = json.dumps(deck.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# Fields a native client may set. Anything outside this list is either
# derived (``intent``), internal (``legacy_kind``), or beyond the four
# authoring intents, and is refused rather than silently dropped so a
# client can't believe it wrote something it didn't.
PATCHABLE = frozenset(
    {
        "name",
        "enabled",
        "device_ids",
        "page_ids",
        "dwell_minutes",
        "interval_minutes",
        "fires_at",
        "anchor",
    }
)


def apply_patch(deck: Deck, body: dict[str, Any]) -> Deck:
    """A partial edit laid over the stored record. Raises ValueError.

    Every field the body doesn't mention keeps its stored value. That's the
    whole contract: a client that understands six fields must not flatten
    the twenty a Lineup can hold, and the only way to guarantee that is to
    start from what's stored rather than rebuild from the request (#203).
    """
    unknown = sorted(set(body) - PATCHABLE)
    if unknown:
        raise ValueError(f"cannot edit {', '.join(unknown)} from the app")

    changes: dict[str, Any] = {}
    if "name" in body:
        name = str(body["name"] or "").strip()
        if not name:
            raise ValueError("name cannot be empty")
        changes["name"] = name
    if "enabled" in body:
        changes["enabled"] = bool(body["enabled"])
    if "device_ids" in body:
        changes["device_ids"] = [str(d) for d in body["device_ids"] or []]
    if "interval_minutes" in body:
        changes["advance_interval_minutes"] = max(1, int(body["interval_minutes"]))
    if "fires_at" in body:
        changes["advance_fires_at"] = str(body["fires_at"] or "") or None
    if "anchor" in body:
        changes["advance_anchor"] = str(body["anchor"] or "00:00") or "00:00"

    # Reordering or re-picking the dashboards rebuilds the page list, but
    # each page keeps whatever else it carried (links, conditions, its own
    # refresh override) when it survives the edit.
    if "page_ids" in body:
        page_ids = [str(p) for p in body["page_ids"] or [] if p]
        if not page_ids:
            raise ValueError("a lineup needs at least one dashboard")
        dwell = {str(k): int(v) for k, v in (body.get("dwell_minutes") or {}).items()}
        existing = {page.page_id: page for page in deck.pages}
        rebuilt = []
        for page_id in page_ids:
            page = existing.get(page_id)
            if page is None:
                page = DeckPage(page_id=page_id)
            if page_id in dwell:
                page = page.model_copy(update={"dwell_minutes": dwell[page_id] or None})
            rebuilt.append(page)
        changes["pages"] = rebuilt
    elif "dwell_minutes" in body:
        dwell = {str(k): int(v) for k, v in (body.get("dwell_minutes") or {}).items()}
        changes["pages"] = [
            page.model_copy(update={"dwell_minutes": dwell[page.page_id] or None})
            if page.page_id in dwell
            else page
            for page in deck.pages
        ]

    return deck.model_copy(update=changes)


def lineup_dict(
    deck: Deck,
    *,
    page_names: dict[str, str],
    page_devices: Mapping[str, Sequence[str]],
    current_pages: dict[str, str],
    web_url: str,
    next_advance_epoch: int | None = None,
) -> dict[str, Any]:
    """One Lineup as the app sees it.

    ``current_pages`` maps device id to the page that device is showing, so
    a Lineup bound to several displays can report each one rather than a
    single fictional "current" page.

    ``page_devices`` maps dashboard id to the displays it is bound to, which
    is what ``resolved_device_ids`` needs for a Lineup that carries no
    binding of its own.

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
        # The authored binding above, the displays it actually paints here.
        # They differ only for schedule-style records, which bind nothing and
        # fire at their dashboards' own displays; the app targets and labels
        # from this one rather than working it out (#203).
        "resolved_device_ids": resolved_device_ids(deck, page_devices),
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
