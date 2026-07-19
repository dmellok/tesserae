"""Pending OTA descriptors, staged per device (issue #121 / OTA Phase 1).

An admin (or a signing pipeline) stages a signed ``{payload, signature}``
descriptor for a device; the ``/status`` handler hands it back to that device
once, on its next heartbeat, provided the device advertised a compatible OTA
schema. The store is the boundary between "an update has been prepared" and
"the device has been told about it".

One JSON file, ``<data_root>/core/ota_pending.json``, mapping ``device_id`` to
a staged entry::

    {
      "<device_id>": {
        "descriptor": {"payload": "...", "signature": "..."},
        "device_kind": "esp32_client",
        "fw_version": "1.4.0",
        "schema_version": 1,
        "staged_at": 1721400000.0
      }
    }

The descriptor is the source of truth; the sibling metadata is a decoded copy
for cheap delivery-time checks and admin listing. A staged entry is offered
until it is cleared: the firmware's own ``already_current`` guard makes
re-offering an update the device already applied a no-op, so there is no need
to clear on delivery.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class OtaStagingStore:
    """Thread-safe single-file store of pending OTA descriptors."""

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
        """The staged entry for a device, or ``None`` when nothing is pending."""
        entry = self._load().get(device_id)
        return entry if isinstance(entry, dict) else None

    def all(self) -> dict[str, Any]:
        """Every staged entry, keyed by device id (a copy)."""
        return dict(self._load())

    def stage(
        self,
        device_id: str,
        descriptor: dict[str, str],
        *,
        device_kind: str,
        fw_version: str,
        schema_version: int,
        staged_at: float | None = None,
    ) -> dict[str, Any]:
        """Stage (or replace) a pending descriptor for a device. Returns the
        stored entry."""
        entry = {
            "descriptor": {"payload": descriptor["payload"], "signature": descriptor["signature"]},
            "device_kind": device_kind,
            "fw_version": fw_version,
            "schema_version": int(schema_version),
            "staged_at": staged_at if staged_at is not None else time.time(),
        }
        with self._lock:
            data = self._load()
            data[device_id] = entry
            self._save(data)
        return entry

    def clear(self, device_id: str) -> bool:
        """Remove a device's pending descriptor. Returns True if one was there."""
        with self._lock:
            data = self._load()
            if device_id not in data:
                return False
            del data[device_id]
            self._save(data)
        return True
