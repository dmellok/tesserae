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
import math
import re
import time
from datetime import datetime, timedelta, tzinfo
from typing import Any, Literal, cast

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app.deck_suggest import graph_for_pages, suggest_decks
from app.lineup_authoring import build_lineup
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
    """Composer live-preview URL per dashboard (the design's screen cards).

    ``sent=1`` makes the card show the composition actually pushed for that
    dashboard, which is what its screen is displaying. A re-render can't match
    a dashboard whose output moves on its own (a fractal draws differently
    every time), so re-rendering here shows something the panel never had.

    ``refresh=300`` still covers the fallback path, for a dashboard that has
    never been pushed and therefore has no frame to show: the preview token
    only changes on edits, so without it such a card would show the first
    render forever."""
    from app.composer import page_preview_token, preview_dims

    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    settings = current_app.config.get("SETTINGS_STORE")
    out: dict[str, str] = {}
    for p in pages:
        try:
            token = page_preview_token(p, preview_dims(p, devices_reg, settings))
        except Exception:
            token = ""
        out[p.id] = (
            url_for("composer.compose_preview", page_id=p.id) + f"?v={token}&refresh=300&sent=1"
        )
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


#: Push sources that mean "someone sent this by hand" on the Lineups page.
_MANUAL_PUSH_SOURCES = frozenset(
    {"page", "manual", "file", "url", "webpage", "note", "resend", "onboarding", "companion"}
)


def _frame_origin(
    device_id: str,
    page_id: str,
    *,
    own_id: str,
    nav_rec: dict[str, Any] | None,
    lineup_names: dict[str, str],
) -> str | None:
    """Where the frame a display holds came from, as a short phrase that
    follows "showing <page> from": another lineup by name, a manual push, a
    tap, a schedule, and so on. ``None`` when the log has no push for that
    page on that display, so the caller says only what is showing.

    The nav record wins when it names the page (a deck-driven display), then
    the newest successful push row for the page on this display; a rotation
    or schedule fire is named through the engine row that links to that
    push."""
    if nav_rec and nav_rec.get("page_id") == page_id and nav_rec.get("deck_id"):
        deck_id = str(nav_rec["deck_id"])
        if deck_id == own_id:
            return "an earlier step"
        return lineup_names.get(deck_id) or "another lineup"
    events = current_app.config.get("EVENT_LOG")
    if events is None:
        return None
    try:
        rows = events.list(type="push", target=page_id, statuses=("sent",), limit=25)
    except Exception:
        return None
    push = next(
        (ev for ev in rows if device_id in (ev.extra.get("device_ids") or [])),
        None,
    )
    if push is None:
        return None
    source = push.source
    if source in _MANUAL_PUSH_SOURCES:
        return "a manual push"
    if source == "button":
        return "a tap"
    if source == "home_assistant":
        return "Home Assistant"
    if source == "webhook":
        return "a webhook"
    if source == "page_refresh":
        return "an auto update"
    if source in ("deck", "deck_init"):
        return "another lineup"
    if source in ("rotation", "scheduler"):
        # The engine row that caused this push names the record.
        try:
            engine_rows = events.list(type=source, limit=50)
        except Exception:
            engine_rows = []
        for row in engine_rows:
            if row.extra.get("push_event_id") != push.id:
                continue
            if source == "scheduler":
                name = row.extra.get("schedule_name")
                return f"the {name} schedule" if name else "a schedule"
            if row.target == own_id:
                return "an earlier step"
            name = row.extra.get("rotation_name")
            return str(name) if name else "another rotation"
        return "a schedule" if source == "scheduler" else "another rotation"
    return None


def _mins_label(minutes: int) -> str:
    """``32 min`` / ``1 h 5 min`` / ``2 h``; under a minute reads ``<1 min``."""
    minutes = max(0, int(minutes))
    if minutes < 1:
        return "<1 min"
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _hhmm(epoch: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(epoch, tz=tz).strftime("%H:%M")


def _panel_states(devices: list[Any]) -> dict[str, dict[str, Any]]:
    """What each display holds right now, as far as the server can tell:
    ``device_id -> {page_id, at, verb, next_wake}``.

    A REST display fetches frames itself, so the frame on its glass is the
    one it last served (``last_served_render_for``), stamped when the served
    digest changed: ``verb`` is ``fetched``. When the latest render has not
    been fetched yet the panel still holds the previous one, whose page is
    known only while the grace copy survives; otherwise the page is
    ``None`` and no claim is made. Every other transport has no handover to
    observe, so the current render's publish moment stands in: ``sent``.

    ``next_wake`` is the telemetry prediction for the display's next
    check-in when one exists and is still ahead; there is no guess
    otherwise."""
    push = current_app.config.get("PUSH_MANAGER")
    telemetry = current_app.config.get("DEVICE_TELEMETRY")
    now_ts = time.time()
    out: dict[str, dict[str, Any]] = {}
    for d in devices:
        pull = getattr(d, "transport", "mqtt") == "rest"
        page_id: Any = None
        at: Any = None
        latest_fn = getattr(push, "latest_render_for", None)
        latest = latest_fn(d.id) if callable(latest_fn) else None
        if isinstance(latest, dict):
            if pull:
                served_fn = getattr(push, "last_served_render_for", None)
                served = served_fn(d.id) if callable(served_fn) else None
                if isinstance(served, dict):
                    at = served.get("served_at")
                    if served.get("digest") == latest.get("digest"):
                        page_id = latest.get("page_id")
                    else:
                        prev_fn = getattr(push, "previous_render_for", None)
                        prev = prev_fn(d.id, max_age_s=math.inf) if callable(prev_fn) else None
                        if isinstance(prev, dict) and prev.get("digest") == served.get("digest"):
                            page_id = prev.get("page_id")
            else:
                page_id = latest.get("page_id")
                at = latest.get("timestamp")
        next_wake: float | None = None
        if telemetry is not None:
            try:
                entry = telemetry.get(d.id)
            except Exception:
                entry = None
            predicted = getattr(entry, "predicted_next_wake_at", None)
            if isinstance(predicted, (int, float)) and predicted > now_ts:
                next_wake = float(predicted)
        out[d.id] = {
            "page_id": page_id if isinstance(page_id, str) and page_id else None,
            "at": float(at) if isinstance(at, (int, float)) else None,
            "verb": "fetched" if pull else "sent",
            "next_wake": next_wake,
        }
    return out


def _nav_badge(deck: Deck) -> str:
    """Card badge for a navigable deck.

    ``manual`` only ever moves when someone taps it. ``both`` also runs on a
    timer, so it needs the cadence on the card; saying "By hand" there implies
    the deck is idle when it is not."""
    if deck.advance == "manual":
        return "By hand"
    dwells = {p.effective_dwell_minutes(deck.advance_interval_minutes) for p in deck.pages}
    if len(dwells) <= 1:
        every = max(1, int(next(iter(dwells), deck.advance_interval_minutes) or 0))
        return f"Every {every} min + tap"
    # Mixed per-page dwells: the fallback interval describes nothing the
    # cycle actually does, so badge the full loop instead (#266).
    return f"Loop {max(1, int(deck.advance_cycle_minutes or 0))} min + tap"


def _nav_meta(deck: Deck) -> str:
    count = f"{len(deck.pages)} dashboard{'s' if len(deck.pages) != 1 else ''}"
    if deck.advance == "manual":
        return f"{count} · button, tap, swipe"
    return f"{count} · auto-advance, button, tap, swipe"


def _cycle_fires_on(rotation: Any, day_start: datetime) -> list[datetime]:
    """Projected step starts for a cycle record on the local day that begins
    at ``day_start``: every dwell window the engine would open between the
    anchor and ``end_at`` (or midnight), each one a push to the display.

    Mirrors the gates in ``scheduler._compute_step_state``: day-of-week,
    anchor, ``end_at``; an end earlier than the anchor stops at midnight
    because the engine treats before-anchor as dormant. Conditions and the
    minimum hold can skip windows at run time, so this is a ceiling, the
    same way the schedule rail is."""
    from app.schedule_routes import MAX_PROJECTED_FIRES

    if not rotation.enabled or day_start.weekday() not in rotation.days_of_week:
        return []
    if rotation.cycle_minutes <= 0:
        return []
    hh, mm = (int(part) for part in rotation.anchor.split(":"))
    start = day_start.replace(hour=hh, minute=mm, second=0, microsecond=0)
    end = day_start + timedelta(days=1)
    if rotation.end_at:
        eh, em = (int(part) for part in rotation.end_at.split(":"))
        end_today = day_start.replace(hour=eh, minute=em, second=0, microsecond=0)
        if end_today > start:
            end = end_today
    fires: list[datetime] = []
    t = start
    while t < end and len(fires) < MAX_PROJECTED_FIRES:
        for step in rotation.steps:
            if t >= end:
                break
            fires.append(t)
            t += timedelta(minutes=step.dwell_minutes)
    return fires


#: Ticks the 24h rail draws. A rail is a few hundred pixels wide, so past
#: this the marks stop being separable and only cost DOM.
MAX_RAIL_MARKS = 48


def _thin_marks(marks: list[float]) -> list[float]:
    """Reduce *marks* to at most :data:`MAX_RAIL_MARKS`, spread across the day.

    The rail used to draw ``marks[:48]``. A 3-minute schedule projects ~480
    fires, so the ticks ran out about two hours in and the lane read as "the
    schedule stopped firing" -- while the ``refreshes today`` label beside it
    still showed the full count, so the two disagreed (#166).

    Taking every *n*th mark instead keeps the lane spanning the window it
    covers, which is what the rail is for: the reader is judging *when* the
    schedule fires and roughly how densely, not counting ticks. The count
    label remains the honest total, and it is now consistent with a lane that
    reaches the end of the day.

    The first and last marks are always kept, so the lane starts and ends
    where the schedule does.
    """
    if len(marks) <= MAX_RAIL_MARKS:
        return marks
    step = (len(marks) - 1) / (MAX_RAIL_MARKS - 1)
    thinned = [marks[round(i * step)] for i in range(MAX_RAIL_MARKS)]
    # ``round`` can land twice on the same index at the tail; de-duplicate
    # while keeping order so two ticks never stack on one pixel.
    seen: set[float] = set()
    out: list[float] = []
    for mark in thinned:
        if mark not in seen:
            seen.add(mark)
            out.append(mark)
    return out


def _design_cards(
    *,
    nav_decks: list[Deck],
    rotations: list[Any],
    schedules: list[Any],
    current_step: dict[str, dict[str, Any]],
    schedule_status: dict[str, dict[str, Any]],
    pages: list[Any],
    devices: list[Any],
    deck_devices: dict[str, list[str]] | None = None,
    highlight_id: str | None = None,
) -> dict[str, Any]:
    """View-models for the Lineups page: one row per deck with a
    kind-specific body (screen cards, 24h rail, steppers), grouped per
    display. Returns {"cards": [...], "groups": [...], "now_hhmm": ...}.

    Every timed card carries ``refreshes_today``, the number of pushes its
    timer projects for the local day (#278), and each display section sums
    them so the refresh load per panel is readable at a glance. By-hand
    decks carry ``None``: taps have no schedule to project."""
    from app.schedule_routes import _project_fires
    from app.scheduler import _deck_to_rotation
    from app.tz_resolve import app_timezone

    tz = app_timezone()
    now_tz = datetime.now(tz)
    now_ts = now_tz.timestamp()
    day_start = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
    now_pct = min(100.0, max(0.0, (now_ts - day_start.timestamp()) / 864.0 / 100 * 100))
    page_names = {p.id: p.name for p in pages}
    page_devices = {p.id: list(p.device_ids) for p in pages}
    device_names = {d.id: d.display_name for d in devices}
    deck_devices = deck_devices or {}
    thumbs = _page_thumbs(pages)
    live = _live_map()
    panel_states = _panel_states(devices)
    nav = _nav_store()
    lineup_names = {d.id: d.name for d in nav_decks}
    lineup_names.update({r.id: r.name for r in rotations})
    for deck in _store().all():
        lineup_names.setdefault(deck.id, deck.name)
    origin_cache: dict[tuple[str, str, str], str | None] = {}

    def paused_reason(card: dict[str, Any], device_id: str | None) -> str | None:
        """Why an enabled rotation is not playing on ``device_id``: the
        display holds something else (a manual push, a tap, another lineup,
        a schedule), or the server does not know its frame yet. ``None``
        while the rotation plays, is disabled, or is outside its hours."""
        if not device_id:
            return None
        if not card["enabled"] or card["playing"] or card["planned_page_id"] is None:
            return None
        intended = card["planned_page_id"]
        state = panel_states.get(device_id)
        rec = live.get(device_id)
        held: str | None = None
        if state is not None and state["page_id"] and state["page_id"] != intended:
            held = state["page_id"]
        elif rec is not None:
            held = rec[1]
        if not held:
            return "waiting for the panel"
        if held == intended:
            return None
        nav_rec = None
        if nav is not None:
            try:
                nav_rec = nav.get(device_id)
            except Exception:
                nav_rec = None
        key = (device_id, held, card["id"])
        if key not in origin_cache:
            origin_cache[key] = _frame_origin(
                device_id,
                held,
                own_id=card["id"],
                nav_rec=nav_rec,
                lineup_names=lineup_names,
            )
        origin = origin_cache[key]
        label = f"showing {page_names.get(held, held)}"
        if origin:
            label += f" from {origin}"
        if card["next_advance"]:
            label += f" · resumes {card['next_advance']}"
        return label

    def cycle_for_display(card: dict[str, Any], device_id: str | None) -> dict[str, Any]:
        """A rotation row as one display sees it: the frame on that panel,
        and whether the step the server intends has reached it yet. The
        planned strip marks the intended step live when the panel shows
        it, waiting when it does not; a panel whose page the server cannot
        name raises no alarm. Other kinds pass through unchanged."""
        if card["kind"] != "cycle":
            return card
        state = panel_states.get(device_id) if device_id else None
        intended = card["planned_page_id"]
        on_panel: dict[str, Any] | None = None
        waiting = False
        if state is not None and state["page_id"]:
            pid = state["page_id"]
            at = state["at"]
            on_panel = {
                "page_id": pid,
                "name": page_names.get(pid, pid),
                "thumb": thumbs.get(pid, ""),
                "at_label": f"{state['verb']} {_hhmm(at, tz)}" if at is not None else None,
            }
            waiting = intended is not None and pid != intended
        behind: int | None = None
        start = card["dwell_start_epoch"]
        if waiting and isinstance(start, (int, float)):
            behind = max(0, int((now_ts - start) // 60))
        next_poll = None
        if waiting and state is not None and state["next_wake"] is not None:
            next_poll = _hhmm(state["next_wake"], tz)
        out = dict(card)
        out["screens"] = [
            {**s, "live": s["intended"] and not waiting, "waiting": s["intended"] and waiting}
            for s in card["screens"]
        ]
        out["on_panel"] = on_panel
        out["panel_empty_label"] = (
            "not fetched yet"
            if state is not None and state["verb"] == "fetched"
            else "nothing sent yet"
        )
        out["waiting"] = waiting
        out["behind_minutes"] = behind
        out["behind_label"] = f"{_mins_label(behind)} behind" if behind is not None else None
        out["next_poll_label"] = next_poll
        out["paused_reason"] = paused_reason(card, device_id)
        return out

    def resolve_devices(explicit: list[str], page_ids: list[str]) -> list[str]:
        """Displays a record lands on: its own binding, else the union of
        its member pages' bindings (delivery falls through the same way)."""
        if explicit:
            return list(explicit)
        seen: dict[str, None] = {}
        for pid in page_ids:
            for did in page_devices.get(pid, []):
                seen.setdefault(did, None)
        return list(seen)

    def binding_warning(explicit: list[str], page_ids: list[str]) -> str | None:
        """Delivery gaps worth flagging on the card: an unbound multi-page
        record whose members live on different displays sends each page to
        its own panel, so no display plays the set as a unit; a bound record
        can't render members that are bound to a different display."""
        if not explicit:
            if len(page_ids) > 1 and len(resolve_devices([], page_ids)) > 1:
                return (
                    "These dashboards are on different displays, so no single "
                    "display plays the whole set. Edit it and pick one display."
                )
            return None
        stranded = [
            page_names.get(pid, pid)
            for pid in page_ids
            if page_devices.get(pid) and not (set(page_devices[pid]) & set(explicit))
        ]
        if stranded:
            listed = ", ".join(stranded[:3]) + (" and more" if len(stranded) > 3 else "")
            return (
                f"{listed} belong{'s' if len(stranded) == 1 else ''} to a different "
                "display and cannot show here. Rebind the dashboard or remove it."
            )
        return None

    def screen(
        pid: str,
        index: int | None = None,
        is_live: bool = False,
        has_conditions: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": pid,
            "name": page_names.get(pid, pid),
            "thumb": thumbs.get(pid, ""),
            "index": index,
            "live": is_live,
            # The step the server intends, before any display's view of it
            # splits ``live`` into live-or-waiting (see cycle_for_display).
            "intended": is_live,
            "cond": has_conditions,
        }

    cards: list[dict[str, Any]] = []

    for deck in nav_decks:
        live_page = None
        for device_id in deck.device_ids:
            rec = live.get(device_id)
            if rec and rec[0] == deck.id:
                live_page = rec[1]
                break
        deck_device_ids = resolve_devices(deck.device_ids, [dp.page_id for dp in deck.pages])
        # A "both" deck on the cycle trigger rides the rotation engine, so
        # its timer pushes project the same way a rotation's do.
        timer_refreshes: int | None = None
        if deck.advance == "both" and deck.advance_trigger == "cycle":
            adapted = _deck_to_rotation(deck)
            if adapted is not None:
                timer_refreshes = len(_cycle_fires_on(adapted, day_start))
        cards.append(
            {
                "kind": "nav",
                "id": deck.id,
                "name": deck.name,
                "advance": deck.advance,
                "refreshes_today": timer_refreshes,
                "warning": binding_warning(deck.device_ids, [dp.page_id for dp in deck.pages]),
                "enabled": deck.enabled,
                "playing": live_page is not None,
                # A "both" deck advances on a timer AND accepts taps, so
                # labelling it "By hand" (as this card did unconditionally)
                # reads as "nothing is going to happen on its own" and hides
                # the cadence it is actually running on.
                "badge": _nav_badge(deck),
                "badge_icon": "hand-tap" if deck.advance == "manual" else "clock-clockwise",
                "device_ids": deck_device_ids,
                "device_names": [device_names.get(d, d) for d in deck_device_ids],
                "meta": _nav_meta(deck),
                "screens": [
                    screen(dp.page_id, None, dp.page_id == live_page, bool(dp.conditions))
                    for dp in deck.pages
                ],
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
        rot_device_ids = resolve_devices(r.device_ids, [s.page_id for s in r.steps])
        # The current dwell window: how far through it the rotation is, and
        # how long until the next advance. Both come from the same step
        # state ``next_advance`` does, so the bar and the time agree.
        dwell_start = cur.get("step_started_epoch") if active else None
        dwell_start_label = None
        dwell_pct = None
        next_in_label = None
        if (
            isinstance(dwell_start, (int, float))
            and isinstance(nxt, (int, float))
            and nxt > dwell_start
        ):
            dwell_start_label = _hhmm(dwell_start, tz)
            dwell_pct = round(
                min(100.0, max(0.0, (now_ts - dwell_start) / (nxt - dwell_start) * 100))
            )
            next_in_label = f"in {_mins_label(math.ceil(max(0.0, nxt - now_ts) / 60))}"
        cards.append(
            {
                "kind": "cycle",
                "id": r.id,
                "name": r.name,
                "planned_page_id": current_page,
                "planned_step_index": step_index,
                "dwell_start_epoch": dwell_start if isinstance(dwell_start, (int, float)) else None,
                "dwell_start_label": dwell_start_label,
                "dwell_pct": dwell_pct,
                "next_in_label": next_in_label,
                "warning": binding_warning(r.device_ids, [s.page_id for s in r.steps]),
                "enabled": r.enabled,
                "playing": playing,
                "badge": "Playing · Rotation" if playing else "Rotation",
                "badge_icon": "arrows-clockwise",
                "device_ids": rot_device_ids,
                "device_names": [device_names.get(d, d) for d in rot_device_ids],
                "meta": (
                    f"{n} dashboard{'s' if n != 1 else ''}"
                    f" · cycle {r.cycle_minutes} min · from {r.anchor}"
                    + (f" to {r.end_at}" if r.end_at else "")
                ),
                "screens": [
                    screen(s.page_id, i, active and i == step_index, bool(s.conditions))
                    for i, s in enumerate(r.steps)
                ],
                "steps_total": n,
                "step_index": step_index,
                "live_name": page_names.get(current_page, current_page) if playing else None,
                "next_advance": next_advance,
                "refreshes_today": len(_cycle_fires_on(r, day_start)) if r.enabled else None,
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
        sched_device_ids = resolve_devices(deck_devices.get(s.id, []), [s.page_id])
        cards.append(
            {
                "kind": "send",
                "id": s.id,
                "name": s.name,
                "enabled": s.enabled,
                "playing": showing,
                "badge": "Schedule",
                "badge_icon": "clock",
                "device_ids": sched_device_ids,
                "device_names": [device_names.get(d, d) for d in sched_device_ids],
                "meta": f"{page_names.get(s.page_id, s.page_id)} · {cadence}"
                + (f" · last sent {_ago(last, now_ts)}" if _ago(last, now_ts) else ""),
                "screens": [screen(s.page_id, None, showing, bool(s.conditions))],
                "live_name": page_names.get(s.page_id) if showing else None,
                "marks": _thin_marks(marks),
                "now_pct": round(now_pct, 2),
                "fires_label": (
                    f"fires {s.fires_at.strftime('%H:%M')}"
                    if s.type == "daily" and s.fires_at
                    else f"every {s.interval_minutes} min"
                ),
                "next_fire": next_fire,
                "refreshes_today": len(fires_today) if s.enabled else None,
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
    # The flat list carries each rotation as its first display sees it, so
    # callers reading ``cards`` get the on-panel fields too; the per-display
    # sections below resolve the same row against their own panel.
    cards = [cycle_for_display(c, c["device_ids"][0] if c["device_ids"] else None) for c in cards]

    # One section per display, in registry order; a card targeting several
    # displays appears under each. Displays with nothing lined up are
    # omitted entirely. Empty bindings and bindings to missing displays
    # get separate trailing groups, so retained Lineups remain manageable
    # after their last display is deleted.
    groups: list[dict[str, Any]] = []

    def refresh_total(group_cards: list[dict[str, Any]]) -> int | None:
        counted = [c["refreshes_today"] for c in group_cards if c["refreshes_today"] is not None]
        return sum(counted) if counted else None

    for d in devices:
        dev_cards = [cycle_for_display(c, d.id) for c in cards if d.id in c["device_ids"]]
        if not dev_cards:
            continue
        # "showing" is what the panel holds, the same resolution the
        # on-panel column uses, so the header never contradicts the card
        # beneath it (#280). The nav / latest-render view stands in only
        # when the panel's page is unknown.
        state = panel_states.get(d.id) or {}
        live_pid = state.get("page_id")
        showing_title = None
        if live_pid:
            at = state.get("at")
            if at is not None:
                showing_title = f"{state['verb']} {_hhmm(at, tz)}"
        else:
            rec = live.get(d.id)
            live_pid = rec[1] if rec else None
        groups.append(
            {
                "id": d.id,
                "name": device_names.get(d.id, d.id),
                "icon": d.icon,
                "showing": page_names.get(live_pid, live_pid) if live_pid else None,
                "showing_title": showing_title,
                "thumb": thumbs.get(live_pid, "") if live_pid else "",
                "refreshes_today": refresh_total(dev_cards),
                "cards": dev_cards,
            }
        )
    unbound = [c for c in cards if not c["device_ids"]]
    if unbound:
        groups.append(
            {
                "id": "",
                "name": "Not on a display yet",
                "icon": "plugs",
                "showing": None,
                "thumb": "",
                "refreshes_today": None,
                "cards": unbound,
            }
        )
    unavailable = [
        c
        for c in cards
        if c["device_ids"] and not any(did in device_names for did in c["device_ids"])
    ]
    if unavailable:
        groups.append(
            {
                "id": "",
                "name": "Unavailable displays",
                "icon": "warning-circle",
                "showing": None,
                "thumb": "",
                "refreshes_today": None,
                "cards": unavailable,
            }
        )
    # The IANA name, not just the rendered time: the rail's now-marker ticks
    # client-side, and a browser in a different zone from the configured one
    # would otherwise recompute the mark against its own clock and jump by the
    # offset a minute after load (#165, same class as #143 / #164 / #170).
    tz_name = getattr(tz, "key", "") or ""
    return {
        "cards": cards,
        "groups": groups,
        "now_hhmm": now_tz.strftime("%H:%M"),
        "now_tz_name": tz_name,
    }


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

    # Archived dashboards stay out of the new-record pickers; a lineup member
    # can't be archived, so existing records never lose a page here.
    pages = _pages().list_active()
    # Pure timer decks render as rotation / schedule rows (they ARE the
    # decommissioned rotations and schedules); cards show the navigable
    # decks (manual and both modes).
    all_decks = _store().all()
    decks = [d for d in all_decks if d.advance != "timer"]

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
    # The wizard's escape hatch carries the chosen display too (#300); an
    # unknown id degrades to "every display the dashboard is on".
    wz_device = request.args.get("wz_device", "").strip()
    prefill_device_ids = [wz_device] if any(d.id == wz_device for d in instances) else []
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
        devices=instances,
        deck_devices={d.id: list(d.device_ids) for d in all_decks},
        highlight_id=request.args.get("hl"),
    )
    # The pause switch lives under Settings > Server > Automation because it
    # also stops schedules and buttons; this page only points at it.
    settings = current_app.config.get("SETTINGS_STORE")
    automation_paused = False
    if settings is not None:
        automation_paused = bool((settings.get_section("app") or {}).get("automation_paused"))
    return render_template(
        "decks.html",
        decks=decks,
        pages=pages,
        page_names=page_names,
        devices=instances,
        graphs=graphs,
        suggestions=suggestions,
        edit_id=request.args.get("edit"),
        automation_paused=automation_paused,
        automation_url=url_for("auth.settings_area", area="server") + "#server-automation_paused",
        # -- the Lineups list: one row per deck, grouped per display -------
        cards=design["cards"],
        groups=design["groups"],
        now_hhmm=design["now_hhmm"],
        now_tz_name=design.get("now_tz_name", ""),
        # -- schedules forms ----------------------------------------------
        schedules=schedules,
        status=schedule_status,
        schedule_running_states=schedule_pills,
        schedule_edit_id=request.args.get("sedit"),
        smart_sync_states=_smart_sync_states(schedules, pages),
        prefill_page=request.args.get("prefill_page", ""),
        prefill_type=prefill_type,
        prefill_interval=prefill_interval,
        prefill_device_ids=prefill_device_ids,
        prefill_fires_at_dt=prefill_fires_at_dt,
        prefill_name=prefill_name,
        show_migration_notice=migration_notice_visible(),
    )


def _json_error(msg: str) -> Response:
    resp = jsonify({"ok": False, "error": msg})
    resp.status_code = 400
    return resp


@bp.post("/new")
def create() -> Response:
    # The setup wizard submits with respond=json (fetch) so it can stay on
    # its created screen and hand off to the editor with the new deck id.
    wants_json = request.form.get("respond") == "json"
    try:
        deck = _parse_form(request.form)
    except (ValidationError, ValueError) as exc:
        msg = f"Invalid deck: {_first_error(exc) if isinstance(exc, ValidationError) else exc}"
        if wants_json:
            return _json_error(msg)
        flash(msg, "error")
        return redirect(url_for("decks.index"))
    if not _ID_RE.match(deck.id):
        msg = f"Bad id {deck.id!r} (snake_case only)."
        if wants_json:
            return _json_error(msg)
        flash(msg, "error")
        return redirect(url_for("decks.index"))
    _store().upsert(deck)
    _invalidate(deck)
    flash(f"Deck {deck.name!r} saved.", "ok")
    if wants_json:
        return jsonify(
            {
                "ok": True,
                "id": deck.id,
                "url": url_for("decks.index") + f"#deck-{deck.id}",
                "editor_url": url_for("decks.editor", deck_id=deck.id),
            }
        )
    return redirect(url_for("decks.index") + f"#deck-{deck.id}")


@bp.post("/new/lineup")
def create_lineup() -> Response:
    """Create a Lineup from an authoring intent, the one write path (#204).

    The setup wizard used to post its four buttons to three different
    routes backed by three different stores, so what a record ended up
    being depended on which button made it. All four land here now and
    become a Deck, which the scheduler already runs natively.

    JSON-only (the wizard fetches); the classic per-store forms are
    untouched for the Rotations / Schedules pages that still use them.
    """
    form = request.form
    intent = (form.get("intent") or "").strip().lower()
    name = (form.get("name") or "").strip()
    device_ids = [d for d in form.getlist("device_ids") if d]
    page_ids = [p for p in form.getlist("page_ids") if p]
    dwell: dict[str, int] = {}
    for page_id, raw in zip(page_ids, form.getlist("dwell_minutes"), strict=False):
        with contextlib.suppress(TypeError, ValueError):
            dwell[page_id] = int(raw)
    try:
        interval = int(form.get("interval_minutes") or 30)
    except (TypeError, ValueError):
        interval = 30
    try:
        deck = build_lineup(
            intent=intent,
            lineup_id=_unique_id(_slug_from(name)),
            name=name or "Lineup",
            page_ids=page_ids,
            device_ids=device_ids,
            dwell_minutes=dwell,
            interval_minutes=interval,
            fires_at=(form.get("fires_at") or "").strip() or None,
            anchor=(form.get("anchor") or "00:00").strip() or "00:00",
        )
    except (ValidationError, ValueError) as exc:
        msg = _first_error(exc) if isinstance(exc, ValidationError) else str(exc)
        return _json_error(f"Invalid lineup: {msg}")
    _store().upsert(deck)
    _invalidate(deck)
    # A dashboard picked here that isn't on any display yet binds to this
    # Lineup's display, so a freshly-created Lineup can actually render every
    # step. Same rule the deck editor's save has always applied.
    display = deck.device_ids[0] if deck.device_ids else None
    if display:
        pages = _pages()
        for page in (pages.get(p.page_id) for p in deck.pages):
            if page is not None and not page.device_ids:
                pages.save(page.model_copy(update={"device_ids": [display]}))
    return jsonify(
        {
            "ok": True,
            "id": deck.id,
            "url": url_for("decks.index", hl=deck.id) + f"#udeck-{deck.id}",
            "editor_url": url_for("decks.editor", deck_id=deck.id),
        }
    )


def _merged_deck(existing: Deck, **changes: Any) -> Deck:
    """``existing`` with ``changes`` overlaid, re-validated.

    Forms here are partial: the management form has no advance controls, the
    graph form has no name field. Rebuilding a ``Deck`` from only the posted
    fields resets everything else to its model default, which is how a deck
    set to advance automatically came back as "By hand". Dumping and overlaying
    keeps unposted fields, and still runs validation (unlike ``model_copy``)."""
    data = existing.model_dump()
    data.update(changes)
    return Deck.model_validate(data)


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
        # Overlay onto the stored deck rather than rebuilding it: this form
        # doesn't carry the advance settings, and constructing a fresh Deck
        # silently reset them to the model defaults, turning a timer or both
        # deck back into a manual one on any management save (#194 follow-up).
        # Overlaying also means a field added later can't be dropped here.
        deck = _merged_deck(
            existing,
            name=(form.get("name") or existing.name).strip() or existing.name,
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
        # Docstring says "the management fields are kept", which listing them
        # by hand did not achieve: anything absent from the list (every advance
        # setting) was reset to its default.
        deck = _merged_deck(
            existing,
            pages=pages,
            entry_page_id=existing.entry_page_id if existing.entry_page_id in page_ids else None,
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

    # The page library offers working dashboards only. Members are kept
    # regardless so an existing lineup always renders its own pages.
    current_members = set(deck.page_ids) if deck is not None else set()
    pages = [p for p in _pages().list() if not p.archived or p.id in current_members]
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
                "thumb": url_for("composer.compose_preview", page_id=p.id)
                + f"?v={token}&refresh=300",
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
    # A binding to a display that has since been deleted still needs an
    # option, or the select falls back to "Choose a display" and a plain
    # save would silently unbind the deck. It stays selected (and labelled)
    # until the user picks a live display.
    if deck is not None:
        known = {d["id"] for d in device_meta}
        device_meta.extend(
            {"id": did, "name": f"{did} (unavailable)"}
            for did in deck.device_ids
            if did not in known
        )

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
        "cadence": deck.refresh_interval_minutes if deck else 15,
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
        cadence = max(0, min(1440, int(form.get("refresh_interval_minutes") or 15)))
    except ValueError:
        cadence = 15

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

    fields: dict[str, Any] = {
        "id": deck_id,
        "name": name,
        "enabled": form.get("enabled") in ("on", "true", "1"),
        "device_ids": [d for d in form.getlist("device_ids") if d],
        "pages": pages,
        "entry_page_id": entry,
        "home_page_id": home,
        "home_timeout_minutes": timeout,
        "refresh_interval_minutes": cadence,
        "advance": advance,
        "advance_interval_minutes": adv_interval,
        "advance_anchor": adv_anchor,
        "advance_end_at": adv_end_at,
        "advance_days_of_week": adv_dow,
        "advance_priority": adv_priority,
        "advance_smart_sync": adv_smart,
        "advance_smart_sync_lead_s": adv_lead,
        "advance_mode": adv_mode,
        "advance_min_hold_minutes": adv_min_hold,
    }
    try:
        # This form covers the cycle shape only, so an interval or daily deck
        # opened here would lose its trigger, window and fallback if the model
        # were rebuilt from these fields alone. Overlay when editing.
        deck = _merged_deck(existing, **fields) if existing else Deck(**fields)
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
    # Every editor exit lands back on Lineups, highlighting the saved row.
    return redirect(url_for("decks.index", hl=deck.id) + f"#udeck-{deck.id}")


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
