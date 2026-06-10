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
    redirect,
    render_template,
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


def _parse_steps(form: Any) -> list[RotationStep]:
    """Read the parallel ``step_page_ids[]`` + ``step_dwell_minutes[]``
    arrays out of the form and assemble RotationSteps. Empty rows
    (blank page_id) are dropped; the model validator catches
    "no steps" with a useful message."""
    getlist = form.getlist if hasattr(form, "getlist") else lambda _: []
    pages = getlist("step_page_ids[]")
    dwells = getlist("step_dwell_minutes[]")
    out: list[RotationStep] = []
    for page_id, dwell in zip(pages, dwells, strict=False):
        page_id = (page_id or "").strip()
        if not page_id:
            continue
        try:
            dwell_int = int(dwell)
        except (TypeError, ValueError):
            continue
        out.append(RotationStep(page_id=page_id, dwell_minutes=dwell_int))
    return out


def _parse_form(form: Any, *, existing_id: str | None = None) -> Rotation:
    name = (form.get("name") or "").strip()
    rotation_id = existing_id if existing_id is not None else _unique_rotation_id(_slug_from(name))
    payload: dict[str, Any] = {
        "id": rotation_id,
        "name": name,
        "enabled": form.get("enabled") in ("on", "true", "1"),
        "anchor": (form.get("anchor") or "00:00").strip(),
        "days_of_week": _parse_dow(
            form.getlist("days_of_week") if hasattr(form, "getlist") else []
        ),
        "device_ids": [
            d for d in (form.getlist("device_ids") if hasattr(form, "getlist") else []) if d
        ],
        "priority": int(form.get("priority") or 0),
        "steps": [s.model_dump() for s in _parse_steps(form)],
    }
    return Rotation.model_validate(payload)


def _build_projection(rotation: Rotation, *, total_minutes: int = 24 * 60) -> dict[str, Any]:
    """Tiny timeline-style projection of the rotation's step sequence
    over the next ``total_minutes`` minutes, anchored at the current
    moment. Powers a small horizontal preview on the index page so the
    user can sanity-check what the rotation will display when."""
    now_local = datetime.now()
    tz_provider_attr = getattr(_scheduler(), "_tz_provider", None)
    tz = tz_provider_attr() if callable(tz_provider_attr) else None
    if tz is not None:
        now_local = datetime.now(tz)
    bands: list[dict[str, Any]] = []
    minutes_into = 0
    safety = 0
    pages_by_id = {p.id: p.name for p in _pages().list()}
    while minutes_into < total_minutes and safety < 200:
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
        idx, step = picked
        bands.append(
            {
                "index": idx,
                "page_id": step.page_id,
                "page_name": pages_by_id.get(step.page_id, step.page_id),
                "start_min": minutes_into,
                "dwell_minutes": step.dwell_minutes,
                "pct": minutes_into / total_minutes * 100,
                "width_pct": step.dwell_minutes / total_minutes * 100,
            }
        )
        minutes_into += step.dwell_minutes
    return {
        "total_minutes": total_minutes,
        "bands": bands[:200],
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
    """Snapshot of which step each rotation is currently on, so the
    list view can show 'now: page X' next to each row. Uses the same
    ``compute_current_step`` the scheduler uses, no inconsistency."""
    tz_provider_attr = getattr(_scheduler(), "_tz_provider", None)
    tz = tz_provider_attr() if callable(tz_provider_attr) else None
    now = datetime.now(UTC)
    pages_by_id = {p.id: p.name for p in _pages().list()}
    out: dict[str, dict[str, Any]] = {}
    for r in rotations:
        picked = compute_current_step(r, now, tz)
        if picked is None:
            out[r.id] = {"active": False, "step_index": None, "page_id": None}
            continue
        idx, step = picked
        out[r.id] = {
            "active": True,
            "step_index": idx,
            "page_id": step.page_id,
            "page_name": pages_by_id.get(step.page_id, step.page_id),
        }
    return out


def _device_options(selected: set[str]) -> list[dict[str, Any]]:
    """Build the list-of-dicts shape the ``device-checklist`` macro
    expects, same as the page editor uses. Built-in kinds and devices
    without a panel block are skipped (not bindable targets)."""
    registry = current_app.config.get("DEVICE_REGISTRY")
    out: list[dict[str, Any]] = []
    if registry is None:
        return out
    for dev in sorted(registry.devices.values(), key=lambda d: d.name.lower()):
        if dev.kind_of is None or dev.panel is None:
            continue
        out.append(
            {
                "id": dev.id,
                "name": dev.name,
                "icon": dev.icon,
                "label": dev.display_name,
                "dims": f"{dev.panel['w']}×{dev.panel['h']}",
                "checked": dev.id in selected,
            }
        )
    return out


@bp.get("")
def index() -> str:
    rotations = _store().all()
    pages = _pages().list()
    edit_id = request.args.get("edit")
    # When editing, the form's device-checklist needs to reflect the
    # rotation's existing bindings; otherwise the empty form gets
    # everything unchecked.
    selected_for_form: set[str] = set()
    if edit_id is not None:
        existing = next((r for r in rotations if r.id == edit_id), None)
        if existing is not None:
            selected_for_form = set(existing.device_ids)
    return render_template(
        "rotations.html",
        rotations=rotations,
        pages=pages,
        page_names={p.id: p.name for p in pages},
        device_options=_device_options(selected_for_form),
        # Per-card device_options snapshots, keyed by rotation id, so
        # an inline-edit form for a specific rotation gets the right
        # ticks pre-set without us swapping global state per row.
        device_options_by_rotation={
            r.id: _device_options(set(r.device_ids)) for r in rotations
        },
        current_step=_current_step_for_each(rotations),
        edit_id=edit_id,
        projections={r.id: _build_projection(r) for r in rotations},
    )


@bp.post("/new")
def create() -> Response:
    try:
        rotation = _parse_form(request.form)
    except ValidationError as exc:
        flash(f"Invalid rotation: {_first_error(exc)}", "error")
        return redirect(url_for("rotations.index"))
    if not _ID_RE.match(rotation.id):
        flash(f"Bad id {rotation.id!r} (snake_case only).", "error")
        return redirect(url_for("rotations.index"))
    _store().upsert(rotation)
    flash(f"Rotation {rotation.name!r} saved.", "ok")
    return redirect(url_for("rotations.index"))


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
        return redirect(url_for("rotations.index", edit=rotation_id))
    rotation = rotation.model_copy(update={"id": rotation_id})
    _store().upsert(rotation)
    flash(f"Rotation {rotation.name!r} updated.", "ok")
    return redirect(url_for("rotations.index"))


@bp.post("/<rotation_id>/toggle")
def toggle(rotation_id: str) -> Response:
    existing = _store().get(rotation_id)
    if existing is None:
        flash(f"No rotation with id {rotation_id!r}.", "error")
        return redirect(url_for("rotations.index"))
    updated = existing.model_copy(update={"enabled": not existing.enabled})
    _store().upsert(updated)
    return redirect(url_for("rotations.index"))


@bp.post("/<rotation_id>/delete")
def delete(rotation_id: str) -> Response:
    deleted = _store().delete(rotation_id)
    if not deleted:
        flash(f"No rotation with id {rotation_id!r}.", "error")
    else:
        flash("Rotation deleted.", "ok")
    return redirect(url_for("rotations.index"))


@bp.post("/<rotation_id>/fire")
def fire(rotation_id: str) -> Response:
    """Manual fire: pushes whichever step the rotation is currently
    on. Useful for "I changed the steps, show me the new current
    page now without waiting for the next tick"."""
    rotation = _store().get(rotation_id)
    if rotation is None:
        flash(f"No rotation with id {rotation_id!r}.", "error")
        return redirect(url_for("rotations.index"))
    sched = _scheduler()
    tz = sched._tz_provider() if hasattr(sched, "_tz_provider") else None
    picked = compute_current_step(rotation, datetime.now(UTC), tz)
    if picked is None:
        flash(
            f"Rotation {rotation.name!r} isn't active right now "
            "(day-of-week filter or before anchor).",
            "warn",
        )
        return redirect(url_for("rotations.index"))
    step_index, _step = picked
    result = sched._fire_rotation(rotation, step_index, datetime.now(UTC))
    if result.status == "sent":
        flash(f"Fired rotation {rotation_id!r} (step {step_index}).", "ok")
    else:
        flash(
            f"Fired rotation {rotation_id!r}: {result.status}, {result.error or ''}",
            "error",
        )
    return redirect(url_for("rotations.index"))


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
