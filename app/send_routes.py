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

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    make_response,
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

logger = logging.getLogger(__name__)

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
    are dropped; an empty list means "no selection" (the caller is
    expected to reject the request via :func:`_require_target_devices`,
    not silently fan out to every renderer — that path produced a
    frame rendered at the global panel preset and shipped it to every
    device, including ones whose actual panel didn't match)."""
    registry = _devices()
    if registry is None:
        return []
    return [
        raw
        for raw in (v.strip() for v in request.form.getlist("device_id"))
        if raw and raw in registry.devices
    ]


def _render_send_with_form(tab: str) -> Response:
    """Re-render the Send page on the current tab with the user's typed
    form values preserved.

    The Send routes used to ``redirect`` back to ``send.index`` on
    validation failure (no device ticked, blank URL, etc.), which
    silently destroyed everything the user had typed — pasting the
    URL again, re-selecting the fit, re-picking gallery folder/file
    was a frustration tax. Re-rendering instead surfaces the flash
    message AND keeps the form populated so the user can fix the one
    missing field and resubmit. The file-upload field can't be
    preserved (browser security), but every other input round-trips.
    """
    history = _history_view(_events().list(type="push", limit=100))
    pages = _pages().list()
    return make_response(
        render_template(
            "send.html",
            pages=pages,
            history=history,
            panel=resolve_settings_panel(_settings()),
            device_options=_device_options(),
            tab=tab,
            gallery=_gallery_ref(),
            form_values=request.form.to_dict(flat=True),
            # ``device_id`` is the only multi-value picker — flat
            # ``to_dict`` loses every value except the last. Pass the
            # full list separately so the checklist re-ticks every
            # device the user picked, not just the last one.
            form_device_ids=request.form.getlist("device_id"),
        )
    )


def _require_target_devices(tab: str) -> list[str] | Response:
    """Read the form's target-device picks, or short-circuit with a
    flash + re-render if the user didn't tick any.

    The Send page's File / URL / Webpage / Gallery flows used to
    silently fall through to a "virtual panel" fan-out when no device
    was selected — Tesserae rendered at the global panel preset and
    blasted the same frame to every renderer in the registry. Devices
    with a different actual panel rejected the frame with a noisy
    ``ValueError: frame size X != expected Y`` in their heartbeat.
    Better to refuse to send and tell the user to pick a target.

    Failed validation re-renders the Send page rather than redirecting
    so the user's URL / viewport / fit / gallery selection survives
    (``_render_send_with_form``)."""
    ids = _form_device_ids()
    if ids:
        return ids
    registry = _devices()
    have_any = bool(registry and registry.devices)
    msg = (
        "Pick at least one device to push to."
        if have_any
        else "No devices registered yet — add one in Settings → Devices."
    )
    flash(msg, "error")
    return _render_send_with_form(tab)


def _run_in_background(work: Callable[[], object], *, label: str) -> None:
    """Run a push off the request thread so the browser isn't blocked on
    the render + transport round-trip (5–15 s for a 1600×1200 panel).

    The push manager already writes a ``type='push'`` event on success
    or failure, and the History tab updates live via SSE — so the user
    sees the result there instead of waiting for the form-POST to
    return. Failures inside ``work()`` get logged but never propagate
    (the request thread is already gone).

    Under ``app.testing`` we run synchronously: a daemon thread is
    fundamentally racy against the test client's ``assert_called_with``
    pattern (the thread may not have run before the assert fires), and
    the latency the bg path exists to hide doesn't matter in tests."""
    app: Flask = current_app._get_current_object()  # type: ignore[attr-defined]

    if app.testing:
        try:
            work()
        except Exception:
            logger.exception("send (%s) failed", label)
        return

    def _runner() -> None:
        with app.app_context():
            try:
                work()
            except Exception:
                logger.exception("background send (%s) failed", label)

    threading.Thread(target=_runner, daemon=True, name=f"send-bg:{label}").start()


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
    """Queue a push once per selected device (or once with ``None`` — the
    virtual-panel fan-out — when none are selected). Each call logs its
    own History row via the push manager, so the user sees per-target
    results stream into the History tab without the request thread
    waiting for the render + transport round-trip."""
    ids: list[str | None] = list(targets) if targets else [None]
    target_count = len(ids)
    target_summary = ", ".join(_device_label(t) for t in ids) if targets else "all renderers"

    def _work() -> None:
        for tid in ids:
            push(tid)

    _run_in_background(_work, label=label)
    flash(
        f"{label}: queued for {target_summary}. "
        f"Watch the History tab for {target_count} render{'s' if target_count != 1 else ''}.",
        "ok",
    )


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


_FIT_MODES: frozenset[str] = frozenset({"fit", "fill", "stretch", "center", "blur"})


def _form_fit() -> str | None:
    """Chosen image fit mode, or None (each renderer's default) when unset
    or not a recognised mode."""
    raw = (request.form.get("fit") or "").strip().lower()
    return raw if raw in _FIT_MODES else None


def _gallery_module() -> Any:
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get("picture_gallery") if reg is not None else None
    return plugin.server_module if plugin is not None else None


def _gallery_ref() -> dict[str, str] | None:
    """When opened from a gallery image (``?g_folder=&g_file=``), return
    ``{folder, file, url}`` for the pre-loaded Gallery section — validating
    the image exists via the picture_gallery plugin."""
    folder = (request.args.get("g_folder") or "").strip()
    filename = (request.args.get("g_file") or "").strip()
    if not folder or not filename:
        return None
    gallery = _gallery_module()
    if gallery is None or gallery.resolve_image_path(folder, filename) is None:
        return None
    return {
        "folder": folder,
        "file": filename,
        "url": url_for("picture_gallery_admin.serve_image", folder=folder, filename=filename),
    }


@bp.get("")
def index() -> str:
    history = _history_view(_events().list(type="push", limit=100))
    pages = _pages().list()
    gallery = _gallery_ref()
    tab = request.args.get("tab") or ("gallery" if gallery else "file")
    return render_template(
        "send.html",
        pages=pages,
        history=history,
        panel=resolve_settings_panel(_settings()),
        device_options=_device_options(),
        tab=tab,
        gallery=gallery,
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
    fit = _form_fit()
    targets = _require_target_devices("file")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"File {filename!r}",
        targets,
        lambda tid: _push().push_image(image_bytes, source_label=filename, device_id=tid, fit=fit),
    )
    return redirect(url_for("send.index", tab="history"))


@bp.post("/page")
def send_page() -> Response:
    page_id = (request.form.get("page_id") or "").strip()
    if not page_id:
        flash("Pick a saved dashboard first.", "error")
        return redirect(url_for("send.index", tab="saved"))
    page_name = next((p.name for p in _pages().list() if p.id == page_id), page_id)
    _run_in_background(lambda: _push().push(page_id), label=f"page:{page_id}")
    flash(
        f"Sending {page_name!r}. The History tab will update when the render lands.",
        "ok",
    )
    return redirect(url_for("send.index", tab="history"))


@bp.post("/url")
def send_url() -> Response:
    url = (request.form.get("url") or "").strip()
    if not url:
        flash("Paste an image URL first.", "error")
        return redirect(url_for("send.index", tab="url"))
    fit = _form_fit()
    targets = _require_target_devices("url")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"URL {url}",
        targets,
        lambda tid: _push().push_url_image(url, device_id=tid, fit=fit),
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
    fit = _form_fit()
    targets = _require_target_devices("webpage")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"Webpage {url}",
        targets,
        lambda tid: _push().push_webpage(
            url, viewport_w=viewport_w, viewport_h=viewport_h, device_id=tid, fit=fit
        ),
    )
    return redirect(url_for("send.index", tab="history"))


@bp.post("/gallery")
def send_gallery() -> Response:
    """Push a gallery image to the chosen displays + fit. The Send page's
    Gallery section posts here with the image reference (g_folder/g_file)."""
    folder = (request.form.get("g_folder") or "").strip()
    filename = (request.form.get("g_file") or "").strip()
    gallery = _gallery_module()
    path = gallery.resolve_image_path(folder, filename) if gallery is not None else None
    if path is None:
        flash("Gallery image not found.", "error")
        return redirect(url_for("send.index"))
    try:
        image_bytes = path.read_bytes()
    except OSError as err:
        flash(f"Could not read {filename}: {err}", "error")
        return redirect(url_for("send.index", tab="gallery", g_folder=folder, g_file=filename))
    fit = _form_fit()
    label = f"gallery:{folder}/{filename}"
    targets = _require_target_devices("gallery")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"Gallery {filename!r}",
        targets,
        lambda tid: _push().push_image(image_bytes, source_label=label, device_id=tid, fit=fit),
    )
    return redirect(url_for("send.index", tab="history"))


@bp.post("/history/<int:event_id>/resend")
def resend(event_id: int) -> Response:
    _run_in_background(lambda: _push().republish(event_id), label=f"resend:{event_id}")
    flash(
        f"Resending #{event_id}. The History tab will update when the render lands.",
        "ok",
    )
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
