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

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.battery_offset import apply_to_mv, apply_to_pct, get_offset
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
    same in-memory cache the topbar indicator + widget use.

    Per-device ``battery_offset`` manifests (see :mod:`app.battery_offset`)
    apply at this read site so the dashboard, history chart, and the
    SQLite write path all read the same corrected values. Raw firmware
    readings stay untouched in ``DEVICE_STATUS`` so a recalibration
    tomorrow doesn't lose the historical record."""
    status: dict[str, dict[str, Any]] = current_app.config.get("DEVICE_STATUS") or {}
    registry = _registry()
    devices = registry.devices if registry is not None else {}
    out: dict[str, dict[str, Any]] = {}
    for device_id, entry in status.items():
        parsed = (entry or {}).get("parsed") or {}
        raw_pct = parsed.get("battery_pct")
        raw_mv = parsed.get("battery_mv")
        if raw_pct is None and raw_mv is None:
            continue
        try:
            raw_pct_int: int | None = int(raw_pct) if raw_pct is not None else None
        except (TypeError, ValueError):
            continue
        try:
            raw_mv_int: int | None = int(raw_mv) if raw_mv is not None else None
        except (TypeError, ValueError):
            raw_mv_int = None
        device = devices.get(device_id)
        mv_off, pct_off = get_offset(device.manifest) if device is not None else (0, 0)
        adj_pct = apply_to_pct(raw_pct_int, mv_off, pct_off, raw_mv=raw_mv_int)
        adj_mv = apply_to_mv(raw_mv_int, mv_off)
        if adj_pct is None:
            continue
        out[device_id] = {
            "pct": adj_pct,
            "received_at": entry.get("received_at"),
            "battery_mv": adj_mv,
            # Surface the offset block so the dashboard can mark the
            # card "calibrated" rather than the user guessing whether
            # the number they're looking at was adjusted.
            "offset_mv": mv_off,
            "offset_pct": pct_off,
        }
    return out


def _device_card(
    device_id: str,
    *,
    history: BatteryHistory,
    current: dict[str, Any] | None,
    name: str,
    window_days: int,
    mv_offset: int = 0,
    pct_offset: int = 0,
) -> dict[str, Any]:
    """One device's card data, including the chart series and the
    prediction.

    The chart series carries the **offset-adjusted** percent so the
    chart matches the dashboard's current-battery readout. Without
    this, the user would calibrate a device, see "85% → 100%" on
    the headline, and find the chart still showing the old 85%
    curve, which reads as a bug. ``BatteryHistory.recent`` returns
    the raw rows; the offset is layered at this read site only."""
    samples = history.recent(device_id, window_days=window_days)
    prediction = history.predict(device_id, window_days=window_days)
    series: list[dict[str, Any]] = []
    for row in samples:
        adj = apply_to_pct(row.pct, mv_offset, pct_offset, raw_mv=row.battery_mv)
        series.append(
            {"t_ms": int(row.timestamp * 1000), "pct": adj if adj is not None else row.pct}
        )
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
            "days_to_full": prediction.days_to_full,
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
        # Show registered devices only, with their current + historical
        # battery data. The previous logic was
        # ``current.keys() | store.device_ids()`` which leaked deleted
        # devices in two ways: stored history rows survived a registry
        # drop (SQLite outlives the in-memory registry), and stale
        # DEVICE_STATUS entries from a process where v0.55.1's purge
        # hadn't yet run also stayed visible. Intersecting both sources
        # with ``registered_ids`` is the load-bearing fix: a device
        # has to be in the live registry to render a card here, so any
        # historical or cached data for a no-longer-present device is
        # excluded at read time even when on-disk orphan rows linger.
        registered_ids = {d.id for d in (registry.devices.values() if registry else [])}
        device_ids = (set(current.keys()) | set(store.device_ids())) & registered_ids
        # Stable display order: name asc, falling back to id.
        names = {d.id: d.display_name for d in (registry.devices.values() if registry else [])}
        manifests = {d.id: d.manifest for d in (registry.devices.values() if registry else [])}
        for device_id in sorted(device_ids, key=lambda i: (names.get(i) or i).lower()):
            mv_off, pct_off = get_offset(manifests.get(device_id, {}))
            cards.append(
                _device_card(
                    device_id,
                    history=store,
                    current=current.get(device_id),
                    name=names.get(device_id, device_id),
                    window_days=window_days,
                    mv_offset=mv_off,
                    pct_offset=pct_off,
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
    """JSON dump of the offset-adjusted points for one device, for
    charts that refresh in place without re-rendering the whole page.

    Same offset application as ``index``: raw rows from SQLite, then
    the per-device ``battery_offset`` manifest layered on top so the
    JSON consumer sees what the dashboard sees."""
    store = _history()
    window_days = _resolve_window()
    if store is None:
        return jsonify({"error": "battery history store not configured"}), 503
    rows = store.recent(device_id, window_days=window_days)
    prediction = store.predict(device_id, window_days=window_days)
    registry = _registry()
    device = registry.get(device_id) if registry is not None else None
    mv_off, pct_off = get_offset(device.manifest) if device is not None else (0, 0)
    series: list[dict[str, Any]] = []
    for r in rows:
        adj = apply_to_pct(r.pct, mv_off, pct_off, raw_mv=r.battery_mv)
        series.append({"t_ms": int(r.timestamp * 1000), "pct": adj if adj is not None else r.pct})
    return jsonify(
        {
            "device_id": device_id,
            "window_days": window_days,
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
    )


@bp.post("/<device_id>/clear")
def clear_history(device_id: str) -> Any:
    """Drop every recorded battery sample for ``device_id``. Destructive
    by design: the user is the only authoritative source for "this
    history is wrong, start fresh" (e.g. after a calibration change,
    a battery swap, or a device factory-reset)."""
    store = _history()
    if store is None:
        flash("Battery history store not configured.", "error")
        return redirect(url_for("device_battery.index"))
    store.forget(device_id)
    flash(f"Cleared battery history for {device_id!r}.", "ok")
    return redirect(url_for("device_battery.index"))


def register(app: Flask) -> None:
    app.register_blueprint(bp)
