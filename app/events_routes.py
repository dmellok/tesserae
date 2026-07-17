"""Cross-cutting events timeline.

A dedicated page that reads the same EventLog the Send-page history reads -
but unfiltered. Chips at the top scope to one event type at a time.

Types in v1: ``push``, ``renderer``, ``device``, ``scheduler``, ``auth``,
``telemetry``.

The ``/events/stream`` endpoint is a Server-Sent Events feed: every new
row written to the EventLog is pushed to subscribed clients. Dev werkzeug
supports streaming responses out of the box (threaded=True is Flask's
default). In production behind a reverse proxy, set
``proxy_buffering off`` (nginx), otherwise the proxy will queue events
and the page won't feel live.
"""

from __future__ import annotations

import json
import queue
import time
from collections.abc import Iterator

from flask import (
    Blueprint,
    Flask,
    Response,
    current_app,
    render_template,
    request,
)

from app.state.event_log import EventLog, EventRow

bp = Blueprint("events", __name__, url_prefix="/events")


_KNOWN_TYPES: tuple[str, ...] = (
    "push",
    "touch",
    "renderer",
    "device",
    "scheduler",
    "conditions",
    "auth",
    "telemetry",
)
_MAX_LIMIT: int = 500
# Default page size, keep tight so the IMG thumbnails on /events stay
# in a reasonable bitmap-cache footprint. Each push row's thumbnail
# decodes to ~0.4 MB even with the ``?w=240`` variant; 100 rows ≈ 40 MB
# of decoded images, which is fine. A flood of pushes would otherwise
# trail off into the panel-PNG bitmap pile in Chromium's cache and
# eat multi-GB of an idle admin tab, that was the v0.16.3 fix.
_DEFAULT_LIMIT: int = 100


def _events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


@bp.get("")
def index() -> str:
    log = _events()
    selected = (request.args.get("type") or "all").strip().lower()
    try:
        limit = min(int(request.args.get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT)
    except ValueError:
        limit = _DEFAULT_LIMIT
    if selected == "all" or selected not in _KNOWN_TYPES:
        rows = log.list(limit=limit)
        type_filter = None
    else:
        rows = log.list(type=selected, limit=limit)
        type_filter = selected

    counts = {t: log.count(type=t) for t in _KNOWN_TYPES}
    counts["all"] = sum(counts.values())

    # Page id → friendly name lookup for the conditions-event display;
    # resolved at render time so renaming a page later doesn't leave
    # historical events showing the stale name. Falls back to the id
    # when the page is missing from the store.
    page_store = current_app.config.get("PAGE_STORE")
    page_names = {p.id: p.name for p in page_store.list()} if page_store else {}

    return render_template(
        "events.html",
        rows=rows,
        type_filter=type_filter,
        selected=selected,
        counts=counts,
        known_types=_KNOWN_TYPES,
        limit=limit,
        page_names=page_names,
    )


# SSE knobs. Keepalives prove the connection is alive when the log is
# idle, without them, nginx / browsers eventually close the connection
# and the page stops feeling live. The interval is also the upper bound
# on how long a *closed* client connection stays bound to a waitress
# worker thread — the generator only learns the socket is dead when it
# tries to yield, so a long interval means a stale connection can hold
# a worker for that long. With ``threads=8`` and 8 stale clients (e.g.
# rapid chip-clicking on /events) the whole pool can wedge until the
# next yield round. Tight here so cleanup happens within ~2 seconds.
_KEEPALIVE_INTERVAL_S: float = 2.0
# Per-connection inbound queue. Generous because the page caps the DOM at
# 200 rows so a burst that overflows here is moot.
_QUEUE_MAX: int = 200


def _serialise_row(row: EventRow) -> dict[str, object]:
    return {
        "id": row.id,
        "type": row.type,
        "timestamp": row.timestamp,
        "source": row.source,
        "target": row.target,
        "status": row.status,
        "digest": row.digest,
        "error": row.error,
        "duration_s": row.duration_s,
        "extra": row.extra,
    }


@bp.get("/stream")
def stream() -> Response:
    """Server-Sent Events feed of new event rows. Optional ``?type=`` filter
    scopes to one event type (matching the chips on /events)."""
    log = _events()
    type_filter = (request.args.get("type") or "").strip().lower() or None
    q: queue.Queue[EventRow] = queue.Queue(maxsize=_QUEUE_MAX)

    def on_event(row: EventRow) -> None:
        if type_filter is not None and row.type != type_filter:
            return
        # put_nowait drops on overflow so a slow client can't backpressure
        # the writer that recorded the event.
        try:
            q.put_nowait(row)
        except queue.Full:
            return

    log.add_listener(on_event)

    def generate() -> Iterator[str]:
        # Initial comment frame so the client knows the connection is up
        # before the first real event arrives.
        yield ":connected\n\n"
        last_send = time.monotonic()
        try:
            while True:
                timeout = max(0.0, _KEEPALIVE_INTERVAL_S - (time.monotonic() - last_send))
                try:
                    row = q.get(timeout=timeout or 0.1)
                    payload = json.dumps(_serialise_row(row), default=str)
                    yield f"event: log\ndata: {payload}\n\n"
                    last_send = time.monotonic()
                except queue.Empty:
                    yield ":keepalive\n\n"
                    last_send = time.monotonic()
        finally:
            log.remove_listener(on_event)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Proxies must not buffer; otherwise events arrive in bursts.
            "X-Accel-Buffering": "no",
        },
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
