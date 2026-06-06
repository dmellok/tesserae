"""History page.

Lives at ``/history`` so the push log is a top-level nav destination
rather than a tab buried inside Send. The resend / delete actions still
POST to the ``/send/...`` endpoints owned by ``send_routes`` so the
push pipeline stays in one module — only the read view moved here.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from flask import Blueprint, Flask, current_app, render_template

from app.device_loader import DeviceRegistry
from app.renderer_loader import RendererRegistry
from app.state.event_log import EventLog, EventRow
from app.state.page_store import PageStore

bp = Blueprint("history", __name__, url_prefix="/history")


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


def history_view(rows: list[EventRow]) -> list[dict[str, Any]]:
    """Shape raw event rows for the History page: page name instead of id,
    humanised time, friendly device labels."""
    page_names = {p.id: p.name for p in _pages().list()}
    out: list[dict[str, Any]] = []
    for ev in rows:
        target = page_names.get(ev.target, ev.target) if ev.source == "page" else ev.target
        renderers = [
            {"label": _renderer_label(str(r.get("renderer_id", ""))), "error": r.get("error")}
            for r in (ev.extra.get("renderers") or [])
        ]
        out.append(
            {
                "id": ev.id,
                "status": ev.status,
                "digest": ev.digest,
                "source": ev.source,
                "target": target,
                "rel": _relative(ev.timestamp),
                "abs": datetime.fromtimestamp(ev.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "duration_s": ev.duration_s,
                "error": ev.error,
                "renderers": renderers,
            }
        )
    return out


@bp.get("")
def index() -> str:
    history = history_view(_events().list(type="push", limit=100))
    return render_template("history.html", history=history)


def register(app: Flask) -> None:
    app.register_blueprint(bp)
