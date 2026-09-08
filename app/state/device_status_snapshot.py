"""Persisted copy of each device's last heartbeat.

The live ``DEVICE_STATUS`` cache is rebuilt from heartbeats, so a server
restart forgets every device's last battery, signal, temperature and
humidity reading until the device next reports. Anything rendered from
the cache in between (the status strip widget, the Devices card tiles,
a per-device widget fetch on a button wake, which lands before that same
wake's heartbeat) came out blank. This store keeps the last merged
heartbeat on disk (``data/core/device_status.json``) so the cache can be
seeded with it at boot; the original ``received_at`` rides along, so the
Devices card still reports the reading's true age.

Written from the shared heartbeat path. Heartbeats can be frequent on an
always-on panel, so a beat whose readings match the persisted ones is
skipped unless the persisted copy is older than :data:`REFRESH_AFTER_S`.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

# A steady heartbeat (nothing in ``parsed`` changed) still refreshes the
# persisted ``received_at`` this often, so a restart doesn't age the
# reading by more than this.
REFRESH_AFTER_S: float = 300.0


class DeviceStatusSnapshotStore:
    """Thread-safe single-file store of the last heartbeat per device."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def get(self, device_id: str) -> dict[str, Any] | None:
        entry = self._load().get(device_id)
        return entry if isinstance(entry, dict) else None

    def all(self) -> dict[str, dict[str, Any]]:
        """Every device's last heartbeat, for startup seeding of the
        status cache. Entries missing either field are dropped."""
        out: dict[str, dict[str, Any]] = {}
        for device_id, entry in self._load().items():
            if not isinstance(entry, dict):
                continue
            received_at = entry.get("received_at")
            parsed = entry.get("parsed")
            if not isinstance(received_at, (int, float)) or not isinstance(parsed, dict):
                continue
            out[device_id] = {"received_at": float(received_at), "parsed": dict(parsed)}
        return out

    def record(self, device_id: str, *, received_at: float, parsed: dict[str, Any]) -> None:
        """Store ``parsed`` as ``device_id``'s last heartbeat. Skips the
        write when the readings are unchanged and the stored copy is
        recent (see :data:`REFRESH_AFTER_S`). Values that JSON can't carry
        are dropped rather than failing the heartbeat."""
        clean = _json_safe(parsed)
        with self._lock:
            data = self._load()
            raw = data.get(device_id)
            previous = raw if isinstance(raw, dict) else {}
            prev_at = previous.get("received_at")
            prev_at_f = float(prev_at) if isinstance(prev_at, (int, float)) else None
            if (
                previous.get("parsed") == clean
                and prev_at_f is not None
                and received_at - prev_at_f < REFRESH_AFTER_S
            ):
                return
            data[device_id] = {"received_at": float(received_at), "parsed": clean}
            self._save(data)

    def forget(self, device_id: str) -> None:
        """Drop a device's snapshot, e.g. on unregister, so a future device
        reusing the id doesn't inherit its readings."""
        with self._lock:
            data = self._load()
            if device_id not in data:
                return
            del data[device_id]
            self._save(data)


def _json_safe(value: Any) -> Any:
    """Return ``value`` with anything JSON can't encode dropped, so one odd
    heartbeat field never blocks the snapshot. Keys are always strings."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            safe = _json_safe(item)
            if safe is not _DROP:
                out[str(key)] = safe
        return out
    if isinstance(value, (list, tuple)):
        return [safe for safe in (_json_safe(item) for item in value) if safe is not _DROP]
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            return _DROP
        return value
    return _DROP


_DROP = object()
