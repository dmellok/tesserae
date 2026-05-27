"""Todo plugin: widget cell + admin blueprint.

Persistence is a single JSON file (``lists.json``) inside the plugin's
data_dir. The file is read-modify-write on every mutation; that's fine
for a single-user dashboard where concurrent writes are vanishingly
rare. The 24-hour auto-prune happens on every read so the file never
grows unbounded — no background job needed.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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

PRUNE_AFTER_HOURS = 24


# ----- storage helpers ------------------------------------------------

def _data_dir() -> Path:
    """Path of this plugin's data_dir, looked up via the live registry.
    Safe to call from any request handler — the loader populates the
    registry at app boot and we always run after it."""
    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get("todo")
    if plugin is None:
        raise RuntimeError("todo plugin not registered")
    path: Path = plugin.data_dir
    return path


def _lists_path(data_dir: Path | None = None) -> Path:
    return (data_dir or _data_dir()) / "lists.json"


def _load_raw(data_dir: Path | None = None) -> dict[str, Any]:
    path = _lists_path(data_dir)
    if not path.exists():
        return {"lists": []}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"lists": []}
    if not isinstance(data, dict) or not isinstance(data.get("lists"), list):
        return {"lists": []}
    return data


def _save(data: dict[str, Any], data_dir: Path | None = None) -> None:
    path = _lists_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _prune(data: dict[str, Any]) -> bool:
    """Drop items whose completed_at is older than PRUNE_AFTER_HOURS.
    Returns True when anything was pruned (so the caller can persist)."""
    cutoff = datetime.now(UTC) - timedelta(hours=PRUNE_AFTER_HOURS)
    changed = False
    for lst in data.get("lists", []):
        items = lst.get("items") or []
        kept = []
        for it in items:
            done = _parse_iso(it.get("completed_at"))
            if done is not None and done < cutoff:
                changed = True
                continue
            kept.append(it)
        if kept != items:
            lst["items"] = kept
    return changed


def _load(data_dir: Path | None = None) -> dict[str, Any]:
    """Load + prune in one shot. Persists if prune dropped anything."""
    data = _load_raw(data_dir)
    if _prune(data):
        with contextlib.suppress(OSError):
            _save(data, data_dir)
    return data


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return base or "list"


def _unique_id(data: dict[str, Any], base: str) -> str:
    taken = {lst.get("id") for lst in data.get("lists", [])}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _find_list(data: dict[str, Any], list_id: str) -> dict[str, Any] | None:
    for lst in data.get("lists", []):
        if lst.get("id") == list_id:
            return lst
    return None


# ----- widget API -----------------------------------------------------

def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    data_dir = Path(ctx["data_dir"])
    data = _load(data_dir)

    list_id = options.get("list_id")
    show_completed = options.get("show_completed", True)
    max_items = int(options.get("max_items") or 0)

    lst = _find_list(data, list_id) if list_id else None
    if lst is None:
        # No list selected (or selected one was deleted) — surface a
        # friendly empty state, NOT an error, so the widget shell still
        # renders. The cell editor explains how to choose a list.
        return {
            "list_id":   list_id or "",
            "list_name": "",
            "items":     [],
            "empty":     True,
            "reason":    "no_list" if not list_id else "list_missing",
        }

    items = list(lst.get("items") or [])
    if not show_completed:
        items = [it for it in items if not it.get("completed_at")]
    # Sort: incomplete first (oldest -> newest), then completed (most
    # recently done first) so the eye reads the active list top-down.
    items.sort(key=lambda it: (
        bool(it.get("completed_at")),
        it.get("completed_at") or "",
        it.get("created_at") or "",
    ))
    if max_items > 0:
        items = items[:max_items]

    return {
        "list_id":     lst["id"],
        "list_name":   lst.get("name") or lst["id"],
        "items":       items,
        "total":       len(lst.get("items") or []),
        "completed":   sum(1 for it in (lst.get("items") or []) if it.get("completed_at")),
        "empty":       not items,
        "reason":      "empty_list" if not items else "",
    }


def choices(name: str) -> list[dict[str, str]]:
    """Powers the cell_options `list_id` dropdown via the manifest's
    ``choices_from: "lists"`` declaration."""
    if name != "lists":
        return []
    data = _load()
    return [
        {"value": lst["id"], "label": lst.get("name") or lst["id"]}
        for lst in data.get("lists", [])
    ]


# ----- admin blueprint ------------------------------------------------

def blueprint() -> Blueprint:
    """Mounted by the plugin loader at /plugins/todo/."""
    bp = Blueprint(
        "todo_admin",
        __name__,
        template_folder="templates",
    )

    @bp.get("/")
    def index() -> str:
        data = _load()
        lists = data.get("lists", [])
        # Annotate each list with quick counts so the index shows
        # "5 active · 2 done" style summaries.
        summarised = []
        for lst in lists:
            items = lst.get("items") or []
            done = sum(1 for it in items if it.get("completed_at"))
            summarised.append({
                "id":        lst["id"],
                "name":      lst.get("name") or lst["id"],
                "active":    len(items) - done,
                "completed": done,
            })
        return render_template("todo/index.html", lists=summarised)

    @bp.post("/lists")
    def create_list() -> Response:
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("List name is required.", "warn")
            return redirect(url_for("todo_admin.index"))
        data = _load()
        list_id = _unique_id(data, _slugify(name))
        data["lists"].append({
            "id":         list_id,
            "name":       name,
            "created_at": _now_iso(),
            "items":      [],
        })
        _save(data)
        flash(f"Created list '{name}'.", "ok")
        return redirect(url_for("todo_admin.show_list", list_id=list_id))

    @bp.get("/lists/<list_id>")
    def show_list(list_id: str) -> str:
        data = _load()
        lst = _find_list(data, list_id)
        if lst is None:
            abort(404)
        items = list(lst.get("items") or [])
        items.sort(key=lambda it: (
            bool(it.get("completed_at")),
            it.get("completed_at") or "",
            it.get("created_at") or "",
        ))
        active = [it for it in items if not it.get("completed_at")]
        done   = [it for it in items if it.get("completed_at")]
        return render_template(
            "todo/list.html",
            list=lst, active=active, done=done,
            prune_hours=PRUNE_AFTER_HOURS,
        )

    @bp.post("/lists/<list_id>/items")
    def add_item(list_id: str) -> Response:
        text = (request.form.get("text") or "").strip()
        if not text:
            flash("Item text is required.", "warn")
            return redirect(url_for("todo_admin.show_list", list_id=list_id))
        data = _load()
        lst = _find_list(data, list_id)
        if lst is None:
            abort(404)
        lst.setdefault("items", []).append({
            "id":           secrets.token_hex(6),
            "text":         text,
            "created_at":   _now_iso(),
            "completed_at": None,
        })
        _save(data)
        return redirect(url_for("todo_admin.show_list", list_id=list_id))

    @bp.post("/lists/<list_id>/items/<item_id>/toggle")
    def toggle_item(list_id: str, item_id: str) -> Response:
        data = _load()
        lst = _find_list(data, list_id)
        if lst is None:
            abort(404)
        for it in lst.get("items") or []:
            if it.get("id") == item_id:
                it["completed_at"] = None if it.get("completed_at") else _now_iso()
                break
        else:
            abort(404)
        _save(data)
        return redirect(url_for("todo_admin.show_list", list_id=list_id))

    @bp.post("/lists/<list_id>/items/<item_id>/delete")
    def delete_item(list_id: str, item_id: str) -> Response:
        data = _load()
        lst = _find_list(data, list_id)
        if lst is None:
            abort(404)
        before = len(lst.get("items") or [])
        lst["items"] = [it for it in lst.get("items") or [] if it.get("id") != item_id]
        if len(lst["items"]) == before:
            abort(404)
        _save(data)
        return redirect(url_for("todo_admin.show_list", list_id=list_id))

    @bp.post("/lists/<list_id>/delete")
    def delete_list(list_id: str) -> Response:
        data = _load()
        before = len(data.get("lists", []))
        data["lists"] = [lst for lst in data.get("lists", []) if lst.get("id") != list_id]
        if len(data["lists"]) == before:
            abort(404)
        _save(data)
        flash(f"Deleted list '{list_id}'.", "ok")
        return redirect(url_for("todo_admin.index"))

    return bp
