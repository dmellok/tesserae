"""File-backed per-device deck navigation state.

Tracks which deck a device is currently navigating and which page it's on,
so a button press / touch resolves the next page from the current one via the
deck graph. Same atomic-rename + threading pattern as the rotation stores; one
JSON file mapping ``device_id -> {deck_id, page_id, updated_at}``.

The absence of a record means the device hasn't entered a deck yet; the caller
starts it at the deck's entry page.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


class DeckNavStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._device_locks: dict[str, threading.RLock] = {}
        self._device_locks_guard = threading.Lock()

    def device_lock(self, device_id: str) -> threading.RLock:
        """The lock that serialises one device's navigation: read its
        position, deliver the next page, record the new position. Every
        path that moves a panel within a lineup (button presses, Home
        Assistant's Next / Previous, the scheduler's lineup advances and
        home returns) holds it across all three, so two of them can't
        interleave into "the glass shows one page, the record names
        another". Re-entrant, so a path that takes it can call another."""
        with self._device_locks_guard:
            lock = self._device_locks.get(device_id)
            if lock is None:
                lock = self._device_locks[device_id] = threading.RLock()
            return lock

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
        tmp.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, device_id: str) -> dict[str, Any] | None:
        """The device's current ``{deck_id, page_id, updated_at}`` or None."""
        return self._load_raw().get(device_id)

    def current_page(self, device_id: str, deck_id: str) -> str | None:
        """The page the device is on *within ``deck_id``*, or None if it has no
        record for this deck (caller starts it at the deck's entry page). Guards
        against a stale record from a different deck the device left."""
        rec = self.get(device_id)
        if rec is None or rec.get("deck_id") != deck_id:
            return None
        page = rec.get("page_id")
        return page if isinstance(page, str) else None

    def set(self, device_id: str, deck_id: str, page_id: str) -> None:
        with self._lock:
            raw = self._load_raw()
            raw[device_id] = {
                "deck_id": deck_id,
                "page_id": page_id,
                "updated_at": time.time(),
            }
            self._save_raw(raw)

    def clear(self, device_id: str) -> bool:
        with self._lock:
            raw = self._load_raw()
            if device_id not in raw:
                return False
            del raw[device_id]
            self._save_raw(raw)
            return True


@contextlib.contextmanager
def navigation_locked(
    nav: Any, device_ids: Iterable[str], *, timeout: float | None = None
) -> Iterator[bool]:
    """Hold :meth:`DeckNavStore.device_lock` for every device in
    ``device_ids`` (sorted, so two callers locking overlapping sets can't
    deadlock) for the body. Yields True once all are held; with a
    ``timeout``, yields False (holding none) when one isn't free in time.
    A nav store without per-device locks (a test fake) yields True and
    locks nothing."""
    factory = getattr(nav, "device_lock", None) if nav is not None else None
    if not callable(factory):
        yield True
        return
    held: list[Any] = []
    try:
        for device_id in sorted(set(device_ids)):
            lock = factory(device_id)
            acquired = lock.acquire(timeout=timeout) if timeout is not None else lock.acquire()
            if not acquired:
                break
            held.append(lock)
        else:
            yield True
            return
        yield False
    finally:
        for lock in reversed(held):
            lock.release()
