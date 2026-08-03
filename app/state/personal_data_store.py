"""Latest-only personal-data snapshots for the Companion bridge (#176).

The iOS Companion publishes minimal, expiring Apple Reminders snapshots; the
server keeps only the *latest* snapshot per source and renders it in a widget.
The generic source may group several explicitly selected lists, while the
legacy fridge source keeps its original one-list shape. No history: exactly one
snapshot per ``source_id``, replaced on each accepted PUT and deleted on disable
or expiry. The server never connects to iCloud, it only stores what the phone
explicitly publishes, and drops it once expired. Contract:
``tests/companion/contract/app-v1.openapi.yaml`` (Companion 0.7.0).

Each stored record wraps the raw snapshot with the two epochs the API needs for
ordering and freshness, so this store never parses ISO timestamps itself::

    { "snapshot": {...}, "generated_epoch": float, "expires_epoch": float,
      "stored_at": float }

After expiry the raw ``snapshot`` key is removed while the non-sensitive
timestamps remain as an ``expired`` tombstone. That lets status surfaces and
widgets distinguish "expired" from "never synced" without retaining personal
values past their deadline.

mypy --strict applies to this module.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class PersonalDataSnapshotStore:
    """Thread-safe single-file store of the latest snapshot per ``source_id``."""

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

    def get(self, source_id: str) -> dict[str, Any] | None:
        self.redact_expired(time.time())
        entry = self._load().get(source_id)
        return entry if isinstance(entry, dict) else None

    def all(self) -> dict[str, dict[str, Any]]:
        self.redact_expired(time.time())
        return {k: v for k, v in self._load().items() if isinstance(v, dict)}

    def put(
        self,
        source_id: str,
        *,
        snapshot: dict[str, Any],
        generated_epoch: float,
        expires_epoch: float,
    ) -> None:
        """Replace the latest snapshot for ``source_id``."""
        with self._lock:
            data = self._load()
            data[source_id] = {
                "snapshot": snapshot,
                "generated_epoch": generated_epoch,
                "expires_epoch": expires_epoch,
                "stored_at": time.time(),
            }
            self._save(data)

    def delete(self, source_id: str) -> bool:
        """Remove a source's snapshot. Returns whether one existed."""
        with self._lock:
            data = self._load()
            if source_id not in data:
                return False
            del data[source_id]
            self._save(data)
            return True

    def redact_expired(self, now: float) -> list[str]:
        """Remove raw values from snapshots past ``expires_epoch``.

        Non-sensitive timing metadata remains as a tombstone so callers can
        report ``expired`` without retaining Reminder titles or list contents.
        Returns the source ids redacted during this call.
        """
        with self._lock:
            data = self._load()
            redacted: list[str] = []
            for source_id in list(data):
                entry = data.get(source_id)
                exp = entry.get("expires_epoch") if isinstance(entry, dict) else None
                if (
                    isinstance(entry, dict)
                    and isinstance(exp, (int, float))
                    and now >= exp
                    and "snapshot" in entry
                ):
                    del entry["snapshot"]
                    entry["expired"] = True
                    redacted.append(source_id)
            if redacted:
                self._save(data)
            return redacted

    def purge_expired(self, now: float) -> list[str]:
        """Compatibility alias for the original opportunistic cleanup API."""
        return self.redact_expired(now)
