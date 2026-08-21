"""Rooms: a generator, not a runtime (#90).

A room names a calendar feed, the panels showing it, and optionally an
endpoint that books it. From that, this module generates an ordinary
page, with an ordinary widget in an ordinary cell, bound to ordinary
devices, and then gets out of the way. Nothing renders "through" a room.

That is the whole design constraint. Rooms holds configuration, never
bookings, so Tesserae never becomes the source of truth for whether a
room is free and never inherits the availability contract that comes
with it. And because the output is a plain page, deleting rooms
tomorrow would leave every panel working exactly as a hand-composed
dashboard does today. The escape hatch is the default state.

Phase 1 of #90: read-only reconciliation from a resource calendar,
rendered per room through the normal compose/bind/push flow.
"""

from __future__ import annotations

import logging
from typing import Any

from app.state.page_store import Cell, Page, PageStore
from app.state.room_model import Room

logger = logging.getLogger(__name__)

WIDGET_ID = "room_status"

# A board is one page carrying a cell per room, so it needs no widget of
# its own and no runtime: the same generator, laid out as rows.
BOARD_PAGE_ID = "room_board"

# A board row is a strip, not a panel. The stacked layouts put the room
# name at a few pixels tall and leave most of the row empty, so rows use
# the widget's horizontal layout instead. Pinned rather than left to
# ``auto`` so a board with two rooms (tall rows) still reads as a board.
_BOARD_ROW_LAYOUT = "row"

# Fallback panel when a room has no device bound yet, so the page is
# still previewable while it's being set up. The moment a device is
# bound the real panel wins.
_FALLBACK_W = 800
_FALLBACK_H = 480


def _panel_dims(page: Page, devices: Any, settings: Any) -> tuple[int, int]:
    """Panel pixels for the generated page, resolved the same way the
    editor resolves them so a room's layout matches what the operator
    would have drawn by hand."""
    try:
        from app.panel import resolve_panel_for_page

        panel = resolve_panel_for_page(page, devices, settings)
        w = int(getattr(panel, "w", 0) or 0)
        h = int(getattr(panel, "h", 0) or 0)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        logger.exception("rooms: panel resolution failed for %s", page.id)
    return _FALLBACK_W, _FALLBACK_H


def cell_options(room: Room) -> dict[str, Any]:
    """Widget options for a room's cell.

    ``layout`` stays on ``auto`` deliberately: the widget picks by cell
    shape, so one room definition serves a 7.5in door panel and a small
    wall tile without the operator choosing per panel. Titles stay off,
    because a room panel publishes whatever it renders to a corridor.
    """
    return {
        "feed_id": room.feed_id,
        "room_name": room.name,
        "location_filter": room.location_filter,
        "layout": "auto",
        "show_titles": False,
        "show_book_action": bool(room.book_url),
    }


def build_page(room: Room, *, devices: Any = None, settings: Any = None) -> Page:
    """The page a room generates. Deterministic: same room, same page."""
    page_id = room.resolved_page_id()
    draft = Page(id=page_id, name=room.name, device_ids=list(room.device_ids))
    w, h = _panel_dims(draft, devices, settings)
    on_tap = f"webhook_refresh:{room.book_url}" if room.book_url else None
    return Page(
        id=page_id,
        name=room.name,
        device_ids=list(room.device_ids),
        cells=[
            Cell(
                id=f"{page_id}_cell",
                plugin=WIDGET_ID,
                x=0,
                y=0,
                w=w,
                h=h,
                options=cell_options(room),
                on_tap=on_tap,
            )
        ],
    )


def build_board_page(
    rooms: list[Room],
    *,
    devices: Any = None,
    settings: Any = None,
    page_id: str = BOARD_PAGE_ID,
    name: str = "Room board",
    device_ids: list[str] | None = None,
) -> Page:
    """One page, one row per room, for a lobby or corridor panel.

    Rows rather than a grid: a board is read top to bottom at a glance,
    and a room's name and state have to sit on one line to be scannable.
    Each row is a normal ``room_status`` cell, so the board inherits every
    fix the widget gets and carries no rendering code of its own.
    """
    bound = list(device_ids or [])
    draft = Page(id=page_id, name=name, device_ids=bound)
    w, h = _panel_dims(draft, devices, settings)
    shown = [r for r in rooms if r.enabled]
    cells: list[Cell] = []
    if shown:
        row_h = h // len(shown)
        for i, room in enumerate(shown):
            options = cell_options(room)
            options["layout"] = _BOARD_ROW_LAYOUT
            # A board is a status surface, not a control: tapping a row
            # would book whichever room the finger landed on, which is
            # not a mistake worth making possible.
            options["show_book_action"] = False
            cells.append(
                Cell(
                    id=f"{page_id}_{room.id}",
                    plugin=WIDGET_ID,
                    x=0,
                    y=i * row_h,
                    w=w,
                    # Last row absorbs the remainder so the board fills
                    # the panel exactly rather than leaving a seam.
                    h=(h - i * row_h) if i == len(shown) - 1 else row_h,
                    options=options,
                )
            )
    return Page(id=page_id, name=name, device_ids=bound, cells=cells)


def sync_board(
    rooms: list[Room],
    *,
    page_store: PageStore,
    devices: Any = None,
    settings: Any = None,
    device_ids: list[str] | None = None,
) -> Page:
    """Write the board page, keeping its binding and styling if it exists."""
    existing = page_store.get(BOARD_PAGE_ID)
    bound = device_ids if device_ids is not None else (existing.device_ids if existing else [])
    page = build_board_page(rooms, devices=devices, settings=settings, device_ids=list(bound))
    if existing is not None:
        page.theme = existing.theme
        page.style = existing.style
        page.font = existing.font
    page_store.save(page)
    return page


def sync(room: Room, *, page_store: PageStore, devices: Any = None, settings: Any = None) -> Page:
    """Write the room's page, preserving anything the operator changed
    that rooms does not own.

    Rooms owns the binding, the widget, and the widget's options. It does
    not own the theme, style or font: someone who restyles a room page to
    match the rest of their office should not have it reverted the next
    time they rename the room.
    """
    page = build_page(room, devices=devices, settings=settings)
    existing = page_store.get(room.resolved_page_id())
    if existing is not None:
        page.theme = existing.theme
        page.style = existing.style
        page.font = existing.font
        if existing.cells:
            prior = existing.cells[0]
            page.cells[0].theme = prior.theme
            page.cells[0].style = prior.style
            page.cells[0].font = prior.font
    page_store.save(page)
    return page


def delete_page(room: Room, *, page_store: PageStore) -> bool:
    """Remove a room's generated page. Only ever touches a page whose id
    carries the room prefix, so a hand-made page an operator pointed a
    room at cannot be deleted out from under them."""
    from app.state.room_model import PAGE_ID_PREFIX

    page_id = room.resolved_page_id()
    if not page_id.startswith(PAGE_ID_PREFIX):
        return False
    try:
        return bool(page_store.delete(page_id))
    except Exception:
        logger.exception("rooms: page delete failed for %s", page_id)
        return False


def sync_all(
    rooms: list[Room], *, page_store: PageStore, devices: Any = None, settings: Any = None
) -> int:
    """Regenerate every enabled room's page. Returns how many were written."""
    count = 0
    for room in rooms:
        if not room.enabled:
            continue
        try:
            sync(room, page_store=page_store, devices=devices, settings=settings)
            count += 1
        except Exception:
            logger.exception("rooms: sync failed for %s", room.id)
    return count
