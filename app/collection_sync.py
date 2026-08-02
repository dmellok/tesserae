"""Device-facing frame-cache collection sync: manifest building + versioning.

Generalises the deck cache (:mod:`app.deck_sync`) into the producer-neutral
frame-cache contract in ``docs/dev/frame-cache.md``. The first producer is the
offline photo album (#177): a Picture Gallery folder rendered to a device's
panel, one frame per image, cached on the device's SD card and played back
locally. The shared cache owns digests, the capacity budget, and versioning;
the producer owns the opaque ``producer`` block (here the album's playback
settings). The REST endpoints live in :mod:`app.rest_api`.

Version semantics mirror the deck version: the digest of the manifest content
(frame membership, order, digests, ttls, and the producer block), excluding the
volatile ``version`` / ``cursor`` / ``next_cursor`` fields, so ANY change to what
the device should cache bumps it. Per-device, because frame digests are
per-device renders.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from app.state.album_model import Album

logger = logging.getLogger(__name__)

# Photos never expire (a rendered image is static); ttl_s 0 means "no expiry".
_ALBUM_TTL_S = 0
# Firmware's default hard cap on cached frames for slice 1, used when the device
# advertised frame_cache without a max_frames (older firmware). The contract
# pins 32 for the first slice.
DEFAULT_MAX_FRAMES = 32


class _AlbumRenderSource(Protocol):
    """The slice of PushManager the manifest builder needs."""

    def album_render_for(self, device_id: str, frame_id: str) -> dict[str, Any] | None: ...

    def warm_album_frame(
        self, frame_id: str, device_id: str, image_bytes: bytes, *, fit: str
    ) -> bool: ...


def advertised_frame_cache(payload: bytes | str | dict[str, Any]) -> dict[str, int] | None:
    """The frame-cache capability a device advertises in its register /
    heartbeat body (``{"frame_cache": {"schema": 1, "capacity_bytes": N,
    "max_frames": M}}``), validated, or None.

    Like ``deck_cache`` this is CURRENT-STATE, never carried forward: the
    firmware advertises it only while storage is present and usable, and the
    server must stop offering collection syncs the moment it disappears from a
    heartbeat. ``max_frames`` is optional (older firmware); callers fall back to
    :data:`DEFAULT_MAX_FRAMES`."""
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    cap = body.get("frame_cache")
    if not isinstance(cap, dict):
        return None
    schema = cap.get("schema")
    capacity = cap.get("capacity_bytes")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        return None
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
        return None
    out = {"schema": schema, "capacity_bytes": capacity}
    max_frames = cap.get("max_frames")
    if isinstance(max_frames, int) and not isinstance(max_frames, bool) and max_frames >= 1:
        out["max_frames"] = max_frames
    return out


def bound_album_for(store: Any, device_id: str) -> Album | None:
    """The enabled album bound to a device (first when several), or None.
    Slice 1 is one active producer per device."""
    albums = store.for_device(device_id)
    return albums[0] if albums else None


def frame_id_for(filename: str) -> str:
    """Stable producer id for an album frame. Derived from the filename so it
    survives a re-render (the digest changes, this does not) and is stable
    across order edits."""
    return "photo:" + hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8]


def ordered_frames(album: Album, folder_files: list[str]) -> list[tuple[str, str]]:
    """The album's frames as ``(frame_id, filename)`` in playback order.

    ``album.order`` leads (filtered to files that still exist); any folder file
    the order doesn't mention is appended in the folder's natural sort, so a
    newly uploaded image still appears without a re-author. ``position`` is the
    index in this list."""
    present = set(folder_files)
    ordered: list[str] = [f for f in album.order if f in present]
    seen = set(ordered)
    ordered.extend(f for f in folder_files if f not in seen)
    return [(frame_id_for(f), f) for f in ordered]


def _artifact_size(renders_dir: Path, filename: str) -> int:
    try:
        return (renders_dir / filename).stat().st_size
    except OSError:
        return 0


def version_digest(manifest: dict[str, Any]) -> str:
    """Digest of the manifest content, excluding the volatile paging fields.
    Same truncation convention as frame digests (sha256[:16])."""
    volatile = {"version", "cursor", "next_cursor"}
    body = {k: v for k, v in manifest.items() if k not in volatile}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _producer(album: Album) -> dict[str, Any]:
    return {
        "album": {
            "playback": {
                "mode": album.playback.mode,
                "interval_s": album.playback.interval_s,
                "repeat": album.playback.repeat,
            }
        }
    }


def build_manifest(
    album: Album,
    device_id: str,
    *,
    push_mgr: _AlbumRenderSource,
    renders_dir: Path,
    frames: list[tuple[str, str]],
    image_loader: Callable[[str], bytes | None],
    device_id_for_url: str,
    warm_missing: bool,
    capacity_bytes: int | None = None,
    max_frames: int | None = None,
) -> dict[str, Any]:
    """The collection manifest for one device, per ``docs/dev/frame-cache.md``.

    ``frames`` is the ordered ``(frame_id, filename)`` list from
    :func:`ordered_frames`; ``image_loader`` reads an image's bytes by filename.
    With ``warm_missing`` (the manifest endpoint), frames without a warmed render
    are rendered now so the manifest ships complete; without it (the ``/status``
    version check) it reflects only what's warmed, which is cheap and converges.

    Slice 1 emits a single page: every frame is listed, ``next_cursor`` is null.
    ``cache`` is true for the lowest-``position`` frames that fit BOTH the frame
    count cap (``max_frames``) and the byte budget (``capacity_bytes``); the rest
    are ``cache: false`` and fetched on demand."""
    cap_count = max_frames if (max_frames and max_frames > 0) else DEFAULT_MAX_FRAMES
    budget = capacity_bytes if (capacity_bytes and capacity_bytes > 0) else None

    frame_views: list[dict[str, Any]] = []
    spent = 0
    kept = 0
    for position, (frame_id, filename) in enumerate(frames):
        info = push_mgr.album_render_for(device_id, frame_id)
        if info is None and warm_missing:
            image_bytes = image_loader(filename)
            if image_bytes is not None and push_mgr.warm_album_frame(
                frame_id, device_id, image_bytes, fit=album.fit
            ):
                info = push_mgr.album_render_for(device_id, frame_id)
            else:
                logger.warning(
                    "collection manifest: warm failed album=%s frame=%s device=%s",
                    album.id,
                    filename,
                    device_id,
                )
        digest = str(info.get("digest") or "") if info else ""
        artifact = str(info.get("filename") or "") if info else ""
        size = _artifact_size(renders_dir, artifact) if artifact else 0

        # Budget by position (lowest-position frames cache first).
        cache = kept < cap_count and (budget is None or spent + size <= budget)
        if cache:
            kept += 1
            spent += size

        frame_views.append(
            {
                "frame_id": frame_id,
                "position": position,
                "digest": digest,
                "bytes": size,
                "ttl_s": _ALBUM_TTL_S,
                "cache": cache,
                "url": f"/api/v1/device/{device_id_for_url}/collection/frame/{digest}",
            }
        )

    dropped = [f["frame_id"] for f in frame_views if not f["cache"]]
    if dropped:
        logger.info(
            "collection manifest: %d frame(s) exceed device caps (max_frames=%s "
            "capacity=%s) for %s; marked cache=false",
            len(dropped),
            cap_count,
            capacity_bytes,
            device_id,
        )

    manifest: dict[str, Any] = {
        "schema": 1,
        "collection_id": f"album:{album.id}",
        "kind": "album",
        "total_frames": len(frame_views),
        "cursor": None,
        "next_cursor": None,
        "frames": frame_views,
        "producer": _producer(album),
    }
    manifest["version"] = version_digest(manifest)
    return manifest


def current_version(
    album: Album,
    device_id: str,
    *,
    push_mgr: _AlbumRenderSource,
    renders_dir: Path,
    frames: list[tuple[str, str]],
    device_id_for_url: str,
) -> str:
    """The collection version for a device right now, without warming anything.
    Cheap enough for every ``/status`` response."""
    return str(
        build_manifest(
            album,
            device_id,
            push_mgr=push_mgr,
            renders_dir=renders_dir,
            frames=frames,
            image_loader=lambda _f: None,
            device_id_for_url=device_id_for_url,
            warm_missing=False,
        )["version"]
    )


def frame_entry_by_digest(
    device_id: str,
    digest: str,
    *,
    push_mgr: _AlbumRenderSource,
    frames: list[tuple[str, str]],
) -> dict[str, Any] | None:
    """The warmed render info whose digest matches, scanning the bound album's
    frames for this device. None when no warmed frame carries the digest (stale
    manifest on the client: it should re-sync)."""
    if not digest:
        return None
    for frame_id, _filename in frames:
        info = push_mgr.album_render_for(device_id, frame_id)
        if info is not None and str(info.get("digest") or "") == digest:
            return info
    return None
