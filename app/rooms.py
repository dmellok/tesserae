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
        "show_book_action": room.is_bookable,
    }


def feed_for(room: Room, *, core: Any) -> dict[str, Any] | None:
    """The calendar_core feed record backing this room."""
    loader = getattr(core.server_module, "_load_feeds", None)
    if loader is None:
        return None
    from pathlib import Path as _Path

    try:
        feeds = loader(_Path(core.data_dir)).get("feeds") or []
    except Exception:
        logger.exception("rooms: could not read feeds for %s", room.id)
        return None
    enabled = [f for f in feeds if f.get("enabled", True) and f.get("url")]
    if room.feed_id:
        return next((f for f in enabled if f.get("id") == room.feed_id), None)
    return enabled[0] if enabled else None


def book_now(room: Room, *, core: Any, now: Any = None) -> str:
    """Write a booking into the room's own calendar. Returns the new
    event's URL, or raises ``CalDavWriteError`` with a message fit to show
    an operator.

    Everything needed is already on the feed: ``calendar_core`` stored the
    collection URL and the credentials when the feed was discovered, so
    this adds no new secrets and no new auth path.
    """
    from datetime import UTC, datetime, timedelta

    from app.caldav_write import CalDavWriteError, build_event_ics, new_uid, put_event

    feed = feed_for(room, core=core)
    if feed is None:
        raise CalDavWriteError("This room has no usable calendar feed.")
    collection_url = str(feed.get("url") or "")
    if not collection_url:
        raise CalDavWriteError("This room's feed has no calendar URL.")

    build_opener = getattr(core.server_module, "_build_opener", None)
    feed_auth = getattr(core.server_module, "_feed_auth", None)
    if build_opener is None or feed_auth is None:
        raise CalDavWriteError("This install's calendar_core is too old to book.")

    start = now or datetime.now(UTC)
    end = start + timedelta(minutes=room.book_minutes)
    uid = new_uid()
    ics = build_event_ics(
        uid=uid,
        summary=room.book_summary,
        start=start,
        end=end,
        location=room.name,
        now=start,
    )
    opener = build_opener(collection_url, feed_auth(feed))
    return put_event(collection_url, ics, uid, opener=opener)


def feed_can_write(feed: dict[str, Any] | None) -> bool:
    """Whether a booking could be written into this feed.

    An ICS export URL is read-only however good the credentials are, and
    a CalDAV collection without credentials cannot authenticate, so both
    are needed. Drives the disabled state on the "write straight into
    this calendar" option, where the reason matters more than the fact.
    """
    if not feed:
        return False
    if not str(feed.get("url") or "").strip():
        return False
    mode = str(feed.get("auth_mode") or "none").strip().lower()
    return mode in ("basic", "digest") and bool(str(feed.get("username") or "").strip())


def row_view(
    room: Room,
    *,
    feeds: list[dict[str, Any]],
    devices: Any = None,
    page_store: PageStore | None = None,
) -> dict[str, Any]:
    """Everything the Rooms list shows for one room, resolved once.

    Deliberately returns ``None`` for anything the server cannot actually
    establish rather than a plausible-looking placeholder: a settings page
    that invents a feed's health is worse than one that admits it doesn't
    poll feeds yet.
    """
    by_id = {f.get("id"): f for f in feeds}
    feed = by_id.get(room.feed_id) if room.feed_id else (feeds[0] if feeds else None)
    page = page_store.get(room.resolved_page_id()) if page_store is not None else None

    panels: list[dict[str, Any]] = []
    registry = getattr(devices, "devices", {}) if devices is not None else {}
    for device_id in room.device_ids:
        device = registry.get(device_id)
        panels.append(
            {
                "id": device_id,
                "name": getattr(device, "display_name", device_id) if device else device_id,
                "missing": device is None,
            }
        )

    return {
        "room": room,
        "feed": feed,
        "feed_name": (feed or {}).get("name") or (feed or {}).get("id") or "",
        "feed_missing": room.feed_id != "" and feed is None,
        "feed_implicit": room.feed_id == "" and feed is not None,
        "can_write": feed_can_write(feed),
        "panels": panels,
        "dashboard_built_at": getattr(page, "updated_at", None) if page else None,
        "dashboard_exists": page is not None,
    }


def book_action(room: Room) -> str | None:
    """The cell tap action that books this room, or None.

    ``webhook_refresh`` fires the POST and then repaints once the
    receiver has had time to commit, which is the whole reason a booking
    shows up on the panel without waiting for the next wake.

    The room id rides in the query string because the webhook payload
    cannot carry it: it reports ``device_id`` and, only for a
    rotation-bound page, the step's page id. A room panel is normally
    bound directly, so a receiver serving several rooms would otherwise
    have to reverse a device id back to a room. Appended rather than
    templated, so the operator's own query string survives.
    """
    if room.books_by_caldav:
        # Booked by Tesserae itself, so the action names the room rather
        # than an endpoint: there is nothing outbound to point at.
        return f"room_book:{room.id}"
    if not room.books_by_endpoint:
        return None
    sep = "&" if "?" in room.book_url else "?"
    return f"webhook_refresh:{room.book_url}{sep}room={room.id}"


def build_page(room: Room, *, devices: Any = None, settings: Any = None) -> Page:
    """The page a room generates. Deterministic: same room, same page."""
    page_id = room.resolved_page_id()
    draft = Page(id=page_id, name=room.name, device_ids=list(room.device_ids))
    w, h = _panel_dims(draft, devices, settings)
    on_tap = book_action(room)
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
