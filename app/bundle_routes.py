"""Export dashboards and lineups to a file and import them again (#541).

  GET  /bundles/export?page=<id>&lineup=<id>   download a bundle (``all=1``
                                               for every active dashboard
                                               and every lineup)
  GET  /bundles/import                         the upload form
  POST /bundles/import                         read a file, show what it would do
  POST /bundles/import/apply                   do it

The upload is held on disk between the two POSTs under a random name, so the
confirmation acts on the file that was shown, not on a second upload.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response as WerkzeugResponse

from app import bundle

bp = Blueprint("bundles", __name__, url_prefix="/bundles")

_PENDING_MAX_AGE_S = 3600
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _stores() -> dict[str, Any]:
    return {
        "page_store": current_app.config["PAGE_STORE"],
        "deck_store": current_app.config["DECK_STORE"],
        "registry": current_app.config["PLUGIN_REGISTRY"],
    }


def _pending_dir() -> Path:
    return Path(current_app.config["DATA_ROOT"]) / "core" / "bundle_imports"


def _pending_path(token: str) -> Path | None:
    return _pending_dir() / f"{token}.json" if _TOKEN_RE.match(token or "") else None


def _sweep_pending() -> None:
    cutoff = time.time() - _PENDING_MAX_AGE_S
    with contextlib.suppress(OSError):
        for path in _pending_dir().glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)


def _filename(doc: dict[str, Any]) -> str:
    names = [str(d["page"].get("name") or "") for d in doc["dashboards"]]
    if len(doc["lineups"]) == 1:
        base = str(doc["lineups"][0].get("name") or "lineup")
    elif len(names) == 1 and not doc["lineups"]:
        base = names[0] or "dashboard"
    else:
        base = "export"
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "export"
    return f"tesserae-{slug[:48]}.json"


@bp.get("/export")
def export() -> WerkzeugResponse:
    stores = _stores()
    page_ids = request.args.getlist("page")
    deck_ids = request.args.getlist("lineup")
    if request.args.get("all") == "1":
        page_ids = [p.id for p in stores["page_store"].list_active()]
        deck_ids = [d.id for d in stores["deck_store"].all()]
    doc, notes = bundle.build_bundle(
        page_ids=page_ids,
        deck_ids=deck_ids,
        data_root=Path(current_app.config["DATA_ROOT"]),
        version=str(current_app.config.get("APP_VERSION") or ""),
        **stores,
    )
    if not doc["dashboards"] and not doc["lineups"]:
        flash("Nothing to export.", "error")
        return redirect(url_for("pages.index"))
    if notes:
        doc["left_out"] = notes
    response = Response(json.dumps(doc, indent=2) + "\n", mimetype="application/json")
    response.headers["Content-Disposition"] = f'attachment; filename="{_filename(doc)}"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/import")
def import_form() -> str:
    return render_template("bundle_import.html", plan=None, token="")


@bp.post("/import")
def import_read() -> WerkzeugResponse | str:
    upload = request.files.get("file")
    raw = upload.read(bundle.MAX_BUNDLE_BYTES + 1) if upload is not None else b""
    if not raw:
        flash("Choose an export file first.", "error")
        return redirect(url_for("bundles.import_form"))
    try:
        doc = bundle.read_bundle(raw)
    except bundle.BundleError as err:
        flash(f"That file can't be imported: {err}.", "error")
        return redirect(url_for("bundles.import_form"))
    _sweep_pending()
    token = secrets.token_hex(16)
    directory = _pending_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{token}.json").write_bytes(raw)
    plan = bundle.describe(doc, **_stores())
    plan["left_out"] = [str(n) for n in doc.get("left_out") or [] if isinstance(n, str)][:50]
    return render_template("bundle_import.html", plan=plan, token=token)


@bp.post("/import/apply")
def import_apply() -> WerkzeugResponse:
    path = _pending_path(str(request.form.get("token") or ""))
    if path is None or not path.is_file():
        flash("That import has expired. Choose the file again.", "error")
        return redirect(url_for("bundles.import_form"))
    try:
        doc = bundle.read_bundle(path.read_bytes())
        report = bundle.apply_bundle(
            doc,
            replace_pages=set(request.form.getlist("replace_page")),
            replace_lineups=set(request.form.getlist("replace_lineup")),
            data_root=Path(current_app.config["DATA_ROOT"]),
            **_stores(),
        )
    except bundle.BundleError as err:
        flash(f"Nothing was imported: {err}.", "error")
        return redirect(url_for("bundles.import_form"))
    finally:
        path.unlink(missing_ok=True)

    def count(n: int, noun: str) -> str:
        return f"{n} {noun}{'' if n == 1 else 's'}"

    parts = []
    if report.created_pages:
        parts.append(f"added {count(len(report.created_pages), 'dashboard')}")
    if report.replaced_pages:
        parts.append(f"replaced {count(len(report.replaced_pages), 'dashboard')}")
    if report.created_lineups:
        parts.append(f"added {count(len(report.created_lineups), 'lineup')}")
    if report.replaced_lineups:
        parts.append(f"replaced {count(len(report.replaced_lineups), 'lineup')}")
    if parts:
        summary = ", ".join(parts)
        flash(
            f"Import: {summary}. New dashboards and lineups are not on any display yet.",
            "ok",
        )
    else:
        flash("Nothing was imported.", "error")
    for line in report.skipped:
        flash(f"Skipped {line}.", "warn")
    if report.missing:
        flash(
            "These widgets are not installed here, so their cells will be empty until they are: "
            + ", ".join(report.missing)
            + ".",
            "warn",
        )
    return redirect(url_for("pages.index"))
