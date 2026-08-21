"""When a widget's own data makes the current frame wrong (#243).

``_next_poll_s`` derives its wake time from schedules and rotation steps
via ``project_upcoming``. That machinery cannot see inside widget data, so
anything whose displayed state turns over on its own clock is invisible to
it: a meeting room going free at 15:00, a bin collection at dawn, a
countdown hitting zero.

A widget's ``fetch()`` may return ``next_change_at`` (ISO 8601, or a unix
timestamp) meaning "the output I just produced becomes wrong at this
instant". The composer records the soonest such value across the widgets
on a device's page; the REST status path folds it in alongside the
schedule projection, and the frame path uses it to notice that the
artefact on the panel is now out of date.

In memory on purpose. A restart drops the hints, and every device falls
back to its configured interval until its next render repopulates them,
which is the same answer the server gave before this existed. Persisting
would buy a few minutes of accuracy after a restart and cost a store to
keep consistent with renders that may never happen again.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_KEY = "WIDGET_NEXT_CHANGE"

# A widget that reports a change more than this far out tells us nothing
# useful: the configured interval is the ceiling anyway, and holding a
# week-long hint just keeps stale state alive across page edits.
_MAX_HORIZON_S = 7 * 24 * 3600


def parse_next_change(raw: Any) -> float | None:
    """Coerce a widget's ``next_change_at`` to a unix timestamp.

    Accepts ISO 8601 (with or without offset, ``Z`` included) and a bare
    numeric timestamp, because widgets carry both conventions already.
    Anything else is ignored rather than raised: a malformed hint must
    never break the render that produced it.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Naive means the widget meant local wall time; the server's own
        # zone is the only defensible reading.
        return dt.timestamp()
    return dt.timestamp()


def record(app: Any, device_id: str, timestamps: list[float], *, now: float) -> float | None:
    """Store the soonest future change for ``device_id``. Returns it.

    Replaces rather than merges: a re-render supersedes whatever the last
    one predicted, including a hint that has since been removed because
    the page was edited or a widget swapped out.
    """
    if not device_id:
        return None
    future = [t for t in timestamps if now < t <= now + _MAX_HORIZON_S]
    store = app.config.get(CONFIG_KEY)
    if not isinstance(store, dict):
        store = {}
        app.config[CONFIG_KEY] = store
    if not future:
        store.pop(device_id, None)
        return None
    soonest = min(future)
    store[device_id] = soonest
    return soonest


def peek(app: Any, device_id: str) -> float | None:
    """The recorded change for ``device_id``, or None."""
    store = app.config.get(CONFIG_KEY)
    if not isinstance(store, dict):
        return None
    value = store.get(device_id)
    return float(value) if isinstance(value, (int, float)) else None


def clear(app: Any, device_id: str) -> None:
    store = app.config.get(CONFIG_KEY)
    if isinstance(store, dict):
        store.pop(device_id, None)


def collect(data_by_cell: dict[int, Any]) -> list[float]:
    """Pull ``next_change_at`` out of a render's per-cell fetch results."""
    out: list[float] = []
    for value in data_by_cell.values():
        if not isinstance(value, dict):
            continue
        ts = parse_next_change(value.get("next_change_at"))
        if ts is not None:
            out.append(ts)
    return out
