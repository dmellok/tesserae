"""Latest-only personal-data snapshots for the Companion bridge (#176).

The iOS Companion publishes a minimal, expiring snapshot of one Apple-Reminders
list; the server keeps only the *latest* snapshot per source and renders it in a
widget. No history: exactly one snapshot per ``source_id``, replaced on each
accepted PUT and deleted on disable or expiry. The server never connects to
iCloud, it only stores what the phone explicitly publishes, and drops it once
expired. Contract: ``tests/companion/contract/app-v1.openapi.yaml`` (Companion
0.7.0).

Each stored record wraps the raw snapshot with the two epochs the API needs for
ordering and freshness, so this store never parses ISO timestamps itself::

    { "snapshot": {...}, "generated_epoch": float, "expires_epoch": float,
      "stored_at": float }

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
        entry = self._load().get(source_id)
        return entry if isinstance(entry, dict) else None

    def all(self) -> dict[str, dict[str, Any]]:
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

    def purge_expired(self, now: float) -> list[str]:
        """Delete snapshots whose ``expires_epoch`` has passed. Returns the
        removed source ids. Called opportunistically so expired personal data
        does not linger on disk past its deadline."""
        with self._lock:
            data = self._load()
            removed: list[str] = []
            for source_id in list(data):
                entry = data.get(source_id)
                exp = entry.get("expires_epoch") if isinstance(entry, dict) else None
                if isinstance(exp, (int, float)) and now >= exp:
                    del data[source_id]
                    removed.append(source_id)
            if removed:
                self._save(data)
            return removed
