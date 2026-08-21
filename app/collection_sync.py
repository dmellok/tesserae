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
# Frame entries per manifest page. A manifest response must stay parseable by
# constrained firmware: the ESP32 REST receive ceiling is 32 KiB per response
# body and one frame entry is ~220 bytes of JSON, so 64 entries (~15 KiB plus
# envelope) leaves comfortable headroom. Deliberately above DEFAULT_MAX_FRAMES
# so every ``cache: true`` frame lands in page one and slice-1 firmware that
# reads a single page still syncs its whole cacheable set; firmware advertising
# max_frames > 64 must walk ``next_cursor``.
PAGE_MAX_FRAMES = 64


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


_REPORT_STATES = ("playing", "paused", "syncing", "error")


def reported_collection(payload: bytes | str | dict[str, Any]) -> dict[str, Any] | None:
    """The device's playback/sync report from a /status REQUEST body's
    ``collection`` block (``docs/dev/frame-cache.md`` §Reporting): ``{id,
    version, cached, total, state}``, validated, or None.

    Distinct from the ``collection`` envelope the server puts in the /status
    RESPONSE (what should be active): this is what the device observed. Once
    playback is local the server is not the authority on the current frame, so
    the report is an observation with a timestamp, never a live "current
    screen" claim."""
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    report = body.get("collection")
    if not isinstance(report, dict):
        return None
    coll_id = report.get("id")
    state = report.get("state")
    if not isinstance(coll_id, str) or not coll_id.strip():
        return None
    if state not in _REPORT_STATES:
        return None
    out: dict[str, Any] = {"id": coll_id.strip(), "state": state}
    version = report.get("version")
    if isinstance(version, str) and version.strip():
        out["version"] = version.strip()
    for key in ("cached", "total"):
        value = report.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = value
    return out


def report_changed(prev: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    """Whether a report is a meaningful transition worth an event-log row:
    the collection, its version, or the playback state changed. ``cached``
    counts churn on every beat of a sync, so they don't qualify."""
    if prev is None:
        return True
    keys = ("id", "state", "version")
    return any(prev.get(k) != new.get(k) for k in keys)


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

    The result lists EVERY frame (the endpoint slices it into bounded pages via
    :func:`paged_manifest`; ``version`` must cover the whole collection, so it
    is computed here, before any slicing). ``cache`` is true for the
    lowest-``position`` frames that fit BOTH the frame count cap
    (``max_frames``) and the byte budget (``capacity_bytes``); the rest are
    ``cache: false`` and fetched on demand."""
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
        #
        # A frame with no digest is not cacheable however well it fits
        # (#247). Frames are fetched by digest, so telling a device to
        # cache one it cannot address means it caches nothing and reports
        # "0 of N"; and because an unwarmed frame also measures 0 bytes,
        # it always fit the budget and was always offered.
        cache = bool(digest) and kept < cap_count and (budget is None or spent + size <= budget)
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


def page_cursor(version: str, offset: int) -> str:
    """The opaque continuation token for the page starting at ``offset``.
    Bound to ``version`` so a mid-walk collection change invalidates the walk
    (contract: a stale cursor restarts at a fresh first page)."""
    return f"{version}.{offset}"


def _cursor_offset(cursor: str | None, version: str) -> tuple[int, str | None]:
    """Resolve a requested cursor against the current version, as
    ``(offset, echoed_cursor)``. A missing, malformed, or stale (other-version)
    cursor starts a fresh walk: offset 0, ``cursor: null`` in the response."""
    if not isinstance(cursor, str) or not cursor:
        return 0, None
    prefix, sep, raw_offset = cursor.rpartition(".")
    if not sep or prefix != version:
        return 0, None
    try:
        offset = int(raw_offset)
    except ValueError:
        return 0, None
    return (offset, cursor) if offset >= 0 else (0, None)


def paged_manifest(
    manifest: dict[str, Any], cursor: str | None, *, page_size: int | None = None
) -> dict[str, Any]:
    """One bounded page of a full manifest from :func:`build_manifest`.

    ``version`` stays the full-collection digest (computed before slicing and
    excluding the volatile paging fields), so paging never reads as a content
    change. ``total_frames`` stays the full count. Bounding the page keeps the
    response inside constrained firmware receive buffers (32 KiB on the ESP32
    REST client), which a large single-page manifest would overflow before the
    parser ever saw the cacheable frames."""
    if page_size is None:
        page_size = PAGE_MAX_FRAMES
    frames = manifest["frames"]
    version = str(manifest["version"])
    offset, echoed = _cursor_offset(cursor, version)
    page = frames[offset : offset + page_size]
    end = offset + len(page)
    return {
        **manifest,
        "cursor": echoed,
        "next_cursor": page_cursor(version, end) if end < len(frames) else None,
        "frames": page,
    }


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
