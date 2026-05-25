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

from app.push import PushManager
from app.state.event_log import EventLog
from app.state.page_store import PageStore

bp = Blueprint("send", __name__, url_prefix="/send")


def _push() -> PushManager:
    return current_app.config["PUSH_MANAGER"]  # type: ignore[no-any-return]


def _events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _flash_result(label: str, status: str, error: str | None) -> None:
    if status == "sent":
        flash(f"{label}: sent.", "ok")
    elif status == "busy":
        flash(f"{label}: another push in flight — try again.", "error")
    else:
        flash(f"{label}: {status}{(' — ' + error) if error else ''}", "error")


@bp.get("")
def index() -> str:
    history = _events().list(type="push", limit=100)
    pages = _pages().list()
    return render_template(
        "send.html",
        pages=pages,
        history=history,
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
    result = _push().push_image(image_bytes, source_label=upload.filename)
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
    result = _push().push_url_image(url)
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
    result = _push().push_webpage(url, viewport_w=viewport_w, viewport_h=viewport_h)
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
