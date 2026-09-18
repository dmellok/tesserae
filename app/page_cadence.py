"""Page-level wake cadence (#144).

How often a device wakes has always been a property of the device
(``sleep_interval_s``), but the thing that knows how often it needs
refreshing is the content: a daily agenda wants a day, a transit board
wants two minutes, a weather page wants half an hour. A once-daily
dashboard on a five-minute panel woke roughly 288 times to collect 287
``304``s (discussion #24).

A page may therefore declare its own ``sleep_interval_s``, and the
device currently showing that page wakes on it instead. The resolution
order becomes:

1. the always-on cadence, for a device that never sleeps at all — it is
   not on the sleep grid, so nothing about a page's cadence applies;
2. the displayed page's ``sleep_interval_s``;
3. the device's stored ``sleep_interval_s``;
4. the kind schema's default, then a transport-wide fallback.

Clamped to the kind's own ``min``/``max``, so a page cannot ask a panel
to wake faster than its firmware allows or sleep past what it can be
woken from. Firmware clamps its own side too; this keeps the server from
sending a number it knows is out of range.

"Displayed" is the page behind the frame the device last had rendered
for it, which is what the latest-render record names. A device with no
render yet has no page and keeps its own interval.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import current_app

from app.device_loader import Device

logger = logging.getLogger(__name__)


def _schema_bounds(device: Device) -> tuple[int | None, int | None]:
    """The kind's declared wake-interval bounds, or ``(None, None)``."""
    schema = device.config_schema or {}
    spec = schema.get("sleep_interval_s") if isinstance(schema, dict) else None
    if not isinstance(spec, dict):
        return None, None
    lo = spec.get("min")
    hi = spec.get("max")
    return (
        lo if isinstance(lo, int) and not isinstance(lo, bool) else None,
        hi if isinstance(hi, int) and not isinstance(hi, bool) else None,
    )


def displayed_page_id(device_id: str) -> str | None:
    """The page behind this device's most recent render, if any."""
    push_mgr = current_app.config.get("PUSH_MANAGER")
    if push_mgr is None:
        return None
    reader = getattr(push_mgr, "latest_render_for", None)
    if not callable(reader):
        return None
    latest = reader(device_id)
    page_id = (latest or {}).get("page_id")
    return page_id if isinstance(page_id, str) and page_id else None


def page_sleep_interval_s(device: Device) -> int | None:
    """The wake interval declared by the page on this device's glass,
    clamped to the kind's bounds, or ``None`` when nothing declares one.

    ``None`` covers every uncertainty — no render yet, a page that has
    since been deleted, a page that declares nothing — because the
    device's own interval is the answer in all of them.
    """
    page_id = displayed_page_id(device.id)
    if page_id is None:
        return None
    store = current_app.config.get("PAGE_STORE")
    if store is None:
        return None
    try:
        page = store.get(page_id)
    except Exception:
        logger.exception("page cadence: page lookup failed for device=%s", device.id)
        return None
    raw: Any = getattr(page, "sleep_interval_s", None) if page is not None else None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    lo, hi = _schema_bounds(device)
    if lo is not None:
        raw = max(raw, lo)
    if hi is not None:
        raw = min(raw, hi)
    return int(raw)
