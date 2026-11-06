"""Admin page at ``/devices/battery``.

Per-device battery charts (Chart.js, line trace over each chart's own
range, chosen on the chart) plus the same days-to-empty prediction the device_battery
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

from app.battery_chart import (
    DEFAULT_WINDOW_HOURS,
    MAX_WINDOW_HOURS,
    WakeEvents,
    app_chart_options,
    build_battery_chart,
    lineup_names,
    load_wake_events,
    prediction_dict,
    render_card_parts,
)
from app.battery_offset import apply_to_mv, apply_to_pct, get_offset
from app.device_loader import DeviceRegistry
from app.state.battery_history import BatteryHistory

bp = Blueprint("device_battery", __name__, url_prefix="/devices/battery")


# ``series.json`` takes its window in days (``?window=``); the page itself has
# no window parameter, each chart picks its own range.
SERIES_DAYS_DEFAULT: int = 7
SERIES_DAYS_MAX: int = 90


def _history() -> BatteryHistory | None:
    store = current_app.config.get("BATTERY_HISTORY")
    return store if isinstance(store, BatteryHistory) else None


def _registry() -> DeviceRegistry | None:
    return current_app.config.get("DEVICE_REGISTRY")


def _series_window_days() -> int:
    raw = request.args.get("window")
    try:
        window = int(raw) if raw else SERIES_DAYS_DEFAULT
    except (TypeError, ValueError):
        window = SERIES_DAYS_DEFAULT
    return min(max(window, 1), SERIES_DAYS_MAX)


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


@bp.get("")
def index() -> str:
    store = _history()
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
        devices = registry.devices if registry else {}
        ordered = sorted(device_ids, key=lambda i: (names.get(i) or i).lower())
        # One event-log query for every card's wake dots.
        wake_events = load_wake_events(
            current_app.config.get("EVENT_LOG"), ordered, window_hours=DEFAULT_WINDOW_HOURS
        )
        names_by_lineup = lineup_names(current_app.config)
        for device_id in ordered:
            device = devices.get(device_id)
            mv_off, pct_off = get_offset(device.manifest) if device is not None else (0, 0)
            cards.append(
                build_battery_chart(
                    device_id,
                    history=store,
                    window_hours=DEFAULT_WINDOW_HOURS,
                    name=names.get(device_id, device_id),
                    current=current.get(device_id),
                    mv_offset=mv_off,
                    pct_offset=pct_off,
                    events=wake_events.get(device_id, WakeEvents()),
                    **app_chart_options(current_app.config, device, names=names_by_lineup),
                )
            )
    return render_template(
        "device_battery.html",
        cards=cards,
        now_ms=int(time.time() * 1000),
    )


def device_card(
    device_id: str, *, window_days: float | None = None, window_hours: float | None = None
) -> dict[str, Any] | None:
    """The battery chart card for ONE device, as the Batteries page builds it,
    for the device page's Power section and the per-chart range endpoint.
    The window is ``window_hours`` when given, else ``window_days``, else the
    default (7 days).
    ``None`` when the device is not registered or has no battery history and no
    current reading (nothing to draw)."""
    store = _history()
    registry = _registry()
    device = registry.get(device_id) if registry is not None else None
    if store is None or device is None:
        return None
    current = _current_battery_per_device().get(device_id)
    if current is None and device_id not in set(store.device_ids()):
        return None
    mv_off, pct_off = get_offset(device.manifest)
    hours = (
        window_hours
        if window_hours is not None
        else window_days * 24
        if window_days
        else DEFAULT_WINDOW_HOURS
    )
    events = load_wake_events(current_app.config.get("EVENT_LOG"), [device_id], window_hours=hours)
    return build_battery_chart(
        device_id,
        history=store,
        window_hours=hours,
        name=device.display_name,
        current=current,
        mv_offset=mv_off,
        pct_offset=pct_off,
        events=events.get(device_id, WakeEvents()),
        **app_chart_options(current_app.config, device),
    )


@bp.get("/<device_id>/card.json")
def card_json(device_id: str) -> Any:
    """One device's chart card for a window of ``?hours=`` (1 to 720, default
    168), in the shape ``build_battery_chart`` returns plus the HTML the page
    swaps in when a chart's own range changes: ``tiles_html``, ``hint_html`` and
    ``legend_html``. Same offsets and wake attribution as the page itself."""
    if _history() is None:
        return jsonify({"error": "battery history store not configured"}), 503
    try:
        hours = float(request.args.get("hours", DEFAULT_WINDOW_HOURS))
    except (TypeError, ValueError):
        hours = float(DEFAULT_WINDOW_HOURS)
    hours = min(max(hours, 1.0), float(MAX_WINDOW_HOURS))
    card = device_card(device_id, window_hours=hours)
    if card is None:
        return jsonify({"error": "no battery data for this device"}), 404
    return jsonify({**card, **render_card_parts(card)})


@bp.get("/<device_id>/series.json")
def series_json(device_id: str) -> Any:
    """JSON dump of the offset-adjusted points for one device, for
    charts that refresh in place without re-rendering the whole page.

    Same offset application as ``index``: raw rows from SQLite, then
    the per-device ``battery_offset`` manifest layered on top so the
    JSON consumer sees what the dashboard sees."""
    store = _history()
    window_days = _series_window_days()
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
        # ``mv`` alongside ``pct`` because percent is a curve lookup, and the
        # curve is flat through the middle of a LiPo discharge: two devices
        # losing the same charge per day can report very different percent per
        # day depending on where they sit on it. Anyone diagnosing a drain
        # complaint wants the millivolts. None where the firmware reported no
        # voltage (older clients send pct only).
        series.append(
            {
                "t_ms": int(r.timestamp * 1000),
                "pct": adj if adj is not None else r.pct,
                "mv": apply_to_mv(r.battery_mv, mv_off),
            }
        )
    return jsonify(
        {
            "device_id": device_id,
            "window_days": window_days,
            "series": series,
            "prediction": prediction_dict(prediction),
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
