"""device_battery, current battery level per registered device.

Reads the in-memory ``DEVICE_STATUS`` cache (populated by
``transport_wiring._subscribe_device_status`` on every MQTT heartbeat)
plus, when available, the ``BATTERY_HISTORY`` store for a 7-day
days-to-empty prediction. Mains-powered devices (the Pi paths) have no
``battery_pct`` field and are dropped silently so a panel-only
deployment stays clean.
"""

from __future__ import annotations

import time
from typing import Any

from flask import current_app


def _devices_with_battery() -> list[dict[str, Any]]:
    registry = current_app.config.get("DEVICE_REGISTRY")
    status_cache: dict[str, dict[str, Any]] = current_app.config.get("DEVICE_STATUS") or {}
    if registry is None:
        return []
    out: list[dict[str, Any]] = []
    now = time.time()
    for device in registry.devices.values():
        if device.kind_of is None:
            continue  # built-in kind, not a real registered instance
        status = status_cache.get(device.id) or {}
        parsed = status.get("parsed") or {}
        raw = parsed.get("battery_pct")
        if raw is None:
            continue
        try:
            pct = int(raw)
        except (TypeError, ValueError):
            continue
        pct = max(0, min(100, pct))
        received_at = status.get("received_at")
        seconds_ago = max(0.0, now - float(received_at)) if received_at else None
        out.append(
            {
                "device_id": device.id,
                "name": device.display_name,
                "pct": pct,
                "battery_mv": parsed.get("battery_mv"),
                "seconds_ago": int(seconds_ago) if seconds_ago is not None else None,
            }
        )
    return out


def _enrich_with_prediction(rows: list[dict[str, Any]]) -> None:
    """Stamp ``days_to_empty`` / ``slope_per_day`` on each row when the
    history store can produce a regression. No-op when the store isn't
    installed or there's not enough history; the widget renders the bare
    percentage in that case."""
    store = current_app.config.get("BATTERY_HISTORY")
    if store is None:
        return
    for row in rows:
        try:
            pred = store.predict(row["device_id"], window_days=7)
        except Exception:
            continue
        if pred is None:
            continue
        row["slope_per_day"] = pred.slope_per_day
        row["days_to_20pct"] = pred.days_to_20pct
        row["days_to_empty"] = pred.days_to_empty
        row["samples"] = pred.samples


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    try:
        limit = int(options.get("limit", 8))
    except (TypeError, ValueError):
        limit = 8
    if limit < 1:
        limit = 1
    show_prediction = options.get("show_prediction") is not False

    rows = _devices_with_battery()
    if show_prediction:
        _enrich_with_prediction(rows)
    rows.sort(key=lambda r: (r["pct"], r["name"].lower()))

    return {
        "now_ts": int(time.time()),
        "devices": rows[:limit],
        "total_devices": len(rows),
    }
