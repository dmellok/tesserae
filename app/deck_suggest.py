"""Suggest decks from the page-link graph.

A cell can carry a ``page:<id>`` tap or swipe action (``Cell.on_tap`` /
``Cell.on_swipe``), authored in the canvas editor as "tap this tile to go to
page X". Those are stored, statically-readable edges between pages, which is
exactly what a deck is. This module reads the page store, builds the directed
link graph, finds connected clusters of 2+ pages, and returns a ready-to-save
``Deck`` per cluster with:

* a zone link for each tap action (the linking cell's grid rect, normalised to
  0..1 so it matches where the tile renders);
* a button link for each swipe action (the swipe direction as the button name);
* the entry page set to the cluster's most-linking page (the hub);
* devices unioned from the cluster's pages.

Pages already covered by an existing deck are excluded, so only genuinely-new
groupings are suggested.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from app.button_actions import ButtonActionError, parse_action_spec
from app.state.deck_model import Deck, DeckLink, DeckPage, DeckZone
from app.state.page_store import Page


def _page_target(spec: object) -> str | None:
    """The target page id when ``spec`` is a ``page:<id>`` action, else None."""
    if not isinstance(spec, str) or not spec:
        return None
    try:
        action, arg = parse_action_spec(spec)
    except ButtonActionError:
        return None
    return arg if action == "page" and arg else None


def _grid_extent(page: Page) -> tuple[int, int]:
    """The page grid's column and row count, taken from the furthest cell edge.
    Used to normalise a cell rect into a 0..1 zone."""
    cols = max((c.x + c.w for c in page.cells), default=1)
    rows = max((c.y + c.h for c in page.cells), default=1)
    return max(cols, 1), max(rows, 1)


def _links_from_page(page: Page) -> list[DeckLink]:
    """DeckLinks implied by a page's cells: a ``page:<id>`` tap becomes a zone
    link (the cell's rect), a ``page:<id>`` swipe becomes a button link (the
    direction). Targets are not filtered here; the caller keeps only those in
    the same cluster."""
    cols, rows = _grid_extent(page)
    links: list[DeckLink] = []
    for cell in page.cells:
        tap_target = _page_target(cell.on_tap)
        if tap_target is not None:
            x = round(cell.x / cols, 4)
            y = round(cell.y / rows, 4)
            zone = DeckZone(
                x=x,
                y=y,
                w=min(round(cell.w / cols, 4), 1.0 - x),
                h=min(round(cell.h / rows, 4), 1.0 - y),
            )
            links.append(DeckLink(target_page_id=tap_target, zone=zone))
        if isinstance(cell.on_swipe, dict):
            for direction, spec in cell.on_swipe.items():
                swipe_target = _page_target(spec)
                if swipe_target is not None:
                    links.append(DeckLink(target_page_id=swipe_target, button=str(direction)))
    return links


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "deck"


def graph_for_pages(pages: list[Page], page_ids: list[str]) -> list[DeckPage]:
    """Re-derive DeckPages for a fixed set of page ids from the current page
    links: each page's ``page:<id>`` tap/swipe links, kept only to targets
    within the set. Used to re-sync a deck's graph from what's authored in the
    canvas without disturbing the deck's page set. Per-page refresh overrides
    are not carried here; the caller preserves them from the existing deck."""
    by_id = {p.id: p for p in pages}
    wanted = set(page_ids)
    out: list[DeckPage] = []
    for pid in page_ids:
        page = by_id.get(pid)
        links = (
            [
                lnk
                for lnk in _links_from_page(page)
                if lnk.target_page_id in wanted and lnk.target_page_id != pid
            ]
            if page is not None
            else []
        )
        out.append(DeckPage(page_id=pid, links=links))
    return out


def _build_deck(
    component: set[str], edges: dict[str, list[DeckLink]], by_id: dict[str, Page]
) -> Deck | None:
    """Assemble a suggested Deck for a connected component of page ids."""
    pages: list[DeckPage] = []
    out_degree: dict[str, int] = {}
    for pid in sorted(component):
        links = [lnk for lnk in edges.get(pid, []) if lnk.target_page_id in component]
        pages.append(DeckPage(page_id=pid, links=links))
        out_degree[pid] = len({lnk.target_page_id for lnk in links})
    # Entry = the most-linking page (a hub reads best as the landing page).
    entry = max(sorted(component), key=lambda p: out_degree.get(p, 0))
    device_ids = sorted({d for pid in component for d in (by_id[pid].device_ids or [])})
    name = f"{by_id[entry].name} deck"
    try:
        return Deck(
            id=_slug(name),
            name=name,
            device_ids=device_ids,
            pages=pages,
            entry_page_id=entry,
            refresh_interval_minutes=15,
        )
    except ValidationError:
        return None


def suggest_decks(pages: list[Page], existing_decks: list[Deck]) -> list[Deck]:
    """Suggested decks derived from ``page:<id>`` links across the page store,
    excluding pages already in an existing deck. Each result is a valid, unsaved
    Deck for a cluster of 2+ mutually-linked pages."""
    covered = {pid for deck in existing_decks for pid in deck.page_ids}
    by_id = {p.id: p for p in pages}
    # source page id -> its DeckLinks (targets validated to exist + be uncovered)
    edges: dict[str, list[DeckLink]] = {}
    for page in pages:
        if page.id in covered:
            continue
        kept = [
            lnk
            for lnk in _links_from_page(page)
            if lnk.target_page_id in by_id
            and lnk.target_page_id not in covered
            and lnk.target_page_id != page.id
        ]
        if kept:
            edges[page.id] = kept

    # Undirected adjacency so a one-way link still groups the two pages.
    adj: dict[str, set[str]] = {}
    for src, links in edges.items():
        for lnk in links:
            adj.setdefault(src, set()).add(lnk.target_page_id)
            adj.setdefault(lnk.target_page_id, set()).add(src)

    seen: set[str] = set()
    suggestions: list[Deck] = []
    for node in sorted(adj):
        if node in seen:
            continue
        component: set[str] = set()
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            component.add(cur)
            stack.extend(adj.get(cur, set()) - seen)
        if len(component) < 2:
            continue
        deck = _build_deck(component, edges, by_id)
        if deck is not None:
            suggestions.append(deck)
    return suggestions
