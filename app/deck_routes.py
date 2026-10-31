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
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
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

from app.clock_text import app_cycle, clock_of, clock_time
from app.deck_suggest import graph_for_pages, suggest_decks
from app.display_color import display_color_mode
from app.lineup_authoring import build_lineup
from app.lineup_binding import (
    Consent,
    apply_consent,
    flash_text,
    guard_change,
    refusal,
)
from app.lineups_view import (
    Clash,
    DashboardInfo,
    DisplayInput,
    FrameInput,
    ItemInfo,
    LineupInput,
    LineupKind,
    LineupsView,
    LineupUrls,
    OnScreenInput,
    PauseInput,
    PublishedInput,
    RuntimeStep,
    StepInput,
    UpcomingInput,
    build_view,
    clash_keys,
    clock_range,
    parse_hhmm,
    resolve_day,
    using_time_cycle,
    window_label,
    window_overlaps_pause,
)
from app.next_update import DeviceTiming, reach_for_device
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


def _page_compositions(pages: list[Any]) -> dict[str, str]:
    """Latest successful composition per dashboard (one batched query)."""
    from app.lineup_thumbs import page_compositions

    return page_compositions(pages)


def _page_thumbs(pages: list[Any], compositions: dict[str, str] | None = None) -> dict[str, str]:
    """Versioned small snapshots, shared across every use of a dashboard.
    Saved thumbnails are keyed by the same composition digest that the
    step's Push now sends."""
    from app.lineup_thumbs import page_thumbs

    return page_thumbs(pages, compositions)


def _page_shapes(pages: list[Any]) -> dict[str, tuple[int, int]]:
    """Width and height each dashboard renders at, so a screen card can keep
    the dashboard's own shape (a portrait panel's card is tall, not a cropped
    landscape box). A dashboard whose size can't be resolved is left out and
    its card falls back to the fixed box."""
    from app.composer import preview_dims

    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    settings = current_app.config.get("SETTINGS_STORE")
    out: dict[str, tuple[int, int]] = {}
    for p in pages:
        try:
            w, h = preview_dims(p, devices_reg, settings)
        except Exception:
            continue
        if w > 0 and h > 0:
            out[p.id] = (w, h)
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


#: How the header names a frame no dashboard explains, by push source.
_FRAME_SOURCE_LABELS = {
    "file": "an uploaded file",
    "url": "an image from a link",
    "webpage": "a webpage",
    "note": "a note",
}
_FRAME_BY_HAND = "a frame sent by hand"
#: A push row names the frame only when it is this close to the render.
_FRAME_SOURCE_WINDOW_S = 300.0


def _frame_state(device: Any) -> tuple[FrameInput | None, bool]:
    """``(frame, nothing_sent)`` for a display no dashboard explains.

    The frame is the composition actually on the glass: what a REST display
    last fetched (its latest render when it never fetched), the latest push
    for every other transport. It is resolved to a composition digest so the
    thumbnail is the same immutable one every card uses; a served frame whose
    composition can no longer be named falls back to its device preview.
    ``nothing_sent`` is true only when no render exists at all."""
    from app.lineup_thumbs import sent_thumb_url

    push = current_app.config.get("PUSH_MANAGER")
    latest_fn = getattr(push, "latest_render_for", None)
    latest = latest_fn(device.id) if callable(latest_fn) else None
    if not isinstance(latest, dict):
        return None, callable(latest_fn)
    at = latest.get("timestamp")
    digest = latest.get("composition_digest")
    preview = latest.get("preview_digest")
    if getattr(device, "transport", "mqtt") == "rest":
        served_fn = getattr(push, "last_served_render_for", None)
        served = served_fn(device.id) if callable(served_fn) else None
        if isinstance(served, dict):
            at = served.get("served_at") or at
            if served.get("digest") == latest.get("digest"):
                preview = served.get("preview_digest") or preview
            else:
                resolve = getattr(push, "composition_for_digest", None)
                digest = resolve(device.id, served["digest"]) if callable(resolve) else None
                preview = served.get("preview_digest")
    thumb_url = sent_thumb_url(digest) or sent_thumb_url(preview)
    label = _FRAME_BY_HAND
    events = current_app.config.get("EVENT_LOG")
    if events is not None:
        try:
            rows = events.list(type="push", statuses=("sent",), limit=25)
        except Exception:
            rows = []
        row = next((ev for ev in rows if device.id in (ev.extra.get("device_ids") or [])), None)
        if (
            row is not None
            and isinstance(at, (int, float))
            and abs(float(at) - row.timestamp) <= _FRAME_SOURCE_WINDOW_S
        ):
            label = _FRAME_SOURCE_LABELS.get(row.source, _FRAME_BY_HAND)
    return FrameInput(
        label=label, thumb_url=thumb_url, at=float(at) if isinstance(at, (int, float)) else None
    ), False


def _frame_pending(device: Any) -> bool:
    """True when a REST display has a render it has not fetched yet: its
    latest digest differs from the last one served (or none was served)."""
    if getattr(device, "transport", "mqtt") != "rest":
        return False
    push = current_app.config.get("PUSH_MANAGER")
    latest_fn = getattr(push, "latest_render_for", None)
    latest = latest_fn(device.id) if callable(latest_fn) else None
    if not isinstance(latest, dict) or not latest.get("digest"):
        return False
    served_fn = getattr(push, "last_served_render_for", None)
    served = served_fn(device.id) if callable(served_fn) else None
    return not isinstance(served, dict) or served.get("digest") != latest.get("digest")


def _heard_from(device: Any) -> tuple[float | None, bool]:
    """``(last heartbeat epoch, recent for its wake interval)``."""
    from app.device_capability import heartbeat_freshness

    status = (current_app.config.get("DEVICE_STATUS") or {}).get(device.id)
    try:
        freshness, _ = heartbeat_freshness(device, status)
    except Exception:
        return None, False
    seen = status.get("received_at") if isinstance(status, dict) else None
    return (float(seen) if isinstance(seen, (int, float)) else None), freshness == "fresh"


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
    """``3:48 PM`` / ``15:48`` in the app's clock style (display text only)."""
    return clock_of(datetime.fromtimestamp(epoch, tz=tz))


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
        connected = False
        try:
            connected = reach_for_device(d, now=now_ts).live
        except Exception:
            connected = False
        if telemetry is not None and not connected:
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
            "connected": connected,
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
    shapes = _page_shapes(pages)
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

    device_objs = {d.id: d for d in devices}
    timings: dict[str, Any] = {}

    def next_update_for(device_id: str | None, push_at: Any) -> dict[str, Any] | None:
        """When this display actually gets the card's next push: the push
        time weighed against when the frame can take it (connected now, or
        its next check-in) and any quiet-hours / pause hold. ``None`` when
        the card has no next push or the display is unknown."""
        device = device_objs.get(device_id) if device_id else None
        if device is None or not isinstance(push_at, (int, float)):
            return None
        try:
            if device.id not in timings:
                timings[device.id] = DeviceTiming(device, now=now_ts)
            return cast("dict[str, Any]", timings[device.id].view(float(push_at)))
        except Exception:
            return None

    def cycle_for_display(card: dict[str, Any], device_id: str | None) -> dict[str, Any]:
        """A rotation row as one display sees it: the frame on that panel,
        and whether the step the server intends has reached it yet. The
        planned strip marks the intended step live when the panel shows
        it, waiting when it does not; a panel whose page the server cannot
        name raises no alarm. Other kinds pass through unchanged."""
        if card["kind"] == "send":
            return {**card, "next_update": next_update_for(device_id, card["next_fire_epoch"])}
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
                "w": shapes.get(pid, (0, 0))[0],
                "h": shapes.get(pid, (0, 0))[1],
                "at_label": f"{state['verb']} {_hhmm(at, tz)}" if at is not None else None,
            }
            waiting = intended is not None and pid != intended
        behind: int | None = None
        start = card["dwell_start_epoch"]
        if waiting and isinstance(start, (int, float)):
            behind = max(0, int((now_ts - start) // 60))
        next_poll = None
        if (
            waiting
            and state is not None
            and state["next_wake"] is not None
            and not state["connected"]
        ):
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
        out["next_update"] = next_update_for(device_id, card["next_advance_epoch"])
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
        return _binding_warning(explicit, page_ids, page_devices, page_names)

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
            "w": shapes.get(pid, (0, 0))[0],
            "h": shapes.get(pid, (0, 0))[1],
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
        next_advance = _hhmm(nxt, tz) if active and isinstance(nxt, (int, float)) else None
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
                "next_advance_epoch": float(nxt)
                if active and isinstance(nxt, (int, float))
                else None,
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
        next_fire = clock_of(upcoming[0]) if upcoming else None
        cadence = (
            f"every {s.interval_minutes} min"
            if s.type == "interval"
            else f"daily at {clock_time(s.fires_at) if s.fires_at else '?'}"
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
                    f"fires {clock_time(s.fires_at)}"
                    if s.type == "daily" and s.fires_at
                    else f"every {s.interval_minutes} min"
                ),
                "next_fire": next_fire,
                "next_fire_epoch": upcoming[0].timestamp() if upcoming else None,
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
        "now_label": clock_of(now_tz),
        "now_tz_name": tz_name,
    }


def _fold_by_default(groups: list[dict[str, Any]]) -> bool:
    """Whether Lineups rows start folded: more than one display has a
    lineup with several items. One busy display keeps today's open look;
    with several, the page is mostly thumbnails and folds to headers."""
    busy = sum(
        1
        for g in groups
        if g.get("id") and any(len(c.get("screens") or []) > 1 for c in g["cards"])
    )
    return busy > 1


# -- Lineups redesign: view-model adapter ------------------------------------
#
# ``app.lineups_view`` is pure; everything below reads the stores and the
# live state and turns them into its inputs.

#: Items listed for a multi-item step (a gallery folder): enough for the peek
#: grid, without walking a folder of thousands of photos into the page.
MAX_STEP_ITEMS = 60


def _lineup_kind(deck: Deck) -> LineupKind:
    if deck.advance == "manual":
        return "manual"
    return deck.advance_trigger


def _lineup_input(
    deck: Deck,
    *,
    runtime: RuntimeStep | None = None,
    playing: bool = False,
    with_urls: bool = True,
    last_fired: float | None = None,
) -> LineupInput:
    """A deck as the view-model's lineup: its window, days and steps in the
    shape its trigger runs them (see ``app.lineups_view``)."""
    kind = _lineup_kind(deck)
    start: str | None
    end: str | None
    if kind == "cycle":
        start, end = deck.advance_anchor, deck.advance_end_at
    elif kind == "interval":
        start, end = deck.advance_window_start, deck.advance_window_end
    elif kind == "daily":
        start, end = deck.advance_fires_at, None
    else:
        start = end = None
    steps = tuple(
        StepInput(
            dashboard_id=p.page_id,
            dwell_minutes=p.effective_dwell_minutes(deck.advance_interval_minutes),
            conditional=bool(p.conditions),
        )
        for p in deck.pages
    )
    urls = LineupUrls()
    if with_urls:
        from app.state.legacy_projections import is_cycle_view_deck, is_timed_view_deck

        if is_cycle_view_deck(deck):
            fire = url_for("rotations.fire", rotation_id=deck.id)
        elif is_timed_view_deck(deck):
            fire = url_for("schedules.fire", schedule_id=deck.id)
        else:
            fire = url_for("decks.push", deck_id=deck.id)
        urls = LineupUrls(
            edit=url_for("decks.editor", deck_id=deck.id),
            toggle=url_for("decks.toggle", deck_id=deck.id),
            delete=url_for("decks.delete", deck_id=deck.id),
            fire=fire,
            push_step=url_for("decks.push_step", deck_id=deck.id),
            window=url_for("decks.edit_window", deck_id=deck.id),
            days=url_for("decks.edit_days", deck_id=deck.id),
            bind=url_for("decks.bind_pages", deck_id=deck.id),
        )
    return LineupInput(
        id=deck.id,
        name=deck.name,
        kind=kind,
        steps=steps,
        enabled=deck.enabled,
        days=tuple(deck.advance_days_of_week),
        start=start,
        end=end,
        interval_minutes=deck.advance_interval_minutes if kind == "interval" else 0,
        runtime=runtime,
        playing=playing,
        urls=urls,
        priority=deck.advance_priority,
        last_fired=last_fired,
        explicit_display=bool(deck.device_ids),
    )


def _display_label(devices_reg: Any, device_id: str) -> str:
    """``Frame (480×800)`` for a display id; the bare id when it is gone."""
    device = devices_reg.get(device_id) if devices_reg is not None else None
    if device is None:
        return device_id
    w, h, _spec = _device_size(device)
    return f"{device.display_name} ({w}×{h})" if w and h else str(device.display_name)


def _deck_displays(deck: Deck, page_devices: dict[str, list[str]]) -> list[str]:
    """Displays a lineup lands on: its own binding, else the union of its
    pages' bindings (delivery falls through the same way)."""
    if deck.device_ids:
        return list(deck.device_ids)
    seen: dict[str, None] = {}
    for p in deck.pages:
        for did in page_devices.get(p.page_id, []):
            seen.setdefault(did, None)
    return list(seen)


def _pause_input(device: Any) -> PauseInput | None:
    from app.quiet_hours import resolve_quiet_hours

    settings = current_app.config.get("SETTINGS_STORE")
    try:
        window = resolve_quiet_hours(
            (settings.get_section("app") if settings else None) or {}, device
        )
    except Exception:
        return None
    if window is None:
        return None
    return PauseInput(
        start_min=window.start.hour * 60 + window.start.minute,
        end_min=window.end.hour * 60 + window.end.minute,
        days=tuple(sorted(window.days)),
        all_day=tuple(sorted(window.all_day)),
    )


def _device_size(device: Any) -> tuple[int, int, str]:
    """Panel width, height and a spec line (``7.5in · 800×480``)."""
    w = h = 0
    name = ""
    try:
        from app.panel import device_panel

        panel = device_panel(device)
        if panel is not None:
            w, h = int(panel.w), int(panel.h)
    except Exception:
        panel = None
    block = getattr(device, "panel", None)
    if isinstance(block, dict):
        if not (w and h):
            w, h = int(block.get("w") or 0), int(block.get("h") or 0)
        name = str(block.get("name") or "")
    size = f"{w}×{h}" if w and h else ""
    spec = " · ".join(x for x in (name, size) if x)
    return w, h, spec


#: folder -> (stamp, total count, first MAX_STEP_ITEMS names). The stamp is
#: the folder's mtime (adding or removing a photo changes it), or a one-minute
#: bucket when the folder path can't be resolved, so a polling page doesn't
#: list a large folder on every refresh.
_GALLERY_CACHE: dict[str, tuple[float, int, tuple[str, ...]]] = {}


def _gallery_files(module: Any, folder: str) -> tuple[int, tuple[str, ...]] | None:
    stamp: float = float(int(time.time() // 60))
    resolve = getattr(module, "_folder_path", None)
    data_dir = getattr(module, "_data_dir", None)
    if callable(resolve) and callable(data_dir):
        try:
            path = resolve(folder, data_dir())
            if path is not None:
                stamp = -float(path.stat().st_mtime_ns)
        except Exception:
            pass
    cached = _GALLERY_CACHE.get(folder)
    if cached is not None and cached[0] == stamp:
        return cached[1], cached[2]
    lister = getattr(module, "list_folder_files", None)
    if not callable(lister):
        return None
    try:
        files = [str(f) for f in lister(folder)]
    except Exception:
        return None
    entry = (stamp, len(files), tuple(files[:MAX_STEP_ITEMS]))
    _GALLERY_CACHE[folder] = entry
    return entry[1], entry[2]


def _step_items(page: Any) -> tuple[int, tuple[ItemInfo, ...]]:
    """Items a dashboard shows one per visit: a page whose only widget with
    a source is a Picture Gallery cell walks that folder's photos. Anything
    else is one item."""
    cells = [c for c in getattr(page, "cells", []) or [] if getattr(c, "plugin", None)]
    galleries = [c for c in cells if c.plugin == "picture_gallery"]
    if len(galleries) != 1:
        return 1, ()
    folder = str(galleries[0].options.get("folder") or "")
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get("picture_gallery") if reg is not None else None
    module = getattr(plugin, "server_module", None) if plugin is not None else None
    listed = _gallery_files(module, folder) if module is not None else None
    if listed is None or listed[0] <= 1:
        return 1, ()
    count, names = listed
    version = getattr(module, "thumb_version", None)
    items: list[ItemInfo] = []
    for i, name in enumerate(names):
        try:
            thumb = url_for(
                "picture_gallery_admin.serve_thumbnail",
                folder=folder or "_root",
                filename=name,
                v=str(version(folder, name)) if callable(version) else None,
            )
        except Exception:
            thumb = ""
        items.append(ItemInfo(i, name, thumb))
    return count, tuple(items)


def _upcoming_inputs(device: Any, now_utc: datetime) -> tuple[UpcomingInput, ...] | None:
    """The scheduler's own projection of the display's next updates
    (``Scheduler.upcoming_for_device``), or ``None`` when it can't be read."""
    from app.quiet_hours import resolve_quiet_hours

    scheduler = current_app.config.get("SCHEDULER")
    fn = getattr(scheduler, "upcoming_for_device", None)
    if not callable(fn):
        return None
    settings = current_app.config.get("SETTINGS_STORE")
    try:
        window = resolve_quiet_hours(
            (settings.get_section("app") if settings else None) or {}, device
        )
        # A week and a day, so a once-a-week lineup still shows up; the
        # projection caps its own walk per record, and the limit caps the rest.
        events = fn(device.id, now=now_utc, hours=8 * 24, limit=12, quiet_window=window)
    except Exception:
        # Logged, then the view falls back to its own schedule model.
        current_app.logger.exception("lineups: projection failed for %s", device.id)
        return None
    return tuple(
        UpcomingInput(
            at=ev.scheduled_at.timestamp(),
            lineup_id=ev.lineup_id,
            dashboard_id=ev.dashboard_id,
        )
        for ev in events
    )


def _published_state(device: Any) -> tuple[PublishedInput | None, bool]:
    """The frame last published for the display (its latest render), which
    a sleeping display picks up at its next check-in, and whether that answer
    is known: ``(None, True)`` means nothing was published, ``(None, False)``
    that the push manager could not be asked."""
    push = current_app.config.get("PUSH_MANAGER")
    latest_fn = getattr(push, "latest_render_for", None)
    if not callable(latest_fn):
        return None, False
    try:
        latest = latest_fn(device.id)
    except Exception:
        current_app.logger.exception("lineups: latest render unreadable for %s", device.id)
        return None, False
    if not isinstance(latest, dict):
        return None, True
    page_id = latest.get("page_id")
    if not isinstance(page_id, str) or not page_id:
        return None, True
    at = latest.get("timestamp")
    return PublishedInput(page_id, float(at) if isinstance(at, (int, float)) else None), True


def build_lineups_view(
    selected_day: str | None = None, display_id: str | None = None
) -> LineupsView:
    """Cached per request (``flask.g``), so a page that asks twice builds
    once. Edit responses build after the write, a fresh request state."""
    from flask import g

    cache: dict[tuple[str | None, str | None], LineupsView] = g.setdefault(
        "_lineups_view_cache", {}
    )
    key = (selected_day, display_id)
    if key not in cache:
        cache[key] = _build_lineups_view(selected_day, display_id)
    return cache[key]


def _time_cycle() -> str:
    """The Lineups page's clock style (``h12`` / ``h23``) from the app-wide
    locale (Settings, else the host's): one page shows every display, so it
    reads in one style rather than each display's own locale."""
    return app_cycle()


def _binding_warning(
    explicit: list[str],
    page_ids: list[str],
    page_devices: dict[str, list[str]],
    page_names: dict[str, str],
) -> str | None:
    """Delivery gaps worth flagging on the card: an unbound multi-page
    record whose members live on different displays sends each page to
    its own panel, so no display plays the set as a unit; a bound record
    can't render members that are bound to a different display."""
    if not explicit:
        if (
            len(page_ids) > 1
            and len({did for pid in page_ids for did in page_devices.get(pid, [])}) > 1
        ):
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


def _build_lineups_view(
    selected_day: str | None = None, display_id: str | None = None
) -> LineupsView:
    """The Lineups page view-model from the live stores (see
    ``app.lineups_view``). ``selected_day`` is the raw ``?day=`` value;
    ``display_id`` builds just that display (and no unassigned lineups)."""
    from app.next_update import Reach, hold_for_device, reach_for_device
    from app.rotation_routes import _current_step_for_each
    from app.tz_resolve import app_timezone

    tz = app_timezone()
    now = datetime.now(tz)
    now_ts = now.timestamp()
    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    devices = [d for d in (devices_reg.all() if devices_reg is not None else []) if d.kind_of]
    if display_id:
        devices = [d for d in devices if d.id == display_id]
    decks = _store().all()
    pages = _pages().list()
    page_by_id = {p.id: p for p in pages}
    page_devices = {p.id: list(p.device_ids) for p in pages}

    rotations = current_app.config["ROTATION_STORE"].all()
    try:
        current = _current_step_for_each(rotations)
    except Exception:
        current = {}
    panel_states = _panel_states(devices)
    live = _live_map()

    known = {d.id for d in devices}
    scheduler = current_app.config.get("SCHEDULER")
    last_fired_map: dict[str, Any] = {}
    status_fn = getattr(scheduler, "status", None)
    if callable(status_fn):
        try:
            last_fired_map = {
                k: v.get("last_fired") for k, v in status_fn().items() if isinstance(v, dict)
            }
        except Exception:
            current_app.logger.exception("lineups: scheduler status unreadable")

    def lands_here(deck: Deck) -> bool:
        if not display_id:
            return True
        return any(t in known for t in _deck_displays(deck, page_devices))

    used: dict[str, None] = {}
    for deck in decks:
        if not lands_here(deck):
            continue
        for p in deck.pages:
            used.setdefault(p.page_id, None)
    for state in panel_states.values():
        if state.get("page_id"):
            used.setdefault(state["page_id"], None)
    used_pages = [page_by_id[pid] for pid in used if pid in page_by_id]
    compositions = _page_compositions(used_pages)
    thumbs = _page_thumbs(used_pages, compositions)
    shapes = _page_shapes(used_pages)
    dashboards: dict[str, DashboardInfo] = {}
    for pid in used:
        page = page_by_id.get(pid)
        count, items = _step_items(page) if page is not None else (1, ())
        w, h = shapes.get(pid, (0, 0))
        bound_ids = tuple(page.device_ids) if page is not None else ()
        dashboards[pid] = DashboardInfo(
            id=pid,
            name=page.name if page is not None else pid,
            missing=page is None,
            bound_ids=bound_ids,
            bound_labels=tuple(_display_label(devices_reg, did) for did in bound_ids),
            thumb_url=thumbs.get(pid, ""),
            composition_digest=compositions.get(pid),
            width=w,
            height=h,
            item_count=count,
            items=items,
        )

    by_display: dict[str, list[LineupInput]] = {d.id: [] for d in devices}
    unassigned: list[LineupInput] = []
    for deck in decks:
        if not lands_here(deck):
            continue
        fired = last_fired_map.get(deck.id)
        last_fired = float(fired) if isinstance(fired, (int, float)) else None
        cur = current.get(deck.id) or {}
        runtime = None
        if cur.get("active") and isinstance(cur.get("step_index"), int):
            since = cur.get("step_started_epoch")
            nxt = cur.get("next_transition_epoch")
            runtime = RuntimeStep(
                step_index=int(cur["step_index"]),
                since=float(since) if isinstance(since, (int, float)) else None,
                next_at=float(nxt) if isinstance(nxt, (int, float)) else None,
            )
        targets = [t for t in _deck_displays(deck, page_devices) if t in known]
        if not targets:
            if not display_id:
                unassigned.append(_lineup_input(deck, runtime=runtime, last_fired=last_fired))
            continue
        for did in targets:
            rec = live.get(did)
            playing = bool(rec and rec[0] == deck.id)
            by_display[did].append(
                _lineup_input(deck, runtime=runtime, playing=playing, last_fired=last_fired)
            )

    inputs: list[DisplayInput] = []
    for d in devices:
        lineups = by_display.get(d.id) or []
        if not lineups:
            continue
        try:
            reach = reach_for_device(d, now=now_ts)
        except Exception:
            reach = Reach("unknown")
        try:
            hold = hold_for_device(d, now=now_ts)
        except Exception:
            hold = None
        state = panel_states.get(d.id) or {}
        on_screen = None
        on_pid: str | None = state.get("page_id")
        # A display that fetches its frames holds what it last fetched; when
        # that can't be named the glass is unknown, and the nav record or the
        # latest render (what the server *meant* it to show) must not stand
        # in for it, or a queued step would read as already delivered.
        if not on_pid and state.get("verb") != "fetched":
            rec = live.get(d.id)
            on_pid = rec[1] if rec else None
        if on_pid:
            on_screen = OnScreenInput(
                dashboard_id=on_pid,
                since=state.get("at"),
                verb=str(state.get("verb") or "sent"),
            )
        frame, nothing_sent = _frame_state(d) if not on_pid else (None, False)
        last_seen, heard = _heard_from(d)
        pending = _frame_pending(d)
        w, h, spec = _device_size(d)
        published, published_known = _published_state(d)
        color = display_color_mode(
            d, current_app.config.get("RENDERER_REGISTRY"), current_app.config.get("SETTINGS_STORE")
        )
        inputs.append(
            DisplayInput(
                id=d.id,
                name=d.display_name,
                width=w,
                height=h,
                icon=d.icon,
                spec=spec,
                reach=reach,
                lineups=tuple(lineups),
                pause=_pause_input(d),
                on_screen=on_screen,
                hold=hold,
                upcoming=_upcoming_inputs(d, datetime.now(UTC)),
                published=published,
                published_known=published_known,
                settings_url=url_for("auth.device_page", instance_id=d.id),
                gamut=color.gamut,
                grey_levels=color.levels,
                dither=color.dither,
                frame=frame,
                nothing_sent=nothing_sent,
                last_seen=last_seen,
                heard=heard,
                pending=pending,
            )
        )
    today = now.weekday()
    view = build_view(
        inputs,
        dashboards,
        now=now,
        selected_day=resolve_day(selected_day, today),
        unassigned=unassigned,
        tz_name=getattr(tz, "key", "") or "",
        time_cycle=_time_cycle(),
    )

    deck_by_id = {deck.id: deck for deck in decks}
    page_names = {p.id: p.name for p in pages}
    for lineup in [*(row for d in view.displays for row in d.lineups), *view.unassigned]:
        deck = deck_by_id[lineup.id]
        warning = _binding_warning(deck.device_ids, deck.page_ids, page_devices, page_names)
        lineup.warnings = [warning] if warning else []
        lineup.display_removed = bool(_deck_displays(deck, page_devices)) and not any(
            t in known for t in _deck_displays(deck, page_devices)
        )
        if deck.advance == "both":
            lineup.mode_label += " + by hand"
    nav = _nav_store()
    lineup_names = {deck.id: deck.name for deck in decks}
    for display in view.displays:
        state = panel_states.get(display.id) or {}
        live_lineup = next(
            (
                row
                for row in display.lineups
                if display.scheduled and row.id == display.scheduled.lineup_id
            ),
            None,
        )
        behind_schedule = bool(
            display.scheduled
            and display.on_screen
            and display.scheduled.dashboard_id != display.on_screen.dashboard_id
        )
        if display.on_screen:
            nav_rec = nav.get(display.id) if nav is not None else None
            origin = _frame_origin(
                display.id,
                display.on_screen.dashboard_id,
                own_id="",  # The header names the source, rather than comparing one row to another.
                nav_rec=nav_rec,
                lineup_names=lineup_names,
            )
            if origin:
                display.show_suffix = f"from {origin}"
                live_runtime = current.get(live_lineup.id, {}) if live_lineup else {}
                resume = live_runtime.get("next_transition_epoch")
                if behind_schedule and isinstance(resume, (int, float)):
                    display.show_suffix += f" · resumes {_hhmm(resume, tz)}"
        diagnostics = [display.show_title]
        if behind_schedule and live_lineup:
            live_runtime = current.get(live_lineup.id) or {}
            since = live_runtime.get("step_started_epoch")
            if isinstance(since, (int, float)):
                diagnostics.append(f"{max(0, int((now_ts - since) // 60))} min behind")
            poll = state.get("next_wake")
            if not state.get("connected") and isinstance(poll, (int, float)):
                diagnostics.append(f"poll ≈{_hhmm(poll, tz)}")
        display.status_title = " · ".join(diagnostics)
    return view


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
    # The redesigned page renders from one view-model; a failure building it
    # must not take the page down while the classic list still renders.
    lineups_view: dict[str, Any] | None
    try:
        lineups_view = build_lineups_view(request.args.get("day")).to_dict()
    except Exception:
        current_app.logger.exception("lineups view-model failed")
        lineups_view = None
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
        now_label=design["now_label"],
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
        # -- Lineups redesign view-model (app.lineups_view) ----------------
        lineups_view=lineups_view,
    )


def _json_error(msg: str) -> Response:
    resp = jsonify({"ok": False, "error": msg})
    resp.status_code = 400
    return resp


# -- Lineups redesign: JSON view + inline edits --------------------------------


@bp.get("/view.json")
def view_json() -> Response:
    """The Lineups view-model (``app.lineups_view``) as JSON, for the day
    picker and live refresh. ``?day=`` is a weekday ``0``-``6`` (``day=1`` is
    Tuesday), ``today``, ``tomorrow`` or an offset (``day=%2B1``; or use
    ``?offset=1``, which needs no encoding); ``?display=<id>`` keeps one
    display. A failure answers JSON ``{ok: false, error}`` with 500."""
    only = (request.args.get("display") or "").strip() or None
    day = request.args.get("day")
    offset = (request.args.get("offset") or "").strip()
    if offset:
        if not offset.lstrip("+-").isascii() or not offset.lstrip("+-").isdigit():
            return _edit_error_json(_EditError("offset must be a whole number of days."))
        day = offset if offset[0] in "+-" else "+" + offset
    try:
        data = build_lineups_view(day, display_id=only).to_dict()
    except Exception:
        current_app.logger.exception("lineups view.json failed")
        resp = jsonify({"ok": False, "error": "The Lineups view could not be built."})
        resp.status_code = 500
        return resp
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-store"
    return resp


class _EditError(ValueError):
    """An inline edit the server refuses: ``status`` 400 (bad input), 404
    (unknown lineup) or 409 (clashes with another lineup)."""

    def __init__(self, message: str, status: int = 400, lineup_id: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.lineup_id = lineup_id


_UNSET: Any = object()


def _json_body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _clean_time(raw: Any, what: str, deck_id: str | None = None) -> str:
    """``HH:MM`` (``24:00`` allowed) or an ``_EditError`` naming the lineup."""
    if not isinstance(raw, str) or parse_hhmm(raw) is None:
        raise _EditError(f"{what} must be a time like 06:30.", 400, deck_id)
    h, m = raw.strip().split(":")
    return f"{int(h):02d}:{int(m):02d}"


def _edited_deck(deck: Deck, *, start: Any = _UNSET, end: Any = _UNSET, days: Any = None) -> Deck:
    """``deck`` with a new time window and/or weekday set, in the fields its
    trigger runs on. Raises ``_EditError`` for input the deck can't take."""
    kind = _lineup_kind(deck)
    changes: dict[str, Any] = {}
    if start is not _UNSET or end is not _UNSET:
        if kind == "manual":
            raise _EditError(f"{deck.name} runs by hand; it has no time window.", 400, deck.id)
        if kind == "daily":
            s = _clean_time(
                start if start is not _UNSET else deck.advance_fires_at, "Start", deck.id
            )
            if s == "24:00":
                raise _EditError("Start must be before 24:00.", 400, deck.id)
            changes["advance_fires_at"] = s
        elif kind == "cycle":
            s = _clean_time(start if start is not _UNSET else deck.advance_anchor, "Start", deck.id)
            raw_end = end if end is not _UNSET else deck.advance_end_at
            e = None if raw_end in (None, "") else _clean_time(raw_end, "End", deck.id)
            s_min = parse_hhmm(s) or 0
            e_min = 1440 if e is None else (parse_hhmm(e) or 0)
            if s == "24:00":
                raise _EditError("Start must be before 24:00.", 400, deck.id)
            if e_min <= s_min:
                raise _EditError("End must be after start.", 400, deck.id)
            changes["advance_anchor"] = s
            changes["advance_end_at"] = None if e in (None, "24:00") else e
        else:  # interval: a window that may wrap past midnight
            raw_start = start if start is not _UNSET else deck.advance_window_start
            raw_end = end if end is not _UNSET else deck.advance_window_end
            if raw_start in (None, "") and raw_end in (None, ""):
                changes["advance_window_start"] = None
                changes["advance_window_end"] = None
            else:
                # An unset end runs to the end of the day (the scheduler reads
                # it as 23:59:59), so "24:00" is stored as no end; "00:00"
                # would wrap past midnight instead.
                s_val = (
                    None if raw_start in (None, "") else _clean_time(raw_start, "Start", deck.id)
                )
                e_val = None if raw_end in (None, "") else _clean_time(raw_end, "End", deck.id)
                if s_val == "24:00":
                    raise _EditError("Start must be before 24:00.", 400, deck.id)
                if e_val == "24:00":
                    e_val = None
                if s_val is not None and e_val is not None and s_val == e_val:
                    raise _EditError("Start and end must differ.", 400, deck.id)
                changes["advance_window_start"] = s_val
                changes["advance_window_end"] = e_val
    if days is not None:
        if not isinstance(days, list) or not all(
            isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days
        ):
            raise _EditError("Days must be a list of weekdays 0 (Mon) to 6 (Sun).", 400, deck.id)
        if not days:
            raise _EditError(f"{deck.name} needs at least one day.", 400, deck.id)
        changes["advance_days_of_week"] = sorted(set(days))
    if not changes:
        return deck
    try:
        return _merged_deck(deck, **changes)
    except ValidationError as exc:
        raise _EditError(_first_error(exc), 400, deck.id) from exc


def _check_batch(
    before: dict[str, Deck],
    after: dict[str, Deck],
    order: list[str],
    page_devices: dict[str, list[str]],
) -> None:
    """Refuse (``_EditError`` 409) a batch that makes two lineups on a shared
    display meet. The clash sets of the whole snapshot before and the whole
    snapshot after are compared, so two windows that both move into each
    other are caught, while a clash that already existed (the scheduler
    settles overlaps by priority) does not block an unrelated edit."""
    displays = {d.id: set(_deck_displays(d, page_devices)) for d in after.values()}
    touched: set[str] = set()
    for deck_id in order:
        touched |= displays.get(deck_id, set())
    relevant = {i for i, ds in displays.items() if ds & touched}

    def shares(a: str, b: str) -> bool:
        return bool(displays.get(a, set()) & displays.get(b, set()))

    def inputs(snapshot: dict[str, Deck]) -> list[LineupInput]:
        return [
            _lineup_input(d, with_urls=False)
            for i, d in snapshot.items()
            if i in relevant and d.advance != "manual"
        ]

    final = {lu.id: lu for lu in inputs(after)}
    new = clash_keys(list(final.values()), shares) - clash_keys(inputs(before), shares)
    for deck_id in order:
        mine = sorted(k for k in new if deck_id in k[:2])
        if not mine:
            continue
        a, b, day = mine[0]
        clash = Clash(final[b if a == deck_id else a], day)
        added = set(after[deck_id].advance_days_of_week) - set(before[deck_id].advance_days_of_week)
        msg = clash.message_for_day() if day in added else clash.message_for_window()
        raise _EditError(msg, 409, deck_id)


def _pause_warnings(deck: Deck, page_devices: dict[str, list[str]]) -> list[str]:
    """One warning per display whose quiet hours a lineup's run falls in."""
    targets = set(_deck_displays(deck, page_devices))
    candidate = _lineup_input(deck, with_urls=False)
    warnings: list[str] = []
    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    for device in devices_reg.all() if devices_reg is not None else []:
        if device.id not in targets:
            continue
        pause = _pause_input(device)
        if pause is not None and window_overlaps_pause(pause, candidate):
            label = clock_range(pause.start_min, pause.end_min)
            warnings.append(
                f"Part of {deck.name} ({window_label(candidate)}) falls in "
                f"{device.display_name}'s Quiet hours ({label}); nothing is pushed then."
            )
    return warnings


def _edit_result(
    saved: list[Deck], warnings: list[str], page_devices: dict[str, list[str]], day: Any
) -> Response:
    touched: set[str] = set()
    for deck in saved:
        touched.update(_deck_displays(deck, page_devices))
    displays: list[dict[str, Any]] = []
    refresh_warning: str | None = None
    try:
        view = build_lineups_view(str(day) if day is not None else None).to_dict()
        displays = [d for d in view["displays"] if d["id"] in touched]
    except Exception:
        # The edit is saved; say so rather than answer 500 and invite a retry.
        current_app.logger.exception("lineups: view rebuild after an edit failed")
        refresh_warning = "Saved, but the page could not be refreshed. Reload to see it."
    return jsonify(
        {
            "ok": True,
            "warning": refresh_warning,
            "ids": [d.id for d in saved],
            "lineups": [
                {
                    "id": d.id,
                    "window": window_label(_lineup_input(d, with_urls=False)),
                    "days": sorted(d.advance_days_of_week),
                }
                for d in saved
            ],
            "warnings": warnings,
            "pause_overlap": bool(warnings),
            "displays": displays,
        }
    )


def _edit_error_json(exc: _EditError) -> Response:
    resp = jsonify({"ok": False, "error": str(exc), "id": exc.lineup_id})
    resp.status_code = exc.status
    return resp


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_weekday(value: Any, what: str, deck_id: str | None) -> int:
    """An int weekday 0-6: type first (``"1"``, ``True``, ``[]`` refused),
    then range."""
    if not _is_int(value):
        raise _EditError(f"{what} must be a whole number 0 (Mon) to 6 (Sun).", 400, deck_id)
    if not 0 <= value <= 6:
        raise _EditError(f"{what} must be 0 (Mon) to 6 (Sun).", 400, deck_id)
    return int(value)


_EDIT_FIELDS = frozenset({"id", "start", "end", "days", "toggle_day", "on"})


def _validated_edit(raw: Any, *, require_on: bool = False) -> dict[str, Any]:
    """One edit with every field type-checked, then range-checked, before
    anything reads or changes a lineup. ``require_on`` makes a day toggle
    say which way (``on``), so a retried request can't flip it back."""
    if not isinstance(raw, dict):
        raise _EditError("Each edit must be an object.")
    deck_id = raw.get("id")
    if not isinstance(deck_id, str) or not deck_id:
        raise _EditError("Each edit needs a lineup id.")
    unknown = sorted(str(k) for k in raw if k not in _EDIT_FIELDS)
    if unknown:
        raise _EditError(f"Unknown field {unknown[0]!r}.", 400, deck_id)
    for key in ("start", "end"):
        if key in raw and raw[key] is not None and not isinstance(raw[key], str):
            raise _EditError(f"{key.title()} must be a time like 06:30.", 400, deck_id)
    if raw.get("days") is not None:
        days = raw["days"]
        if not isinstance(days, list):
            raise _EditError("Days must be a list of weekdays 0 (Mon) to 6 (Sun).", 400, deck_id)
        for d in days:
            _check_weekday(d, "Each day", deck_id)
    if "toggle_day" in raw:
        if raw.get("days") is not None:
            raise _EditError("Send days or a day to toggle, not both.", 400, deck_id)
        _check_weekday(raw["toggle_day"], "Day", deck_id)
    if require_on and "toggle_day" in raw and not isinstance(raw.get("on"), bool):
        raise _EditError("A day toggle in a batch needs 'on': true or false.", 400, deck_id)
    if "on" in raw:
        if "toggle_day" not in raw:
            raise _EditError("'on' goes with a day to toggle.", 400, deck_id)
        if raw["on"] is not None and not isinstance(raw["on"], bool):
            raise _EditError("'on' must be true or false.", 400, deck_id)
    return dict(raw)


def _body_view_day(body: dict[str, Any], *, day_alias: bool) -> Any:
    """The selected day for the refreshed view: ``view_day`` on every edit
    route; ``day`` is accepted as the same thing where it isn't the weekday
    being toggled (``/window`` and ``/edits``, not ``/days``)."""
    if "view_day" in body:
        return body["view_day"]
    return body.get("day") if day_alias else None


def _view_day(raw: Any) -> str | None:
    """The selected day for the refreshed view in an edit response: absent,
    a weekday 0-6, or a word/offset ``resolve_day`` reads."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return str(_check_weekday(raw, "Day", None))


def _apply_edits(edits: list[Any], day: Any, *, require_on: bool = False) -> Response:
    """Apply a batch of edits as one transaction under the deck store's
    lock: read, validate every edit against the state after all of them,
    write. Any refusal saves nothing, and a concurrent edit is either fully
    before or fully after this one (never lost, never validated against a
    stale read)."""
    try:
        checked = [_validated_edit(e, require_on=require_on) for e in edits]
        view_day = _view_day(day)
    except _EditError as exc:
        return _edit_error_json(exc)
    page_devices = {p.id: list(p.device_ids) for p in _pages().list()}
    cycle = _time_cycle()

    def plan(
        decks: dict[str, Deck],
    ) -> tuple[list[Deck], tuple[list[Deck], list[Deck], list[str]]]:
        after = dict(decks)
        order: list[str] = []
        for edit in checked:
            deck_id = str(edit["id"])
            current = after.get(deck_id)
            if current is None:
                raise _EditError(f"No lineup with id {deck_id!r}.", 404, deck_id or None)
            days = edit.get("days")
            if days is None and "toggle_day" in edit:
                # A single-day toggle reads the days inside the transaction,
                # so two quick toggles both land.
                d = edit["toggle_day"]
                have = set(current.advance_days_of_week)
                on = edit.get("on")
                turn_on = (d not in have) if on is None else bool(on)
                days = sorted(have | {d}) if turn_on else sorted(have - {d})
            after[deck_id] = _edited_deck(
                current,
                start=edit.get("start", _UNSET),
                end=edit.get("end", _UNSET),
                days=days,
            )
            if deck_id not in order:
                order.append(deck_id)
        warnings: list[str] = []
        with using_time_cycle(cycle):
            _check_batch(decks, after, order, page_devices)
            for deck_id in order:
                warnings.extend(_pause_warnings(after[deck_id], page_devices))
        changed = [after[i] for i in order if after[i] != decks[i]]
        return changed, (changed, [after[i] for i in order], warnings)

    try:
        _changed, edited, warnings = _store().transaction(plan)
    except _EditError as exc:
        return _edit_error_json(exc)
    # Window and day edits change when a lineup plays, never its pages, so
    # the warmed frames and nav positions stay valid: no ``_invalidate``.
    return _edit_result(edited, warnings, page_devices, view_day)


@bp.post("/<deck_id>/window")
def edit_window(deck_id: str) -> Response:
    """Inline edit of a lineup's time window. JSON ``{"start": "HH:MM",
    "end": "HH:MM" | null}`` (``end`` ``24:00`` or null = until midnight
    for a rotation; ignored for a daily push). Refuses (409) a window that
    meets another lineup of the same display on a shared day; a window in
    quiet hours saves with a warning."""
    body = _json_body()
    edit: dict[str, Any] = {"id": deck_id}
    for key in ("start", "end"):
        if key in body:
            edit[key] = body[key]
    if "start" not in edit and "end" not in edit:
        return _edit_error_json(_EditError("Send a start and/or end time.", 400, deck_id))
    return _apply_edits([edit], _body_view_day(body, day_alias=True))


@bp.post("/<deck_id>/days")
def edit_days(deck_id: str) -> Response:
    """Inline edit of a lineup's weekdays. JSON ``{"days": [0, 1, 2]}`` sets
    them; ``{"day": 3, "on": true}`` toggles one (``on`` omitted flips it).
    At least one day must remain; a day that clashes is refused (409)."""
    body = _json_body()
    edit: dict[str, Any] = {"id": deck_id}
    if body.get("days") is not None:
        edit["days"] = body["days"]
    elif "day" in body:
        # Passed through untouched; _validated_edit refuses "1", true, [] and
        # a non-boolean "on" before anything changes.
        edit["toggle_day"] = body["day"]
        if "on" in body:
            edit["on"] = body["on"]
    else:
        return _edit_error_json(_EditError("Send days or a day to toggle.", 400, deck_id))
    return _apply_edits([edit], _body_view_day(body, day_alias=False))


@bp.post("/edits")
def save_edits() -> Response:
    """The pending-edits Save: JSON ``{"edits": [{"id", "start"?, "end"?,
    "days"?}, ...]}``. Every edit is checked against the others (two moved
    windows may only clash with each other), then all save in one write or
    none do."""
    body = _json_body()
    edits = body.get("edits")
    if not isinstance(edits, list) or not edits:
        return _edit_error_json(_EditError("Send a non-empty list of edits."))
    return _apply_edits(edits, _body_view_day(body, day_alias=True), require_on=True)


@bp.post("/<deck_id>/push-step")
def push_step(deck_id: str) -> Response:
    """Push one dashboard of a lineup to its display now (the step tile's
    "Push now"). JSON ``{"index": n}`` or ``{"page_id": id}``, optional
    ``composition_digest`` pins the shown upright composition, and optional
    ``display_id`` (else every display the lineup lands on). Same path and
    rules as a manual send from the Send page: force-published, no
    coalescing, not held by quiet hours. The lineup's own schedule is not
    changed; its next advance replaces the frame as usual."""
    from app.next_update import DeviceTiming
    from app.push_delivery import delivery_notices, flash_delivery
    from app.send_routes import _run_in_background

    plain_form = (
        not request.is_json
        and request.headers.get("X-Requested-With") != "XMLHttpRequest"
        and "application/json" not in request.headers.get("Accept", "")
    )

    def fail(err: _EditError) -> Response:
        if plain_form:
            flash(str(err), "error")
            return redirect(url_for("decks.index", hl=deck_id))
        return _edit_error_json(err)

    body: dict[str, Any] = _json_body()
    if not body and request.form:
        # The no-JS form posts strings; only a plain digit string is an index.
        body = dict[str, Any](request.form.to_dict())
        if isinstance(body.get("index"), str) and body["index"].isdigit():
            body["index"] = int(body["index"])
    digest = body.get("composition_digest")
    if digest is not None and (
        not isinstance(digest, str) or (digest != "" and not re.fullmatch(r"[0-9a-fA-F]+", digest))
    ):
        return fail(_EditError("composition_digest must be a hex digest.", 400, deck_id))
    for key in ("page_id", "display_id"):
        if body.get(key) is not None and not isinstance(body[key], str):
            return fail(_EditError(f"{key} must be a string.", 400, deck_id))
    if "index" in body and body["index"] is not None and not _is_int(body["index"]):
        return fail(_EditError("index must be a whole number.", 400, deck_id))
    deck = _store().get(deck_id)
    if deck is None:
        return fail(_EditError(f"No lineup with id {deck_id!r}.", 404, deck_id))
    page_id = str(body.get("page_id") or "")
    if not page_id:
        raw_index = body.get("index")
        if not isinstance(raw_index, int) or isinstance(raw_index, bool):
            return fail(_EditError("Send a step index or page_id.", 400, deck_id))
        index = raw_index
        if not 0 <= index < len(deck.pages):
            return fail(_EditError(f"Step {index + 1} doesn't exist.", 400, deck_id))
        page_id = deck.pages[index].page_id
    elif page_id not in deck.page_ids:
        return fail(_EditError("That dashboard isn't in this lineup.", 400, deck_id))
    page_devices = {p.id: list(p.device_ids) for p in _pages().list()}
    devices_reg = current_app.config.get("DEVICE_REGISTRY")
    known = {d.id: d for d in (devices_reg.all() if devices_reg is not None else [])}
    targets = [t for t in _deck_displays(deck, page_devices) if t in known]
    only = str(body.get("display_id") or "")
    if only:
        if only not in targets:
            return fail(_EditError("This lineup doesn't play on that display.", 400, deck_id))
        targets = [only]
    if not targets:
        return fail(_EditError("Bind a display to this lineup first.", 400, deck_id))
    pusher = current_app.config.get("PUSH_MANAGER")
    if pusher is None:
        return fail(_EditError("Push pipeline not ready.", 503, deck_id))
    page = _pages().get(page_id)
    name = page.name if page is not None else page_id
    composition: bytes | None = None
    if digest:
        events = current_app.config.get("EVENT_LOG")
        rows = (
            events.list(
                type="push",
                target=page_id,
                statuses=("sent", "ok"),
                limit=events.count(type="push"),
            )
            if events is not None
            else []
        )
        if not any((row.extra.get("composition_digest") or row.digest) == digest for row in rows):
            return fail(
                _EditError("That composition does not belong to this dashboard.", 409, deck_id)
            )
        with contextlib.suppress(FileNotFoundError):
            # Read on the request thread so GC cannot evict it between
            # accepting the click and the background send. Never read an artifact.
            composition = (Path(current_app.config["RENDERS_DIR"]) / f"{digest}.png").read_bytes()
    fresh = composition is None

    def send_step() -> None:
        if composition is None:
            pusher.push(page_id, device_ids=set(targets), bypass_coalesce=True, force_publish=True)
        else:
            # History's normal per-device transform handles resizing and
            # physical rotation. Resolve each target's own panel dimensions.
            for did in targets:
                pusher.republish_composition(composition, page_id=page_id, device_ids={did})

    _run_in_background(send_step, label=f"lineup-step:{deck_id}:{page_id}")
    now_ts = time.time()
    lands: list[dict[str, Any]] = []
    for did in targets:
        label = "lands now"
        try:
            view = DeviceTiming(known[did], now=now_ts).view(now_ts)
            if view.get("mode") != "connected" and view.get("when"):
                label = f"lands {view['when_display']}"
        except Exception:
            label = "lands now"
        lands.append({"display_id": did, "name": known[did].display_name, "lands": label})
    first = lands[0]
    # A sleeping frame only fetches the step at its next check-in: say so
    # rather than "lands now", or the push looks like it did nothing.
    delivery = delivery_notices(targets, now=now_ts)
    first_notice = next((n for n in delivery if n["device_id"] == first["display_id"]), None)
    if digest and fresh:
        message = "Sent a fresh render, the earlier picture is no longer stored"
    elif first_notice is not None and first_notice["mode"] != "live":
        message = f"Queued {name} for {first['name']}. {first_notice['text']}"
    else:
        message = f"Pushed {name} to {first['name']}, {first['lands']}."
    if plain_form:
        queued = (
            first_notice is not None and first_notice["mode"] != "live" and not (digest and fresh)
        )
        flash(f"Pushed {name} to {first['name']}." if queued else message, "success")
        flash_delivery(targets, now=now_ts)
        return redirect(url_for("decks.index", hl=deck_id))
    return jsonify(
        {
            "ok": True,
            "queued": True,
            "id": deck_id,
            "page_id": page_id,
            "name": name,
            "displays": lands,
            "lands": first["lands"],
            "message": message,
            "fresh": fresh,
            "delivery": delivery,
        }
    )


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
    consent = Consent.from_form(request.form)
    stranded = guard_change(deck.device_ids, deck.page_ids, consent)
    if stranded:
        if wants_json:
            return refusal(deck.device_ids, stranded)
        flash(flash_text(deck.device_ids, stranded), "error")
        return redirect(url_for("decks.index"))
    _store().upsert(deck)
    apply_consent(deck.device_ids, deck.page_ids, consent)
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
    consent = Consent.from_form(form)
    stranded = guard_change(deck.device_ids, deck.page_ids, consent)
    if stranded:
        return refusal(deck.device_ids, stranded)
    _store().upsert(deck)
    apply_consent(deck.device_ids, deck.page_ids, consent)
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
    consent = Consent.from_form(form)
    stranded = guard_change(
        deck.device_ids,
        deck.page_ids,
        consent,
        old_displays=existing.device_ids,
        old_pages=existing.page_ids,
    )
    if stranded:
        flash(flash_text(deck.device_ids, stranded), "error")
        return redirect(url_for("decks.index", edit=deck_id) + f"#deck-{deck_id}")
    _store().upsert(deck)
    apply_consent(deck.device_ids, deck.page_ids, consent)
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
    consent = Consent.from_form(request.form)
    stranded = guard_change(
        deck.device_ids,
        deck.page_ids,
        consent,
        old_displays=existing.device_ids,
        old_pages=existing.page_ids,
    )
    if stranded:
        flash(flash_text(deck.device_ids, stranded), "error")
        return redirect(url_for("decks.index", edit=deck_id) + f"#deck-{deck_id}")
    _store().upsert(deck)
    apply_consent(deck.device_ids, deck.page_ids, consent)
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
            dims = preview_dims(p, devices_reg, settings)
            token = page_preview_token(p, dims)
        except Exception:
            dims = (0, 0)
            token = ""
        page_meta.append(
            {
                "w": dims[0],
                "h": dims[1],
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

    # Steps whose dashboard was deleted still belong to the lineup. List them,
    # flagged, in their own position so they can be seen and removed; hiding
    # them would silently keep the step on every save.
    library = {p.id for p in pages}
    for dp in deck.pages if deck is not None else []:
        if dp.page_id not in library and not any(m["id"] == dp.page_id for m in page_meta):
            page_meta.append(
                {
                    "w": 0,
                    "h": 0,
                    "id": dp.page_id,
                    "name": "Missing dashboard (deleted)",
                    "thumb": "",
                    "devices": [],
                    "kind": "",
                    "missing": True,
                }
            )

    device_meta = []
    touch_bound = False
    if devices_reg is not None:
        for d in devices_reg.all():
            if d.kind_of is None:
                continue
            dw, dh, _spec = _device_size(d)
            device_meta.append({"id": d.id, "name": d.display_name, "w": dw, "h": dh})
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
        page_names={m["id"]: m["name"] for m in page_meta},
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
    # The editor prompts before it submits; a save that still carries steps
    # bound to another display (no JS, a stale tab, a crafted POST) is refused
    # rather than stored as a lineup that can never render.
    consent = Consent.from_form(form)
    stranded = guard_change(deck.device_ids, ordered, consent)
    if stranded:
        flash(flash_text(deck.device_ids, stranded), "error")
        return redirect(
            url_for("decks.editor", deck_id=deck_id)
            if existing
            else (request.referrer or url_for("decks.editor"))
        )
    _store().upsert(deck)
    apply_consent(deck.device_ids, ordered, consent)
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


@bp.post("/bind-check")
def bind_check() -> Response:
    """Which of ``page_ids`` are bound only to displays other than
    ``device_ids``: the dashboards a form must get consent to bind before it
    can save. Read-only; the forms that call it re-post with ``bind_pages``."""
    from app.lineup_binding import display_info, stranded_pages

    body = _json_body()
    raw_pages = body.get("page_ids")
    raw_devices = body.get("device_ids")
    page_ids = [str(p) for p in raw_pages if p] if isinstance(raw_pages, list) else []
    device_ids = [str(d) for d in raw_devices if d] if isinstance(raw_devices, list) else []
    stranded = stranded_pages(device_ids, page_ids)
    return jsonify(
        {
            "ok": True,
            "display": display_info(device_ids[0]) if device_ids else None,
            "unbound_pages": [s.to_dict() for s in stranded],
        }
    )


@bp.post("/<deck_id>/bind-pages")
def bind_pages(deck_id: str) -> Response:
    """Bind a lineup's off-display steps to its display (the Lineups page's
    one-click fix). ``page_ids`` must be steps of this lineup; ``display_id``
    picks which of its displays; ``also`` keeps the dashboards' other
    displays instead of moving them."""
    deck = _store().get(deck_id)
    if deck is None:
        return _json_error(f"No deck with id {deck_id!r}.")
    body = _json_body()
    raw = body.get("page_ids")
    page_ids = [str(p) for p in raw if p] if isinstance(raw, list) else []
    if not page_ids or not set(page_ids) <= set(deck.page_ids):
        return _json_error("page_ids must name steps of this lineup.")
    display = str(body.get("display_id") or "")
    displays = [display] if display in deck.device_ids else list(deck.device_ids)
    if not displays:
        return _json_error("This lineup has no display to bind to.")
    ids = frozenset(page_ids)
    consent = Consent(ids, ids if body.get("also") else frozenset())
    apply_consent(displays, page_ids, consent)
    _invalidate(deck)
    return jsonify({"ok": True, "bound": page_ids, "display_id": displays[0]})


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
    stepped: list[str] = []
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
        stepped.append(device_id)
    if not moved:
        flash("No display took the step; see the events log.", "error")
    else:
        from app.push_delivery import flash_delivery

        flash_delivery(stepped)
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
    if result.status != "failed":
        from app.push_delivery import flash_delivery

        flash_delivery(deck.device_ids)
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
