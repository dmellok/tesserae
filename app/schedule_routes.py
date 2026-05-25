"""Admin schedule routes: list, create, edit, delete, fire.

A single ``/schedules`` page lists every schedule plus an inline 'new'
form. ``/schedules/<id>/edit`` is an in-page form for an existing row.
Delete and fire-now are POST endpoints.

Manual trigger ('fire now') bypasses every gate — the user is asking
for a push, give them one — but it still goes through the same
PushManager.push() the scheduler uses, so single-flight and renderer
fanout work the same way.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
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

from app.scheduler import Scheduler
from app.state.page_store import PageStore
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore

bp = Blueprint("schedules", __name__, url_prefix="/schedules")

_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")


def _store() -> ScheduleStore:
    return current_app.config["SCHEDULE_STORE"]  # type: ignore[no-any-return]


def _scheduler() -> Scheduler:
    return current_app.config["SCHEDULER"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _slug_from(value: str) -> str:
    """Derive a snake-cased id from a free-text name. Used when the user
    leaves the id field blank in the new-schedule form."""
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "schedule"


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


def _hhmm_to_today_dt(value: str) -> datetime:
    h, m = value.split(":")
    today = datetime.now().date()
    return datetime.combine(today, datetime.min.time()).replace(hour=int(h), minute=int(m))


def _parse_form(form: dict[str, Any], *, existing_id: str | None = None) -> Schedule:
    """Build a Schedule from a form dict. Raises ValidationError on bad
    input — the caller flashes and re-renders."""
    name = (form.get("name") or "").strip()
    schedule_id = (form.get("id") or "").strip().lower()
    if not schedule_id:
        schedule_id = existing_id or _slug_from(name)
    schedule_type = form.get("type", "interval")

    payload: dict[str, Any] = {
        "id": schedule_id,
        "name": name,
        "page_id": (form.get("page_id") or "").strip(),
        "enabled": form.get("enabled") in ("on", "true", "1"),
        "type": schedule_type,
        "days_of_week": _parse_dow(
            form.getlist("days_of_week") if hasattr(form, "getlist") else []
        ),
        "priority": int(form.get("priority") or 0),
    }
    if schedule_type == "interval":
        try:
            payload["interval_minutes"] = int(form.get("interval_minutes") or 0)
        except ValueError as exc:
            raise ValidationError.from_exception_data(
                "Schedule",
                [
                    {
                        "type": "int_parsing",
                        "loc": ("interval_minutes",),
                        "input": form.get("interval_minutes"),
                    }
                ],
            ) from exc
        start = (form.get("time_of_day_start") or "").strip() or None
        end = (form.get("time_of_day_end") or "").strip() or None
        payload["time_of_day_start"] = start
        payload["time_of_day_end"] = end
    elif schedule_type == "daily":
        fires_at_str = (form.get("fires_at") or "").strip()
        if not fires_at_str:
            raise ValidationError.from_exception_data(
                "Schedule", [{"type": "missing", "loc": ("fires_at",), "input": None}]
            )
        payload["fires_at"] = _hhmm_to_today_dt(fires_at_str).isoformat()
    return Schedule.model_validate(payload)


@bp.get("")
def index() -> str:
    schedules = _store().all()
    status = _scheduler().status()
    pages = _pages().list()
    return render_template(
        "schedules.html",
        schedules=schedules,
        status=status,
        pages=pages,
        edit_id=request.args.get("edit"),
    )


@bp.post("/new")
def create() -> Response:
    try:
        schedule = _parse_form(request.form)
    except ValidationError as exc:
        flash(f"Invalid schedule: {_first_error(exc)}", "error")
        return redirect(url_for("schedules.index"))
    if not _ID_RE.match(schedule.id):
        flash(f"Bad id {schedule.id!r} (snake_case only).", "error")
        return redirect(url_for("schedules.index"))
    _store().upsert(schedule)
    flash(f"Schedule {schedule.name!r} saved.", "ok")
    return redirect(url_for("schedules.index"))


@bp.post("/<schedule_id>/update")
def update(schedule_id: str) -> Response:
    existing = _store().get(schedule_id)
    if existing is None:
        flash(f"No schedule with id {schedule_id!r}.", "error")
        return redirect(url_for("schedules.index"))
    try:
        schedule = _parse_form(request.form, existing_id=schedule_id)
    except ValidationError as exc:
        flash(f"Invalid schedule: {_first_error(exc)}", "error")
        return redirect(url_for("schedules.index", edit=schedule_id))
    # Don't let the user rename the id via this endpoint; force-pin to
    # the URL's schedule_id so a typo can't fork into a second record.
    schedule = schedule.model_copy(update={"id": schedule_id})
    _store().upsert(schedule)
    flash(f"Schedule {schedule.name!r} updated.", "ok")
    return redirect(url_for("schedules.index"))


@bp.post("/<schedule_id>/toggle")
def toggle(schedule_id: str) -> Response:
    existing = _store().get(schedule_id)
    if existing is None:
        flash(f"No schedule with id {schedule_id!r}.", "error")
        return redirect(url_for("schedules.index"))
    updated = existing.model_copy(update={"enabled": not existing.enabled})
    _store().upsert(updated)
    return redirect(url_for("schedules.index"))


@bp.post("/<schedule_id>/delete")
def delete(schedule_id: str) -> Response:
    deleted = _store().delete(schedule_id)
    if not deleted:
        flash(f"No schedule with id {schedule_id!r}.", "error")
    else:
        flash("Schedule deleted.", "ok")
    return redirect(url_for("schedules.index"))


@bp.post("/<schedule_id>/fire")
def fire(schedule_id: str) -> Response:
    result = _scheduler().fire_now(schedule_id)
    if result is None:
        flash(f"No schedule with id {schedule_id!r}.", "error")
    elif result.status == "sent":
        flash(f"Fired {schedule_id!r}.", "ok")
    else:
        flash(f"Fired {schedule_id!r}: {result.status} — {result.error or ''}", "error")
    return redirect(url_for("schedules.index"))


def _first_error(exc: ValidationError) -> str:
    """Pluck the first usable error message out of a Pydantic
    ValidationError so the flash isn't a wall of JSON."""
    errors = exc.errors()
    if not errors:
        return "validation failed"
    err = errors[0]
    loc = ".".join(str(x) for x in err.get("loc", ()))
    return f"[{loc}] {err.get('msg', 'invalid')}"


def register(app: Flask) -> None:
    app.register_blueprint(bp)
