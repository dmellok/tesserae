"""Admin page at ``/devices/battery``.

Per-device battery charts (Chart.js, line trace over the selected
window) plus the same days-to-empty prediction the device_battery
widget shows. Reads from the ``BATTERY_HISTORY`` SQLite store the
heartbeat handler writes to on every MQTT status message that carries
a ``battery_pct`` field.

The route always renders, even when there's no history yet: a brand-new
install with no battery devices registered hits this page and gets the
"once a device reports a battery_pct, you'll see it here" empty state
instead of a 500.
"""

from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, Flask, current_app, jsonify, render_template, request

from app.device_loader import DeviceRegistry
from app.state.battery_history import BatteryHistory

bp = Blueprint("device_battery", __name__, url_prefix="/devices/battery")


WINDOW_DAYS_DEFAULT: int = 7
WINDOW_DAYS_MAX: int = 90


def _history() -> BatteryHistory | None:
    store = current_app.config.get("BATTERY_HISTORY")
    return store if isinstance(store, BatteryHistory) else None


def _registry() -> DeviceRegistry | None:
    return current_app.config.get("DEVICE_REGISTRY")


def _resolve_window() -> int:
    raw = request.args.get("window")
    try:
        window = int(raw) if raw else WINDOW_DAYS_DEFAULT
    except (TypeError, ValueError):
        window = WINDOW_DAYS_DEFAULT
    if window < 1:
        return 1
    if window > WINDOW_DAYS_MAX:
        return WINDOW_DAYS_MAX
    return window


def _current_battery_per_device() -> dict[str, dict[str, Any]]:
    """Best-known current battery state per device, pulled from the
    same in-memory cache the topbar indicator + widget use."""
    status: dict[str, dict[str, Any]] = current_app.config.get("DEVICE_STATUS") or {}
    out: dict[str, dict[str, Any]] = {}
    for device_id, entry in status.items():
        parsed = (entry or {}).get("parsed") or {}
        raw = parsed.get("battery_pct")
        if raw is None:
            continue
        try:
            pct = int(raw)
        except (TypeError, ValueError):
            continue
        out[device_id] = {
            "pct": max(0, min(100, pct)),
            "received_at": entry.get("received_at"),
            "battery_mv": parsed.get("battery_mv"),
        }
    return out


def _device_card(
    device_id: str,
    *,
    history: BatteryHistory,
    current: dict[str, Any] | None,
    name: str,
    window_days: int,
) -> dict[str, Any]:
    samples = history.recent(device_id, window_days=window_days)
    prediction = history.predict(device_id, window_days=window_days)
    series = [{"t_ms": int(row.timestamp * 1000), "pct": row.pct} for row in samples]
    return {
        "id": device_id,
        "name": name,
        "current": current or {},
        "samples": len(samples),
        "series": series,
        "prediction": {
            "slope_per_day": prediction.slope_per_day,
            "days_to_20pct": prediction.days_to_20pct,
            "days_to_empty": prediction.days_to_empty,
            "samples": prediction.samples,
        }
        if prediction is not None
        else None,
    }


@bp.get("")
def index() -> str:
    store = _history()
    window_days = _resolve_window()
    cards: list[dict[str, Any]] = []
    if store is not None:
        registry = _registry()
        current = _current_battery_per_device()
        # Devices we actually want to show: any device currently
        # reporting a battery + any device with stored history (even
        # if it's offline right now).
        device_ids = set(current.keys()) | set(store.device_ids())
        # Stable display order: name asc, falling back to id.
        names = {d.id: d.display_name for d in (registry.devices.values() if registry else [])}
        for device_id in sorted(device_ids, key=lambda i: (names.get(i) or i).lower()):
            cards.append(
                _device_card(
                    device_id,
                    history=store,
                    current=current.get(device_id),
                    name=names.get(device_id, device_id),
                    window_days=window_days,
                )
            )
    return render_template(
        "device_battery.html",
        cards=cards,
        window_days=window_days,
        window_options=(1, 3, 7, 14, 30, 90),
        now_ms=int(time.time() * 1000),
    )


@bp.get("/<device_id>/series.json")
def series_json(device_id: str) -> Any:
    """JSON dump of the raw points for one device, for charts that want
    to refresh in place without re-rendering the whole page."""
    store = _history()
    window_days = _resolve_window()
    if store is None:
        return jsonify({"error": "battery history store not configured"}), 503
    rows = store.recent(device_id, window_days=window_days)
    prediction = store.predict(device_id, window_days=window_days)
    return jsonify(
        {
            "device_id": device_id,
            "window_days": window_days,
            "series": [{"t_ms": int(r.timestamp * 1000), "pct": r.pct} for r in rows],
            "prediction": {
                "slope_per_day": prediction.slope_per_day,
                "days_to_20pct": prediction.days_to_20pct,
                "days_to_empty": prediction.days_to_empty,
                "samples": prediction.samples,
            }
            if prediction is not None
            else None,
        }
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
