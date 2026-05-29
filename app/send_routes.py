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
from collections.abc import Callable
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
from app.push import PushManager, PushResult
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
        opts.append(
            {
                "id": dev.id,
                "label": dev.display_name,
                "icon": dev.icon,
                "dims": f"{dev.panel['w']}×{dev.panel['h']}",
            }
        )
    return opts


def _form_device_ids() -> list[str]:
    """Read + validate the multi-select target-device field. Unknown ids
    are dropped; an empty list means "any" (fan out to every renderer
    using the virtual panel)."""
    registry = _devices()
    if registry is None:
        return []
    return [
        raw
        for raw in (v.strip() for v in request.form.getlist("device_id"))
        if raw and raw in registry.devices
    ]


def _flash_result(label: str, status: str, error: str | None) -> None:
    if status == "sent":
        flash(f"{label}: sent.", "ok")
    elif status == "busy":
        flash(f"{label}: another push in flight — try again.", "error")
    else:
        flash(f"{label}: {status}{(' — ' + error) if error else ''}", "error")


def _device_label(device_id: str | None) -> str:
    """Friendly name for a target id; ``None`` is the virtual-panel fan-out."""
    if device_id is None:
        return "all renderers"
    registry = _devices()
    dev = registry.devices.get(device_id) if registry is not None else None
    return dev.display_name if dev is not None else device_id


def _push_to_targets(
    label: str, targets: list[str], push: Callable[[str | None], PushResult]
) -> None:
    """Run a one-off push once per selected device (or once with ``None`` —
    the virtual-panel fan-out — when none are selected), then flash one
    combined summary. Each call logs its own History row."""
    ids: list[str | None] = list(targets) if targets else [None]
    results = [(tid, push(tid)) for tid in ids]
    sent = [tid for tid, r in results if r.status == "sent"]
    failed = [(tid, r) for tid, r in results if r.status != "sent"]
    if sent and not failed:
        flash(f"{label}: sent to {', '.join(_device_label(t) for t in sent)}.", "ok")
    elif sent and failed:
        names = ", ".join(_device_label(t) for t, _ in failed)
        flash(
            f"{label}: sent to {', '.join(_device_label(t) for t in sent)}; failed for {names}.",
            "warn",
        )
    else:
        _, first = failed[0]
        if first.status == "busy":
            flash(f"{label}: another push in flight — try again.", "error")
        else:
            flash(f"{label}: {first.status}{(' — ' + first.error) if first.error else ''}", "error")


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
    filename = upload.filename
    _push_to_targets(
        f"File {filename!r}",
        _form_device_ids(),
        lambda tid: _push().push_image(image_bytes, source_label=filename, device_id=tid),
    )
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
    _push_to_targets(
        f"URL {url}",
        _form_device_ids(),
        lambda tid: _push().push_url_image(url, device_id=tid),
    )
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
    _push_to_targets(
        f"Webpage {url}",
        _form_device_ids(),
        lambda tid: _push().push_webpage(
            url, viewport_w=viewport_w, viewport_h=viewport_h, device_id=tid
        ),
    )
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
