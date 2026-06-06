"""glances_core, saved Glances server registry + admin page.

No widget cell; serves as the shared store the ``glances_status``
widget reads to resolve an instance ID into a base URL. The admin
page (``/plugins/glances_core/``) lets a user add / edit / delete
saved instances; each cell picks one by ID via a select dropdown.

Persistence is a single JSON file (``instances.json``) inside the
plugin's data_dir, same pattern as the ``todo`` plugin. Single-user
admin → read-modify-write is fine.

Shape on disk::

    {
      "instances": [
        {
          "id":       "media-server",   # slug, stable across renames
          "name":     "Media server",   # display label
          "base_url": "http://media.lan:61208",
          "created_at": "ISO timestamp"
        },
        ...
      ]
    }
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.wrappers import Response

# ----- storage helpers ------------------------------------------------


def _data_dir() -> Path:
    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get("glances_core")
    if plugin is None:
        raise RuntimeError("glances_core plugin not registered")
    path: Path = plugin.data_dir
    return path


def _store_path(data_dir: Path | None = None) -> Path:
    return (data_dir or _data_dir()) / "instances.json"


def _load(data_dir: Path | None = None) -> dict[str, Any]:
    path = _store_path(data_dir)
    if not path.exists():
        return {"instances": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"instances": []}
    if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
        return {"instances": []}
    return data


def _save(data: dict[str, Any], data_dir: Path | None = None) -> None:
    path = _store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    s = _SLUG_RE.sub("-", value.lower()).strip("-")
    return s or secrets.token_hex(3)


def _unique_id(data: dict[str, Any], base: str) -> str:
    """Slugify ``base`` and append ``-2``, ``-3``, … if the id is in
    use. Keeps round-tripping stable when two saved instances happen
    to share a name."""
    existing = {inst["id"] for inst in data.get("instances", []) if "id" in inst}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _normalize_url(raw: str) -> str:
    """Trim trailing slashes and require a scheme, but accept just a
    host:port and prefix ``http://`` so the user doesn't have to think
    about it for a LAN box."""
    url = raw.strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    return url


def _validate_url(raw: str) -> str | None:
    """Return an error message string when the URL is unusable; None
    when it parses cleanly."""
    if not raw:
        return "URL is required."
    try:
        parts = urlsplit(raw)
    except ValueError as err:
        return f"Bad URL: {err}"
    if not parts.scheme or parts.scheme not in ("http", "https"):
        return "URL must be http:// or https://."
    if not parts.hostname:
        return "URL needs a hostname."
    return None


def _find(data: dict[str, Any], instance_id: str) -> dict[str, Any] | None:
    for inst in data.get("instances", []):
        if inst.get("id") == instance_id:
            return inst
    return None


# ----- public helpers consumed by glances_status ----------------------


def get_instance(instance_id: str) -> dict[str, Any] | None:
    """Look up a saved instance by id (used by glances_status). Returns
    None when the id was deleted out from under a cell that still
    references it."""
    if not instance_id:
        return None
    return _find(_load(), instance_id)


def list_instances() -> list[dict[str, Any]]:
    return list(_load().get("instances", []))


def choices(name: str) -> list[dict[str, str]]:
    """Powers the glances_status cell_options dropdown via
    ``choices_from: "instances"``. Empty value means "use the inline
    URL field" for backward compat with cells configured before the
    instances list existed."""
    if name != "instances":
        return []
    insts = list_instances()
    return [{"value": inst["id"], "label": inst.get("name") or inst["id"]} for inst in insts]


# ----- admin blueprint ------------------------------------------------


def blueprint() -> Blueprint:
    bp = Blueprint("glances_core_admin", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        data = _load()
        return render_template(
            "glances_core/index.html",
            instances=data.get("instances", []),
        )

    @bp.post("/instances")
    def create() -> Response:
        name = (request.form.get("name") or "").strip()
        url = _normalize_url(request.form.get("base_url") or "")
        if not name:
            flash("Name is required.", "warn")
            return redirect(url_for("glances_core_admin.index"))
        err = _validate_url(url)
        if err:
            flash(err, "warn")
            return redirect(url_for("glances_core_admin.index"))
        data = _load()
        inst_id = _unique_id(data, _slugify(name))
        data["instances"].append(
            {
                "id": inst_id,
                "name": name,
                "base_url": url,
                "created_at": _now_iso(),
            }
        )
        _save(data)
        flash(f"Added {name}.", "ok")
        return redirect(url_for("glances_core_admin.index"))

    @bp.post("/instances/<instance_id>/edit")
    def edit(instance_id: str) -> Response:
        data = _load()
        inst = _find(data, instance_id)
        if inst is None:
            abort(404)
        name = (request.form.get("name") or "").strip()
        url = _normalize_url(request.form.get("base_url") or "")
        if not name:
            flash("Name is required.", "warn")
            return redirect(url_for("glances_core_admin.index"))
        err = _validate_url(url)
        if err:
            flash(err, "warn")
            return redirect(url_for("glances_core_admin.index"))
        inst["name"] = name
        inst["base_url"] = url
        _save(data)
        flash(f"Saved {name}.", "ok")
        return redirect(url_for("glances_core_admin.index"))

    @bp.post("/instances/<instance_id>/delete")
    def delete(instance_id: str) -> Response:
        data = _load()
        before = len(data.get("instances", []))
        data["instances"] = [
            inst for inst in data.get("instances", []) if inst.get("id") != instance_id
        ]
        if len(data["instances"]) == before:
            abort(404)
        _save(data)
        flash("Deleted.", "ok")
        return redirect(url_for("glances_core_admin.index"))

    return bp
