"""Latest-only personal-data snapshots for the Companion bridge (#176).

The iOS Companion publishes minimal Apple Reminders and Health
summaries; the server keeps only the *latest* snapshot per paired publisher and
source, then renders it in a widget. No history: exactly one snapshot per
``(publisher_id, source_id)``, replaced on each accepted PUT and deleted on
disable or expiry. The server never connects to iCloud or HealthKit, it only
stores what each phone explicitly publishes, and drops it once expired. An
explicit null deadline retains the latest snapshot until replaced or deleted.
Contract: ``tests/companion/contract/app-v1.openapi.yaml``.

Each stored record wraps the raw snapshot with the two epochs the API needs for
ordering and freshness, so this store never parses ISO timestamps itself::

    { "_format": 2, "publishers": {
        "companion_<digest>": { "name": "Kitchen iPhone", "sources": {
          "reminders": { "snapshot": {...}, "generated_epoch": float,
            "expires_epoch": float | None, "stored_at": float }
        }}
    }}

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

_FORMAT_VERSION = 2
_LEGACY_PUBLISHER_ID = "legacy"
_LEGACY_PUBLISHER_NAME = "Previously synced Companion"


class PersonalDataSnapshotStore:
    """Thread-safe latest snapshot per publisher and source.

    Reads that omit ``publisher_id`` retain the original single-publisher API
    by returning the publication with the newest generated timestamp. Writes
    and deletes require an explicit publisher so no caller can accidentally
    create new data in the migration-only legacy namespace.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"_format": _FORMAT_VERSION, "publishers": {}}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"_format": _FORMAT_VERSION, "publishers": {}}
        if not isinstance(raw, dict):
            return {"_format": _FORMAT_VERSION, "publishers": {}}
        publishers = raw.get("publishers")
        if raw.get("_format") == _FORMAT_VERSION and isinstance(publishers, dict):
            return raw

        # v1 stored ``{source_id: record}``. Keep it readable under an
        # explicit legacy publisher until the first authenticated publisher
        # replaces that source; no personal values are duplicated.
        legacy_sources = {key: value for key, value in raw.items() if isinstance(value, dict)}
        return {
            "_format": _FORMAT_VERSION,
            "publishers": (
                {
                    _LEGACY_PUBLISHER_ID: {
                        "name": _LEGACY_PUBLISHER_NAME,
                        "sources": legacy_sources,
                    }
                }
                if legacy_sources
                else {}
            ),
        }

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    @staticmethod
    def _sources(data: dict[str, Any], publisher_id: str) -> dict[str, Any] | None:
        publishers = data.get("publishers")
        if not isinstance(publishers, dict):
            return None
        publisher = publishers.get(publisher_id)
        if not isinstance(publisher, dict):
            return None
        sources = publisher.get("sources")
        return sources if isinstance(sources, dict) else None

    def get(self, source_id: str, *, publisher_id: str | None = None) -> dict[str, Any] | None:
        self.redact_expired(time.time())
        data = self._load()
        if publisher_id is not None:
            sources = self._sources(data, publisher_id)
            entry = sources.get(source_id) if sources is not None else None
            return entry if isinstance(entry, dict) else None

        # Backward-compatible read for existing widgets: return the newest
        # publisher's record. Multi-publisher-aware widgets use publications().
        newest: dict[str, Any] | None = None
        newest_generated = float("-inf")
        for publication in self.publications(source_id):
            generated = publication.get("generated_epoch")
            generated_value = (
                float(generated) if isinstance(generated, (int, float)) else float("-inf")
            )
            if newest is None or generated_value > newest_generated:
                newest = {
                    key: value
                    for key, value in publication.items()
                    if not key.startswith("publisher_")
                }
                newest_generated = generated_value
        return newest

    def all(self, *, publisher_id: str | None = None) -> dict[str, dict[str, Any]]:
        self.redact_expired(time.time())
        data = self._load()
        if publisher_id is not None:
            sources = self._sources(data, publisher_id) or {}
            return {key: value for key, value in sources.items() if isinstance(value, dict)}

        source_ids: set[str] = set()
        publishers = data.get("publishers")
        if isinstance(publishers, dict):
            for raw_publisher in publishers.values():
                if not isinstance(raw_publisher, dict):
                    continue
                raw_sources = raw_publisher.get("sources")
                if isinstance(raw_sources, dict):
                    source_ids.update(str(key) for key in raw_sources)
        out: dict[str, dict[str, Any]] = {}
        for source_id in source_ids:
            record = self.get(source_id)
            if record is not None:
                out[source_id] = record
        return out

    def publications(self, source_id: str) -> list[dict[str, Any]]:
        """Return every publisher record for one source, with safe metadata."""
        self.redact_expired(time.time())
        data = self._load()
        publishers = data.get("publishers")
        if not isinstance(publishers, dict):
            return []
        out: list[dict[str, Any]] = []
        for publisher_id, raw_publisher in publishers.items():
            if not isinstance(publisher_id, str) or not isinstance(raw_publisher, dict):
                continue
            sources = raw_publisher.get("sources")
            record = sources.get(source_id) if isinstance(sources, dict) else None
            if not isinstance(record, dict):
                continue
            name = raw_publisher.get("name")
            out.append(
                {
                    **record,
                    "publisher_id": publisher_id,
                    "publisher_name": (
                        name.strip() if isinstance(name, str) and name.strip() else publisher_id
                    ),
                }
            )
        return sorted(
            out,
            key=lambda item: (str(item["publisher_name"]).casefold(), str(item["publisher_id"])),
        )

    def put(
        self,
        source_id: str,
        *,
        snapshot: dict[str, Any],
        generated_epoch: float,
        expires_epoch: float | None,
        publisher_id: str,
        publisher_name: str | None = None,
    ) -> None:
        """Replace the latest snapshot for one publisher and source."""
        with self._lock:
            data = self._load()
            publishers = data.get("publishers")
            if not isinstance(publishers, dict):
                publishers = {}
                data["publishers"] = publishers

            if publisher_id != _LEGACY_PUBLISHER_ID:
                legacy_sources = self._sources(data, _LEGACY_PUBLISHER_ID)
                if legacy_sources is not None:
                    legacy_sources.pop(source_id, None)
                    if not legacy_sources:
                        publishers.pop(_LEGACY_PUBLISHER_ID, None)

            publisher = publishers.get(publisher_id)
            if not isinstance(publisher, dict):
                publisher = {"name": publisher_name or publisher_id, "sources": {}}
                publishers[publisher_id] = publisher
            if isinstance(publisher_name, str) and publisher_name.strip():
                publisher["name"] = publisher_name.strip()
            sources = publisher.get("sources")
            if not isinstance(sources, dict):
                sources = {}
                publisher["sources"] = sources
            sources[source_id] = {
                "snapshot": snapshot,
                "generated_epoch": generated_epoch,
                "expires_epoch": expires_epoch,
                "stored_at": time.time(),
            }
            self._save(data)

    def delete(self, source_id: str, *, publisher_id: str) -> bool:
        """Remove one publisher's source. Returns whether one existed."""
        with self._lock:
            data = self._load()
            sources = self._sources(data, publisher_id)
            if sources is None or source_id not in sources:
                return False
            del sources[source_id]
            publishers = data.get("publishers")
            if not sources and isinstance(publishers, dict):
                publishers.pop(publisher_id, None)
            self._save(data)
            return True

    def redact_expired(self, now: float) -> list[str]:
        """Remove raw values from snapshots past ``expires_epoch``.

        Non-sensitive timing metadata remains as a tombstone so callers can
        report ``expired`` without retaining Reminder or Health values.
        Returns the source ids redacted during this call.
        """
        with self._lock:
            data = self._load()
            redacted: list[str] = []
            publishers = data.get("publishers")
            if isinstance(publishers, dict):
                for raw_publisher in publishers.values():
                    if not isinstance(raw_publisher, dict):
                        continue
                    sources = raw_publisher.get("sources")
                    if not isinstance(sources, dict):
                        continue
                    for source_id, entry in sources.items():
                        exp = entry.get("expires_epoch") if isinstance(entry, dict) else None
                        if (
                            isinstance(entry, dict)
                            and isinstance(exp, (int, float))
                            and now >= exp
                            and "snapshot" in entry
                        ):
                            del entry["snapshot"]
                            entry["expired"] = True
                            redacted.append(str(source_id))
            if redacted:
                self._save(data)
            return redacted

    def purge_expired(self, now: float) -> list[str]:
        """Compatibility alias for the original opportunistic cleanup API."""
        return self.redact_expired(now)
