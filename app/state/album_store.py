"""File-backed album store. Same atomic-rename pattern as DeckStore.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.state.album_model import Album


class AlbumStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict)]

    def _save_raw(self, raw: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._path)

    def all(self) -> list[Album]:
        with self._lock:
            out: list[Album] = []
            for d in self._load_raw():
                try:
                    out.append(Album.model_validate(d))
                except Exception:
                    continue
            return out

    def get(self, album_id: str) -> Album | None:
        for album in self.all():
            if album.id == album_id:
                return album
        return None

    def upsert(self, album: Album) -> None:
        with self._lock:
            raw = self._load_raw()
            record = album.model_dump(mode="json", exclude_none=True)
            for i, existing in enumerate(raw):
                if existing.get("id") == album.id:
                    raw[i] = record
                    break
            else:
                raw.append(record)
            self._save_raw(raw)

    def delete(self, album_id: str) -> bool:
        with self._lock:
            raw = self._load_raw()
            kept = [d for d in raw if d.get("id") != album_id]
            if len(kept) == len(raw):
                return False
            self._save_raw(kept)
            return True

    def for_device(self, device_id: str) -> list[Album]:
        """Enabled albums bound to a device. Slice 1 is one active producer per
        device, so the manifest / status paths take the first."""
        return [a for a in self.all() if a.enabled and device_id in a.device_ids]
