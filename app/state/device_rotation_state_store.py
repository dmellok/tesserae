"""File-backed store for per-device rotation state.

Same atomic-rename + threading pattern as ``RotationStore``: one JSON
file mapping device_id -> serialised ``DeviceRotationState``. Read on
every button-driven wake to compute the resolved step, written when a
button press updates the manual position.

Devices that have never had a button press don't get a record; the
absence is meaningful (the ``/frame`` handler falls back to the
time-based rotation step directly).

mypy --strict applies via re-export through ``app.state``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.state.device_rotation_state_model import DeviceRotationState


class DeviceRotationStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save_raw(self, raw: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2, default=str, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, device_id: str) -> DeviceRotationState | None:
        with self._lock:
            raw = self._load_raw().get(device_id)
        if raw is None:
            return None
        try:
            return DeviceRotationState.model_validate(raw)
        except Exception:
            return None

    def upsert(self, state: DeviceRotationState) -> None:
        with self._lock:
            raw = self._load_raw()
            raw[state.device_id] = state.model_dump(mode="json", exclude_none=True)
            self._save_raw(raw)

    def delete(self, device_id: str) -> bool:
        """Remove a device's manual state so time-based rotation resumes
        immediately. Returns True if a record existed."""
        with self._lock:
            raw = self._load_raw()
            if device_id not in raw:
                return False
            del raw[device_id]
            self._save_raw(raw)
            return True

    def all(self) -> dict[str, DeviceRotationState]:
        with self._lock:
            raw = self._load_raw()
        out: dict[str, DeviceRotationState] = {}
        for device_id, record in raw.items():
            try:
                out[device_id] = DeviceRotationState.model_validate(record)
            except Exception:
                continue
        return out
