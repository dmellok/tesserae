"""Live view of what the MCP agent is doing.

Every authorised call to the MCP surface (:mod:`app.mcp_api`) is recorded here
as one *step*: a friendly label, what it touched, how long it took, whether it
worked. Steps group into *runs* - a run is one uninterrupted burst of agent
work, split whenever the surface goes quiet for ``_RUN_IDLE_S``.

Nothing is persisted. This is live scaffolding for the UI (the pipeline rail in
the canvas editor, the follow-the-agent toast in the admin shell), not an audit
trail; the EventLog already records the durable outcomes.

Two read surfaces, deliberately different:

* ``/agent/stream`` is Server-Sent Events, for the canvas editor, where a step
  should land the instant it happens. An open stream pins a waitress worker
  thread for its lifetime (see ``app/main.py``), so it's reserved for the one
  or two tabs that actually watch the build.
* ``/agent/activity.json`` is a plain snapshot the admin shell polls while its
  tab is visible. The follow toast doesn't need sub-second latency and every
  open Tesserae tab would otherwise hold a thread it never uses.

The bus is in-process: it reflects the agent only when the bridge talks to
*this* worker. Tesserae runs a single application process, so that holds.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from flask import Blueprint, Flask, abort, current_app, jsonify, request
from werkzeug.wrappers import Response

from app import experiments

logger = logging.getLogger(__name__)

bp = Blueprint("agent_activity", __name__, url_prefix="/agent")

_EXPERIMENT = "mcp"

# A gap this long ends a run: the next call starts a fresh pipeline in the UI
# rather than appending to a build the operator watched finish minutes ago.
_RUN_IDLE_S: float = 45.0
# Steps kept in memory. A busy build (streaming a code element chunk by chunk)
# is a few dozen calls, so this holds several runs.
_CAP: int = 300


# -- the step table -----------------------------------------------------
#
# Endpoint name (minus the ``mcp_api.`` prefix) -> how the UI narrates it.
# ``kind`` drives the rail's typography: ``build`` steps are the spine of the
# pipeline, ``render`` / ``send`` are milestones, and ``probe`` steps are the
# agent reading the room (dimmed, collapsible - there are a lot of them).

_PROBE = "probe"
_BUILD = "build"
_RENDER = "render"
_SEND = "send"


@dataclass(frozen=True)
class StepSpec:
    """How one endpoint is narrated.

    ``label`` names the step in a list ("Add element"); ``verb`` is the present
    participle the UI leads with while it's the step in flight ("Adding an
    element"). Both live here rather than being derived in JavaScript, so the
    phrasing is editable in one place and the client stays a renderer.
    """

    label: str
    verb: str
    icon: str
    kind: str


_STEPS: dict[str, StepSpec] = {
    # reading the room
    "catalog": StepSpec("Read catalog", "Reading the widget catalog", "books", _PROBE),
    "icons": StepSpec("List icons", "Looking through the icons", "smiley", _PROBE),
    "services": StepSpec("List services", "Checking the services", "plugs", _PROBE),
    "widget_options": StepSpec(
        "Read widget options", "Reading a widget's options", "sliders-horizontal", _PROBE
    ),
    "widget_choices": StepSpec(
        "Read widget choices", "Reading a widget's choices", "list-checks", _PROBE
    ),
    "widget_data": StepSpec(
        "Probe widget data", "Probing live widget data", "magnifying-glass", _PROBE
    ),
    "probe_url": StepSpec("Probe URL", "Probing a URL", "globe", _PROBE),
    "list_authored_widgets": StepSpec(
        "List widgets", "Listing installed widgets", "shapes", _PROBE
    ),
    "widget_render": StepSpec("Render widget", "Rendering a widget", "image", _PROBE),
    "devices": StepSpec("List devices", "Looking for panels", "devices", _PROBE),
    "list_pages": StepSpec("List dashboards", "Listing the dashboards", "cards", _PROBE),
    "get_canvas": StepSpec("Read canvas", "Reading the canvas", "file-code", _PROBE),
    "list_page_assets": StepSpec("List images", "Listing the images", "images", _PROBE),
    "describe_actions": StepSpec(
        "Read action reference", "Reading the action reference", "book-open", _PROBE
    ),
    "measure_text": StepSpec("Measure text", "Measuring text", "ruler", _PROBE),
    "mcp_list_rotations": StepSpec(
        "List rotations", "Listing rotations", "arrows-clockwise", _PROBE
    ),
    "mcp_list_schedules": StepSpec("List schedules", "Listing schedules", "clock", _PROBE),
    "mcp_list_decks": StepSpec("List lineups", "Listing lineups", "stack", _PROBE),
    "mcp_suggest_decks": StepSpec("Suggest lineups", "Working out lineups", "lightbulb", _PROBE),
    "instructions": StepSpec("Read instructions", "Reading the handbook", "book-open", _PROBE),
    # building the dashboard
    "create_page": StepSpec("Create dashboard", "Creating the dashboard", "plus-square", _BUILD),
    "delete_page": StepSpec("Delete dashboard", "Deleting a dashboard", "trash", _BUILD),
    "set_canvas": StepSpec("Write canvas", "Writing the whole canvas", "file-code", _BUILD),
    "patch_canvas": StepSpec("Adjust canvas", "Adjusting the canvas", "pencil-simple", _BUILD),
    "append_element": StepSpec("Add element", "Adding an element", "plus-circle", _BUILD),
    "append_elements_bulk": StepSpec("Add elements", "Adding elements", "squares-four", _BUILD),
    "patch_element": StepSpec("Update element", "Reworking an element", "pencil-simple", _BUILD),
    "append_element_code": StepSpec("Stream code", "Streaming code in", "code", _BUILD),
    "delete_element": StepSpec("Remove element", "Removing an element", "minus-circle", _BUILD),
    "generate_background": StepSpec(
        "Generate background", "Generating a background", "paint-brush-broad", _BUILD
    ),
    "layout": StepSpec("Arrange", "Arranging the layout", "grid-four", _BUILD),
    "add_page_asset": StepSpec("Add image", "Adding an image", "image-square", _BUILD),
    "delete_page_asset": StepSpec("Remove image", "Removing an image", "trash", _BUILD),
    "install_widget": StepSpec("Install widget", "Installing a widget", "download-simple", _BUILD),
    "uninstall_widget": StepSpec("Uninstall widget", "Uninstalling a widget", "trash", _BUILD),
    "reload_plugins": StepSpec("Reload plugins", "Reloading plugins", "arrows-clockwise", _BUILD),
    "bind_devices": StepSpec("Bind devices", "Binding the panels", "devices", _BUILD),
    "mcp_create_rotation": StepSpec(
        "Create rotation", "Setting up a rotation", "arrows-clockwise", _BUILD
    ),
    "mcp_delete_rotation": StepSpec("Delete rotation", "Deleting a rotation", "trash", _BUILD),
    "mcp_create_schedule": StepSpec("Create schedule", "Setting up a schedule", "clock", _BUILD),
    "mcp_delete_schedule": StepSpec("Delete schedule", "Deleting a schedule", "trash", _BUILD),
    "mcp_create_deck": StepSpec("Create lineup", "Setting up a lineup", "stack", _BUILD),
    "mcp_delete_deck": StepSpec("Delete lineup", "Deleting a lineup", "trash", _BUILD),
    # looking at the result / shipping it
    "preview": StepSpec("Render preview", "Rendering a preview", "image", _RENDER),
    "render_report": StepSpec("Check render", "Checking the render", "clipboard-text", _RENDER),
    "push": StepSpec("Push to panel", "Pushing to the panel", "paper-plane-tilt", _SEND),
}

# Endpoints whose *response* carries the interesting number. Everything else is
# summarised from the request alone, so the common path never parses a body it
# doesn't need.
_READS_RESPONSE: frozenset[str] = frozenset(
    {"append_element", "append_elements_bulk", "append_element_code", "push", "create_page"}
)


def _spec(endpoint: str) -> StepSpec:
    spec = _STEPS.get(endpoint)
    if spec is not None:
        return spec
    # An endpoint added to mcp_api without a table entry still narrates,
    # just generically: "Append element code" from "append_element_code".
    label = endpoint.replace("_", " ").strip().capitalize() or "Agent call"
    return StepSpec(label, "Working on " + label.lower(), "circle", _PROBE)


# -- the bus ------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One MCP call, as the UI narrates it.

    ``target`` is what it touched (a widget key, a device count, an element
    kind); ``detail`` is the quantity worth showing next to it (bytes streamed,
    elements added). Both are display strings - nothing parses them back.
    """

    seq: int
    run: int
    ts: float
    endpoint: str
    label: str
    verb: str
    icon: str
    kind: str
    status: str  # ok | error
    code: int
    duration_ms: int
    target: str = ""
    detail: str = ""
    page_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run": self.run,
            "ts": self.ts,
            "endpoint": self.endpoint,
            "label": self.label,
            "verb": self.verb,
            "icon": self.icon,
            "kind": self.kind,
            "status": self.status,
            "code": self.code,
            "duration_ms": self.duration_ms,
            "target": self.target,
            "detail": self.detail,
            "page_id": self.page_id,
        }


@dataclass
class ActivityBus:
    """In-memory ring of recent steps, with listeners for the SSE feed."""

    cap: int = _CAP
    run_idle_s: float = _RUN_IDLE_S
    _steps: deque[Step] = field(default_factory=lambda: deque(maxlen=_CAP), init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _listeners: list[Callable[[Step], None]] = field(default_factory=list, init=False)
    _listener_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _seq: int = field(default=0, init=False)
    _run: int = field(default=0, init=False)
    _last_ts: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if self.cap != _CAP:
            self._steps = deque(maxlen=self.cap)

    # -- listeners ------------------------------------------------------

    def add_listener(self, callback: Callable[[Step], None]) -> None:
        with self._listener_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Step], None]) -> None:
        with self._listener_lock, contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def _emit(self, step: Step) -> None:
        with self._listener_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(step)
            except Exception:
                logger.exception("agent activity listener %r raised", cb)

    # -- writing --------------------------------------------------------

    def record(
        self,
        *,
        endpoint: str,
        status: str,
        code: int,
        duration_ms: int,
        target: str = "",
        detail: str = "",
        page_id: str | None = None,
        now: float | None = None,
    ) -> Step:
        spec = _spec(endpoint)
        ts = time.time() if now is None else now
        with self._lock:
            if ts - self._last_ts > self.run_idle_s:
                self._run += 1
            self._last_ts = ts
            self._seq += 1
            step = Step(
                seq=self._seq,
                run=self._run,
                ts=ts,
                endpoint=endpoint,
                label=spec.label,
                verb=spec.verb,
                icon=spec.icon,
                kind=spec.kind,
                status=status,
                code=code,
                duration_ms=duration_ms,
                target=target,
                detail=detail,
                page_id=page_id,
            )
            self._steps.append(step)
        # Fire outside the lock so a slow subscriber can't stall the agent's
        # request, same rule the EventLog follows.
        self._emit(step)
        return step

    # -- reading --------------------------------------------------------

    def snapshot(self, *, since: int = 0, limit: int = 120) -> tuple[list[Step], int, int]:
        """Steps newer than ``since``, the current run number, and the latest
        seq (so a poller can ask for the delta next time)."""
        with self._lock:
            steps = [s for s in self._steps if s.seq > since][-limit:]
            return steps, self._run, self._seq

    def current_run(self, *, limit: int = 120) -> list[Step]:
        with self._lock:
            run = self._run
            return [s for s in self._steps if s.run == run][-limit:]

    def idle_for(self, *, now: float | None = None) -> float:
        """Seconds since the last step; ``inf`` when nothing has happened."""
        with self._lock:
            if self._last_ts <= 0:
                return float("inf")
            return (time.time() if now is None else now) - self._last_ts


def bus() -> ActivityBus:
    got: ActivityBus = current_app.config["AGENT_ACTIVITY"]
    return got


# -- request summaries --------------------------------------------------


def _kb(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def summarise(
    endpoint: str,
    view_args: dict[str, Any] | None,
    body: Any,
    payload: dict[str, Any] | None,
) -> tuple[str, str, str | None]:
    """``(target, detail, page_id)`` for one MCP call.

    Kept next to the step table so adding a tool means editing one file. Never
    raises: a summary is decoration, and a malformed body already failed
    validation in the route that produced it.
    """
    args = view_args or {}
    page_id = args.get("page_id") or args.get("canvas_id")
    b = body if isinstance(body, dict) else {}
    p = payload or {}
    target = ""
    detail = ""

    if endpoint in ("widget_options", "widget_choices", "widget_data", "widget_render"):
        target = str(args.get("key") or "")
    elif endpoint == "create_page":
        target = str(b.get("name") or "")
        w, h = b.get("w"), b.get("h")
        detail = f"{w}x{h}" if w and h else ""
        page_id = str(p.get("id") or "") or page_id
    elif endpoint == "append_element":
        target = str(b.get("kind") or "element")
        src = b.get("source") or b.get("field")
        detail = str(src) if src else ""
    elif endpoint == "append_elements_bulk":
        els = b.get("elements")
        n = len(els) if isinstance(els, list) else 0
        target = f"{n} element{'' if n == 1 else 's'}"
        kinds = (
            [str(e.get("kind")) for e in els if isinstance(e, dict) and e.get("kind")]
            if isinstance(els, list)
            else []
        )
        detail = ", ".join(sorted(set(kinds))[:3])
    elif endpoint in ("patch_element", "delete_element"):
        target = str(args.get("element_id") or "")
        if endpoint == "patch_element":
            detail = ", ".join(sorted(k for k in b if k != "id")[:3])
    elif endpoint == "append_element_code":
        target = str(b.get("field") or "")
        text = b.get("text")
        chunk = len(text) if isinstance(text, str) else 0
        total = p.get("length")
        detail = f"+{_kb(chunk)}" + (f" of {_kb(int(total))}" if isinstance(total, int) else "")
    elif endpoint == "set_canvas":
        els = b.get("els")
        n = len(els) if isinstance(els, list) else 0
        target = f"{n} element{'' if n == 1 else 's'}"
    elif endpoint == "patch_canvas":
        target = ", ".join(sorted(k for k in b if k != "id")[:3])
    elif endpoint == "bind_devices":
        ids = b.get("device_ids")
        n = len(ids) if isinstance(ids, list) else 0
        target = f"{n} device{'' if n == 1 else 's'}"
        detail = ", ".join(str(x) for x in ids[:2]) if isinstance(ids, list) else ""
    elif endpoint == "push":
        sent = p.get("sent")
        n = len(sent) if isinstance(sent, list) else 0
        target = f"{n} panel{'' if n == 1 else 's'}"
        errs = p.get("errors")
        detail = f"{len(errs)} failed" if isinstance(errs, list) and errs else ""
    elif endpoint == "generate_background":
        target = str(b.get("prompt") or "")[:60]
    elif endpoint == "layout":
        target = str(b.get("preset") or b.get("mode") or "")
    elif endpoint == "measure_text":
        target = str(b.get("text") or "")[:40]
    elif endpoint in ("mcp_create_rotation", "mcp_create_schedule", "mcp_create_deck"):
        target = str(b.get("name") or b.get("id") or "")
    elif endpoint == "probe_url":
        target = str(b.get("url") or "")[:60]

    return target, detail, (str(page_id) if page_id else None)


# -- routes -------------------------------------------------------------


def _guard() -> None:
    """404 the whole blueprint unless the ``mcp`` experiment is on: with no
    agent surface there is no agent to watch."""
    if not experiments.is_enabled(_EXPERIMENT):
        abort(404)


def _page_names() -> dict[str, str]:
    store = current_app.config.get("PAGE_STORE")
    if store is None:
        return {}
    try:
        return {p.id: p.name for p in store.list()}
    except Exception:
        return {}


def _serialise(step: Step, names: dict[str, str]) -> dict[str, Any]:
    out = step.as_dict()
    if step.page_id:
        out["page_name"] = names.get(step.page_id, "")
    return out


@bp.get("/activity.json")
def activity() -> Response:
    """Snapshot of recent agent steps. ``?since=<seq>`` returns only what is
    newer, so the admin shell's poll stays a few hundred bytes once warm."""
    _guard()
    try:
        since = int(request.args.get("since") or 0)
    except ValueError:
        since = 0
    steps, run, seq = bus().snapshot(since=since)
    names = _page_names()
    return jsonify(
        {
            "run": run,
            "seq": seq,
            "idle_s": bus().idle_for(),
            "steps": [_serialise(s, names) for s in steps],
        }
    )


_STREAM_KEEPALIVE_S: float = 10.0
_QUEUE_MAX: int = 200


@bp.get("/stream")
def stream() -> Response:
    """Server-Sent Events feed of agent steps, for the canvas editor's rail.

    Opens with ``event: snapshot`` (the current run, so a tab that joins
    mid-build shows the steps already taken) then one ``event: step`` per call.
    """
    _guard()
    activity_bus = bus()
    # The generator runs after the request context is gone, so anything it
    # touches has to be bound here: the app for a fresh context per step (page
    # names change mid-run, a dashboard the agent just created has to resolve)
    # and the bus itself.
    app = current_app._get_current_object()  # type: ignore[attr-defined]
    q: queue.Queue[Step] = queue.Queue(maxsize=_QUEUE_MAX)

    def on_step(step: Step) -> None:
        # Drop on overflow rather than backpressure the agent's request; the
        # rail caps its own DOM anyway, so a lost step in a flood is moot.
        try:
            q.put_nowait(step)
        except queue.Full:
            return

    names = _page_names()
    opening = [_serialise(s, names) for s in activity_bus.current_run()]
    activity_bus.add_listener(on_step)

    def generate() -> Iterator[str]:
        yield ":connected\n\n"
        yield f"event: snapshot\ndata: {json.dumps({'steps': opening}, default=str)}\n\n"
        last_send = time.monotonic()
        try:
            while True:
                timeout = max(0.1, _STREAM_KEEPALIVE_S - (time.monotonic() - last_send))
                try:
                    step = q.get(timeout=timeout)
                    if step.page_id:
                        with app.app_context():
                            row = _serialise(step, _page_names())
                    else:
                        row = _serialise(step, {})
                    yield f"event: step\ndata: {json.dumps(row, default=str)}\n\n"
                except queue.Empty:
                    yield ":keepalive\n\n"
                last_send = time.monotonic()
        finally:
            activity_bus.remove_listener(on_step)

    return current_app.response_class(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def register(app: Flask) -> None:
    app.config.setdefault("AGENT_ACTIVITY", ActivityBus())
    app.register_blueprint(bp)
