"""ha_locks — door / window / garage / lock security overview.

Auto-discovers everything in one ``get_states`` pass:

* every ``lock.*`` entity becomes a ``kind="lock"`` entry whose state is
  ``"locked"`` / ``"unlocked"``;
* every ``binary_sensor.*`` whose ``device_class`` is one of ``door``,
  ``window``, ``garage_door`` or ``opening`` is mapped to a
  ``kind="door"`` / ``"window"`` / ``"garage"`` entry whose state is
  ``"open"`` / ``"closed"`` (HA reports binary sensors as ``on`` / ``off``
  where ``on`` means "open").

The variants paint from this shape::

    {
      "place": str, "time": str,
      "entries": [
        {
          "entity_id": "lock.front_door",
          "name": "Front door",
          "kind": "lock" | "door" | "window" | "garage",
          "state": "locked" | "unlocked" | "open" | "closed",
          "secured": bool,                  # locked / closed = True
          "last_changed": str               # ISO-8601, may be ""
        },
        ...
      ],
      "summary": {"secured": int, "unsecured": int, "total": int}
    }

The cell stays drawing even when HA is unreachable or no matching
entities exist — the variants render an "ALL SECURED" / "NOTHING TO
WATCH" panel in those cases.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from flask import current_app

# device_class values on binary_sensor that we treat as openings, mapped
# to our internal ``kind``. HA's docs canonicalise these strings, but
# we lower-case defensively because user-defined templates sometimes
# slip through with mixed case.
DOOR_CLASSES = {"door"}
WINDOW_CLASSES = {"window"}
GARAGE_CLASSES = {"garage_door"}
# "opening" is HA's generic catch-all; we bucket it as a door since the
# wording ("open" / "closed") matches the door icon most cleanly.
GENERIC_CLASSES = {"opening"}


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Entity picker — surfaces ``lock.*`` and the door/window/garage
    binary sensors. The user can narrow further if they want to drop a
    noisy garage-door sensor from a "front of house" widget."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("lock", "binary_sensor"))
    return []


# -- helpers -----------------------------------------------------------


def _truthy(value: Any, default: bool = True) -> bool:
    """Coerce a cell option to bool. Cell options round-trip as JSON, so
    we'll usually see ``True``/``False`` already, but accept the common
    string forms too in case the editor ever ships a checkbox that
    serialises as ``"true"`` / ``"false"``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def _kind_for(state: dict[str, Any]) -> str | None:
    """Return our internal ``kind`` for a HA state dict, or ``None`` when
    the entity isn't something this widget cares about."""
    eid = str(state.get("entity_id") or "")
    domain = eid.split(".", 1)[0] if "." in eid else ""
    if domain == "lock":
        return "lock"
    if domain != "binary_sensor":
        return None
    dc = str((state.get("attributes") or {}).get("device_class") or "").strip().lower()
    if dc in DOOR_CLASSES:
        return "door"
    if dc in WINDOW_CLASSES:
        return "window"
    if dc in GARAGE_CLASSES:
        return "garage"
    if dc in GENERIC_CLASSES:
        return "door"
    return None


def _entry(state: dict[str, Any], kind: str, core: Any) -> dict[str, Any]:
    """Project a single HA state dict to our entry shape."""
    raw = str(state.get("state") or "").strip().lower()
    if kind == "lock":
        # HA's lock domain reports ``locked`` / ``unlocked`` / ``jammed``
        # / ``unknown`` / ``unavailable``. We treat anything that isn't
        # the canonical "locked" as unsecured so the widget errs on the
        # side of nagging you about a possibly-unlocked door.
        secured = raw == "locked"
        state_label = "locked" if secured else "unlocked"
    else:
        # Binary sensors: ``on`` = open, ``off`` = closed.
        secured = raw in ("off", "closed")
        state_label = "closed" if secured else "open"
    return {
        "entity_id": str(state.get("entity_id") or ""),
        "name": core.friendly_name(state),
        "kind": kind,
        "state": state_label,
        "secured": secured,
        "last_changed": str(state.get("last_changed") or ""),
    }


# -- entry point -------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    include_doors = _truthy(options.get("include_doors"), True)
    include_windows = _truthy(options.get("include_windows"), True)
    try:
        limit = int(options.get("limit") or 12)
    except (TypeError, ValueError):
        limit = 12
    if limit < 1:
        limit = 12

    place = options.get("label") or "Doors & locks"
    now = datetime.now().strftime("%H:%M")

    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    # User-picked entities skip the kind/include_* filtering — if you
    # asked for it you get it. Auto-discovery still honours the include
    # toggles for noisy household sensors.
    picked_raw = options.get("entities") or []
    if isinstance(picked_raw, str):
        picked = [tok.strip() for tok in picked_raw.replace(",", "\n").splitlines() if tok.strip()]
    else:
        picked = [str(x).strip() for x in picked_raw if str(x).strip()]
    picked_set = set(picked)

    entries: list[dict[str, Any]] = []
    for st in states:
        eid = str(st.get("entity_id") or "")
        if picked_set and eid not in picked_set:
            continue
        kind = _kind_for(st)
        if kind is None:
            continue
        if not picked_set:
            if kind == "door" and not include_doors:
                continue
            if kind == "window" and not include_windows:
                continue
        entries.append(_entry(st, kind, core))

    # Sort: unsecured first (so the user sees what needs attention), then
    # locks before doors/windows/garages, then by name. The "kind order"
    # gives a stable visual rhythm in the lists.
    kind_order = {"lock": 0, "door": 1, "garage": 2, "window": 3}
    entries.sort(
        key=lambda e: (
            0 if not e["secured"] else 1,
            kind_order.get(e["kind"], 9),
            e["name"].lower(),
        )
    )

    total = len(entries)
    unsecured = sum(1 for e in entries if not e["secured"])
    secured = total - unsecured

    # Cap how many rows the variants paint, but keep summary counts for
    # the full set so the "X SECURED" header doesn't lie.
    capped = entries[:limit]

    return {
        "place": place,
        "time": now,
        "entries": capped,
        "summary": {
            "secured": secured,
            "unsecured": unsecured,
            "total": total,
        },
        "_fetched_at": int(time.time()),
    }
