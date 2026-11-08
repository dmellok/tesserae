"""Settings → Logs: a live view of the server's own log output.

Reads the in-memory ring in :mod:`app.log_buffer`. ``/logs`` renders the
newest lines at the chosen watch level; ``/logs/stream`` pushes new lines
over Server-Sent Events (same keepalive and proxy notes as
``/events/stream``); ``/logs/lines`` is the JSON poll fallback;
``/logs/record_level`` changes what the process records until restart;
``/logs/download`` saves the lines as text.
"""

from __future__ import annotations

import json
import logging
import queue
import time
from collections.abc import Iterator
from datetime import datetime

from flask import Blueprint, Flask, Response, current_app, jsonify, render_template, request

from app import log_buffer
from app.log_buffer import LEVELS, LogBuffer, LogLine, parse_level

bp = Blueprint("logs", __name__, url_prefix="/logs")
logger = logging.getLogger(__name__)

_DEFAULT_WATCH = "info"
_PAGE_LINES = 500
_KEEPALIVE_INTERVAL_S = 2.0
_QUEUE_MAX = 500


def _buffer() -> LogBuffer:
    return current_app.config["LOG_BUFFER"]  # type: ignore[no-any-return]


def _watch_level() -> tuple[str, int]:
    raw = request.args.get("level") or _DEFAULT_WATCH
    value = parse_level(raw)
    if value is None:
        return _DEFAULT_WATCH, logging.INFO
    return log_buffer.level_name(value), value


def _after() -> int:
    try:
        return max(0, int(request.args.get("after") or 0))
    except ValueError:
        return 0


def _record_state() -> dict[str, object]:
    current = log_buffer.record_level()
    return {
        "record_level": log_buffer.level_name(current),
        "record_levelno": current,
        "startup_level": current_app.config.get(
            "LOG_STARTUP_LEVEL", log_buffer.level_name(current)
        ),
    }


@bp.get("")
def index() -> str:
    buf = _buffer()
    watch, watch_no = _watch_level()
    lines = buf.lines(min_level=watch_no, limit=_PAGE_LINES)
    counts = buf.counts()
    names = [name for name, _ in LEVELS]
    # "error and up" folds critical in, and so on down.
    at_or_above = {name: sum(counts[n] for n in names[i:]) for i, name in enumerate(names)}
    return render_template(
        "logs.html",
        lines=lines,
        watch=watch,
        watch_no=watch_no,
        levels=[name for name, _ in LEVELS],
        at_or_above=at_or_above,
        capacity=buf.capacity,
        page_lines=_PAGE_LINES,
        last_seq=buf.last_seq,
        **_record_state(),
    )


@bp.get("/lines")
def lines() -> Response:
    buf = _buffer()
    _watch, watch_no = _watch_level()
    rows = buf.lines(
        min_level=watch_no, after=_after(), limit=_PAGE_LINES, query=request.args.get("q")
    )
    return jsonify(
        {"lines": [ln.to_dict() for ln in rows], "last_seq": buf.last_seq, **_record_state()}
    )


@bp.post("/record_level")
def set_record_level() -> Response:
    body = request.get_json(silent=True) or request.form
    value = parse_level(body.get("level"))
    if value is None or value == logging.CRITICAL:
        return jsonify({"ok": False, "error": "level must be debug, info, warning or error"}), 400  # type: ignore[return-value]
    before = log_buffer.level_name(log_buffer.record_level())
    if before == log_buffer.level_name(value):
        return jsonify({"ok": True, **_record_state()})
    log_buffer.set_record_level(value)
    # Logged at WARNING so the change is visible at any watch level.
    logger.warning(
        "log record level changed from %s to %s from Settings → Logs (until restart)",
        before,
        log_buffer.level_name(value),
    )
    return jsonify({"ok": True, **_record_state()})


@bp.post("/clear")
def clear() -> Response:
    _buffer().clear()
    return jsonify({"ok": True})


@bp.get("/download")
def download() -> Response:
    buf = _buffer()
    watch, watch_no = _watch_level()
    out = []
    for ln in buf.lines(min_level=watch_no, query=request.args.get("q")):
        stamp = datetime.fromtimestamp(ln.ts).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        out.append(f"{stamp} {ln.level.upper()} {ln.logger}, {ln.message}")
        if ln.exc:
            out.append(ln.exc)
    name = f"tesserae-{watch}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    return Response(
        "\n".join(out) + ("\n" if out else ""),
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@bp.get("/stream")
def stream() -> Response:
    buf = _buffer()
    _watch, watch_no = _watch_level()
    after = _after()
    q: queue.Queue[LogLine] = queue.Queue(maxsize=_QUEUE_MAX)

    def on_line(line: LogLine) -> None:
        if line.levelno < watch_no:
            return
        try:
            q.put_nowait(line)
        except queue.Full:
            return

    buf.add_listener(on_line)
    # Lines written between the page render and this connection opening.
    backlog = buf.lines(min_level=watch_no, after=after, limit=_PAGE_LINES) if after else []

    def generate() -> Iterator[str]:
        yield ":connected\n\n"
        seen = after
        for line in backlog:
            seen = line.seq
            yield f"event: line\ndata: {json.dumps(line.to_dict())}\n\n"
        last_send = time.monotonic()
        try:
            while True:
                timeout = max(0.0, _KEEPALIVE_INTERVAL_S - (time.monotonic() - last_send))
                try:
                    line = q.get(timeout=timeout or 0.1)
                    if line.seq <= seen:
                        continue
                    seen = line.seq
                    yield f"event: line\ndata: {json.dumps(line.to_dict())}\n\n"
                except queue.Empty:
                    yield ":keepalive\n\n"
                last_send = time.monotonic()
        finally:
            buf.remove_listener(on_line)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def register(app: Flask) -> None:
    buf = log_buffer.install()
    app.config["LOG_BUFFER"] = buf
    app.config.setdefault("LOG_STARTUP_LEVEL", log_buffer.level_name(log_buffer.record_level()))
    app.register_blueprint(bp)
