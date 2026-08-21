"""File-backed per-device collection resync tokens.

A device re-syncs its frame cache when the collection ``version`` it holds
differs from the one the server reports. That version is a digest of the
manifest *content* (see ``app.collection_sync.version_digest``), which is
what makes it truthful: it changes exactly when the frames, their order, or
the playback settings change, and not otherwise.

The cost of that design is that there is no way to say "sync it again" when
the two sides disagree for a reason the content can't express: the device
dropped frames it can no longer address, a card was swapped, a sync was
interrupted and never resumed (#247). Before this store the only lever was to
change the album, which is a real edit made for a fake reason.

A resync token is that lever. It is an opaque string mixed into the version
*after* the content digest, so the manifest a device receives stays
byte-identical and only the version it compares against moves. Bumping it
makes the next check-in look exactly like a genuine content change, which is
the one code path firmware already handles.

One JSON file mapping ``device_id -> {token, album_id, updated_at}``. The
record is per device, not per album: a token bumped for one album is
meaningless once a different album is bound, because the content digest
underneath it has changed anyway.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class CollectionResyncStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save_raw(self, raw: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, device_id: str) -> dict[str, Any] | None:
        """The device's current ``{token, album_id, updated_at}`` or None."""
        return self._load_raw().get(device_id)

    def token(self, device_id: str) -> str | None:
        """The token to mix into this device's collection version, or None
        when no resync has been asked for. Must be read on BOTH the ``/status``
        version check and the manifest build, or the two disagree forever."""
        rec = self.get(device_id)
        if rec is None:
            return None
        token = rec.get("token")
        return token if isinstance(token, str) and token else None

    def bump(self, device_id: str, *, album_id: str) -> str:
        """Mint a fresh token, so this device's next version differs from the
        one it holds. Includes a random suffix as well as the clock: two
        resyncs within the same second still have to produce different
        versions, or the second one silently does nothing."""
        token = f"{int(time.time())}.{uuid.uuid4().hex[:8]}"
        with self._lock:
            raw = self._load_raw()
            raw[device_id] = {
                "token": token,
                "album_id": album_id,
                "updated_at": time.time(),
            }
            self._save_raw(raw)
        return token

    def clear(self, device_id: str) -> bool:
        """Drop the device's token. The version falls back to the plain content
        digest, which is itself a change, so this also forces one resync."""
        with self._lock:
            raw = self._load_raw()
            if device_id not in raw:
                return False
            del raw[device_id]
            self._save_raw(raw)
            return True
