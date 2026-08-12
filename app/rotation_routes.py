"""Admin rotation routes: list, create, edit, delete, fire-now.

Mirrors the shape of ``schedule_routes`` so users coming from the
Schedules page see a familiar form. The key differences:

* Steps are an ordered list of ``(page_id, dwell_minutes)`` rows the
  user can add, reorder, or remove.
* ``anchor`` is a single HH:MM wall-clock time (when step 0 starts
  each local day); there's no separate time-of-day window because the
  whole cycle IS the window.
* Manual "fire" pushes the rotation's current step (whichever bucket
  the wall clock falls into right now).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    request,
    url_for,
)
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app.scheduler import Scheduler, compute_current_step
from app.state.page_store import PageStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore

bp = Blueprint("rotations", __name__, url_prefix="/rotations")

_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")


def _store() -> RotationStore:
    return current_app.config["ROTATION_STORE"]  # type: ignore[no-any-return]


def _scheduler() -> Scheduler:
    return current_app.config["SCHEDULER"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _slug_from(value: str) -> str:
    """Derive a snake-cased id from a free-text name. Mirrors the
    schedule_routes helper so the two pages feel consistent."""
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "rotation"


def _parse_dow(values: Iterable[str]) -> list[int]:
    out: list[int] = []
    for v in values:
        try:
            day = int(v)
        except ValueError:
            continue
        if 0 <= day <= 6:
            out.append(day)
    return sorted(set(out)) or list(range(7))


def _unique_rotation_id(base: str) -> str:
    taken = {r.id for r in _store().all()}
    candidate = base
    n = 1
    while candidate in taken:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


def _parse_step_conditions_json(raw: str) -> list[dict[str, Any]]:
    """Parse a single step's ``conditions_json`` cell. Empty input
    means "no conditions"; otherwise a JSON array of condition dicts."""
    import json

    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Bad JSON falls through to the model validator below, which
        # rejects the rotation with a per-step error message. We don't
        # raise here because we still want the rest of the form to
        # parse so the editor can show what survived.
        return [{"__invalid__": text}]  # signals "this row failed JSON"
    if not isinstance(parsed, list):
        return [{"__invalid__": text}]
    return parsed


def _parse_steps(form: Any) -> list[RotationStep]:
    """Read the parallel ``step_page_ids[]`` + ``step_dwell_minutes[]``
    + ``step_conditions_json[]`` arrays out of the form and assemble
    RotationSteps. Empty rows (blank page_id) are dropped; the model
    validator catches "no steps" with a useful message. Per-step
    conditions JSON is parsed inline (empty = no conditions)."""
    getlist = form.getlist if hasattr(form, "getlist") else lambda _: []
    pages = getlist("step_page_ids[]")
    dwells = getlist("step_dwell_minutes[]")
    conditions = getlist("step_conditions_json[]")
    out: list[RotationStep] = []
    for idx, (page_id, dwell) in enumerate(zip(pages, dwells, strict=False)):
        page_id = (page_id or "").strip()
        if not page_id:
            continue
        try:
            dwell_int = int(dwell)
        except (TypeError, ValueError):
            continue
        raw_cond = conditions[idx] if idx < len(conditions) else ""
        cond_list = _parse_step_conditions_json(raw_cond)
        out.append(
            RotationStep(
                page_id=page_id,
                dwell_minutes=dwell_int,
                conditions=cond_list,  # type: ignore[arg-type]
            )
        )
    return out


def _parse_form(form: Any, *, existing_id: str | None = None) -> Rotation:
    name = (form.get("name") or "").strip()
    rotation_id = existing_id if existing_id is not None else _unique_rotation_id(_slug_from(name))
    mode_raw = (form.get("mode") or "scheduled").strip()
    if mode_raw not in ("scheduled", "priority"):
        mode_raw = "scheduled"
    getlist = form.getlist if hasattr(form, "getlist") else (lambda _k: [])
    payload: dict[str, Any] = {
        "id": rotation_id,
        "name": name,
        "enabled": form.get("enabled") in ("on", "true", "1"),
        "anchor": (form.get("anchor") or "00:00").strip(),
        "end_at": (form.get("end_at") or "").strip() or None,
        "days_of_week": _parse_dow(getlist("days_of_week")),
        # The wizard binds the rotation to its chosen display so the whole
        # cycle plays there. Empty (the full form has no picker) falls
        # through to each step page's own bindings.
        "device_ids": [d.strip() for d in getlist("device_ids") if d and d.strip()],
        "priority": int(form.get("priority") or 0),
        "smart_sync": form.get("smart_sync") in ("on", "true", "1"),
        "smart_sync_lead_s": int(form.get("smart_sync_lead_s") or 10),
        # v0.48 routing + flap protection.
        "mode": mode_raw,
        "min_hold_minutes": int(form.get("min_hold_minutes") or 5),
        "steps": [s.model_dump() for s in _parse_steps(form)],
    }
    return Rotation.model_validate(payload)


def _build_projection(rotation: Rotation, *, total_minutes: int = 24 * 60) -> dict[str, Any]:
    """Tiny timeline-style projection of the rotation's step sequence
    over the next ``total_minutes`` minutes, anchored at the current
    moment. Powers a small horizontal preview on the index page so the
    user can sanity-check what the rotation will display when.

    Conditions are evaluated against current HA state and projected
    forward, so a step gated on ``binary_sensor.octoprint_printing ==
    on`` shows the WALKED-FORWARD eligible step in its time slot (the
    same step the autonomous tick would fire) rather than the
    time-naive cycle position. The bar's ``conditioned_skipped`` flag
    flags slots whose original step was gated out, so the template
    can hint that the layout shifted vs the configured cycle.
    """
    now_local = datetime.now()
    sched = _scheduler()
    tz_provider_attr = getattr(sched, "_tz_provider", None)
    tz = tz_provider_attr() if callable(tz_provider_attr) else None
    if tz is not None:
        now_local = datetime.now(tz)
    bands: list[dict[str, Any]] = []
    minutes_into = 0
    iter_cap = total_minutes + 64
    safety = 0
    pages_by_id = {p.id: p.name for p in _pages().list()}
    while minutes_into < total_minutes and safety < iter_cap:
        safety += 1
        sample_time = (
            now_local.astimezone(UTC) if now_local.tzinfo is not None else now_local.astimezone()
        )
        delta_seconds = minutes_into * 60
        sample = sample_time.fromtimestamp(sample_time.timestamp() + delta_seconds, tz=UTC)
        picked = compute_current_step(rotation, sample, tz)
        if picked is None:
            minutes_into += 5
            continue
        time_idx, time_step = picked
        # Apply conditions like the autonomous tick does: the user
        # should see WHICH step will actually fire in this slot, not
        # the time-naive cycle position.
        eligible_idx = sched._pick_eligible_step(rotation, time_idx, sample)
        if eligible_idx is None:
            # All steps gated out at this moment; mark the slot as a
            # "held" band so the template can render it muted.
            bands.append(
                {
                    "index": time_idx,
                    "page_id": None,
                    "page_name": "Held",
                    "start_min": minutes_into,
                    "dwell_minutes": time_step.dwell_minutes,
                    "pct": minutes_into / total_minutes * 100,
                    "width_pct": time_step.dwell_minutes / total_minutes * 100,
                    "held": True,
                    "conditioned_skipped": True,
                }
            )
            minutes_into += time_step.dwell_minutes
            continue
        eligible_step = rotation.steps[eligible_idx]
        bands.append(
            {
                "index": eligible_idx,
                "page_id": eligible_step.page_id,
                "page_name": pages_by_id.get(eligible_step.page_id, eligible_step.page_id),
                "start_min": minutes_into,
                "dwell_minutes": time_step.dwell_minutes,
                "pct": minutes_into / total_minutes * 100,
                "width_pct": time_step.dwell_minutes / total_minutes * 100,
                "held": False,
                "conditioned_skipped": eligible_idx != time_idx,
            }
        )
        minutes_into += time_step.dwell_minutes
    return {
        "total_minutes": total_minutes,
        "bands": bands,
    }


def _relative(epoch: float | None) -> str:
    if not epoch:
        return ""
    seconds = max(0.0, time.time() - epoch)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds / 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)} h ago"
    return f"{int(seconds / 86400)} d ago"


def _current_step_for_each(rotations: list[Rotation]) -> dict[str, dict[str, Any]]:
    """Snapshot of which step each rotation is on PLUS the dwell-window
    edges (epoch seconds) so the index template can render a live
    countdown bar. Uses the scheduler's override-aware
    ``compute_step_state`` so a manual "play step N" poke reflects
    in the UI immediately."""
    sched = _scheduler()
    now = datetime.now(UTC)
    pages_by_id = {p.id: p.name for p in _pages().list()}
    out: dict[str, dict[str, Any]] = {}
    for r in rotations:
        state = sched.compute_step_state(r, now)
        if state is None:
            out[r.id] = {
                "active": False,
                "step_index": None,
                "page_id": None,
                "step_started_epoch": None,
                "next_transition_epoch": None,
                "dwell_seconds": None,
                "override_in_effect": False,
            }
            continue
        step = r.steps[state.step_index]
        out[r.id] = {
            "active": True,
            "step_index": state.step_index,
            "page_id": step.page_id,
            "page_name": pages_by_id.get(step.page_id, step.page_id),
            "step_started_epoch": state.step_started_at.timestamp(),
            "next_transition_epoch": state.next_transition_at.timestamp(),
            "dwell_seconds": step.dwell_minutes * 60,
            "override_in_effect": state.forced_at is not None,
        }
    return out


def _devices_for_page(pages: list[Any]) -> dict[str, list[dict[str, str]]]:
    """Map ``page_id -> [{id, name, icon}]`` so each step row in the
    read-only preview can show which physical devices a step's page
    will push to. Built-in-kind devices and panel-less entries are
    skipped (they aren't real targets)."""
    registry = current_app.config.get("DEVICE_REGISTRY")
    out: dict[str, list[dict[str, str]]] = {}
    if registry is None:
        for p in pages:
            out[p.id] = []
        return out
    by_id: dict[str, Any] = {}
    for dev in registry.devices.values():
        if dev.kind_of is None or dev.panel is None:
            continue
        by_id[dev.id] = dev
    for p in pages:
        out[p.id] = [
            {"id": d.id, "name": d.display_name, "icon": d.icon}
            for d in (by_id.get(did) for did in p.device_ids)
            if d is not None
        ]
    return out


def _running_state_view(
    rotation: Rotation, status_row: dict[str, Any] | None
) -> dict[str, str] | None:
    """v0.48 running-state pill for the rotation card. Returns ``None``
    when there's nothing exceptional to surface (the existing
    Disabled / Now / Not active pill already covers normal state);
    the template renders this as an extra sibling pill when present.
    """
    if not rotation.enabled:
        return None
    last_status = (status_row or {}).get("last_status")
    last_reason = (status_row or {}).get("last_reason")
    if last_status == "held":
        # Two reasons land here: "no step's conditions are met" (all
        # step conditions failed) and "all devices manually held"
        # (button / touch page-away still active), so the tooltip just
        # echoes the recorded reason.
        reason = last_reason or "no step is eligible right now"
        return {
            "label": "held",
            "tone": "is-held",
            "icon": "funnel",
            "tooltip": f"{reason[:1].upper()}{reason[1:]}.",
        }
    if last_status == "failed":
        return {
            "label": "failed",
            "tone": "is-danger",
            "icon": "warning-circle",
            "tooltip": last_reason or "Last fire failed; see the events log.",
        }
    return None


@bp.get("")
def index() -> Response:
    """#167: cycles are decks; the old URL lands on the unified list, and
    an ``edit`` deep link opens the deck editor (the one editor for every
    shape). Every POST endpoint on this blueprint is unchanged."""
    edit = request.args.get("edit")
    if edit:
        return redirect(url_for("decks.editor", deck_id=edit))
    return redirect(url_for("decks.index"))


def _json_error(msg: str) -> Response:
    resp = jsonify({"ok": False, "error": msg})
    resp.status_code = 400
    return resp


@bp.post("/new")
def create() -> Response:
    # The setup wizard submits with respond=json (fetch) so it can stay on
    # its created screen instead of following the redirect.
    wants_json = request.form.get("respond") == "json"
    try:
        rotation = _parse_form(request.form)
    except ValidationError as exc:
        msg = f"Invalid rotation: {_first_error(exc)}"
        if wants_json:
            return _json_error(msg)
        flash(msg, "error")
        return redirect(url_for("rotations.index"))
    if not _ID_RE.match(rotation.id):
        msg = f"Bad id {rotation.id!r} (snake_case only)."
        if wants_json:
            return _json_error(msg)
        flash(msg, "error")
        return redirect(url_for("rotations.index"))
    _store().upsert(rotation)
    # A picked dashboard that isn't on any display yet binds to the
    # rotation's display, mirroring the deck editor's save behaviour, so a
    # bound rotation can always render every step.
    display = rotation.device_ids[0] if rotation.device_ids else None
    if display:
        pages = _pages()
        for step_ in rotation.steps:
            page = pages.get(step_.page_id)
            if page is not None and not page.device_ids:
                pages.save(page.model_copy(update={"device_ids": [display]}))
    flash(f"Rotation {rotation.name!r} saved.", "ok")
    # Land on the unified list with the new card highlighted (#167).
    url = url_for("decks.index", hl=rotation.id) + f"#udeck-{rotation.id}"
    if wants_json:
        return jsonify({"ok": True, "id": rotation.id, "url": url})
    return redirect(url)


@bp.post("/<rotation_id>/update")
def update(rotation_id: str) -> Response:
    existing = _store().get(rotation_id)
    if existing is None:
        flash(f"No rotation with id {rotation_id!r}.", "error")
        return redirect(url_for("rotations.index"))
    try:
        rotation = _parse_form(request.form, existing_id=rotation_id)
    except ValidationError as exc:
        flash(f"Invalid rotation: {_first_error(exc)}", "error")
        # Bounce back into edit mode with the fragment pointing at
        # the rotation card so the browser scrolls the in-flight
        # edit form back into view instead of dropping the user at
        # the top of the page.
        return redirect(url_for("rotations.index", edit=rotation_id) + f"#rotation-{rotation_id}")
    rotation = rotation.model_copy(update={"id": rotation_id})
    _store().upsert(rotation)
    flash(f"Rotation {rotation.name!r} updated.", "ok")
    # Scroll back to the just-saved rotation's card so the user lands
    # on the read view of what they just changed, instead of the top
    # of the rotations list.
    return redirect(url_for("rotations.index") + f"#rotation-{rotation_id}")


@bp.post("/<rotation_id>/toggle")
def toggle(rotation_id: str) -> Response:
    existing = _store().get(rotation_id)
    if existing is None:
        flash(f"No rotation with id {rotation_id!r}.", "error")
        return redirect(url_for("rotations.index"))
    updated = existing.model_copy(update={"enabled": not existing.enabled})
    _store().upsert(updated)
    # Disabling clears any manual override so the rotation comes back
    # clean when re-enabled. (Enabling-after-disable falls through to
    # the no-op branch since there's nothing to clear.)
    if not updated.enabled:
        _scheduler().clear_anchor_override(rotation_id)
    return redirect(url_for("rotations.index"))


@bp.post("/<rotation_id>/delete")
def delete(rotation_id: str) -> Response:
    deleted = _store().delete(rotation_id)
    if not deleted:
        flash(f"No rotation with id {rotation_id!r}.", "error")
    else:
        _scheduler().clear_anchor_override(rotation_id)
        flash("Rotation deleted.", "ok")
    return redirect(url_for("rotations.index"))


@bp.post("/<rotation_id>/fire")
def fire(rotation_id: str) -> Response:
    """Manual fire: pushes whichever step the rotation is currently
    on. Useful for "I changed the steps, show me the new current
    page now without waiting for the next tick".

    Honours per-step conditions exactly like the scheduler tick: if the
    time-current step has a failing condition we walk forward to the
    next eligible step (scheduled mode) or pick the highest-priority
    matching step (priority mode). Use the per-step "Play this step"
    button to bypass conditions explicitly.
    """
    rotation = _store().get(rotation_id)
    if rotation is None:
        flash(f"No rotation with id {rotation_id!r}.", "error")
        return redirect(url_for("rotations.index"))
    sched = _scheduler()
    now = datetime.now(UTC)
    state = sched.compute_step_state(rotation, now)
    if state is None:
        flash(
            f"Rotation {rotation.name!r} isn't active right now "
            "(day-of-week filter or before anchor).",
            "warn",
        )
        return redirect(url_for("rotations.index"))
    eligible = sched._pick_eligible_step(rotation, state.step_index, now)
    if eligible is None:
        flash(
            f"Rotation {rotation.name!r}: no step's conditions are met right now.",
            "warn",
        )
        return redirect(url_for("rotations.index"))
    result = sched._fire_rotation(rotation, eligible, now, bypass_holds=True)
    if result.status in ("sent", "no_change"):
        detail = " (already showing it)" if result.status == "no_change" else ""
        flash(f"Fired rotation {rotation_id!r} (step {eligible}){detail}.", "ok")
    else:
        flash(
            f"Fired rotation {rotation_id!r}: {_status_detail(result)}",
            "error",
        )
    return redirect(url_for("rotations.index"))


@bp.post("/<rotation_id>/play/<int:step_index>")
def play(rotation_id: str, step_index: int) -> Response:
    """Play a specific step right now and re-anchor the cycle from
    there. Lasts until tomorrow's anchor catches up (or the user
    clicks another step). Override is in-memory only — a server
    restart returns the rotation to its anchor-deterministic
    schedule."""
    rotation = _store().get(rotation_id)
    if rotation is None:
        flash(f"No rotation with id {rotation_id!r}.", "error")
        return redirect(url_for("rotations.index"))
    if not 0 <= step_index < len(rotation.steps):
        flash(f"Step {step_index + 1} doesn't exist on this rotation.", "error")
        return redirect(url_for("rotations.index"))
    sched = _scheduler()
    now = datetime.now(UTC)
    try:
        state = sched.force_step(rotation, step_index, now)
    except IndexError:
        flash(f"Step {step_index + 1} doesn't exist on this rotation.", "error")
        return redirect(url_for("rotations.index"))
    if state is None:
        flash(
            f"Rotation {rotation.name!r} isn't active right now "
            "(day-of-week filter or outside its window). Can't play a step.",
            "warn",
        )
        return redirect(url_for("rotations.index"))
    result = sched._fire_rotation(rotation, state.step_index, now, bypass_holds=True)
    page_label = next(
        (p.name for p in _pages().list() if p.id == rotation.steps[state.step_index].page_id),
        rotation.steps[state.step_index].page_id,
    )
    if result.status in ("sent", "no_change"):
        # no_change is a success: the panel already shows this step's frame, so
        # there was nothing to repaint. Saying so beats both a bogus error and
        # a bare "playing", which would leave the user watching for a refresh
        # that correctly never comes.
        detail = " (already showing it, nothing to repaint)" if result.status == "no_change" else ""
        flash(
            f"Playing step {state.step_index + 1} ({page_label}) on rotation "
            f"{rotation.name!r}{detail}; cycle continues from here.",
            "ok",
        )
    else:
        flash(
            f"Couldn't play step {state.step_index + 1} on {rotation.name!r}: "
            f"{_status_detail(result)}",
            "error",
        )
    return redirect(url_for("rotations.index"))


def _status_detail(result: Any) -> str:
    """``"failed, connection refused"`` with an error, bare ``"failed"``
    without one (avoids the dangling-comma flash)."""
    return f"{result.status}, {result.error}" if result.error else str(result.status)


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "validation failed"
    err = errors[0]
    loc = ".".join(str(x) for x in err.get("loc", ()))
    return f"[{loc}] {err.get('msg', 'invalid')}"


def register(app: Flask) -> None:
    app.register_blueprint(bp)


# Re-relative used by the index template via Jinja context.
__all__ = ["_relative", "bp", "register"]
