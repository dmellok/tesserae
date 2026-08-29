"""History page.

Lives at ``/history`` so the push log is a top-level nav destination
rather than a tab buried inside Send. The resend / delete actions still
POST to the ``/send/...`` endpoints owned by ``send_routes`` so the
push pipeline stays in one module, only the read view moved here.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from flask import Blueprint, Flask, current_app, render_template, request

from app.device_loader import DeviceRegistry
from app.renderer_loader import RendererRegistry
from app.state.event_log import EventLog, EventRow
from app.state.page_store import PageStore
from app.tz_resolve import app_timezone

bp = Blueprint("history", __name__, url_prefix="/history")

# Sources we expose as filter chips. Order is the visual order in the
# history page header. Anything outside the list shows under "Other"
# (still filterable). Mirrors templates/history.html's SOURCE_META,
# minus a couple of internal-only triggers.
FILTERABLE_SOURCES = (
    "page",
    "scheduler",
    "rotation",
    "deck",
    "deck_warm",
    "deck_init",
    "page_refresh",
    "webhook",
    "home_assistant",
    "file",
    "url",
    "webpage",
    "manual",
    "resend",
    "onboarding",
    "button",
)


def _events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _devices() -> DeviceRegistry | None:
    return current_app.config.get("DEVICE_REGISTRY")


def _relative(epoch: float) -> str:
    """Short 'time since' label for the history feed."""
    seconds = max(0.0, time.time() - epoch)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds / 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)} h ago"
    return f"{int(seconds / 86400)} d ago"


def _renderer_label(renderer_id: str) -> str:
    """Friendly name for a renderer id. A per-instance clone
    (``pi_bin__bin_mini``) resolves to its device's display name; a base
    renderer falls back to the renderer's own name; an unknown id (renderer
    since removed) shows verbatim."""
    renderers: RendererRegistry | None = current_app.config.get("RENDERER_REGISTRY")
    renderer = renderers.get(renderer_id) if renderers is not None else None
    if renderer is None:
        return renderer_id
    devices = _devices()
    if devices is not None:
        device = devices.devices.get(renderer.device)
        if device is not None and device.kind_of is not None:
            return device.display_name
    return renderer.name


def _preview_digest(ev: EventRow) -> str | None:
    """Composition PNG to show for a History row.

    Normal push rows use the top-level digest, which also means they can be
    resent. Non-push button outcomes keep that field empty; ``fetch_latest``
    instead snapshots a preview-only composition digest in ``extra`` so its
    thumbnail can be shown without making the row resendable.
    """
    if isinstance(ev.digest, str) and ev.digest:
        return ev.digest
    candidate = ev.extra.get("composition_digest")
    return candidate if isinstance(candidate, str) and candidate else None


def history_view(rows: list[EventRow], *, fold_presses: bool = False) -> list[dict[str, Any]]:
    """Shape raw event rows for the History page: page name instead of id,
    humanised time, friendly device labels.

    With ``fold_presses`` a press/touch row that carries the id of the
    push row its action logged (``extra.push_event_id``, written by
    ``ButtonService`` since the push completes before the press row) is
    merged with that push row into one display row: the press row keeps
    its identity (source chip, detail line) and borrows the push row's
    thumbnail, duration, renderers, and resend target, and the standalone
    push row is dropped. A press whose partner was deleted or fell
    outside the fetched window renders unfolded, same as today.
    """
    pages = _pages().list()
    page_names = {p.id: p.name for p in pages}
    devices = _devices()
    # Cache device id → {name, icon} for the per-row device chip on the
    # v0.56 history view. Devices removed from the registry mid-stream
    # drop out gracefully (the chip is just skipped).
    device_meta: dict[str, dict[str, str]] = {}
    if devices is not None:
        for did, dev in devices.devices.items():
            if dev.kind_of is not None:
                device_meta[did] = {
                    "name": dev.display_name,
                    "icon": dev.icon or "monitor",
                }
    # Press → push pairing for the fold. Only pairs where both rows are
    # inside the fetched batch fold; ids are unique so a stale
    # push_event_id (partner deleted) simply never matches.
    folded: dict[int, EventRow] = {}
    absorbed: set[int] = set()
    if fold_presses:
        by_id = {ev.id: ev for ev in rows}
        for ev in rows:
            pid = ev.extra.get("push_event_id")
            if isinstance(pid, int) and pid != ev.id and pid in by_id:
                folded[ev.id] = by_id[pid]
                absorbed.add(pid)
    out: list[dict[str, Any]] = []
    for ev in rows:
        if ev.id in absorbed:
            continue
        push_ev = folded.get(ev.id)
        # Press rows target a device (not a page), so resolve any target
        # that is a known device id through device_meta first. Everything
        # else goes through page_names as usual, and unknown targets fall
        # through to the raw value via the dict default. Same treatment
        # keeps the History view honest about "which device was
        # this?", not just "which page did we send?". (Was previously
        # gated on source == "button", which left deck/touch press rows
        # showing the raw device id.)
        if ev.target in device_meta:
            target = device_meta[ev.target]["name"]
        else:
            target = page_names.get(ev.target, ev.target)
        renderers = [
            {"label": _renderer_label(str(r.get("renderer_id", ""))), "error": r.get("error")}
            for r in ((push_ev or ev).extra.get("renderers") or [])
        ]
        # Device chips: use only the ``device_ids`` snapshot the push
        # pipeline wrote to ``extra`` at push time. v0.69.17 (issue #52
        # follow-up): previously the code fell back to the page's
        # current ``device_ids`` when the snapshot was missing, which
        # contaminated old rows with devices that hadn't been added yet
        # when the push originally fired. For rows without a snapshot
        # (pre-v0.5x events, or bare-URL pushes with no device targets)
        # we show no chip: "we don't know" is honest, showing "today's
        # devices" isn't. Button rows always target a device directly,
        # so pull the target chip from ``ev.target`` in that case.
        device_ids = list(ev.extra.get("device_ids") or [])
        if not device_ids and ev.target in device_meta:
            device_ids = [ev.target]
        target_devices = [device_meta[did] for did in device_ids if did in device_meta]
        # Press rows carry the pressed button, resolved action, and
        # resulting page in ``extra``. Fold those into a short detail
        # string the template renders below the main row so the "what
        # actually happened" is visible without opening the raw event
        # (which the History page doesn't expose today). Rows without
        # those keys (ordinary pushes) resolve to None and drop the line.
        button_detail = _button_detail(ev, page_names)
        preview_digest = _preview_digest(ev) or (_preview_digest(push_ev) if push_ev else None)
        # A folded row resends and times as its push half; deletion
        # removes both halves via ``ids``.
        resend_ev = push_ev if push_ev is not None and push_ev.digest else ev
        out.append(
            {
                "id": ev.id,
                "ids": [ev.id] + ([push_ev.id] if push_ev is not None else []),
                "status": ev.status,
                "push_status": push_ev.status if push_ev is not None else None,
                "digest": ev.digest,
                "preview_digest": preview_digest,
                "can_resend": bool(resend_ev.digest),
                "resend_id": resend_ev.id,
                "source": ev.source,
                "target": target,
                "target_devices": target_devices,
                "rel": _relative(ev.timestamp),
                # v0.69.6 (issue #52 item 2): render in the user's configured
                # timezone rather than the container's local (UTC on Docker /
                # MicroCloud defaults). Falls back to system-local when the
                # setting is empty or "system"; see ``app_timezone`` for the
                # resolution ladder.
                "abs": datetime.fromtimestamp(ev.timestamp, tz=app_timezone()).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "duration_s": ev.duration_s or (push_ev.duration_s if push_ev is not None else 0.0),
                "error": ev.error or (push_ev.error if push_ev is not None else None),
                "renderers": renderers,
                "button_detail": button_detail,
            }
        )
    return out


def _button_detail(ev: EventRow, page_names: dict[str, str]) -> str | None:
    """Render the button-specific extras as a short detail string.

    ``extra`` carries ``button`` (the pressed name), ``action_spec``
    (the resolved spec, e.g. ``rotate_next`` or ``page:morning``),
    ``action_description`` (human-readable outcome from the action
    fn), ``pushed_page_id`` (when the action fired a push), and the
    resolved rotation position when the device is bound to a rotation.
    We stitch enough of that into one line so the History row shows
    what actually happened without a full JSON expand pane.

    Returns ``None`` when there's nothing informative to show; the
    template drops the detail line entirely in that case.
    """
    extra = ev.extra
    button = extra.get("button")
    action_spec = extra.get("action_spec")
    description = extra.get("action_description")
    pushed_page_id = extra.get("pushed_page_id")
    step_index = extra.get("step_index")
    step_page_id = extra.get("step_page_id")

    parts: list[str] = []
    if isinstance(button, str) and button:
        parts.append(f"button {button!s}")
    if isinstance(action_spec, str) and action_spec:
        parts.append(f"→ {action_spec}")
    elif isinstance(description, str) and description:
        parts.append(f"→ {description}")

    # If a page was pushed, prefer its friendly name.
    if isinstance(pushed_page_id, str) and pushed_page_id:
        friendly = page_names.get(pushed_page_id, pushed_page_id)
        parts.append(f"pushed {friendly}")
    elif isinstance(step_page_id, str) and step_page_id and isinstance(step_index, int):
        friendly = page_names.get(step_page_id, step_page_id)
        parts.append(f"step {step_index}: {friendly}")

    if not parts:
        return None
    return " ".join(parts)


# Statuses we hide from the History view by default. These are the
# "nothing actually went to the panel" outcomes that bury real fires
# under noise: quiet-hours skips (every bound device was inside its
# quiet window) and condition-held schedules (the gate kept the
# default page suppressed and no fallback was configured). Show them
# with ``?include_skipped=1`` when you actually need to see why a
# slot didn't fire.
_DEFAULT_HIDDEN_STATUSES: tuple[str, ...] = ("quiet", "held")

# Sources whose successful rows are background warms (rendered into the
# deck / album side cache, never painted on a panel). Their rows carry
# status ``warmed`` and are hidden by default so the feed reads as "what
# the panels actually showed"; ``?include_background=1`` (or filtering to
# one of these sources directly) brings them back.
_BACKGROUND_SOURCES: tuple[str, ...] = ("deck_warm", "album_warm")


@bp.get("")
def index() -> str:
    raw_source = (request.args.get("source") or "").strip()
    source = raw_source or None
    include_skipped = (request.args.get("include_skipped") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    include_background = (request.args.get("include_background") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # Press rows fold into the push row they triggered by default; the
    # toggle shows the raw two-row form for anyone auditing the log.
    split_presses = (request.args.get("split_presses") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # v0.69.17 (issue #52 follow-up): opt-in sort by dashboard name so
    # a user drilling into "how did dashboard X fare over the last week"
    # can read consecutive rows without scanning back and forth. Default
    # (missing / anything else) is chronological, newest first.
    sort_mode = (request.args.get("sort") or "").strip().lower()
    if sort_mode not in ("dashboard",):
        sort_mode = "time"
    events = _events()
    hidden: tuple[str, ...] = () if include_skipped else _DEFAULT_HIDDEN_STATUSES
    if not include_background and source not in _BACKGROUND_SOURCES:
        hidden = (*hidden, "warmed")
    exclude_statuses = hidden or None
    history = history_view(
        events.list(
            type="push",
            source=source,
            exclude_statuses=exclude_statuses,
            limit=100,
        ),
        fold_presses=not split_presses,
    )
    if sort_mode == "dashboard":
        # Stable-sort by the resolved target label (page name or
        # device name for button rows) so entries of the same
        # dashboard clump. The base list is already newest-first, so
        # within each dashboard clump the recency order is preserved.
        history.sort(key=lambda row: (row.get("target") or "").casefold())
    # Per-source counts power the filter-chip badges. We include zero-
    # count chips for the canonical sources so the filter row is stable
    # across page loads (chips don't appear/disappear as the log churns).
    counts = events.source_counts(type="push")
    total = sum(counts.values())
    chips = [{"source": "", "count": total, "active": source is None}]
    for src in FILTERABLE_SOURCES:
        if counts.get(src, 0) == 0 and source != src:
            continue
        chips.append({"source": src, "count": counts.get(src, 0), "active": source == src})
    # Surface any non-canonical sources that exist in the log so the
    # filter row never hides events that actually happened.
    for src in sorted(counts.keys()):
        if src in FILTERABLE_SOURCES or counts.get(src, 0) == 0:
            continue
        chips.append({"source": src, "count": counts[src], "active": source == src})
    return render_template(
        "history.html",
        history=history,
        chips=chips,
        active_source=source,
        include_skipped=include_skipped,
        include_background=include_background,
        split_presses=split_presses,
        sort_mode=sort_mode,
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
