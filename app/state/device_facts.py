"""Persisted per-device facts that outlive the in-memory status cache.

The live ``DEVICE_STATUS`` cache is a plain dict rebuilt from heartbeats,
so a server restart forgets what firmware a device runs and whether it
advertised the OTA capability until the device next wakes; on the Firmware
page that briefly (mis)read as "USB update only". Those are stable device
facts, not runtime state: this store keeps the last known values on disk
(``data/core/device_facts.json``) so the UI stays truthful across restarts.

Written from the shared heartbeat path only when a value actually changes,
so steady-state heartbeats cost no disk writes.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class DeviceFactsStore:
    """Thread-safe single-file store of last-known per-device facts."""

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
        """Every device's facts, for startup seeding of the status cache."""
        return {k: v for k, v in self._load().items() if isinstance(v, dict)}

    def record(
        self,
        device_id: str,
        *,
        fw_version: str | None = None,
        ota_schema: int | None = None,
        overlay: dict[str, Any] | None = None,
        proto: dict[str, Any] | None = None,
        can_stay_awake: bool | None = None,
    ) -> None:
        """Merge the given facts for ``device_id``; writes only on change.
        ``None`` values mean "no new information", never "clear".
        ``overlay`` is the advertised overlay capability (a firmware
        property like ``ota_schema``); persisting it means a server
        restart doesn't demote a patch-capable panel to full-repaint
        reconciles until its next heartbeat."""
        with self._lock:
            data = self._load()
            raw = data.get(device_id)
            entry: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
            changed = False
            if fw_version and entry.get("fw_version") != fw_version:
                entry["fw_version"] = fw_version
                changed = True
            if ota_schema is not None and entry.get("ota_schema") != ota_schema:
                entry["ota_schema"] = ota_schema
                changed = True
            if isinstance(overlay, dict) and entry.get("overlay") != overlay:
                entry["overlay"] = overlay
                changed = True
            if isinstance(proto, dict) and entry.get("proto") != proto:
                entry["proto"] = proto
                changed = True
            if can_stay_awake is not None and entry.get("can_stay_awake") != can_stay_awake:
                entry["can_stay_awake"] = can_stay_awake
                changed = True
            if not changed:
                return
            entry["updated_at"] = time.time()
            data[device_id] = entry
            self._save(data)

    def forget(self, device_id: str) -> None:
        """Drop a device's facts, e.g. on unregister, so a future device
        reusing the id starts clean."""
        with self._lock:
            data = self._load()
            if device_id not in data:
                return
            del data[device_id]
            self._save(data)
