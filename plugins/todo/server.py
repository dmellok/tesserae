"""Todo state + admin blueprint — multi-list edition.

State lives at ``data/plugins/todo/lists.json`` (the loader's data_dir).
Shape::

    {
      "lists": [
        {
          "id": "default",
          "name": "Default",
          "items": [
            {"id": "...", "text": "...", "created_at": 123, "completed_at": 124?},
            ...
          ]
        },
        ...
      ]
    }

Marking an item done sets ``completed_at`` rather than deleting; completed
items linger for 24h so the dashboard reflects recent progress, then prune
themselves automatically on the next read.

A legacy single-list ``items.json`` is auto-migrated into a "default" list
the first time we touch the new file.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, redirect, render_template_string, request

_LISTS_FILE = "lists.json"
_LEGACY_ITEMS_FILE = "items.json"
_COMPLETED_TTL = 24 * 60 * 60  # 24 hours
_DEFAULT_LIST_ID = "default"
_DEFAULT_LIST_NAME = "Default"


def _data_dir() -> Path:
    plugin = current_app.config["PLUGIN_REGISTRY"].plugins["todo"]
    return plugin.data_dir


def _lists_path() -> Path:
    return _data_dir() / _LISTS_FILE


def _new_list(list_id: str, name: str) -> dict[str, Any]:
    return {"id": list_id, "name": name, "items": []}


def _load_raw(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _migrate_from_legacy(data_dir: Path) -> list[dict[str, Any]]:
    """If only the old items.json exists, wrap its contents in a default list.
    Returns the migrated list array (which the caller should persist)."""
    legacy = data_dir / _LEGACY_ITEMS_FILE
    if not legacy.exists():
        return [_new_list(_DEFAULT_LIST_ID, _DEFAULT_LIST_NAME)]
    try:
        items = json.loads(legacy.read_text())
        if not isinstance(items, list):
            items = []
    except (json.JSONDecodeError, OSError):
        items = []
    return [{"id": _DEFAULT_LIST_ID, "name": _DEFAULT_LIST_NAME, "items": items}]


def _load_lists(data_dir: Path) -> list[dict[str, Any]]:
    """Load the lists array, migrating from legacy storage on first read."""
    path = data_dir / _LISTS_FILE
    raw = _load_raw(path)
    if raw is None:
        lists = _migrate_from_legacy(data_dir)
        _save_lists(data_dir, lists)
        return lists
    lists_raw = raw.get("lists")
    if not isinstance(lists_raw, list) or not lists_raw:
        # Empty or malformed — start fresh.
        lists = [_new_list(_DEFAULT_LIST_ID, _DEFAULT_LIST_NAME)]
        _save_lists(data_dir, lists)
        return lists
    return [_coerce_list(entry) for entry in lists_raw if isinstance(entry, dict)]


def _coerce_list(entry: dict[str, Any]) -> dict[str, Any]:
    items = entry.get("items")
    return {
        "id": str(entry.get("id") or _DEFAULT_LIST_ID),
        "name": str(entry.get("name") or _DEFAULT_LIST_NAME),
        "items": items if isinstance(items, list) else [],
    }


def _save_lists(data_dir: Path, lists: list[dict[str, Any]]) -> None:
    path = data_dir / _LISTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"lists": lists}, indent=2))
    tmp.replace(path)


def _find_list(lists: list[dict[str, Any]], list_id: str) -> dict[str, Any] | None:
    return next((lst for lst in lists if lst["id"] == list_id), None)


def _resolve_list(lists: list[dict[str, Any]], list_id: str | None) -> dict[str, Any]:
    """Return the requested list, or the first list as a fallback. Lists are
    never empty (we guarantee at least the default) so this always returns
    something."""
    if list_id:
        found = _find_list(lists, list_id)
        if found is not None:
            return found
    return lists[0]


def _prune_expired(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop items whose completed_at is older than _COMPLETED_TTL."""
    now = time.time()
    return [
        item
        for item in items
        if not item.get("completed_at") or now - float(item["completed_at"]) < _COMPLETED_TTL
    ]


def _sorted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open items first (newest-first by created_at); completed last
    (most-recently-completed first), so the dashboard shows the freshest
    achievements on top of the done pile."""
    open_items = sorted(
        (i for i in items if not i.get("completed_at")),
        key=lambda i: i.get("created_at", 0),
        reverse=True,
    )
    done_items = sorted(
        (i for i in items if i.get("completed_at")),
        key=lambda i: i.get("completed_at", 0),
        reverse=True,
    )
    return [*open_items, *done_items]


def _prune_and_persist(data_dir: Path) -> list[dict[str, Any]]:
    """Load lists, drop expired items per list, persist if anything changed."""
    lists = _load_lists(data_dir)
    changed = False
    for lst in lists:
        pruned = _prune_expired(lst["items"])
        if len(pruned) != len(lst["items"]):
            lst["items"] = pruned
            changed = True
    if changed:
        _save_lists(data_dir, lists)
    return lists


_LIST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _slugify_list_name(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", slug)
    return slug.strip("-")


def _unique_list_id(base: str, existing: set[str]) -> str:
    if base and base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


# ---------------------------------------------------------------------------
# Plugin hooks
# ---------------------------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    """Cell render hook. Returns the chosen list's items + its display name."""
    data_dir = Path(ctx["data_dir"])
    lists = _prune_and_persist(data_dir)
    requested = options.get("list")
    chosen = _resolve_list(lists, requested if isinstance(requested, str) else None)
    return {
        "items": _sorted(chosen["items"]),
        "list_id": chosen["id"],
        "list_name": chosen["name"],
        "list_count": len(lists),
    }


def choices(name: str) -> list[dict[str, Any]]:
    """Dynamic dropdown values for the editor's ``cell_options[list]``."""
    if name != "lists":
        return []
    lists = _load_lists(_data_dir())
    return [{"value": lst["id"], "label": lst["name"]} for lst in lists]


# ---------------------------------------------------------------------------
# Admin UI
# ---------------------------------------------------------------------------


_TEMPLATE = """
{% extends "_base.html" %}
{% from "_components.html" import card_head %}
{% block title %}Todo — Tesserae{% endblock %}
{% block main %}
<header class="page-head">
  <h1><i class="ph ph-list-checks"></i> Todo</h1>
  <p class="lede">
    Quick-entry lists. Mark items done to strike them through; completed
    items linger for 24h, then auto-prune. Each dashboard cell using the
    Todo widget can pick which list it shows.
  </p>
</header>

<section class="card">
  {{ card_head('Lists', icon_name='folders') }}
  <nav class="tabs todo-lists">
    {% for lst in lists %}
    <a class="tab {% if lst.id == active.id %}is-active{% endif %}"
       href="/plugins/todo/?list={{ lst.id }}">
      <i class="ph ph-list" aria-hidden="true"></i>
      <span>{{ lst.name }}</span>
      <span class="muted">{{ lst.open_count }}</span>
    </a>
    {% endfor %}
  </nav>
  <form class="form" method="post" action="/plugins/todo/lists/add">
    <div class="field-grid">
      <div class="field">
        <label for="todo-new-list">New list name</label>
        <input type="text" id="todo-new-list" name="name" placeholder="Groceries" maxlength="40" required>
      </div>
      <div class="actions" style="align-self: end;">
        <button type="submit" class="ghost"><i class="ph ph-plus" aria-hidden="true"></i><span>Add list</span></button>
      </div>
    </div>
  </form>
</section>

<section class="card">
  <header class="card-head">
    <h2>
      <span class="icon-box"><i class="ph ph-list-checks" aria-hidden="true"></i></span>
      <span>{{ active.name }}</span>
    </h2>
    <div class="card-head-action">
      <form method="post" action="/plugins/todo/lists/rename/{{ active.id }}" class="inline"
            onsubmit="var n=prompt('Rename list', {{ active.name|tojson }}); if(!n) return false; this.elements.name.value=n;">
        <input type="hidden" name="name" value="">
        <button type="submit" class="ghost" {% if lists|length == 1 and active.id == 'default' %}disabled title="Rename the only list once you've added another"{% endif %}>
          <i class="ph ph-pencil-simple" aria-hidden="true"></i><span>Rename</span>
        </button>
      </form>
      <form method="post" action="/plugins/todo/lists/delete/{{ active.id }}" class="inline"
            onsubmit="return confirm('Delete the {{ active.name }} list and all its items?');">
        <button type="submit" class="ghost danger" {% if lists|length <= 1 %}disabled title="Add another list before deleting this one"{% endif %}>
          <i class="ph ph-trash" aria-hidden="true"></i>
        </button>
      </form>
    </div>
  </header>

  <form method="post" action="/plugins/todo/{{ active.id }}/add" class="form todo-add">
    <input type="text" name="text" placeholder="What needs doing?" autofocus required maxlength="200">
    <button type="submit" class="primary">
      <i class="ph ph-plus" aria-hidden="true"></i><span>Add</span>
    </button>
  </form>

  {% if open_items %}
  <ul class="todo-items">
    {% for item in open_items %}
    <li>
      <form method="post" action="/plugins/todo/{{ active.id }}/done/{{ item.id }}" class="inline todo-toggle">
        <button type="submit" class="todo-check" aria-label="Mark done">
          <i class="ph ph-circle" aria-hidden="true"></i>
        </button>
      </form>
      <span class="todo-text">{{ item.text }}</span>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <div class="empty-state empty-state--inline">
    <span class="empty-state-icon"><i class="ph-duotone ph-check-circle" aria-hidden="true"></i></span>
    <h3>All done</h3>
    <p class="lede">Nothing left on this list. Add something above.</p>
  </div>
  {% endif %}

  {% if done_items %}
  <h3 class="todo-section-title">Recently done <span class="muted">· auto-prunes after 24h</span></h3>
  <ul class="todo-items is-done">
    {% for item in done_items %}
    <li>
      <form method="post" action="/plugins/todo/{{ active.id }}/undone/{{ item.id }}" class="inline todo-toggle">
        <button type="submit" class="todo-check is-done" aria-label="Undo">
          <i class="ph-fill ph-check-circle" aria-hidden="true"></i>
        </button>
      </form>
      <span class="todo-text">{{ item.text }}</span>
      <span class="muted todo-age">{{ item.completed_age }}</span>
      <form method="post" action="/plugins/todo/{{ active.id }}/delete/{{ item.id }}" class="inline">
        <button type="submit" class="ghost danger" title="Delete now">
          <i class="ph ph-trash" aria-hidden="true"></i>
        </button>
      </form>
    </li>
    {% endfor %}
  </ul>
  {% endif %}
</section>
{% endblock %}
"""


def _human_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def blueprint() -> Blueprint:
    bp = Blueprint("todo_admin", __name__)

    def _redirect_to(list_id: str) -> Any:
        return redirect(f"/plugins/todo/?list={list_id}")

    @bp.get("/")
    def index() -> str:
        lists = _prune_and_persist(_data_dir())
        requested = request.args.get("list")
        active = _resolve_list(lists, requested)

        # Sidebar tab counts: open items per list.
        sidebar = [
            {
                "id": lst["id"],
                "name": lst["name"],
                "open_count": sum(1 for i in lst["items"] if not i.get("completed_at")),
            }
            for lst in lists
        ]

        now = time.time()
        items = active["items"]
        open_items = sorted(
            (i for i in items if not i.get("completed_at")),
            key=lambda i: i.get("created_at", 0),
            reverse=True,
        )
        done_items = sorted(
            (i for i in items if i.get("completed_at")),
            key=lambda i: i.get("completed_at", 0),
            reverse=True,
        )
        for i in done_items:
            i["completed_age"] = _human_age(now - float(i["completed_at"]))
        return render_template_string(
            _TEMPLATE,
            lists=sidebar,
            active={"id": active["id"], "name": active["name"]},
            open_items=open_items,
            done_items=done_items,
        )

    # -- list-level CRUD --------------------------------------------------

    @bp.post("/lists/add")
    def add_list() -> Any:
        name = (request.form.get("name") or "").strip()
        if not name:
            return redirect("/plugins/todo/")
        data_dir = _data_dir()
        lists = _load_lists(data_dir)
        existing = {lst["id"] for lst in lists}
        slug = _slugify_list_name(name) or "list"
        list_id = _unique_list_id(slug, existing)
        if not _LIST_ID_RE.match(list_id):
            list_id = _unique_list_id("list", existing)
        lists.append(_new_list(list_id, name[:40]))
        _save_lists(data_dir, lists)
        return _redirect_to(list_id)

    @bp.post("/lists/rename/<list_id>")
    def rename_list(list_id: str) -> Any:
        new_name = (request.form.get("name") or "").strip()
        if not new_name:
            return _redirect_to(list_id)
        data_dir = _data_dir()
        lists = _load_lists(data_dir)
        target = _find_list(lists, list_id)
        if target is None:
            return redirect("/plugins/todo/")
        target["name"] = new_name[:40]
        _save_lists(data_dir, lists)
        return _redirect_to(list_id)

    @bp.post("/lists/delete/<list_id>")
    def delete_list(list_id: str) -> Any:
        data_dir = _data_dir()
        lists = _load_lists(data_dir)
        # Refuse to delete the last list — guarantees fetch() always has one.
        if len(lists) <= 1:
            return _redirect_to(list_id)
        remaining = [lst for lst in lists if lst["id"] != list_id]
        if len(remaining) == len(lists):
            return redirect("/plugins/todo/")
        _save_lists(data_dir, remaining)
        return _redirect_to(remaining[0]["id"])

    # -- item-level CRUD, scoped under a list_id --------------------------

    @bp.post("/<list_id>/add")
    def add_item(list_id: str) -> Any:
        text = (request.form.get("text") or "").strip()
        if not text:
            return _redirect_to(list_id)
        data_dir = _data_dir()
        lists = _prune_and_persist(data_dir)
        target = _find_list(lists, list_id)
        if target is None:
            return redirect("/plugins/todo/")
        target["items"].append(
            {
                "id": uuid.uuid4().hex[:12],
                "text": text[:200],
                "created_at": int(time.time()),
            }
        )
        _save_lists(data_dir, lists)
        return _redirect_to(list_id)

    @bp.post("/<list_id>/done/<item_id>")
    def done_item(list_id: str, item_id: str) -> Any:
        data_dir = _data_dir()
        lists = _prune_and_persist(data_dir)
        target = _find_list(lists, list_id)
        if target is None:
            return redirect("/plugins/todo/")
        for item in target["items"]:
            if item.get("id") == item_id and not item.get("completed_at"):
                item["completed_at"] = int(time.time())
                break
        _save_lists(data_dir, lists)
        return _redirect_to(list_id)

    @bp.post("/<list_id>/undone/<item_id>")
    def undone_item(list_id: str, item_id: str) -> Any:
        data_dir = _data_dir()
        lists = _prune_and_persist(data_dir)
        target = _find_list(lists, list_id)
        if target is None:
            return redirect("/plugins/todo/")
        for item in target["items"]:
            if item.get("id") == item_id and item.get("completed_at"):
                item.pop("completed_at", None)
                break
        _save_lists(data_dir, lists)
        return _redirect_to(list_id)

    @bp.post("/<list_id>/delete/<item_id>")
    def delete_item(list_id: str, item_id: str) -> Any:
        data_dir = _data_dir()
        lists = _prune_and_persist(data_dir)
        target = _find_list(lists, list_id)
        if target is None:
            return redirect("/plugins/todo/")
        target["items"] = [i for i in target["items"] if i.get("id") != item_id]
        _save_lists(data_dir, lists)
        return _redirect_to(list_id)

    return bp
