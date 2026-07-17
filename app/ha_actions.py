"""Shared Home Assistant picker data for the touch Interaction editors
(issue #49 phase 3+).

Both the canvas editor (``panels_routes``) and the grid editor
(``page_routes``) offer a "Home Assistant" touch action whose form needs
the list of callable services and entities. This fetches them through
the ``ha_core`` plugin's shared connection so the two editors stay in
lock-step and neither reimplements the REST calls.

``fetch_ha_actions`` never raises: an unconfigured or unreachable HA
instance returns ``configured``/``error`` flags the editor renders as a
hint, so the picker degrades instead of 500-ing.
"""

from __future__ import annotations

from typing import Any

from flask import current_app


def fetch_ha_actions() -> dict[str, Any]:
    """Return ``{configured, services, entities}`` for the HA action form.

    ``services`` is ``[{id: "domain.service", name}]`` and ``entities`` is
    ``[{id: entity_id, name}]``, both sorted by id. When HA isn't set up,
    ``configured`` is False and the lists are empty. A fetch failure keeps
    ``configured`` True and adds an ``error`` string."""
    plugin = None
    registry = current_app.config.get("PLUGIN_REGISTRY")
    if registry is not None:
        plugin = registry.get("ha_core")
    mod = getattr(plugin, "server_module", None) if plugin is not None else None
    if mod is None or not getattr(mod, "is_configured", lambda: False)():
        return {"configured": False, "services": [], "entities": []}

    services: list[dict[str, str]] = []
    entities: list[dict[str, str]] = []
    try:
        raw_services = mod.request_json("/api/services")
        for block in raw_services if isinstance(raw_services, list) else []:
            domain = str(block.get("domain") or "")
            svc_map = block.get("services") or {}
            if not domain or not isinstance(svc_map, dict):
                continue
            for svc, meta in svc_map.items():
                name = str(meta.get("name") or "") if isinstance(meta, dict) else ""
                services.append({"id": f"{domain}.{svc}", "name": name or f"{domain}.{svc}"})
        for state in mod.get_states():
            eid = str(state.get("entity_id") or "")
            if eid:
                entities.append({"id": eid, "name": mod.friendly_name(state)})
    except Exception as err:
        return {
            "configured": True,
            "error": str(err),
            "services": services,
            "entities": entities,
        }
    services.sort(key=lambda s: s["id"])
    entities.sort(key=lambda e: e["id"])
    return {"configured": True, "services": services, "entities": entities}
