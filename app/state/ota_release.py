"""Per-kind OTA releases: manual promote + canary rollout (issue #121).

Where ``ota_staging`` targets a single device (a deliberate one-off), this
store targets a device *kind*: an admin sets a signed build as the kind's
release, offers it to a canary device or two first, then promotes it to every
device of that kind. Nothing ships without an explicit promote.

One JSON file, ``<data_root>/core/ota_releases.json``, keyed by device kind::

    {
      "seeed_reterminal_e1001": {
        "descriptor": {"payload": "...", "signature": "..."},
        "fw_version": "1.5.0",
        "state": "canary",              # canary | promoted | paused
        "canary_device_ids": ["hall_esp"],
        "updated_at": 1721400000.0
      }
    }

Delivery (in the /status handler) offers the descriptor to a device when its
kind has a release that is not paused, the device is eligible (promoted, or in
the canary list), and the release's firmware version is newer than the version
the device reports. The per-device ``ota_staging`` override still wins when set.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

# Rollout states. ``canary`` offers only to listed devices; ``promoted`` offers
# to every device of the kind; ``paused`` offers to none (kept for quick resume).
STATE_CANARY = "canary"
STATE_PROMOTED = "promoted"
STATE_PAUSED = "paused"


class OtaReleaseStore:
    """Thread-safe single-file store of per-kind OTA releases."""

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

    def get(self, kind_id: str) -> dict[str, Any] | None:
        entry = self._load().get(kind_id)
        return entry if isinstance(entry, dict) else None

    def all(self) -> dict[str, Any]:
        return dict(self._load())

    def set_target(
        self,
        kind_id: str,
        descriptor: dict[str, str],
        *,
        fw_version: str,
        canary_device_ids: list[str] | None = None,
        updated_at: float | None = None,
    ) -> dict[str, Any]:
        """Set (or replace) a kind's release, in canary state. Canary list may
        be empty, in which case nothing ships until ``promote``."""
        entry = {
            "descriptor": {"payload": descriptor["payload"], "signature": descriptor["signature"]},
            "fw_version": fw_version,
            "state": STATE_CANARY,
            "canary_device_ids": list(canary_device_ids or []),
            "updated_at": updated_at if updated_at is not None else time.time(),
        }
        with self._lock:
            data = self._load()
            data[kind_id] = entry
            self._save(data)
        return entry

    def _set_state(self, kind_id: str, state: str) -> bool:
        with self._lock:
            data = self._load()
            entry = data.get(kind_id)
            if not isinstance(entry, dict):
                return False
            entry["state"] = state
            entry["updated_at"] = time.time()
            self._save(data)
        return True

    def promote(self, kind_id: str) -> bool:
        """Offer the release to every device of the kind."""
        return self._set_state(kind_id, STATE_PROMOTED)

    def pause(self, kind_id: str) -> bool:
        """Stop offering the release (resume with promote / re-set)."""
        return self._set_state(kind_id, STATE_PAUSED)

    def clear(self, kind_id: str) -> bool:
        with self._lock:
            data = self._load()
            if kind_id not in data:
                return False
            del data[kind_id]
            self._save(data)
        return True

    def descriptor_for(self, kind_id: str, device_id: str) -> dict[str, str] | None:
        """The descriptor to offer this device for its kind's release, or None.

        Applies the state gate (paused → none; canary → only listed devices;
        promoted → all) but NOT the firmware-version gate, which the caller
        applies since it needs the device's reported version.
        """
        entry = self.get(kind_id)
        if entry is None:
            return None
        state = entry.get("state")
        if state == STATE_PAUSED:
            return None
        if state == STATE_CANARY and device_id not in (entry.get("canary_device_ids") or []):
            return None
        descriptor = entry.get("descriptor")
        if isinstance(descriptor, dict) and "payload" in descriptor and "signature" in descriptor:
            return {
                "payload": str(descriptor["payload"]),
                "signature": str(descriptor["signature"]),
            }
        return None
