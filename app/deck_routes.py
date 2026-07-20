"""Deck admin routes (Decks feature).

CRUD for decks, mirroring ``rotation_routes``. A deck's page graph (pages +
their button / zone links) is edited as a validated JSON blob; the basic fields
(name, bound devices, entry page, refresh cadence) are plain form inputs. On any
change the affected devices' pre-render cache and nav position are cleared so a
stale warmed frame or a position pointing at a removed page can't linger.
"""

from __future__ import annotations

import json
import re
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app.deck_suggest import suggest_decks
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.page_store import PageStore

bp = Blueprint("decks", __name__, url_prefix="/decks")

_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")


def _store() -> DeckStore:
    return current_app.config["DECK_STORE"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _slug_from(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "deck"


def _unique_id(base: str) -> str:
    taken = {d.id for d in _store().all()}
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "validation failed"
    err = errors[0]
    loc = ".".join(str(x) for x in err.get("loc", ()))
    return f"[{loc}] {err.get('msg', 'invalid')}"


def _parse_form(form: Any, *, existing_id: str | None = None) -> Deck:
    """Build a Deck from the admin form. Raises ValidationError (bad shape) or
    ValueError (bad graph JSON)."""
    name = (form.get("name") or "").strip()
    device_ids = [d for d in form.getlist("device_ids") if d]
    entry_page_id = (form.get("entry_page_id") or "").strip() or None
    try:
        refresh = int(form.get("refresh_interval_minutes") or 15)
    except (TypeError, ValueError):
        refresh = 15
    raw = (form.get("graph_json") or "").strip()
    try:
        pages_data = json.loads(raw) if raw else []
    except json.JSONDecodeError as exc:
        raise ValueError(f"page graph is not valid JSON: {exc}") from exc
    if not isinstance(pages_data, list):
        raise ValueError("page graph must be a JSON array of pages")
    pages = [DeckPage.model_validate(p) for p in pages_data]
    return Deck(
        id=existing_id or _unique_id(_slug_from(name)),
        name=name,
        device_ids=device_ids,
        pages=pages,
        entry_page_id=entry_page_id,
        refresh_interval_minutes=refresh,
    )


def _invalidate(deck: Deck) -> None:
    """Drop the warmed frames + nav position for a deck's devices, so a changed
    or removed page doesn't leave a stale warmed frame or a dangling position."""
    push = current_app.config.get("PUSH_MANAGER")
    nav = current_app.config.get("DECK_NAV_STORE")
    for device_id in deck.device_ids:
        if push is not None and hasattr(push, "clear_deck_cache"):
            push.clear_deck_cache(device_id)
        if nav is not None:
            nav.clear(device_id)


def _graph_json(deck_pages: Any) -> str:
    return json.dumps([p.model_dump(exclude_none=True) for p in deck_pages], indent=2)


@bp.get("")
def index() -> str:
    pages = _pages().list()
    decks = _store().all()
    devices = current_app.config.get("DEVICE_REGISTRY")
    instances = [d for d in (devices.all() if devices is not None else []) if d.kind_of is not None]
    graphs = {d.id: _graph_json(d.pages) for d in decks}
    # Suggested decks derived from page:<id> tap/swipe links across pages, so a
    # user who wired navigation in the canvas editor can create the deck in one
    # click instead of hand-authoring the graph.
    suggestions = [
        {
            "name": s.name,
            "device_ids": s.device_ids,
            "entry_page_id": s.entry_page_id,
            "refresh": s.refresh_interval_minutes,
            "graph_json": _graph_json(s.pages),
            "page_ids": s.page_ids,
        }
        for s in suggest_decks(pages, decks)
    ]
    return render_template(
        "decks.html",
        decks=decks,
        pages=pages,
        page_names={p.id: p.name for p in pages},
        devices=instances,
        graphs=graphs,
        suggestions=suggestions,
        edit_id=request.args.get("edit"),
    )


@bp.post("/new")
def create() -> Response:
    try:
        deck = _parse_form(request.form)
    except (ValidationError, ValueError) as exc:
        flash(
            f"Invalid deck: {_first_error(exc) if isinstance(exc, ValidationError) else exc}",
            "error",
        )
        return redirect(url_for("decks.index"))
    if not _ID_RE.match(deck.id):
        flash(f"Bad id {deck.id!r} (snake_case only).", "error")
        return redirect(url_for("decks.index"))
    _store().upsert(deck)
    _invalidate(deck)
    flash(f"Deck {deck.name!r} saved.", "ok")
    return redirect(url_for("decks.index") + f"#deck-{deck.id}")


@bp.post("/<deck_id>/update")
def update(deck_id: str) -> Response:
    existing = _store().get(deck_id)
    if existing is None:
        flash(f"No deck with id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    try:
        deck = _parse_form(request.form, existing_id=deck_id)
    except (ValidationError, ValueError) as exc:
        msg = _first_error(exc) if isinstance(exc, ValidationError) else str(exc)
        flash(f"Invalid deck: {msg}", "error")
        return redirect(url_for("decks.index", edit=deck_id) + f"#deck-{deck_id}")
    deck = deck.model_copy(update={"enabled": existing.enabled})
    _store().upsert(deck)
    _invalidate(deck)
    flash(f"Deck {deck.name!r} updated.", "ok")
    return redirect(url_for("decks.index") + f"#deck-{deck_id}")


@bp.post("/<deck_id>/toggle")
def toggle(deck_id: str) -> Response:
    existing = _store().get(deck_id)
    if existing is None:
        flash(f"No deck with id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    updated = existing.model_copy(update={"enabled": not existing.enabled})
    _store().upsert(updated)
    _invalidate(updated)
    return redirect(url_for("decks.index") + f"#deck-{deck_id}")


@bp.post("/<deck_id>/delete")
def delete(deck_id: str) -> Response:
    existing = _store().get(deck_id)
    if _store().delete(deck_id):
        if existing is not None:
            _invalidate(existing)
        flash("Deck deleted.", "ok")
    else:
        flash(f"No deck with id {deck_id!r}.", "error")
    return redirect(url_for("decks.index"))


def register(app: Flask) -> None:
    app.register_blueprint(bp)


__all__ = ["bp", "register"]
