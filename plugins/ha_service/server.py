"""Home Assistant service (kind: service).

A non-placeable data source that exposes the Home Assistant REST API to a code
element. It reuses the connection configured in Settings → Widgets → Home Assistant Core
(base URL + Long-Lived Access Token) via the plugin registry, so it shares one
credential with the whole ha_* widget family and never stores its own.

The agent lists it via list_services(), probes it with an empty scope to learn
the scopes, then requests one: ``states`` (every entity, optionally filtered to
a domain list), ``entity`` (one entity's full state), ``history`` (a series for
one entity), ``services`` / ``config`` (metadata), or ``raw`` (any GET /api path
it names). fetch() returns the parsed HA JSON so the code element reads whatever
fields it needs off ctx.data.<name>.

There is no render side (kind "service"); ``fetch`` is the whole plugin.
"""

from __future__ import annotations

import re
from typing import Any

from flask import current_app


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def _domains(raw: Any) -> tuple[str, ...]:
    return tuple(tok for tok in re.split(r"[\s,]+", str(raw or "").strip()) if tok)


def _discovery() -> dict[str, Any]:
    """Self-describing map returned when no scope is set, so the agent can
    explore the API before choosing what to fetch."""
    return {
        "service": "home-assistant",
        "auth": "shared (Settings → Widgets → Home Assistant Core: base URL + token)",
        "scopes": {
            "states": "Every entity state. Set options.domain (comma list, e.g. "
            "'light,sensor') to filter. Returns {entities: [...], count}.",
            "entity": "One entity's full state + attributes. Set options.entity_id.",
            "history": "State-change series for one entity. Set options.entity_id "
            "and options.hours (default 24). Returns {samples: [...]}.",
            "services": "All available services grouped by domain (GET /api/services).",
            "config": "Home Assistant config (GET /api/config).",
            "raw": "Any GET /api path you name in options.path (must start with /api).",
        },
        "usage": "Set options.scope to one of the scopes above.",
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    scope = str(options.get("scope") or "").strip()
    if not scope:
        return _discovery()

    core = _core()
    if core is None:
        return {"error": "ha_core plugin not available"}
    if not core.is_configured():
        return {
            "error": "Home Assistant is not configured (Settings → Widgets → Home Assistant Core)"
        }

    try:
        if scope == "states":
            states = core.get_states()
            domains = _domains(options.get("domain"))
            if domains:
                states = [
                    s for s in states if str(s.get("entity_id") or "").split(".", 1)[0] in domains
                ]
            return {"entities": states, "count": len(states)}

        if scope == "entity":
            entity_id = str(options.get("entity_id") or "").strip()
            if not entity_id:
                return {"error": "entity scope needs options.entity_id"}
            return {"entity": core.get_state(entity_id)}

        if scope == "history":
            entity_id = str(options.get("entity_id") or "").strip()
            if not entity_id:
                return {"error": "history scope needs options.entity_id"}
            try:
                hours = max(1, min(720, int(options.get("hours") or 24)))
            except (TypeError, ValueError):
                hours = 24
            return {
                "samples": core.history(entity_id, hours=hours),
                "entity_id": entity_id,
                "hours": hours,
            }

        if scope == "services":
            return {"services": core.request_json("/api/services")}

        if scope == "config":
            return {"config": core.request_json("/api/config")}

        if scope == "raw":
            path = str(options.get("path") or "").strip()
            if not path.startswith("/api"):
                return {"error": "raw scope needs options.path starting with /api"}
            return {"data": core.request_json(path), "path": path}
    except Exception as err:
        return {"error": core.coerce_error(err), "scope": scope}

    return {"error": f"unknown scope {scope!r}"}
