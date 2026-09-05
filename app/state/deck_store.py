"""File-backed deck store. Same atomic-rename pattern as RotationStore.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.state.deck_model import Deck

logger = logging.getLogger(__name__)


class DeckStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        # Change listeners, called (outside the lock) after every upsert /
        # delete so surfaces that mirror the deck list (HA discovery's
        # per-lineup entities) can republish without polling.
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback()
            except Exception:
                logger.exception("deck store listener failed")

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

    def all(self) -> list[Deck]:
        with self._lock:
            out: list[Deck] = []
            for d in self._load_raw():
                try:
                    out.append(Deck.model_validate(d))
                except Exception:
                    continue
            return out

    def get(self, deck_id: str) -> Deck | None:
        for deck in self.all():
            if deck.id == deck_id:
                return deck
        return None

    def upsert(self, deck: Deck) -> None:
        with self._lock:
            raw = self._load_raw()
            record = deck.model_dump(mode="json", exclude_none=True)
            for i, existing in enumerate(raw):
                if existing.get("id") == deck.id:
                    raw[i] = record
                    break
            else:
                raw.append(record)
            self._save_raw(raw)
        self._notify()

    def delete(self, deck_id: str) -> bool:
        with self._lock:
            raw = self._load_raw()
            kept = [d for d in raw if d.get("id") != deck_id]
            if len(kept) == len(raw):
                return False
            self._save_raw(kept)
        self._notify()
        return True

    def for_device(self, device_id: str) -> list[Deck]:
        """Enabled decks bound to a device. Used by the navigation + refresh
        paths to find which deck (if any) a device is driving. All shapes
        qualify (#167 decommission): a bound pure timer deck syncing its
        frames to SD is legitimate, and link-less decks are inert on the
        button-link path anyway."""
        return [d for d in self.all() if d.enabled and device_id in d.device_ids]
