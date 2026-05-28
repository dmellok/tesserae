"""Admin Send page.

Five tabs, one URL: ``/send``. Each tab POSTs to a dedicated endpoint and
redirects back so the result lands in the History tab's event-log feed.

Tabs:

* **File** — multipart upload, pushed as an image
* **Saved** — pick a saved dashboard, render through the composer
* **URL**   — fetch an image URL, push the bytes
* **Webpage** — Playwright-screenshot an arbitrary URL, push the bytes
* **History** — last N push events with resend / delete
"""

from __future__ import annotations

import time
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
from werkzeug.wrappers import Response

from app.device_loader import DeviceRegistry
from app.panel import resolve_settings_panel
from app.push import PushManager
from app.renderer_loader import RendererRegistry
from app.state.event_log import EventLog, EventRow
from app.state.page_store import PageStore
from app.state.settings_store import SettingsStore

bp = Blueprint("send", __name__, url_prefix="/send")


def _push() -> PushManager:
    return current_app.config["PUSH_MANAGER"]  # type: ignore[no-any-return]


def _events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _devices() -> DeviceRegistry | None:
    return current_app.config.get("DEVICE_REGISTRY")


def _device_options() -> list[dict[str, str]]:
    """Registered instances that can be targeted by a manual send. Mirrors
    the page-editor picker: instances only (kinds aren't bindable)."""
    registry = _devices()
    if registry is None:
        return []
    opts: list[dict[str, str]] = []
    for dev in sorted(registry.devices.values(), key=lambda d: d.name.lower()):
        if dev.kind_of is None or dev.panel is None:
            continue
        opts.append({"id": dev.id, "label": dev.display_name})
    return opts


def _form_device_id() -> str | None:
    """Read + validate the optional target-device field. Empty / unknown
    falls back to None (fan out to every renderer using the virtual
    panel)."""
    raw = (request.form.get("device_id") or "").strip()
    if not raw:
        return None
    registry = _devices()
    if registry is None or raw not in registry.devices:
        return None
    return raw


def _flash_result(label: str, status: str, error: str | None) -> None:
    if status == "sent":
        flash(f"{label}: sent.", "ok")
    elif status == "busy":
        flash(f"{label}: another push in flight — try again.", "error")
    else:
        flash(f"{label}: {status}{(' — ' + error) if error else ''}", "error")


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


def _history_view(rows: list[EventRow]) -> list[dict[str, Any]]:
    """Shape raw event rows for the History tab: page name instead of id,
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
    history = _history_view(_events().list(type="push", limit=100))
    pages = _pages().list()
    return render_template(
        "send.html",
        pages=pages,
        history=history,
        panel=resolve_settings_panel(_settings()),
        device_options=_device_options(),
        tab=request.args.get("tab", "file"),
    )


@bp.post("/file")
def send_file() -> Response:
    upload = request.files.get("image")
    if upload is None or not upload.filename:
        flash("No file selected.", "error")
        return redirect(url_for("send.index", tab="file"))
    image_bytes = upload.read()
    if not image_bytes:
        flash("Uploaded file was empty.", "error")
        return redirect(url_for("send.index", tab="file"))
    result = _push().push_image(
        image_bytes, source_label=upload.filename, device_id=_form_device_id()
    )
    _flash_result(f"File {upload.filename!r}", result.status, result.error)
    return redirect(url_for("send.index", tab="history"))


@bp.post("/page")
def send_page() -> Response:
    page_id = (request.form.get("page_id") or "").strip()
    if not page_id:
        flash("Pick a saved dashboard first.", "error")
        return redirect(url_for("send.index", tab="saved"))
    result = _push().push(page_id)
    _flash_result(f"Page {page_id!r}", result.status, result.error)
    return redirect(url_for("send.index", tab="history"))


@bp.post("/url")
def send_url() -> Response:
    url = (request.form.get("url") or "").strip()
    if not url:
        flash("Paste an image URL first.", "error")
        return redirect(url_for("send.index", tab="url"))
    result = _push().push_url_image(url, device_id=_form_device_id())
    _flash_result(f"URL {url}", result.status, result.error)
    return redirect(url_for("send.index", tab="history"))


@bp.post("/webpage")
def send_webpage() -> Response:
    url = (request.form.get("url") or "").strip()
    if not url:
        flash("Paste a webpage URL first.", "error")
        return redirect(url_for("send.index", tab="webpage"))
    try:
        viewport_w = int(request.form.get("viewport_w") or 1600)
        viewport_h = int(request.form.get("viewport_h") or 1200)
    except ValueError:
        flash("Viewport dimensions must be integers.", "error")
        return redirect(url_for("send.index", tab="webpage"))
    result = _push().push_webpage(
        url, viewport_w=viewport_w, viewport_h=viewport_h, device_id=_form_device_id()
    )
    _flash_result(f"Webpage {url}", result.status, result.error)
    return redirect(url_for("send.index", tab="history"))


@bp.post("/history/<int:event_id>/resend")
def resend(event_id: int) -> Response:
    result = _push().republish(event_id)
    _flash_result(f"Resend #{event_id}", result.status, result.error)
    return redirect(url_for("send.index", tab="history"))


@bp.post("/history/<int:event_id>/delete")
def delete(event_id: int) -> Response:
    ok = _push().delete_history(event_id)
    if ok:
        flash("History entry deleted.", "ok")
    else:
        flash(f"No history entry #{event_id}.", "error")
    return redirect(url_for("send.index", tab="history"))


def register(app: Flask) -> None:
    app.register_blueprint(bp)
