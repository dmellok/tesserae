"""Admin schedule routes: list, create, edit, delete, fire.

A single ``/schedules`` page lists every schedule plus an inline 'new'
form. ``/schedules/<id>/edit`` is an in-page form for an existing row.
Delete and fire-now are POST endpoints.

Manual trigger ('fire now') bypasses every gate, the user is asking
for a push, give them one, but it still goes through the same
PushManager.push() the scheduler uses, so single-flight and renderer
fanout work the same way.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
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
from app.tz_resolve import app_timezone

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


def _unique_schedule_id(base: str) -> str:
    taken = {s.id for s in _store().all()}
    candidate = base
    n = 1
    while candidate in taken:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


def _parse_conditions_json(raw: str) -> list[dict[str, Any]]:
    """Parse the form's ``conditions_json`` textarea contents. Empty
    text means "no conditions"; otherwise the textarea must contain a
    JSON array. Each element is passed through to the model which
    validates per-source-kind shape via ``Condition``. JSON syntax
    errors raise ``ValidationError`` so the same flash-and-re-render
    path the rest of ``_parse_form`` uses surfaces them in the editor.
    """
    import json

    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError.from_exception_data(
            "Schedule",
            [
                {
                    "type": "json_invalid",
                    "loc": ("conditions",),
                    "input": text,
                    "ctx": {"error": str(exc)},
                }
            ],
        ) from exc
    if not isinstance(parsed, list):
        raise ValidationError.from_exception_data(
            "Schedule",
            [{"type": "list_type", "loc": ("conditions",), "input": parsed}],
        )
    return parsed


def _parse_form(form: dict[str, Any], *, existing_id: str | None = None) -> Schedule:
    """Build a Schedule from a form dict. Raises ValidationError on bad
    input, the caller flashes and re-renders.

    Auto-generates an id from the name when creating; existing schedules
    keep their id (pinned by the URL in the update route)."""
    name = (form.get("name") or "").strip()
    schedule_id = existing_id if existing_id is not None else _unique_schedule_id(_slug_from(name))
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
        # Smart sync (issue #10). Only the interval form shows these,
        # but accept them on daily submissions too without crashing,
        # the model ignores them for daily-type schedules.
        "smart_sync": form.get("smart_sync") in ("on", "true", "1"),
        "smart_sync_lead_s": int(form.get("smart_sync_lead_s") or 10),
        # v0.48: conditional schedules + fallback page. Empty inputs
        # round-trip to existing-default behaviour (no conditions,
        # no fallback).
        "conditions": _parse_conditions_json(form.get("conditions_json") or ""),
        "fallback_page_id": (form.get("fallback_page_id") or "").strip() or None,
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


def _project_fires(schedule: Schedule, start: datetime, end: datetime) -> list[datetime]:
    """Cheap, deterministic projection of when a schedule will fire in
    [start, end). Used by the timeline visualisation only; the real
    scheduler still decides what actually runs."""
    if not schedule.enabled:
        return []
    fires: list[datetime] = []
    cur = start.replace(second=0, microsecond=0)
    if schedule.type == "daily":
        assert schedule.fires_at is not None
        target_t = schedule.fires_at.time()
        day = cur.date()
        while day <= end.date():
            if day.weekday() in schedule.days_of_week:
                # Match the tz-awareness of the window bounds so the comparison
                # is valid (start/end are tz-aware from _build_timeline; naive
                # when a caller passes naive bounds).
                candidate = datetime.combine(day, target_t, tzinfo=start.tzinfo)
                if start <= candidate < end:
                    fires.append(candidate)
            day = day + timedelta(days=1)
        return fires

    assert schedule.interval_minutes is not None
    step = timedelta(minutes=schedule.interval_minutes)
    window_start = schedule.time_of_day_start
    window_end = schedule.time_of_day_end

    def _in_window(dt: datetime) -> bool:
        if dt.weekday() not in schedule.days_of_week:
            return False
        if window_start is None and window_end is None:
            return True
        hhmm = dt.strftime("%H:%M")
        if window_start and hhmm < window_start:
            return False
        return not (window_end and hhmm > window_end)

    t = cur
    safety = 0
    while t < end and safety < 500:
        if _in_window(t):
            fires.append(t)
        t = t + step
        safety += 1
    return fires


def _build_timeline(schedules: Iterable[Schedule], hours: int = 24) -> dict[str, Any]:
    """Bucket projected fires per schedule across the next ``hours``
    hours, plus hour markers and a "now" position. The window snaps
    its start to the top of the current hour so the now-marker falls
    visibly inside the timeline rather than flush against the left
    edge (which is what you'd get if start == now)."""
    # Anchor "now" in the operator's configured timezone, not the server's
    # container TZ (UTC on typical Docker installs). A naive datetime.now()
    # here put the now-marker and hour ticks an offset off the wall clock the
    # user actually reads (#164, #170; same class as #143).
    now = datetime.now(app_timezone())
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=hours)
    total_seconds = (end - start).total_seconds()

    def _pct(dt: datetime) -> float:
        return max(0.0, min(100.0, (dt - start).total_seconds() / total_seconds * 100.0))

    rows: list[dict[str, Any]] = []
    for s in schedules:
        fires = _project_fires(s, now, end)
        rows.append(
            {
                "id": s.id,
                "name": s.name,
                "enabled": s.enabled,
                "kind": s.type,
                "fires": [{"at": dt, "pct": _pct(dt)} for dt in fires[:200]],
                "count": len(fires),
            }
        )
    hour_marks = [
        {"hour": (start + timedelta(hours=h)).hour, "pct": (h / hours) * 100.0}
        for h in range(hours + 1)
        if h % 3 == 0
    ]
    return {
        "start": start,
        "end": end,
        "now": now,
        "now_pct": _pct(now),
        "rows": rows,
        "hour_marks": hour_marks,
        "any_fires": any(r["fires"] for r in rows),
    }


def _relative(epoch: float | None) -> str:
    """Short 'time since' label for the last-fired column."""
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


def _last_fired_view(epoch: float | None) -> dict[str, str] | None:
    """Humanised last-fired: a relative label + an absolute tooltip.
    ``None`` when the schedule has never fired."""
    if not epoch:
        return None
    return {
        "rel": _relative(epoch),
        "abs": datetime.fromtimestamp(epoch, app_timezone()).strftime("%Y-%m-%d %H:%M"),
    }


def _running_state_view(schedule: Schedule, status_row: dict[str, Any] | None) -> dict[str, str]:
    """Compute the v0.48 running-state pill for ``schedule``.

    The pill collapses (enabled? + last_status + last_reason) into a
    single ``{label, tone, icon, tooltip}`` view-model so the template
    can render it with one ``{{ … }}`` block. Tone strings map to the
    ``pill.is-*`` modifier classes in schedules.css.
    """
    if not schedule.enabled:
        return {
            "label": "disabled",
            "tone": "",
            "icon": "pause",
            "tooltip": "This schedule is disabled and won't fire.",
        }
    last_status = (status_row or {}).get("last_status")
    last_reason = (status_row or {}).get("last_reason")
    if last_status is None:
        return {
            "label": "pending",
            "tone": "",
            "icon": "hourglass",
            "tooltip": "Waiting for the first fire since the scheduler started.",
        }
    if last_status == "held":
        return {
            "label": "held",
            "tone": "is-held",
            "icon": "funnel",
            "tooltip": f"Last tick was silently held: {last_reason or 'conditions not met'}.",
        }
    if last_status == "sent":
        if last_reason and "fallback" in last_reason:
            return {
                "label": "fallback",
                "tone": "is-warn",
                "icon": "arrow-bend-down-right",
                "tooltip": ("Conditions failed, so the fallback page was pushed instead."),
            }
        return {
            "label": "active",
            "tone": "is-ok",
            "icon": "check-circle",
            "tooltip": "Last fire was sent successfully.",
        }
    if last_status == "quiet":
        return {
            "label": "quiet hours",
            "tone": "",
            "icon": "moon",
            "tooltip": "Devices are in quiet hours; pushes will resume after the window.",
        }
    if last_status == "failed":
        return {
            "label": "failed",
            "tone": "is-danger",
            "icon": "warning-circle",
            "tooltip": last_reason or "Last fire failed; see the events log.",
        }
    return {
        "label": last_status,
        "tone": "",
        "icon": "circle",
        "tooltip": last_reason or "",
    }


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
        # id -> display name so the table shows the dashboard's name, not
        # its opaque id. Missing ids (deleted page) fall back in-template.
        page_names={p.id: p.name for p in pages},
        last_fired={sid: _last_fired_view(st.get("last_fired")) for sid, st in status.items()},
        running_states={s.id: _running_state_view(s, status.get(s.id)) for s in schedules},
        timeline=_build_timeline(schedules),
        edit_id=request.args.get("edit"),
        smart_sync_states=_smart_sync_states(schedules, pages),
        # ``prefill_page`` lets the page editor's inline schedule card
        # link straight here with the dashboard already selected in
        # the New-schedule form. Just affects the blank-form default;
        # editing existing schedules is unaffected.
        prefill_page=request.args.get("prefill_page", ""),
    )


def _smart_sync_states(schedules: list[Schedule], pages: list[Any]) -> dict[str, dict[str, str]]:
    """Per-schedule smart-sync indicator state for the list view dot.

    States:
      - ``off``    : ``smart_sync`` disabled. No dot.
      - ``warming``: enabled, page has bound devices, but none are
                    trusted yet (warming-up window). Yellow.
      - ``active`` : enabled, at least one bound device is trusted;
                    JIT firing is live. Green.
      - ``blocked``: enabled but the page has no bound devices, so
                    smart-sync will never fire and the schedule
                    permanently falls back to interval cadence. Red.

    Falls back to ``off`` cleanly when the telemetry store isn't
    wired (test path / bare run)."""
    telemetry = current_app.config.get("DEVICE_TELEMETRY")
    out: dict[str, dict[str, str]] = {}
    page_by_id = {p.id: p for p in pages}
    for s in schedules:
        if not s.smart_sync:
            out[s.id] = {"state": "off", "tooltip": "Smart sync off"}
            continue
        page = page_by_id.get(s.page_id)
        if page is None or not page.device_ids:
            out[s.id] = {
                "state": "blocked",
                "tooltip": (
                    "Smart sync on but the page has no bound devices, "
                    "the schedule falls back to its interval cadence"
                ),
            }
            continue
        if telemetry is None:
            out[s.id] = {"state": "warming", "tooltip": "Smart sync enabled"}
            continue
        trusted = 0
        for device_id in page.device_ids:
            entry = telemetry.get(device_id)
            if entry is not None and entry.is_trusted:
                trusted += 1
        if trusted == 0:
            out[s.id] = {
                "state": "warming",
                "tooltip": (
                    f"Smart sync warming up, {len(page.device_ids)} device"
                    f"{'' if len(page.device_ids) == 1 else 's'} bound but "
                    "none trusted yet (need 3 consecutive on-time wakes)"
                ),
            }
        else:
            out[s.id] = {
                "state": "active",
                "tooltip": (
                    f"Smart sync active, {trusted} of {len(page.device_ids)} "
                    "bound device(s) trusted"
                ),
            }
    return out


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
        flash(f"Fired {schedule_id!r}: {result.status}, {result.error or ''}", "error")
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
