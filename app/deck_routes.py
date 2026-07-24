"""Deck admin routes (Decks feature).

CRUD for decks, mirroring ``rotation_routes``. A deck's page graph (pages +
their button / zone links) is edited as a validated JSON blob; the basic fields
(name, bound devices, entry page, refresh cadence) are plain form inputs. On any
change the affected devices' pre-render cache and nav position are cleared so a
stale warmed frame or a position pointing at a removed page can't linger.
"""

from __future__ import annotations

import contextlib
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

from app.deck_suggest import graph_for_pages, suggest_decks
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.page_store import PageStore

bp = Blueprint("decks", __name__, url_prefix="/decks")

_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")


def _store() -> DeckStore:
    return current_app.config["DECK_STORE"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _nav_store() -> Any:
    return current_app.config.get("DECK_NAV_STORE")


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


def _apply_page_refresh(page: DeckPage, raw: str | None) -> DeckPage:
    """Apply a per-page refresh override from a form field. None (field absent)
    leaves the page unchanged; empty clears the override (inherit); a number
    sets it."""
    if raw is None:
        return page
    raw = raw.strip()
    if raw == "":
        return page.model_copy(update={"refresh_interval_minutes": None})
    try:
        return page.model_copy(update={"refresh_interval_minutes": max(0, min(1440, int(raw)))})
    except ValueError:
        return page


def _edit_error(deck_id: str, exc: Exception) -> Response:
    msg = _first_error(exc) if isinstance(exc, ValidationError) else str(exc)
    flash(f"Invalid deck: {msg}", "error")
    return redirect(url_for("decks.index", edit=deck_id) + f"#deck-{deck_id}")


@bp.post("/<deck_id>/update")
def update(deck_id: str) -> Response:
    """Management update: name, devices, entry, refresh cadence, and per-page
    refresh overrides. The page graph (links) is preserved, it's authored in the
    canvas and synced, not hand-edited here."""
    existing = _store().get(deck_id)
    if existing is None:
        flash(f"No deck with id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    form = request.form
    pages = [_apply_page_refresh(p, form.get(f"page_refresh_{p.page_id}")) for p in existing.pages]
    try:
        refresh = int(form.get("refresh_interval_minutes") or existing.refresh_interval_minutes)
        deck = Deck(
            id=deck_id,
            name=(form.get("name") or existing.name).strip() or existing.name,
            enabled=existing.enabled,
            device_ids=[d for d in form.getlist("device_ids") if d],
            pages=pages,
            entry_page_id=(form.get("entry_page_id") or "").strip() or None,
            refresh_interval_minutes=max(0, min(1440, refresh)),
        )
    except (ValidationError, ValueError) as exc:
        return _edit_error(deck_id, exc)
    _store().upsert(deck)
    _invalidate(deck)
    flash(f"Deck {deck.name!r} updated.", "ok")
    return redirect(url_for("decks.index") + f"#deck-{deck_id}")


@bp.post("/<deck_id>/sync")
def sync(deck_id: str) -> Response:
    """Re-derive the deck's graph (links + zones) from the current page
    ``page:<id>`` tap/swipe links, keeping its page set + per-page refresh. Use
    after changing navigation in the canvas editor."""
    existing = _store().get(deck_id)
    if existing is None:
        flash(f"No deck with id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    refresh_by_id = {p.page_id: p.refresh_interval_minutes for p in existing.pages}
    pages = [
        p.model_copy(update={"refresh_interval_minutes": refresh_by_id.get(p.page_id)})
        for p in graph_for_pages(_pages().list(), existing.page_ids)
    ]
    deck = existing.model_copy(update={"pages": pages})
    _store().upsert(deck)
    _invalidate(deck)
    flash("Deck graph re-synced from the pages' links.", "ok")
    return redirect(url_for("decks.index") + f"#deck-{deck_id}")


@bp.post("/<deck_id>/graph")
def edit_graph(deck_id: str) -> Response:
    """Advanced: replace the whole page graph from raw JSON. The management
    fields are kept; the entry page is cleared if it's no longer a page."""
    existing = _store().get(deck_id)
    if existing is None:
        flash(f"No deck with id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    raw = (request.form.get("graph_json") or "").strip()
    try:
        data = json.loads(raw) if raw else []
        if not isinstance(data, list):
            raise ValueError("page graph must be a JSON array of pages")
        pages = [DeckPage.model_validate(p) for p in data]
        page_ids = {p.page_id for p in pages}
        deck = Deck(
            id=deck_id,
            name=existing.name,
            enabled=existing.enabled,
            device_ids=existing.device_ids,
            pages=pages,
            entry_page_id=existing.entry_page_id if existing.entry_page_id in page_ids else None,
            refresh_interval_minutes=existing.refresh_interval_minutes,
        )
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        return _edit_error(deck_id, exc)
    _store().upsert(deck)
    _invalidate(deck)
    flash(f"Deck {deck.name!r} graph updated.", "ok")
    return redirect(url_for("decks.index") + f"#deck-{deck_id}")


@bp.get("/new")
@bp.get("/<deck_id>/edit")
def editor(deck_id: str | None = None) -> str | Response:
    """The deck editor ("dense rail + inspector" redesign): pick pages
    and their flip order; navigation derives automatically. GET /new
    renders a blank deck; GET /<id>/edit loads an existing one."""
    deck = None
    if deck_id is not None:
        deck = _store().get(deck_id)
        if deck is None:
            flash(f"No deck with id {deck_id!r}.", "error")
            return redirect(url_for("decks.index"))

    pages = _pages().list()
    from app.composer import page_preview_token, preview_dims

    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    settings = current_app.config.get("SETTINGS_STORE")
    page_meta = []
    for p in pages:
        try:
            token = page_preview_token(p, preview_dims(p, devices_reg, settings))
        except Exception:
            token = ""
        page_meta.append(
            {
                "id": p.id,
                "name": p.name,
                "thumb": url_for("composer.compose_preview", page_id=p.id) + f"?v={token}",
            }
        )

    device_meta = []
    touch_bound = False
    if devices_reg is not None:
        for d in devices_reg.all():
            if d.kind_of is None:
                continue
            device_meta.append({"id": d.id, "name": d.display_name})
            if deck is not None and d.id in deck.device_ids and d.manifest.get("touch") is True:
                touch_bound = True

    from app.deck_suggest import suggest_decks

    suggestions = []
    for sd in suggest_decks(pages, [d for d in _store().all() if deck is None or d.id != deck.id]):
        suggestions.append({"name": sd.name, "page_ids": [pg.page_id for pg in sd.pages]})

    member_ids = [p.page_id for p in deck.pages] if deck else []
    override_map = (
        {
            p.page_id: p.refresh_interval_minutes
            for p in deck.pages
            if p.refresh_interval_minutes is not None
        }
        if deck
        else {}
    )
    editor_state = {
        "deckId": deck.id if deck else "",
        "pages": page_meta,
        "order": member_ids,
        "home": (deck.resolved_home_page_id if deck and deck.pages else ""),
        "timeout": deck.home_timeout_minutes if deck else 0,
        "overrides": override_map,
        "cadence": deck.refresh_interval_minutes if deck else 30,
        "touchBound": touch_bound,
        "suggestions": suggestions,
    }
    return render_template(
        "deck_editor.html",
        deck=deck,
        page_meta=page_meta,
        device_meta=device_meta,
        member_ids=member_ids,
        home_id=editor_state["home"],
        override_map=override_map,
        editor_state=editor_state,
    )


def _ordered_ids_from_form(form: Any) -> list[str]:
    """Member page ids in flip order. JS submits ``pages`` (CSV in rail
    order); the no-JS fallback submits ``member`` checkboxes plus
    ``order[<id>]`` numeric inputs."""
    raw = (form.get("pages") or "").strip()
    if raw:
        seen: list[str] = []
        for pid in raw.split(","):
            pid = pid.strip()
            if pid and pid not in seen:
                seen.append(pid)
        return seen
    members = form.getlist("member")

    def order_key(pid: str) -> tuple[float, str]:
        try:
            return (float(form.get(f"order[{pid}]") or 0), pid)
        except ValueError:
            return (0.0, pid)

    return sorted(dict.fromkeys(members), key=order_key)


@bp.post("/editor-save")
def editor_save() -> Response:
    """Persist the editor form. Links re-derive from the pages'
    authored tap/swipe actions for the chosen set (same as Sync from
    links); the sync manifest's defaults cover everything the graph
    doesn't say, so a bare page pick is fully navigable."""
    form = request.form
    deck_id = (form.get("deck_id") or "").strip()
    name = (form.get("name") or "").strip() or "Deck"
    ordered = _ordered_ids_from_form(form)
    if not ordered:
        flash("Pick at least one page for the deck.", "error")
        return redirect(request.referrer or url_for("decks.index"))

    existing = _store().get(deck_id) if deck_id else None
    if deck_id and existing is None and not _ID_RE.match(deck_id):
        flash(f"Bad id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    if not deck_id:
        deck_id = _unique_id(_slug_from(name))

    from app.deck_suggest import graph_for_pages

    derived = {p.page_id: p for p in graph_for_pages(_pages().list(), ordered)}
    old_refresh = (
        {p.page_id: p.refresh_interval_minutes for p in existing.pages} if existing else {}
    )

    pages: list[DeckPage] = []
    for pid in ordered:
        base = derived.get(pid) or DeckPage(page_id=pid)
        refresh = old_refresh.get(pid)
        raw_override = form.get(f"override[{pid}]")
        if raw_override is not None and raw_override != "":
            with contextlib.suppress(ValueError):
                refresh = max(0, min(1440, int(raw_override)))
        elif raw_override == "":
            refresh = None
        pages.append(base.model_copy(update={"refresh_interval_minutes": refresh}))

    home = (form.get("home") or "").strip() or None
    if home is not None and home not in ordered:
        home = None
    entry = (form.get("entry") or "").strip() or None
    if entry is not None and entry not in ordered:
        entry = None
    try:
        timeout = max(0, min(120, int(form.get("timeout") or 0)))
    except ValueError:
        timeout = 0
    try:
        cadence = max(0, min(1440, int(form.get("refresh_interval_minutes") or 30)))
    except ValueError:
        cadence = 30

    try:
        deck = Deck(
            id=deck_id,
            name=name,
            enabled=form.get("enabled") in ("on", "true", "1"),
            device_ids=[d for d in form.getlist("device_ids") if d],
            pages=pages,
            entry_page_id=entry,
            home_page_id=home,
            home_timeout_minutes=timeout,
            refresh_interval_minutes=cadence,
        )
    except ValidationError as exc:
        flash(f"Invalid deck: {_first_error(exc)}", "error")
        return redirect(
            url_for("decks.editor", deck_id=deck_id) if existing else url_for("decks.index")
        )
    _store().upsert(deck)
    _invalidate(deck)
    flash(f"Deck {deck.name!r} saved.", "ok")
    return redirect(url_for("decks.editor", deck_id=deck.id))


@bp.post("/<deck_id>/push")
def push(deck_id: str) -> Response:
    """Initialize the deck and send it to its panels: warm every page
    for every bound device (so navigation serves pre-rendered frames
    and the sync manifest ships complete on first fetch), then push the
    entry page so the panels actually show the deck. The one-click
    "make this deck live" action; without it, warming waits for the
    scheduler tick and the panel keeps whatever it was showing."""
    deck = _store().get(deck_id)
    if deck is None:
        flash(f"No deck with id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    if not deck.device_ids:
        flash("Bind at least one device to the deck first.", "error")
        return redirect(url_for("decks.index") + f"#deck-{deck_id}")
    pusher = current_app.config.get("PUSH_MANAGER")
    if pusher is None:
        flash("Push pipeline not ready.", "error")
        return redirect(url_for("decks.index") + f"#deck-{deck_id}")

    warmed = failed = 0
    for device_id in deck.device_ids:
        for page in deck.pages:
            if pusher.warm_deck_page(page.page_id, device_id):
                warmed += 1
            else:
                failed += 1
    entry = deck.resolved_entry_page_id
    result = pusher.push(
        entry,
        device_ids=set(deck.device_ids),
        respect_quiet_hours=False,
        force_publish=True,
        source="deck_init",
    )
    nav = _nav_store()
    if nav is not None:
        for device_id in deck.device_ids:
            nav.set(device_id, deck.id, entry)
    if result.status == "failed":
        flash(f"Warmed {warmed} frame(s) but the entry-page push failed.", "error")
    elif failed:
        flash(
            f"Deck pushed: entry page sent, {warmed} frame(s) warmed, {failed} warm(s) failed "
            "(those pages render on first navigation instead).",
            "warn",
        )
    else:
        flash(f"Deck pushed: entry page sent, {warmed} frame(s) warmed.", "ok")
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
