"""ha_todo, items from a Home Assistant todo entity.

Thin widget over ha_core. Calls the ``todo.get_items`` service via
``call_service_with_response`` (HA 2024.5+) and shapes the result for
the client. Pulls the list's friendly name from the entity state in the
same fetch round-trip so the widget has both items + display title in
one place.
"""

from __future__ import annotations

from typing import Any

from flask import current_app


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Entity dropdown for the editor, filtered to todo entities."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("todo",))
    return []


def _normalise_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten HA's todo item into the shape the client expects.

    Some integrations include an iCal-style ``priority`` field (0-9,
    where 1-4 is high, 5 is medium, 6-9 is low, 0/missing is "no
    priority"). Forward it when it's there so the client can paint a
    priority dot."""
    priority = raw.get("priority")
    try:
        priority_n: int | None = int(priority) if priority not in (None, "") else None
    except (TypeError, ValueError):
        priority_n = None
    return {
        "uid": str(raw.get("uid") or ""),
        "summary": str(raw.get("summary") or "").strip(),
        "status": str(raw.get("status") or "needs_action"),
        "due": raw.get("due"),  # ISO datetime, ISO date, or None
        "description": str(raw.get("description") or "").strip(),
        "priority": priority_n,
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}
    if not core.is_configured():
        return {
            "error": "Home Assistant is not configured, set URL + token in Settings → Widgets → Home Assistant Core."
        }

    entity_id = (options.get("entity_id") or "").strip()
    if not entity_id:
        return {"error": "Pick a todo list in the cell options."}
    if not entity_id.startswith("todo."):
        return {"error": f"{entity_id!r} isn't a todo entity."}

    max_items = max(1, min(20, int(options.get("max_items") or 8)))
    include_completed = bool(options.get("include_completed"))
    user_title = (options.get("title") or "").strip()

    # Pull the entity state for friendly_name + total-needs-action count.
    # We could derive the count from the items list below, but the state
    # is cheap and gives us a fallback display title without parsing
    # attributes downstream.
    try:
        state = core.get_state(entity_id)
    except Exception as err:
        return {"error": core.coerce_error(err)}

    list_title = user_title or core.friendly_name(state) or entity_id

    # Items live behind a service call, todo.get_items with the entity
    # as target. ``return_response`` makes HA echo the items back rather
    # than just emitting state changes.
    try:
        payload = core.call_service_with_response(
            "todo", "get_items", data={"entity_id": entity_id}
        )
    except Exception as err:
        return {"error": core.coerce_error(err)}

    response = payload.get("service_response") or {}
    bucket = response.get(entity_id) or {}
    raw_items = bucket.get("items") or []
    items = [_normalise_item(it) for it in raw_items if isinstance(it, dict)]

    needs_action = [it for it in items if it["status"] != "completed"]
    completed = [it for it in items if it["status"] == "completed"]

    # Lead with needs_action; append completed at the tail only when the
    # user opted in. Truncate to max_items so widgets at xs/sm don't
    # spill, but keep the total counts honest in the header.
    shown: list[dict[str, Any]] = needs_action[:max_items]
    if include_completed:
        slack = max(0, max_items - len(shown))
        if slack:
            shown.extend(completed[:slack])

    return {
        "title": list_title,
        "entity_id": entity_id,
        "items": shown,
        "needs_action_count": len(needs_action),
        "completed_count": len(completed),
        "total_count": len(items),
    }
