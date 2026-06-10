"""File-backed rotation store. Same atomic-rename pattern as ScheduleStore.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.state.rotation_model import Rotation


class RotationStore:
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

    def all(self) -> list[Rotation]:
        with self._lock:
            out: list[Rotation] = []
            for d in self._load_raw():
                try:
                    out.append(Rotation.model_validate(d))
                except Exception:
                    continue
            return out

    def get(self, rotation_id: str) -> Rotation | None:
        for r in self.all():
            if r.id == rotation_id:
                return r
        return None

    def upsert(self, rotation: Rotation) -> None:
        with self._lock:
            raw = self._load_raw()
            record = rotation.model_dump(mode="json", exclude_none=True)
            for i, existing in enumerate(raw):
                if existing.get("id") == rotation.id:
                    raw[i] = record
                    break
            else:
                raw.append(record)
            self._save_raw(raw)

    def delete(self, rotation_id: str) -> bool:
        with self._lock:
            raw = self._load_raw()
            kept = [d for d in raw if d.get("id") != rotation_id]
            if len(kept) == len(raw):
                return False
            self._save_raw(kept)
            return True
