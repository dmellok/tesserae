"""Discovery of unregistered devices via wildcard status-topic listening.

The transport subscribes once to ``tesserae/+/status``. Every heartbeat
that arrives for a device id NOT in the device registry gets cached
here; the Settings → Devices page surfaces those as a "Discovered"
strip with one-click register buttons.

Clients participating in discovery embed a few well-known keys in
their heartbeat JSON (see `PROMPTS/updates/*_discovery.md`):

* ``kind`` — the Tesserae device kind id (``pi_bin_client`` /
  ``pi_png_client`` / ``esp32_client``). Tells the UI which kind to
  pre-select in the Add-device form.
* ``panel_w`` / ``panel_h`` — pixel dims the client expects to paint.
* ``fw_version``, ``ip`` (optional) — diagnostic context, surfaced in
  the discovered-device row.

Unknown / missing keys are tolerated — the user can still register a
discovered device by hand, they just don't get the pre-fill convenience.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

# Shared with the device-instance lifecycle so a discovered id always
# passes the same validation as a hand-entered one.
from app.device_service import DEVICE_ID_RE as _DEVICE_ID_RE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredDevice:
    id: str
    received_at: float
    parsed: dict[str, Any]

    @property
    def kind(self) -> str | None:
        value = self.parsed.get("kind")
        return str(value) if isinstance(value, str) else None

    @property
    def panel_w(self) -> int | None:
        return _maybe_int(self.parsed.get("panel_w"))

    @property
    def panel_h(self) -> int | None:
        return _maybe_int(self.parsed.get("panel_h"))

    @property
    def fw_version(self) -> str | None:
        value = self.parsed.get("fw_version")
        return str(value) if isinstance(value, (str, int, float)) else None

    @property
    def ip(self) -> str | None:
        value = self.parsed.get("ip")
        return str(value) if isinstance(value, str) else None


def record_trmnl_discovery(
    cache: DiscoveryCache,
    *,
    token: str,
    headers: dict[str, Any],
    remote_addr: str | None,
) -> DiscoveredDevice | None:
    """Cache an HTTP-polled TRMNL client that polled with an unknown
    token, so the Settings → Devices → Discovered strip surfaces it
    for one-click adoption — same UX as MQTT-side discovery.

    The cache id is synthetic (``trmnl_<token>``) since TRMNL devices
    don't have an MQTT topic. The cached payload includes the original
    ``access_token`` so the register flow can preserve it (the user
    already has it pasted into their Kindle config — making them
    re-paste a freshly-generated one would defeat the point of
    discovery)."""
    safe = re.sub(r"[^a-z0-9_-]", "", token.lower())[:20]
    synthetic_id = f"trmnl_{safe}" if safe else "trmnl_unknown"
    parsed: dict[str, Any] = {
        "kind": "trmnl_client",
        "access_token": token,
    }
    # Map BYOS-style headers to the discovery schema the UI already
    # understands (kind / panel_w / panel_h / fw_version / ip).
    for src_keys, dest in (
        (("png-width", "Width"), "panel_w"),
        (("png-height", "Height"), "panel_h"),
        (("User-Agent",), "fw_version"),
    ):
        for k in src_keys:
            value = headers.get(k)
            if value:
                parsed[dest] = value
                break
    if remote_addr:
        parsed["ip"] = remote_addr
    payload = json.dumps(parsed).encode("utf-8")
    return cache.record(synthetic_id, payload)


def _maybe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    if isinstance(value, float):
        import math

        return int(value) if math.isfinite(value) else None
    return None


class DiscoveryCache:
    """Thread-safe in-memory cache of unregistered devices we've seen.

    Single-writer (the MQTT dispatcher thread) but the settings page
    reads from a Flask request thread, so the lock protects iteration."""

    def __init__(self) -> None:
        self._items: dict[str, DiscoveredDevice] = {}
        self._lock = threading.Lock()

    def record(self, device_id: str, payload: bytes) -> DiscoveredDevice | None:
        """Add or refresh a discovered device. Returns the cached entry,
        or None if ``device_id`` is malformed (we refuse to cache ids
        we'd reject during registration anyway) or the payload is empty.

        An empty payload is a retained-message *tombstone* — what a broker
        delivers after the retained heartbeat is cleared (e.g. on Dismiss).
        It's not a live device, so skip it; otherwise clearing a ghost
        would immediately re-add a kind-less one."""
        if not _DEVICE_ID_RE.match(device_id):
            return None
        if not payload or not payload.strip():
            return None
        try:
            decoded = json.loads(payload.decode("utf-8")) if payload else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = {"raw": payload.decode("utf-8", errors="replace")}
        if not isinstance(decoded, dict):
            decoded = {"raw": decoded}
        with self._lock:
            prev = self._items.get(device_id)
            if prev is not None:
                # Merge over the previous heartbeat so a kind / panel /
                # fw_version seen once persists even if a later, partial
                # heartbeat omits them (e.g. a device that sends a lean
                # heartbeat after a full one). Mirrors the registered-
                # device status cache's merge behaviour.
                merged = dict(prev.parsed)
                merged.update({k: v for k, v in decoded.items() if v is not None})
                decoded = merged
            entry = DiscoveredDevice(
                id=device_id,
                received_at=time.time(),
                parsed=decoded,
            )
            self._items[device_id] = entry
        return entry

    def forget(self, device_id: str) -> bool:
        """Remove a device from the cache. Returns True if it was there.
        Used by the Settings dismiss action and by the registration flow
        once a discovered device becomes a real instance."""
        with self._lock:
            return self._items.pop(device_id, None) is not None

    def get(self, device_id: str) -> DiscoveredDevice | None:
        with self._lock:
            return self._items.get(device_id)

    def all(self) -> list[DiscoveredDevice]:
        """Snapshot, sorted by most-recently-seen first."""
        with self._lock:
            entries = list(self._items.values())
        entries.sort(key=lambda d: d.received_at, reverse=True)
        return entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


# Topic helper kept tiny so the dispatcher in main.py stays readable.
_STATUS_TOPIC_RE = re.compile(r"^tesserae/([^/]+)/status$")


def device_id_from_status_topic(topic: str) -> str | None:
    """Extract the device id from ``tesserae/<id>/status``. None for
    anything else (different prefix, extra path segments, etc.)."""
    m = _STATUS_TOPIC_RE.match(topic)
    return m.group(1) if m else None
