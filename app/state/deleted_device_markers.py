"""Marker file for MAC-tracked device deletions (v0.69.2, issue #48).

When a user deletes a device without ticking the "also wipe stored
state" checkbox, we stash the device's last-known MAC in a small JSON
file so that a later ``/register`` for the same ``device_id`` can
compare MACs. If the incoming MAC differs (or wasn't known before),
the leftovers are auto-wiped before the new instance is created;
same MAC keeps the state (same physical device came back).

The marker file is best-effort: it only records the MAC when it was
known, and readers tolerate a missing / corrupt file by returning
``None`` (which callers treat as "unknown MAC, so proceed with the
same conservative default"). No secrets go through here, so the file
lives alongside the rest of ``data/`` without a permissions dance.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class DeletedDeviceMarkers:
    """Tiny key-value store for last-known MAC per deleted device id.

    Persists to ``<data_root>/deleted_device_markers.json``. Not thread-
    safe past atomic write / read, but the delete + register flows both
    happen on Flask request threads and never collide in the same
    process at meaningful volume."""

    def __init__(self, data_root: Path) -> None:
        self.path = data_root / "deleted_device_markers.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in raw.items()}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, device_id: str, mac: str | None) -> None:
        """Stash the MAC of a device that was just deleted. Empty MAC
        writes an entry with a null value so a later register can tell
        "we saw this id come and go" from "we never saw it."""
        data = self._load()
        entry: dict[str, Any] = {
            "mac": mac.strip().lower() if isinstance(mac, str) and mac.strip() else None,
            "deleted_at": time.time(),
        }
        data[device_id] = entry
        self._save(data)

    def get(self, device_id: str) -> dict[str, Any] | None:
        """Return the marker for ``device_id`` or ``None`` when we
        never recorded a deletion for it."""
        data = self._load()
        return data.get(device_id)

    def clear(self, device_id: str) -> bool:
        """Drop the marker for ``device_id`` (called after a register
        consumes it). Returns True when the marker existed."""
        data = self._load()
        if device_id not in data:
            return False
        data.pop(device_id, None)
        self._save(data)
        return True

    def mac_differs(self, device_id: str, incoming_mac: str | None) -> bool:
        """True when the stored MAC for ``device_id`` differs from
        ``incoming_mac`` (either side may be ``None`` / empty).

        Precedence for the "differs" verdict:
        * No marker exists: returns False (no reason to wipe; this is
          a fresh id from the caller's perspective).
        * Marker exists with a stored MAC and incoming MAC is empty:
          returns True (client stopped advertising MAC, safest to
          wipe rather than silently rebind).
        * Both MACs present and equal (case-insensitive, whitespace-
          normalised): returns False.
        * Anything else: returns True.
        """
        marker = self.get(device_id)
        if marker is None:
            return False
        stored = marker.get("mac")
        if not stored:
            # We deleted without a MAC; can't compare, so don't wipe.
            return False
        stored_norm = str(stored).strip().lower()
        if not incoming_mac:
            return True
        return stored_norm != incoming_mac.strip().lower()
