"""File-backed room store. Same atomic-rename pattern as RotationStore.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.state.room_model import Room


class RoomStore:
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

    def all(self) -> list[Room]:
        with self._lock:
            out: list[Room] = []
            for d in self._load_raw():
                try:
                    out.append(Room(**d))
                except Exception:
                    # A malformed row must not take the whole page down;
                    # the rest of the rooms still render.
                    continue
            return out

    def get(self, room_id: str) -> Room | None:
        for room in self.all():
            if room.id == room_id:
                return room
        return None

    def upsert(self, room: Room) -> Room:
        with self._lock:
            raw = self._load_raw()
            replaced = False
            for i, d in enumerate(raw):
                if d.get("id") == room.id:
                    raw[i] = room.to_json()
                    replaced = True
                    break
            if not replaced:
                raw.append(room.to_json())
            self._save_raw(raw)
        return room

    def delete(self, room_id: str) -> bool:
        with self._lock:
            raw = self._load_raw()
            kept = [d for d in raw if d.get("id") != room_id]
            if len(kept) == len(raw):
                return False
            self._save_raw(kept)
            return True

    def devices_for(self, room_id: str) -> list[str]:
        room = self.get(room_id)
        return list(room.device_ids) if room is not None else []
