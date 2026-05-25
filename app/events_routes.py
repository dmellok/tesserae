"""Cross-cutting events timeline.

A dedicated page that reads the same EventLog the Send-page history reads —
but unfiltered. Chips at the top scope to one event type at a time.

Types in v1: ``push``, ``renderer``, ``device``, ``scheduler``, ``auth``.
New types (added by future plugins / integrations) appear automatically
because the chip list is built from the rows currently in the log.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    Flask,
    current_app,
    render_template,
    request,
)

from app.state.event_log import EventLog

bp = Blueprint("events", __name__, url_prefix="/events")


_KNOWN_TYPES: tuple[str, ...] = ("push", "renderer", "device", "scheduler", "auth")
_MAX_LIMIT: int = 500


def _events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


@bp.get("")
def index() -> str:
    log = _events()
    selected = (request.args.get("type") or "all").strip().lower()
    try:
        limit = min(int(request.args.get("limit") or 200), _MAX_LIMIT)
    except ValueError:
        limit = 200
    if selected == "all" or selected not in _KNOWN_TYPES:
        rows = log.list(limit=limit)
        type_filter = None
    else:
        rows = log.list(type=selected, limit=limit)
        type_filter = selected

    counts = {t: log.count(type=t) for t in _KNOWN_TYPES}
    counts["all"] = sum(counts.values())

    return render_template(
        "events.html",
        rows=rows,
        type_filter=type_filter,
        selected=selected,
        counts=counts,
        known_types=_KNOWN_TYPES,
        limit=limit,
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
