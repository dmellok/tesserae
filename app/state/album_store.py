"""File-backed album store. Same atomic-rename pattern as DeckStore.

One active producer per display. A display plays back a single cached
collection, so two enabled albums naming the same display is not a
configuration with a winner, it's an ambiguity: whichever the store
happened to return first became the one that synced, and the other
silently did nothing. The store now refuses to write that state unless
the caller explicitly asks to take the display over, which gives callers
a real conflict to report rather than a coin flip to explain
(discussion #230).

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.state.album_model import Album


class AlbumConflict(RuntimeError):
    """A write would take a display away from another enabled album.

    ``claims`` maps each contested ``device_id`` to the id of the album
    already playing there, so the caller can name it rather than saying
    "something else is using this"."""

    def __init__(self, claims: dict[str, str]) -> None:
        self.claims = dict(claims)
        listed = ", ".join(f"{did} (album {aid})" for did, aid in sorted(claims.items()))
        super().__init__(f"already playing a collection: {listed}")


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

    @staticmethod
    def _parse(raw: list[dict[str, Any]]) -> list[Album]:
        """Records that still validate, skipping any that don't. Unlocked so
        the write path can read and write under one acquisition."""
        out: list[Album] = []
        for d in raw:
            try:
                out.append(Album.model_validate(d))
            except Exception:
                continue
        return out

    def all(self) -> list[Album]:
        with self._lock:
            return self._parse(self._load_raw())

    def get(self, album_id: str) -> Album | None:
        for album in self.all():
            if album.id == album_id:
                return album
        return None

    @staticmethod
    def _claims_against(albums: list[Album], album: Album) -> dict[str, str]:
        """Displays ``album`` wants that another enabled album already has.

        A disabled album claims nothing, and an album never conflicts with
        itself, so re-saving one to change its interval or membership is
        always allowed."""
        if not album.enabled:
            return {}
        wanted = set(album.device_ids)
        claims: dict[str, str] = {}
        for other in albums:
            if other.id == album.id or not other.enabled:
                continue
            for device_id in other.device_ids:
                if device_id in wanted and device_id not in claims:
                    claims[device_id] = other.id
        return claims

    def conflicts_for(self, album: Album) -> dict[str, str]:
        """``{device_id: album_id}`` this album would take over. Empty means
        :meth:`upsert` will accept it without ``replace``."""
        return self._claims_against(self.all(), album)

    def upsert(self, album: Album, *, replace: bool = False) -> dict[str, str]:
        """Write an album, returning the ``{device_id: album_id}`` it took over
        (empty when nothing was contested).

        Raises :class:`AlbumConflict` when a display is already claimed by
        another enabled album and ``replace`` is not set. Taking a display off
        whatever it is currently playing is a decision, not a side effect of
        saving a form, so the caller has to ask for it."""
        with self._lock:
            raw = self._load_raw()
            albums = self._parse(raw)
            claims = self._claims_against(albums, album)
            if claims and not replace:
                raise AlbumConflict(claims)
            if claims:
                # Unbind the contested displays from their previous albums.
                # Those albums survive with their remaining targets; an album
                # left with none is defined but not playing anywhere, which is
                # the same state a fresh one starts in.
                displaced = set(claims.values())
                for i, existing in enumerate(raw):
                    if existing.get("id") not in displaced:
                        continue
                    bound = existing.get("device_ids")
                    if isinstance(bound, list):
                        existing["device_ids"] = [d for d in bound if d not in album.device_ids]
                        raw[i] = existing
            record = album.model_dump(mode="json", exclude_none=True)
            for i, existing in enumerate(raw):
                if existing.get("id") == album.id:
                    raw[i] = record
                    break
            else:
                raw.append(record)
            self._save_raw(raw)
        return claims

    def delete(self, album_id: str) -> bool:
        with self._lock:
            raw = self._load_raw()
            kept = [d for d in raw if d.get("id") != album_id]
            if len(kept) == len(raw):
                return False
            self._save_raw(kept)
            return True

    def for_device(self, device_id: str) -> list[Album]:
        """Enabled albums bound to a device, in a stable order.

        :meth:`upsert` refuses to create more than one, so this normally has
        at most a single entry. Sorted by id anyway, because a file written by
        an older build (or edited by hand) can still hold two, and a
        collection that changes on every restart depending on dict order is
        harder to diagnose than one that is consistently wrong."""
        return sorted(
            (a for a in self.all() if a.enabled and device_id in a.device_ids),
            key=lambda a: a.id,
        )
