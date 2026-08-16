"""Turns logged activity into the daily counters behind ``/stats``.

Subscribing to the event log rather than sprinkling counter bumps
through the push manager keeps this to one integration point: every
place that already records an event feeds the stats page for free, and a
new event type shows up as a row in "other activity" without anyone
remembering to wire it. The event log evicts old rows; these aggregates
are what survive.

Heartbeats are the one exception. A steady heartbeat is deliberately not
logged (it would churn the capped log), so wake counts are bumped from
the transport's status handler directly.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import logging
from typing import Any

from app.state.event_log import EventRow
from app.state.stats_store import (
    ACTIVITY_BY_TYPE,
    DEVICE_WAKES,
    FRAMES_BY_DEVICE,
    PUSH_MS_SUM,
    PUSHES_BY_SOURCE,
    PUSHES_BY_STATUS,
    StatsStore,
)

logger = logging.getLogger(__name__)

# How a raw event source is grouped on the page. The store keeps the raw
# value, so regrouping later is a display change and doesn't rewrite
# history. Anything unlisted falls into "other", which is why the page
# always draws that band even when it's empty.
SOURCE_BUCKETS: dict[str, str] = {
    "scheduler": "scheduled",
    "rotation": "scheduled",
    "deck": "scheduled",
    "condition": "scheduled",
    "page": "by hand",
    "file": "by hand",
    "url": "by hand",
    "webpage": "by hand",
    "manual": "by hand",
    "resend": "by hand",
    "webhook": "integrations",
    "home_assistant": "integrations",
    "companion": "integrations",
    "mcp": "integrations",
    "relay": "integrations",
}

BUCKET_ORDER: tuple[str, ...] = ("scheduled", "by hand", "integrations", "other")


def bucket_for(source: str) -> str:
    return SOURCE_BUCKETS.get(source, "other")


class StatsRecorder:
    """Event-log listener that keeps the daily counters up to date."""

    def __init__(self, stats: StatsStore) -> None:
        self._stats = stats

    def on_event(self, row: EventRow) -> None:
        """Count one logged event. The event log already swallows and
        logs listener exceptions, and :meth:`StatsStore.bump` never
        raises, so this can't affect whatever produced the event."""
        if row.type != "push":
            self._stats.bump(ACTIVITY_BY_TYPE, row.type)
            return

        self._stats.bump(PUSHES_BY_STATUS, row.status or "unknown")
        self._stats.bump(PUSHES_BY_SOURCE, row.source or "unknown")
        if row.status != "sent":
            # A skipped or failed push painted nothing, so it doesn't
            # belong in the per-display counts or the render-time
            # average; the outcome counter above is where it shows up.
            return
        self._stats.bump(PUSH_MS_SUM, "", max(0, int(row.duration_s * 1000)))
        for device_id in _device_ids(row.extra):
            self._stats.bump(FRAMES_BY_DEVICE, device_id)

    def record_wake(self, device_id: str) -> None:
        """One display checked in. Called per heartbeat, including the
        steady ones the event log skips."""
        if device_id:
            self._stats.bump(DEVICE_WAKES, device_id)


def _device_ids(extra: dict[str, Any]) -> list[str]:
    raw = extra.get("device_ids")
    if not isinstance(raw, list):
        return []
    return [str(d) for d in raw if isinstance(d, str) and d]
