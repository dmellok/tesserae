"""Take a deleted dashboard out of the lineups that use it.

Deleting a dashboard a lineup lists would leave that lineup pointing at
nothing. When the user approves the delete, :func:`remove_page_from_lineups`
runs first and rewrites every lineup that references the page: its step, any
link that navigates to it, the entry / home / advance-fallback pointers, and
the saved position of any display sitting on it. A lineup whose only
dashboard was this one cannot stay (a lineup needs at least one), so it is
deleted and reported.

Tolerant of already-dangling data and safe to run twice: nothing to remove
means nothing written.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.state.deck_model import Deck

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CascadeResult:
    """What the cascade did, by lineup name, for the flash message."""

    edited: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(self.edited or self.removed)


def references(deck: Deck, page_id: str) -> bool:
    """Whether any part of ``deck`` points at ``page_id``."""
    return (
        any(
            p.page_id == page_id or any(link.target_page_id == page_id for link in p.links)
            for p in deck.pages
        )
        or deck.entry_page_id == page_id
        or deck.home_page_id == page_id
        or deck.advance_fallback_page_id == page_id
    )


def emptied_by(deck_store: Any | None, page_ids: Iterable[str]) -> list[str]:
    """Names of the lineups that would be left with no dashboard if all of
    ``page_ids`` were deleted, i.e. the ones the cascade would delete."""
    if deck_store is None:
        return []
    gone = set(page_ids)
    try:
        decks = deck_store.all()
    except Exception:
        logger.exception("lineup cascade: listing lineups failed")
        return []
    names: list[str] = []
    for deck in decks:
        if deck.page_ids and set(deck.page_ids) <= gone and deck.name not in names:
            names.append(deck.name)
    return names


def deck_without_page(deck: Deck, page_id: str) -> Deck | None:
    """``deck`` with every reference to ``page_id`` dropped, or None when no
    dashboard is left. The result is re-validated, so a deck the model would
    reject never reaches the store."""
    pages = [p for p in deck.pages if p.page_id != page_id]
    if not pages:
        return None
    data: dict[str, Any] = deck.model_dump(mode="json")
    data["pages"] = [
        {
            **p.model_dump(mode="json"),
            "links": [
                link.model_dump(mode="json") for link in p.links if link.target_page_id != page_id
            ],
        }
        for p in pages
    ]
    # An unset pointer resolves to the first remaining page.
    for key in ("entry_page_id", "home_page_id", "advance_fallback_page_id"):
        if data.get(key) == page_id:
            data[key] = None
    return Deck.model_validate(data)


def remove_page_from_lineups(
    deck_store: Any | None, nav_store: Any | None, page_id: str
) -> CascadeResult:
    """Rewrite every lineup that references ``page_id`` so nothing dangles.
    ``nav_store`` (optional) forgets displays parked on the page, so they
    restart at their lineup's entry page."""
    edited: list[str] = []
    removed: list[str] = []
    if deck_store is not None:
        try:
            decks = deck_store.all()
        except Exception:
            logger.exception("lineup cascade: listing lineups failed")
            decks = []
        for deck in decks:
            if not references(deck, page_id):
                continue
            try:
                rewritten = deck_without_page(deck, page_id)
            except Exception:
                logger.exception("lineup cascade: could not rewrite lineup %s", deck.id)
                continue
            if rewritten is None:
                deck_store.delete(deck.id)
                removed.append(deck.name)
            else:
                deck_store.upsert(rewritten)
                edited.append(deck.name)
    forget = getattr(nav_store, "forget_page", None)
    if callable(forget):
        forget(page_id)
    return CascadeResult(edited=edited, removed=removed)
