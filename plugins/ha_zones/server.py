"""ha_zones — who's home / who's away.

Auto-discovers every ``person.*`` entity from Home Assistant and reports
the current zone for each: the canonical ``home`` / ``not_home`` states,
or the friendly name of a custom HA zone the person is currently inside.

Shape returned to the client:

    {
      "label": str,
      "time": str,
      "items": [
        {
          "entity_id": str,
          "name": str,
          "state": "home" | "not_home" | "<zone name>",
          "entity_picture": str | None,   # fully-qualified URL or None
          "last_changed": str | None,      # ISO8601 from HA
          "history": [str, ...]            # optional last-24h state samples (d4 only)
        },
        ...
      ],
      "summary": {"home": int, "away": int, "total": int}
    }

A single ``get_states`` call covers every person on the install; for
the data variant we additionally pull ``history`` per person so the
"history bar" can render. History fetches are best-effort — if HA's
history endpoint is slow or the entity has no recorded changes, we just
omit the strip and the variant falls back to the row-only layout.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from flask import current_app


def choices(name: str) -> list[dict[str, str]]:
    """Entity picker for the editor — restricted to ``person.*``."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("person",))
    return []


def _core() -> Any:
    return current_app.config["PLUGIN_REGISTRY"].get("ha_core").server_module


# -- helpers -----------------------------------------------------------


def _name(state: dict[str, Any]) -> str:
    """Human label — friendly_name if HA has one, else the entity id."""
    attrs = state.get("attributes") or {}
    return str(attrs.get("friendly_name") or state.get("entity_id") or "")


def _resolve_picture(raw: Any, core: Any) -> str | None:
    """Turn HA's ``entity_picture`` attribute into a full URL the
    browser can fetch.

    HA returns it as a server-relative path (e.g. ``/api/image/serve/…``
    or ``/local/…``). We prepend the configured base_url so the image
    works whether the cell is rendered in the admin preview or pushed
    to a remote panel. Absolute URLs pass through unchanged."""
    if not raw:
        return None
    url = str(raw).strip()
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    base = core.base_url()
    if not base:
        return None
    if not url.startswith("/"):
        url = "/" + url
    return base + url


def _zone_state(state: dict[str, Any]) -> str:
    """Normalise the ``state`` value.

    HA's person domain emits ``home`` / ``not_home`` for the built-in
    zones and the zone's friendly name for any custom zone the person
    is currently inside. We pass that through verbatim — the client
    decides how to colour it — but trim and lowercase the canonical
    pair so variant code can switch on them reliably."""
    raw = str(state.get("state") or "").strip()
    if raw.lower() in ("home", "not_home"):
        return raw.lower()
    return raw or "unknown"


# -- entry point -------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    try:
        limit = int(options.get("limit", 8))
    except (TypeError, ValueError):
        limit = 8
    if limit < 1:
        limit = 1

    variant = str(options.get("variant") or "r1").lower()

    # User-picked list takes priority; blank = every person.*
    picked_raw = options.get("entities") or []
    if isinstance(picked_raw, str):
        picked = [tok.strip() for tok in picked_raw.replace(",", "\n").splitlines() if tok.strip()]
    else:
        picked = [str(x).strip() for x in picked_raw if str(x).strip()]
    picked_set = set(picked)

    all_items: list[dict[str, Any]] = []
    for st in states:
        eid = str(st.get("entity_id") or "")
        if picked_set:
            if eid not in picked_set:
                continue
        elif not eid.startswith("person."):
            continue
        attrs = st.get("attributes") or {}
        picture = _resolve_picture(attrs.get("entity_picture"), core)
        all_items.append(
            {
                "entity_id": eid,
                "name": _name(st),
                "state": _zone_state(st),
                "entity_picture": picture,
                "last_changed": st.get("last_changed") or None,
            }
        )

    # Sort: home first (so present housemates surface), then alphabetical
    # by name. The variants assume this ordering for the colour-block
    # arrangement and the avatars grid.
    all_items.sort(key=lambda it: (0 if it["state"] == "home" else 1, it["name"].lower()))

    home_count = sum(1 for it in all_items if it["state"] == "home")
    total = len(all_items)
    summary = {
        "home": home_count,
        "away": total - home_count,
        "total": total,
    }

    items = all_items[:limit]

    # Data variant gets a "history bar" — the last 24h of state changes
    # for each shown person so the strip can render presence churn at a
    # glance. Best-effort: if HA's history endpoint errors we just skip
    # the strip for that person.
    if variant == "d4":
        for it in items:
            try:
                hist = core.history(it["entity_id"], hours=24)
            except Exception:
                hist = []
            it["history"] = [str(s.get("state") or "") for s in hist]

    return {
        "label": options.get("label", "Home"),
        "time": datetime.now().strftime("%H:%M"),
        "items": items,
        "summary": summary,
        "_fetched_at": int(time.time()),
    }
