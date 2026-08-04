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
from datetime import datetime
from typing import Any, Literal, cast

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


def _ago(epoch: Any, now_ts: float) -> str | None:
    """Compact relative-time label."""
    if not isinstance(epoch, (int, float)):
        return None
    delta = max(0, int(now_ts - epoch))
    if delta < 90:
        return "just now"
    if delta < 5400:
        return f"{delta // 60} min ago"
    if delta < 172_800:
        return f"{delta // 3600} h ago"
    return f"{delta // 86_400} d ago"


def _page_thumbs(pages: list[Any]) -> dict[str, str]:
    """Composer live-preview URL per dashboard (the design's screen cards)."""
    from app.composer import page_preview_token, preview_dims

    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    settings = current_app.config.get("SETTINGS_STORE")
    out: dict[str, str] = {}
    for p in pages:
        try:
            token = page_preview_token(p, preview_dims(p, devices_reg, settings))
        except Exception:
            token = ""
        out[p.id] = url_for("composer.compose_preview", page_id=p.id) + f"?v={token}"
    return out


def _live_map() -> dict[str, tuple[str | None, str | None]]:
    """device_id -> (deck_id or None, page_id) currently on glass, from the
    nav record first (deck-driven displays) then the last served render."""
    out: dict[str, tuple[str | None, str | None]] = {}
    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    nav = _nav_store()
    push = current_app.config.get("PUSH_MANAGER")
    for d in devices_reg.all() if devices_reg is not None else []:
        if getattr(d, "kind_of", None) is None:
            continue
        rec = None
        if nav is not None:
            try:
                rec = nav.get(d.id)
            except Exception:
                rec = None
        if rec and rec.get("page_id"):
            out[d.id] = (rec.get("deck_id"), rec.get("page_id"))
            continue
        latest = None
        if push is not None and hasattr(push, "latest_render_for"):
            latest = push.latest_render_for(d.id)
        pid = latest.get("page_id") if isinstance(latest, dict) else None
        if isinstance(pid, str) and pid:
            out[d.id] = (None, pid)
    return out


def _design_cards(
    *,
    nav_decks: list[Deck],
    rotations: list[Any],
    schedules: list[Any],
    current_step: dict[str, dict[str, Any]],
    schedule_status: dict[str, dict[str, Any]],
    pages: list[Any],
    highlight_id: str | None = None,
) -> dict[str, Any]:
    """View-models for the design-handoff Decks page: one row per deck with
    a kind-specific body (screen cards, 24h rail, steppers), plus the
    on-air summary. Returns {"cards": [...], "onair": {...} | None}."""
    from datetime import timedelta

    from app.schedule_routes import _project_fires
    from app.tz_resolve import app_timezone

    tz = app_timezone()
    now_tz = datetime.now(tz)
    now_ts = now_tz.timestamp()
    day_start = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
    now_pct = min(100.0, max(0.0, (now_ts - day_start.timestamp()) / 864.0 / 100 * 100))
    page_names = {p.id: p.name for p in pages}
    thumbs = _page_thumbs(pages)
    live = _live_map()

    def screen(pid: str, index: int | None = None, is_live: bool = False) -> dict[str, Any]:
        return {
            "id": pid,
            "name": page_names.get(pid, pid),
            "thumb": thumbs.get(pid, ""),
            "index": index,
            "live": is_live,
        }

    cards: list[dict[str, Any]] = []

    for deck in nav_decks:
        live_page = None
        for device_id in deck.device_ids:
            rec = live.get(device_id)
            if rec and rec[0] == deck.id:
                live_page = rec[1]
                break
        cards.append(
            {
                "kind": "nav",
                "id": deck.id,
                "name": deck.name,
                "enabled": deck.enabled,
                "playing": live_page is not None,
                "badge": "By hand",
                "badge_icon": "hand-tap",
                "meta": (
                    f"{len(deck.pages)} dashboard{'s' if len(deck.pages) != 1 else ''}"
                    f" · {len(deck.device_ids)} display{'s' if len(deck.device_ids) != 1 else ''}"
                    " · button, tap, swipe"
                ),
                "screens": [screen(dp.page_id, None, dp.page_id == live_page) for dp in deck.pages],
                "live_name": page_names.get(live_page, live_page) if live_page else None,
                "fire_url": url_for("decks.push", deck_id=deck.id),
                "edit_url": url_for("decks.editor", deck_id=deck.id),
                "toggle_url": url_for("decks.toggle", deck_id=deck.id),
                "delete_url": url_for("decks.delete", deck_id=deck.id),
                "step_url": url_for("decks.step", deck_id=deck.id),
                "play_url": None,
            }
        )

    for r in rotations:
        cur = current_step.get(r.id) or {}
        active = bool(cur.get("active")) and r.enabled
        step_index = cur.get("step_index") if active else None
        current_page = cur.get("page_id") if active else None
        nxt = cur.get("next_transition_epoch")
        next_advance = (
            datetime.fromtimestamp(nxt, tz=tz).strftime("%H:%M")
            if active and isinstance(nxt, (int, float))
            else None
        )
        playing = active and any(
            rec[1] == current_page and (rec[0] in (r.id, None)) for rec in live.values()
        )
        n = len(r.steps)
        play_next = ((step_index or 0) + 1) % n if n else 0
        cards.append(
            {
                "kind": "cycle",
                "id": r.id,
                "name": r.name,
                "enabled": r.enabled,
                "playing": playing,
                "badge": "Playing · Timer cycle" if playing else "Timer cycle",
                "badge_icon": "arrows-clockwise",
                "meta": (
                    f"{n} dashboard{'s' if n != 1 else ''}"
                    f" · cycle {r.cycle_minutes} min · from {r.anchor}"
                    + (f" to {r.end_at}" if r.end_at else "")
                ),
                "screens": [
                    screen(s.page_id, i, active and i == step_index) for i, s in enumerate(r.steps)
                ],
                "steps_total": n,
                "step_index": step_index,
                "live_name": page_names.get(current_page, current_page) if playing else None,
                "next_advance": next_advance,
                "fire_url": url_for("rotations.fire", rotation_id=r.id),
                "play_url": url_for("rotations.play", rotation_id=r.id, step_index=play_next),
                "edit_url": url_for("decks.editor", deck_id=r.id),
                "toggle_url": url_for("rotations.toggle", rotation_id=r.id),
                "delete_url": url_for("rotations.delete", rotation_id=r.id),
                "step_url": None,
            }
        )

    for s in schedules:
        fires_today = (
            _project_fires(s, day_start, day_start + timedelta(hours=24)) if s.enabled else []
        )
        marks = [
            max(0.0, min(100.0, (f.timestamp() - day_start.timestamp()) / 864.0))
            for f in fires_today
        ]
        upcoming = [f for f in fires_today if f.timestamp() >= now_ts]
        if not upcoming and s.enabled:
            tomorrow = _project_fires(
                s, day_start + timedelta(hours=24), day_start + timedelta(hours=48)
            )
            upcoming = tomorrow[:1]
        next_fire = upcoming[0].strftime("%H:%M") if upcoming else None
        cadence = (
            f"every {s.interval_minutes} min"
            if s.type == "interval"
            else f"daily at {s.fires_at.strftime('%H:%M') if s.fires_at else '?'}"
        )
        showing = any(rec[1] == s.page_id for rec in live.values())
        last = (schedule_status.get(s.id) or {}).get("last_fired")
        cards.append(
            {
                "kind": "send",
                "id": s.id,
                "name": s.name,
                "enabled": s.enabled,
                "playing": showing,
                "badge": "Timed send",
                "badge_icon": "clock",
                "meta": f"{page_names.get(s.page_id, s.page_id)} · {cadence}"
                + (f" · last sent {_ago(last, now_ts)}" if _ago(last, now_ts) else ""),
                "screens": [screen(s.page_id, None, showing)],
                "live_name": page_names.get(s.page_id) if showing else None,
                "marks": marks[:48],
                "now_pct": round(now_pct, 2),
                "fires_label": (
                    f"fires {s.fires_at.strftime('%H:%M')}"
                    if s.type == "daily" and s.fires_at
                    else f"every {s.interval_minutes} min"
                ),
                "next_fire": next_fire,
                "fire_url": url_for("schedules.fire", schedule_id=s.id),
                "edit_url": url_for("decks.index", sedit=s.id) + "#schedule-form-card",
                "toggle_url": url_for("schedules.toggle", schedule_id=s.id),
                "delete_url": url_for("schedules.delete", schedule_id=s.id),
                "step_url": None,
                "play_url": None,
            }
        )

    for card in cards:
        card["is_new"] = highlight_id is not None and card["id"] == highlight_id
    cards.sort(key=lambda c: str(c["name"]).lower())

    playing_cards = [c for c in cards if c["playing"]]
    onair = None
    if playing_cards:
        primary = next((c for c in playing_cards if c["kind"] == "cycle"), playing_cards[0])
        detail_bits = []
        if primary.get("live_name"):
            detail_bits.append("showing \u201c" + str(primary["live_name"]) + "\u201d")
        if primary["kind"] == "cycle" and primary.get("step_index") is not None:
            detail_bits.append(f"step {primary['step_index'] + 1} of {primary['steps_total']}")
            if primary.get("next_advance"):
                detail_bits.append(f"advances {primary['next_advance']}")
        live_displays = sum(1 for rec in live.values() if rec[1])
        next_fires = sorted(c["next_fire"] for c in cards if c.get("next_fire"))
        onair = {
            "name": primary["name"],
            "detail": " · ".join(detail_bits),
            "counts": [
                f"{len(cards)} deck{'s' if len(cards) != 1 else ''}",
                f"{live_displays} display{'s' if live_displays != 1 else ''} live",
            ]
            + ([f"next fire {next_fires[0]}"] if next_fires else []),
        }
    return {"cards": cards, "onair": onair, "now_hhmm": now_tz.strftime("%H:%M")}


@bp.get("")
def index() -> str:
    # #167 Phase 3: the one surface for everything a display shows over
    # time. Deck cards render first; schedules + rotations render below as
    # sections (their old pages redirect here), fed from the same helpers
    # their standalone pages used, with prefixed context names so the two
    # sections can't collide.
    from app.rotation_routes import _current_step_for_each
    from app.schedule_routes import _running_state_view as _schedule_state_view
    from app.schedule_routes import (
        _smart_sync_states,
        migration_notice_visible,
    )

    pages = _pages().list()
    # Pure timer decks render in the cycle / timed sections below (they ARE
    # the decommissioned rotations and schedules); cards show the navigable
    # decks (manual and both modes).
    decks = [d for d in _store().all() if d.advance != "timer"]

    # "Help me choose" wizard prefills (#167): the dialog collects intent +
    # details client-side and lands back here with wz_* params; the values
    # seed the existing new-record forms server-side, so the wizard never
    # touches submission paths. Everything is validated and clamped; bad
    # params degrade to the plain page.
    wz_type = request.args.get("wz_type", "")
    prefill_type = wz_type if wz_type in ("interval", "daily") else None
    prefill_name = request.args.get("wz_name", "").strip()[:80]
    prefill_fires_at_dt = None
    wz_time = request.args.get("wz_time", "")
    if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", wz_time):
        hour, minute = (int(part) for part in wz_time.split(":"))
        prefill_fires_at_dt = datetime(2000, 1, 1, hour, minute)
    try:
        prefill_interval = max(1, min(10_080, int(request.args.get("wz_interval", ""))))
    except ValueError:
        prefill_interval = None

    schedules = current_app.config["SCHEDULE_STORE"].all()
    rotations = current_app.config["ROTATION_STORE"].all()
    scheduler = current_app.config["SCHEDULER"]
    schedule_status = scheduler.status()
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
    page_names = {p.id: p.name for p in pages}
    current_step = _current_step_for_each(rotations)
    schedule_pills = {s.id: _schedule_state_view(s, schedule_status.get(s.id)) for s in schedules}
    design = _design_cards(
        nav_decks=decks,
        rotations=rotations,
        schedules=schedules,
        current_step=current_step,
        schedule_status=schedule_status,
        pages=pages,
        highlight_id=request.args.get("hl"),
    )
    return render_template(
        "decks.html",
        decks=decks,
        pages=pages,
        page_names=page_names,
        devices=instances,
        graphs=graphs,
        suggestions=suggestions,
        edit_id=request.args.get("edit"),
        # -- the design-handoff list: one row per deck + on-air summary ---
        cards=design["cards"],
        onair=design["onair"],
        now_hhmm=design["now_hhmm"],
        # -- schedules forms ----------------------------------------------
        schedules=schedules,
        status=schedule_status,
        schedule_running_states=schedule_pills,
        schedule_edit_id=request.args.get("sedit"),
        smart_sync_states=_smart_sync_states(schedules, pages),
        prefill_page=request.args.get("prefill_page", ""),
        prefill_type=prefill_type,
        prefill_interval=prefill_interval,
        prefill_fires_at_dt=prefill_fires_at_dt,
        prefill_name=prefill_name,
        show_migration_notice=migration_notice_visible(),
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
                # Device bindings, so the editor can filter the page library to the
                # dashboards assigned to the chosen display. Empty = unassigned.
                "devices": list(p.device_ids),
                "kind": p.layout_kind,
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

    # "New timer cycle" entry point preselects timer advance (#167).
    advance_default = "timer" if request.args.get("mode") == "timer" else "manual"
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
    dwell_map = (
        {p.page_id: p.dwell_minutes for p in deck.pages if p.dwell_minutes is not None}
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
        # Device-first flow: the primary display drives the page-library filter.
        # Pre-select the deck's first bound device when editing; empty for a new deck.
        "devices": device_meta,
        "primaryDevice": (deck.device_ids[0] if deck and deck.device_ids else ""),
        # Timer advance (Phase 1 of the rotations merge).
        "advance": deck.advance if deck else advance_default,
        "advanceInterval": deck.advance_interval_minutes if deck else 30,
        "advanceAnchor": deck.advance_anchor if deck else "00:00",
        "dwells": dwell_map,
        "returnHome": deck.home_timeout_minutes if deck else 0,
        # Dashboards bound to no display: the "Available pages" chips. Adding one
        # binds it to the deck's display on save.
        "unassigned": [
            {"id": p.id, "name": p.name}
            for p in pages
            if not p.device_ids and p.id not in member_ids
        ],
    }
    return render_template(
        "deck_editor.html",
        deck=deck,
        page_meta=page_meta,
        device_meta=device_meta,
        member_ids=member_ids,
        home_id=editor_state["home"],
        override_map=override_map,
        dwell_map=dwell_map,
        editor_state=editor_state,
        advance_default=advance_default,
        page_names={p.id: p.name for p in pages},
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
    old_dwell = {p.page_id: p.dwell_minutes for p in existing.pages} if existing else {}
    # Per-page conditions are authored in the editor's Page-conditions fold
    # (#167 consolidation); an absent field preserves the stored value so
    # older forms and partial submits never drop them.
    old_conditions = {p.page_id: p.conditions for p in existing.pages} if existing else {}

    def _page_conditions(pid: str) -> list[Any]:
        raw = form.get(f"conditions[{pid}]")
        if raw is None:
            return list(old_conditions.get(pid, []))
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return list(old_conditions.get(pid, []))
        return parsed if isinstance(parsed, list) else list(old_conditions.get(pid, []))

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
        dwell = old_dwell.get(pid)
        raw_dwell = form.get(f"dwell[{pid}]")
        if raw_dwell is not None and raw_dwell != "":
            with contextlib.suppress(ValueError):
                dwell = max(1, min(10_080, int(raw_dwell)))
        elif raw_dwell == "":
            dwell = None
        # model_validate (not model_copy) so authored conditions are checked
        # NOW and bad input flashes, instead of poisoning the stored record.
        try:
            pages.append(
                DeckPage.model_validate(
                    {
                        **base.model_dump(mode="json", exclude_none=True),
                        "refresh_interval_minutes": refresh,
                        "dwell_minutes": dwell,
                        "conditions": _page_conditions(pid),
                    }
                )
            )
        except ValidationError as exc:
            flash(f"Invalid page {pid!r}: {_first_error(exc)}", "error")
            return redirect(
                url_for("decks.editor", deck_id=deck_id) if existing else url_for("decks.index")
            )

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

    advance_raw = form.get("advance") or "manual"
    if advance_raw not in ("manual", "timer", "both"):
        advance_raw = "manual"
    advance = cast(Literal["manual", "timer", "both"], advance_raw)
    try:
        adv_interval = max(1, min(10_080, int(form.get("advance_interval_minutes") or 30)))
    except ValueError:
        adv_interval = 30
    adv_anchor = (form.get("advance_anchor") or "00:00").strip() or "00:00"

    # Advance parity fields (Tier A + B). A missing form field falls back to the
    # existing deck's value (the Advanced fold may omit some) then the model
    # default, so a plain save never clobbers a migrated rotation's config.
    def _adv_int(field: str, lo: int, hi: int, default: int) -> int:
        raw = form.get(field)
        if raw is None:
            return default
        try:
            return max(lo, min(hi, int(raw)))
        except ValueError:
            return default

    adv_end_at = (form.get("advance_end_at") or "").strip() or None
    if "advance_days" in form:
        adv_dow = sorted({int(d) for d in form.getlist("advance_days") if d.isdigit()})
    elif existing is not None:
        adv_dow = list(existing.advance_days_of_week)
    else:
        adv_dow = [0, 1, 2, 3, 4, 5, 6]
    adv_priority = _adv_int(
        "advance_priority", -1000, 1000, existing.advance_priority if existing else 0
    )
    if "advance_smart_sync" in form:
        adv_smart = form.get("advance_smart_sync") in ("on", "true", "1")
    else:
        adv_smart = existing.advance_smart_sync if existing else False
    adv_lead = _adv_int(
        "advance_smart_sync_lead_s", 0, 600, existing.advance_smart_sync_lead_s if existing else 10
    )
    adv_mode_raw = form.get("advance_mode") or (existing.advance_mode if existing else "scheduled")
    adv_mode = cast(
        Literal["scheduled", "priority"],
        adv_mode_raw if adv_mode_raw in ("scheduled", "priority") else "scheduled",
    )
    adv_min_hold = _adv_int(
        "advance_min_hold_minutes", 0, 120, existing.advance_min_hold_minutes if existing else 5
    )

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
            advance=advance,
            advance_interval_minutes=adv_interval,
            advance_anchor=adv_anchor,
            advance_end_at=adv_end_at,
            advance_days_of_week=adv_dow,
            advance_priority=adv_priority,
            advance_smart_sync=adv_smart,
            advance_smart_sync_lead_s=adv_lead,
            advance_mode=adv_mode,
            advance_min_hold_minutes=adv_min_hold,
        )
    except ValidationError as exc:
        flash(f"Invalid deck: {_first_error(exc)}", "error")
        return redirect(
            url_for("decks.editor", deck_id=deck_id) if existing else url_for("decks.index")
        )
    _store().upsert(deck)
    # An unassigned dashboard added to the deck binds to the deck's display, so
    # the "only dashboards bound to this display" invariant holds next time.
    display = deck.device_ids[0] if deck.device_ids else None
    if display:
        for pid in ordered:
            page = _pages().get(pid)
            if page is not None and not page.device_ids:
                _pages().save(page.model_copy(update={"device_ids": [display]}))
    _invalidate(deck)
    flash(f"Deck {deck.name!r} saved.", "ok")
    return redirect(url_for("decks.editor", deck_id=deck.id))


@bp.post("/<deck_id>/step")
def step(deck_id: str) -> Response:
    """Manual stepper for a by-hand deck (design handoff): move each bound
    display one dashboard back or forward through the deck order, promoting
    the pre-warmed frame when one exists. ``dir`` is ``next`` (default) or
    ``prev``."""
    deck = _store().get(deck_id)
    if deck is None or not deck.pages:
        flash(f"No deck with id {deck_id!r}.", "error")
        return redirect(url_for("decks.index"))
    if not deck.device_ids:
        flash("Bind a display to the deck first.", "error")
        return redirect(url_for("decks.index"))
    delta = -1 if request.form.get("dir") == "prev" else 1
    order = [dp.page_id for dp in deck.pages]
    nav = _nav_store()
    pusher = current_app.config.get("PUSH_MANAGER")
    moved = 0
    for device_id in deck.device_ids:
        rec = None
        if nav is not None:
            with contextlib.suppress(Exception):
                rec = nav.get(device_id)
        current = rec.get("page_id") if rec and rec.get("deck_id") == deck.id else None
        try:
            idx = order.index(current) if current is not None else -delta if delta > 0 else 0
        except ValueError:
            idx = 0
        target = order[(idx + delta) % len(order)]
        promoted = False
        if pusher is not None:
            promoter = getattr(pusher, "promote_deck_page", None)
            promoted = callable(promoter) and promoter(device_id, target)
            if not promoted:
                result = pusher.push(
                    target, device_ids={device_id}, respect_quiet_hours=False, source="deck"
                )
                if result.status == "failed":
                    continue
        if nav is not None:
            with contextlib.suppress(Exception):
                nav.set(device_id, deck.id, target)
        moved += 1
    if not moved:
        flash("No display took the step; see the events log.", "error")
    return redirect(url_for("decks.index", hl=deck.id) + f"#udeck-{deck.id}")


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
