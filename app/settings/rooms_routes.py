"""Settings -> Rooms: manage meeting rooms (#90).

Twelve rooms used to mean twelve hand-composed pages with the same widget
options retyped into each. A room names its feed, its panels and an
optional booking endpoint once, and generates the page from that.

These routes are thin: everything real lives in :mod:`app.rooms`, which
writes an ordinary page and stops. Nothing here renders.
"""

from __future__ import annotations

import re
from typing import Any

from flask import current_app, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from app import rooms as rooms_service
from app.state.room_model import Room

from ._shared import bp, devices, settings_store

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def room_store() -> Any:
    return current_app.config.get("ROOM_STORE")


def page_store() -> Any:
    return current_app.config.get("PAGE_STORE")


def _slugify(name: str) -> str:
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return slug[:64] or "room"


def _unique_id(store: Any, base: str) -> str:
    existing = {r.id for r in store.all()}
    if base not in existing:
        return base
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if candidate not in existing:
            return candidate
    return base


def _feeds() -> list[dict[str, Any]]:
    """Calendar feeds from calendar_core, for the picker. Empty when the
    plugin isn't installed, which the template turns into a prompt rather
    than an error."""
    registry = current_app.config.get("PLUGIN_REGISTRY")
    plugin = registry.get("calendar_core") if registry is not None else None
    if plugin is None or plugin.server_module is None:
        return []
    loader = getattr(plugin.server_module, "_load_feeds", None)
    if loader is None:
        return []
    try:
        from pathlib import Path

        feeds = loader(Path(plugin.data_dir)).get("feeds") or []
    except Exception:
        current_app.logger.exception("rooms: could not read calendar feeds")
        return []
    return [
        {"id": f.get("id"), "name": f.get("name") or f.get("id"), "enabled": f.get("enabled", True)}
        for f in feeds
        if f.get("id")
    ]


def _panels() -> list[Any]:
    """Registered device instances a room can be shown on."""
    return [d for d in devices().devices.values() if d.kind_of is not None]


@bp.get("/settings/rooms")
def rooms_index() -> str:
    store = room_store()
    all_rooms = store.all() if store is not None else []
    return render_template(
        "settings_rooms.html",
        active="rooms",
        rooms=all_rooms,
        feeds=_feeds(),
        panels=_panels(),
        widget_installed=_widget_installed(),
    )


def _widget_installed() -> bool:
    registry = current_app.config.get("PLUGIN_REGISTRY")
    return registry is not None and registry.get(rooms_service.WIDGET_ID) is not None


def _form_room(existing: Room | None = None) -> Room:
    name = (request.form.get("name") or "").strip()
    room_id = (existing.id if existing is not None else "") or _unique_id(
        room_store(), _slugify(name)
    )
    return Room(
        id=room_id,
        name=name,
        feed_id=(request.form.get("feed_id") or "").strip(),
        location_filter=(request.form.get("location_filter") or "").strip(),
        device_ids=[d for d in request.form.getlist("device_ids") if d],
        book_url=(request.form.get("book_url") or "").strip(),
        enabled=request.form.get("enabled") is not None,
        page_id=existing.page_id if existing is not None else "",
    )


def _sync(room: Room) -> Room:
    """Generate the page and remember which one it is."""
    page = rooms_service.sync(
        room, page_store=page_store(), devices=devices(), settings=settings_store()
    )
    room.page_id = page.id
    room_store().upsert(room)
    return room


@bp.post("/settings/rooms")
def rooms_create() -> Response:
    store = room_store()
    if store is None:
        flash("Rooms store unavailable.", "error")
        return redirect(url_for("auth.rooms_index"))
    try:
        room = _form_room()
    except Exception as err:
        flash(f"Couldn't add the room: {err}", "error")
        return redirect(url_for("auth.rooms_index"))
    _sync(room)
    flash(f"Added {room.name}.", "ok")
    return redirect(url_for("auth.rooms_index"))


@bp.post("/settings/rooms/<room_id>")
def rooms_update(room_id: str) -> Response:
    store = room_store()
    existing = store.get(room_id) if store is not None else None
    if existing is None:
        flash("No such room.", "error")
        return redirect(url_for("auth.rooms_index"))
    try:
        room = _form_room(existing)
    except Exception as err:
        flash(f"Couldn't save the room: {err}", "error")
        return redirect(url_for("auth.rooms_index"))
    _sync(room)
    flash(f"Saved {room.name}.", "ok")
    return redirect(url_for("auth.rooms_index"))


@bp.post("/settings/rooms/<room_id>/delete")
def rooms_delete(room_id: str) -> Response:
    store = room_store()
    existing = store.get(room_id) if store is not None else None
    if existing is None:
        flash("No such room.", "error")
        return redirect(url_for("auth.rooms_index"))
    # The page goes only when the room generated it. A room pointed at a
    # hand-made dashboard leaves that dashboard alone.
    removed_page = False
    if request.form.get("delete_page") is not None:
        removed_page = rooms_service.delete_page(existing, page_store=page_store())
    store.delete(room_id)
    flash(
        f"Removed {existing.name}." + (" Its dashboard went with it." if removed_page else ""),
        "ok",
    )
    return redirect(url_for("auth.rooms_index"))


@bp.post("/settings/rooms/<room_id>/resync")
def rooms_resync(room_id: str) -> Response:
    """Regenerate the page. The escape hatch for someone who edited a
    generated dashboard by hand and wants the room's version back."""
    store = room_store()
    existing = store.get(room_id) if store is not None else None
    if existing is None:
        flash("No such room.", "error")
        return redirect(url_for("auth.rooms_index"))
    _sync(existing)
    flash(f"Rebuilt {existing.name}'s dashboard.", "ok")
    return redirect(url_for("auth.rooms_index"))
