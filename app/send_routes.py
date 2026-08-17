"""Admin Send page.

Four tabs, one URL: ``/send``. Each tab POSTs to a dedicated endpoint and
redirects to ``/history`` so the result lands in the push log.

Tabs:

* **File**, multipart upload, pushed as an image
* **Saved**, pick a saved dashboard, render through the composer
* **URL**  , fetch an image URL, push the bytes
* **Webpage**, Playwright-screenshot an arbitrary URL, push the bytes

The standalone History page (``history_routes``) shows the push log and
hosts resend / delete actions that POST back to ``/send/resend/...`` and
``/send/delete/...`` defined here.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
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
from app.http_headers import HeaderError, parse_header_map
from app.image_upload import IMAGE_ROTATE_MODES
from app.net_guard import BlockedURLError, assert_operator_url
from app.panel import device_panel, resolve_settings_panel
from app.push import PushManager, PushResult
from app.state.event_log import EventLog
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


def _device_options() -> list[dict[str, Any]]:
    """Registered instances that can be targeted by a manual send. Mirrors
    the page-editor picker: instances only (kinds aren't bindable).

    ``w`` / ``h`` are the device's composition dimensions (post-orientation,
    as ``device_panel`` resolves them). The Send-page template hangs them
    on each checkbox's ``data-panel-w`` / ``-h`` so send.js can reshape
    the live preview frame to match the picked target."""
    registry = _devices()
    if registry is None:
        return []
    opts: list[dict[str, Any]] = []
    for dev in sorted(registry.devices.values(), key=lambda d: d.name.lower()):
        if dev.kind_of is None or dev.panel is None:
            continue
        # device_panel() builds a Pydantic ``Panel`` which validates
        # w > 0, h > 0. A device instance with corrupted panel dims
        # (e.g. saved with panel_w = 0 by an older discover-claim
        # registration before v0.53.2 guarded against it) would
        # raise here and 500 the entire /send page. Skip silently
        # with a log line so the rest of the device list stays
        # usable; admin can fix the bad instance via Settings →
        # Devices → Panel.
        try:
            panel = device_panel(dev)
        except Exception:
            current_app.logger.warning(
                "send: skipping device %r with invalid panel %r",
                dev.id,
                dev.panel,
            )
            continue
        if panel is None:
            continue
        opts.append(
            {
                "id": dev.id,
                "label": dev.display_name,
                "icon": dev.icon,
                "w": panel.w,
                "h": panel.h,
                "dims": f"{panel.w}×{panel.h}",
            }
        )
    return opts


def _form_device_ids() -> list[str]:
    """Read + validate the multi-select target-device field. Unknown ids
    are dropped; an empty list means "no selection" (the caller is
    expected to reject the request via :func:`_require_target_devices`,
    not silently fan out to every renderer, that path produced a
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
    silently destroyed everything the user had typed, pasting the
    URL again, re-selecting the fit, re-picking gallery folder/file
    was a frustration tax. Re-rendering instead surfaces the flash
    message AND keeps the form populated so the user can fix the one
    missing field and resubmit. The file-upload field can't be
    preserved (browser security), but every other input round-trips.
    """
    return make_response(
        render_template(
            "send.html",
            panel=resolve_settings_panel(_settings()),
            device_options=_device_options(),
            tab=tab,
            gallery=_gallery_ref(),
            form_values=request.form.to_dict(flat=True),
            # ``device_id`` is the only multi-value picker, flat
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
    was selected, Tesserae rendered at the global panel preset and
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
        else "No devices registered yet, add one in Settings → Devices."
    )
    flash(msg, "error")
    return _render_send_with_form(tab)


def _run_in_background(work: Callable[[], object], *, label: str) -> None:
    """Run a push off the request thread so the browser isn't blocked on
    the render + transport round-trip (5–15 s for a 1600×1200 panel).

    The push manager already writes a ``type='push'`` event on success
    or failure, and the History tab updates live via SSE, so the user
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
    """Queue a push once per selected device (or once with ``None``, the
    virtual-panel fan-out, when none are selected). Each call logs its
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


_FIT_MODES: frozenset[str] = frozenset({"fit", "fill", "stretch", "center", "blur"})


def _form_fit() -> str | None:
    """Chosen image fit mode, or None (each renderer's default) when unset
    or not a recognised mode."""
    raw = (request.form.get("fit") or "").strip().lower()
    return raw if raw in _FIT_MODES else None


def _form_rotate() -> str | None:
    """Chosen turn, or None (keep the image's own orientation) when unset
    or not a recognised mode. Same vocabulary the webhook image push
    takes, so the page and a script describe a turn the same way."""
    raw = (request.form.get("rotate") or "").strip().lower()
    return raw if raw in IMAGE_ROTATE_MODES and raw != "0" else None


def _gallery_module() -> Any:
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get("picture_gallery") if reg is not None else None
    return plugin.server_module if plugin is not None else None


def _gallery_ref() -> dict[str, str] | None:
    """When opened from a gallery image (``?g_folder=&g_file=``), return
    ``{folder, file, url}`` for the pre-loaded Gallery section, validating
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
    gallery = _gallery_ref()
    tab = request.args.get("tab") or ("gallery" if gallery else "file")
    return render_template(
        "send.html",
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
    rotate = _form_rotate()
    targets = _require_target_devices("file")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"File {filename!r}",
        targets,
        lambda tid: _push().push_image(
            image_bytes, source_label=filename, device_id=tid, fit=fit, rotate=rotate
        ),
    )
    return redirect(url_for("history.index"))


@bp.post("/page")
def send_page() -> Response:
    page_id = (request.form.get("page_id") or "").strip()
    if not page_id:
        flash("Pick a saved dashboard first.", "error")
        return _redirect_after_page_push(page_id, on_error=True)
    page_name = next((p.name for p in _pages().list() if p.id == page_id), page_id)
    # User-initiated click; skip coalescing so the panel never silently
    # gets superseded by a schedule firing on the same device.
    _run_in_background(
        # ``force_publish=True``: this is a user click on Send / Push, so
        # the panel should repaint even when the composition digest is
        # bit-identical to the last render (widget data cached, weather
        # value unchanged, etc.). The content-checksum push skip only
        # applies to scheduled / automated refires (issue #81).
        lambda: _push().push(page_id, bypass_coalesce=True, force_publish=True),
        label=f"page:{page_id}",
    )
    return_to = (request.form.get("return_to") or "").strip()
    # Tailor the flash message to where the user is about to land.
    # "History tab" was helpful from the Send page; from the dashboards
    # list / editor it just confuses since they don't see History next.
    if return_to in ("dashboards", "editor"):
        flash(f"Sending {page_name!r}.", "ok")
    else:
        flash(
            f"Sending {page_name!r}. The History tab will update when the render lands.",
            "ok",
        )
    return _redirect_after_page_push(page_id)


def _push_one_page(page_id: str) -> None:
    """Force-publish one page to its bound devices. Module-level so the bulk
    loop can bind it per id via ``functools.partial`` (a default-arg lambda
    trips mypy's inference)."""
    _push().push(page_id, bypass_coalesce=True, force_publish=True)


@bp.post("/pages")
def send_pages() -> Response:
    """Push several saved dashboards at once (multi-select on the Dashboards
    page). Body: repeated ``page_ids`` form fields. Each fans out to its own
    bound devices, same force-publish / bypass-coalesce as a single Push."""
    ids = [i.strip() for i in request.form.getlist("page_ids") if i.strip()]
    if not ids:
        flash("Select at least one dashboard.", "error")
        return redirect(url_for("pages.index"))
    for pid in ids:
        _run_in_background(functools.partial(_push_one_page, pid), label=f"page:{pid}")
    flash(f"Sending {len(ids)} dashboard{'s' if len(ids) != 1 else ''}.", "ok")
    return redirect(url_for("pages.index"))


def _redirect_after_page_push(page_id: str, *, on_error: bool = False) -> Response:
    """Honour the form's ``return_to`` hint so a Push from the dashboards
    list or the editor doesn't yank the user into Send → History.

    Values are safelisted (``dashboards`` / ``editor``); anything else
    falls back to the default Send-page-History landing, no open-redirect
    risk from a user-supplied path. ``page_id`` is needed to build the
    editor URL when ``return_to=editor``."""
    return_to = (request.form.get("return_to") or "").strip()
    if return_to == "dashboards":
        return redirect(url_for("pages.index"))
    if return_to == "editor" and page_id:
        return redirect(url_for("pages.edit", page_id=page_id))
    if on_error:
        return redirect(url_for("send.index", tab="saved"))
    return redirect(url_for("history.index"))


@bp.post("/url")
def send_url() -> Response:
    url = (request.form.get("url") or "").strip()
    if not url:
        flash("Paste an image URL first.", "error")
        return redirect(url_for("send.index", tab="url"))
    fit = _form_fit()
    rotate = _form_rotate()
    targets = _require_target_devices("url")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"URL {url}",
        targets,
        lambda tid: _push().push_url_image(url, device_id=tid, fit=fit, rotate=rotate),
    )
    return redirect(url_for("history.index"))


@bp.post("/webpage")
def send_webpage() -> Response:
    url = (request.form.get("url") or "").strip()
    if not url:
        flash("Paste a webpage URL first.", "error")
        return redirect(url_for("send.index", tab="webpage"))
    try:
        assert_operator_url(url)
    except BlockedURLError as err:
        flash(str(err), "error")
        return redirect(url_for("send.index", tab="webpage"))
    try:
        viewport_w = int(request.form.get("viewport_w") or 1600)
        viewport_h = int(request.form.get("viewport_h") or 1200)
    except ValueError:
        flash("Viewport dimensions must be integers.", "error")
        return redirect(url_for("send.index", tab="webpage"))
    # Optional request headers (#234), for a page behind a bearer token or an
    # API key. The error text comes from the parser so the operator sees which
    # header it objected to, never the value they typed.
    try:
        headers = parse_header_map(request.form.get("headers"))
    except HeaderError as err:
        flash(str(err), "error")
        return redirect(url_for("send.index", tab="webpage"))
    fit = _form_fit()
    targets = _require_target_devices("webpage")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"Webpage {url}",
        targets,
        lambda tid: _push().push_webpage(
            url,
            viewport_w=viewport_w,
            viewport_h=viewport_h,
            device_id=tid,
            fit=fit,
            headers=headers or None,
        ),
    )
    return redirect(url_for("history.index"))


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
    rotate = _form_rotate()
    label = f"gallery:{folder}/{filename}"
    targets = _require_target_devices("gallery")
    if isinstance(targets, Response):
        return targets
    _push_to_targets(
        f"Gallery {filename!r}",
        targets,
        lambda tid: _push().push_image(
            image_bytes, source_label=label, device_id=tid, fit=fit, rotate=rotate
        ),
    )
    return redirect(url_for("history.index"))


@bp.post("/history/<int:event_id>/resend")
def resend(event_id: int) -> Response:
    _run_in_background(lambda: _push().republish(event_id), label=f"resend:{event_id}")
    flash(
        f"Resending #{event_id}. The History tab will update when the render lands.",
        "ok",
    )
    return redirect(url_for("history.index"))


@bp.post("/history/<int:event_id>/delete")
def delete(event_id: int) -> Response:
    ok = _push().delete_history(event_id)
    if ok:
        flash("History entry deleted.", "ok")
    else:
        flash(f"No history entry #{event_id}.", "error")
    return redirect(url_for("history.index"))


def _plural(n: int) -> str:
    return "entry" if n == 1 else "entries"


@bp.post("/history/clear")
def clear_history() -> Response:
    """Manual history housekeeping (issue #116). ``older_than`` (days) deletes
    everything older than that cutoff; empty/0 deletes all. Orphaned render
    artifacts are pruned after."""
    events = _events()
    raw = (request.form.get("older_than") or "").strip()
    if raw:
        try:
            days = int(raw)
        except ValueError:
            days = 0
        if days <= 0:
            flash("Pick a valid age to delete.", "error")
            return redirect(url_for("history.index"))
        cutoff = time.time() - days * 86400
        removed = events.delete_older_than(cutoff)
        scope = f"older than {days} day{'s' if days != 1 else ''}"
    else:
        removed = events.delete_all()
        scope = "all"
    _push().prune_orphan_renders()
    flash(f"Deleted {removed} history {_plural(removed)} ({scope}).", "ok")
    return redirect(url_for("history.index"))


@bp.post("/history/bulk-delete")
def bulk_delete() -> Response:
    """Delete the checked history rows (the per-row multi-select on the History
    page). Ids come as repeated ``event_ids`` form fields."""
    ids: list[int] = []
    for raw in request.form.getlist("event_ids"):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        flash("No history entries selected.", "error")
        return redirect(url_for("history.index"))
    removed = _events().delete_many(ids)
    _push().prune_orphan_renders()
    flash(f"Deleted {removed} history {_plural(removed)}.", "ok")
    return redirect(url_for("history.index"))


def register(app: Flask) -> None:
    app.register_blueprint(bp)
