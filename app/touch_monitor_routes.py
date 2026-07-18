"""Per-device touch monitor (issue #49).

A diagnostic view for a touch-capable panel (the reTerminal E1003): the
panel drawn at its true aspect ratio with recent touch events plotted on
it, and the last render's touch regions overlaid, so you can see whether
taps are landing inside their targets and what they resolved to. Live via
the shared events SSE stream (``/events/stream?type=touch``); the initial
paint comes from ``data.json``.

Only registered, touch-capable device instances have a monitor; everything
else 404s. mypy --strict applies to this module.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Flask, abort, current_app, jsonify, render_template
from werkzeug.wrappers import Response

from app.device_loader import Device, DeviceRegistry

bp = Blueprint("touch_monitor", __name__, url_prefix="/devices/touch")

# How many recent touch events to seed the view with on load. Live events
# then stream in on top via SSE.
_RECENT_LIMIT = 200


def _registry() -> DeviceRegistry | None:
    return current_app.config.get("DEVICE_REGISTRY")


def _touch_device(device_id: str) -> Device | None:
    """A registered instance that is touch-capable, else None."""
    reg = _registry()
    if reg is None:
        return None
    dev = reg.get(device_id)
    if dev is None or dev.kind_of is None:
        return None
    return dev if dev.manifest.get("touch") is True else None


def _panel_dims(dev: Device) -> tuple[int, int]:
    from app.panel import device_panel

    panel = device_panel(dev)
    if panel is not None:
        return panel.w, panel.h
    block = dev.manifest.get("panel") or {}
    return int(block.get("w") or 0), int(block.get("h") or 0)


@bp.get("/<device_id>")
def monitor(device_id: str) -> str:
    """The touch-monitor page for one touch-capable device."""
    dev = _touch_device(device_id)
    if dev is None:
        abort(404)
    w, h = _panel_dims(dev)
    return render_template(
        "touch_monitor.html",
        device_id=device_id,
        device_name=dev.display_name,
        panel_w=w,
        panel_h=h,
    )


@bp.get("/<device_id>/data.json")
def data(device_id: str) -> Response:
    """Initial paint: panel dims, the last render's touch regions, and the
    recent touch events for this device (newest first)."""
    dev = _touch_device(device_id)
    if dev is None:
        abort(404)
    w, h = _panel_dims(dev)

    regions: list[dict[str, Any]] = []
    push_mgr = current_app.config.get("PUSH_MANAGER")
    if push_mgr is not None:
        latest = push_mgr.latest_render_for(device_id)
        if latest is not None:
            regions = push_mgr.touch_regions_for(str(latest.get("composition_digest") or ""))

    events: list[dict[str, Any]] = []
    log = current_app.config.get("EVENT_LOG")
    if log is not None:
        for row in log.list(type="touch", limit=_RECENT_LIMIT):
            if row.target != device_id:
                continue
            events.append(
                {
                    "id": row.id,
                    "timestamp": row.timestamp,
                    "status": row.status,
                    "gesture": row.extra.get("gesture"),
                    "value": row.extra.get("value"),
                    "touch": row.extra.get("touch"),
                    "region": row.extra.get("region"),
                    "action_spec": row.extra.get("action_spec"),
                }
            )

    return jsonify({"panel": {"w": w, "h": h}, "regions": regions, "events": events})


@bp.post("/<device_id>/clear")
def clear(device_id: str) -> Response:
    """Delete this device's recorded touch events. The monitor seeds from
    touch history on load, so a view-only clear would reappear on the next
    refresh; removing the rows makes it stick. Scoped to ``touch`` events
    for this device, so push / button history is untouched. Returns
    ``{"cleared": <count>}``."""
    dev = _touch_device(device_id)
    if dev is None:
        abort(404)
    removed = 0
    log = current_app.config.get("EVENT_LOG")
    if log is not None:
        removed = log.delete_by_type_target(type="touch", target=device_id)
    return jsonify({"cleared": removed})


def register(app: Flask) -> None:
    app.register_blueprint(bp)
