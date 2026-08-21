"""Companion Offline Album adapter over the existing Album producer.

The phone authors an Offline Album from a Gallery folder (#230): bind a
folder to a storage-capable display, pre-render its frames, and let the
firmware play the cached collection locally with the radio off. This
module is the translation layer for that, the same way
``companion_gallery`` is for photos.

Nothing underneath is new. :class:`app.state.album_store.AlbumStore`
already enforces one active producer per display and raises with the
contested claims; :func:`app.collection_sync.build_manifest` already
computes a per-device cache plan; :func:`app.device_capability.
capability_support` already answers whether a display can play one at
all. The Web form in the Picture Gallery plugin drives the same three,
and the two surfaces are not allowed to disagree.

Three rules shape what's here.

**Identity is opaque, and the stored id does not move.** The contract
wants an album id that is not a folder name, but the frame-cache
``collection_id`` is ``album:<stored id>`` and every firmware report is
keyed on it. So the opaque id is minted over the stored one for the wire
only. Changing what is stored would orphan the observations this surface
exists to report.

**Desired and observed stay apart.** What the operator asked for comes
from the album store. What the display last said comes from its
heartbeat, is carried only while it describes *this* album, and is never
presented as a live current photo: once playback is local the server
does not know which frame is on the panel.

**A projection says only what it can compute.** A warmed album has real
artifact bytes on disk and its plan is exact. A cold one does not, so the
frame-count cap is the only limit that actually applied, and the storage
projection is omitted rather than estimated from source-photo bytes. The
contract defines an absent projection as unknown; it does not define zero
as unknown.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import current_app

from app import collection_sync, companion_gallery
from app.device_capability import FRAME_CACHE, capability_support
from app.state.album_model import Album

logger = logging.getLogger(__name__)

_ALBUM_PREFIX = "alb_"

# Playback states firmware may report. Anything else is a report we don't
# understand, and an observation we can't type is better dropped than
# passed through into a response the client validates strictly.
_OBSERVED_STATES = frozenset({"syncing", "playing", "paused", "error"})


# -- config accessors ----------------------------------------------------


def _store() -> Any:
    return current_app.config.get("ALBUM_STORE")


def _registry() -> Any:
    return current_app.config.get("DEVICE_REGISTRY")


def _status() -> dict[str, Any]:
    cache = current_app.config.get("DEVICE_STATUS")
    return cache if isinstance(cache, dict) else {}


def _push() -> Any:
    return current_app.config.get("PUSH_MANAGER")


def _renders_dir() -> Any:
    return current_app.config.get("RENDERS_DIR")


def _resync_token(device_id: str) -> str | None:
    """The device's pending resync token, folded into the version the same way
    the device-facing endpoints fold it. Omitting it here would leave the app
    comparing a token-free version against a device that holds a token-bearing
    one, i.e. permanent phantom drift after any resync."""
    store = current_app.config.get("COLLECTION_RESYNC_STORE")
    if store is None:
        return None
    try:
        token = store.token(device_id)
    except Exception:
        return None
    return token if isinstance(token, str) and token else None


def albums_available() -> bool:
    """Whether the Offline Album surface can be advertised at all.

    Both halves have to be there: the Gallery plugin owns the source
    photos, and the album store owns the producer. Advertising the
    capability without either would leave the client offering an
    authoring flow whose every call 404s."""
    return companion_gallery.gallery_available() and _store() is not None


# -- identity ------------------------------------------------------------


def album_id(stored_id: str) -> str:
    """The opaque wire id for a stored album."""
    return companion_gallery.encode_opaque(_ALBUM_PREFIX, stored_id)


def stored_id(folder: str) -> str:
    """The album id the store keys this folder's album under.

    One album per folder, and the store has always keyed it by the folder
    name. That stays true: the frame-cache ``collection_id`` is
    ``album:<this>``, and every report a display has ever sent is keyed on
    it, so minting a different stored id here would silently orphan them.
    The opaque id in :func:`album_id` is the wire form of this."""
    return folder


def stored_album(folder: str) -> Album | None:
    """This folder's saved album, or None."""
    store = _store()
    album = store.get(stored_id(folder)) if store is not None else None
    return album if isinstance(album, Album) else None


def album_etag(album: Album) -> str:
    """Version tag for one album, for optimistic concurrency.

    Hashes the stored record, so an edit from any surface changes it: the
    Web form and MCP both write the same albums, and a phone holding a
    stale validator should be refused rather than silently win."""
    payload = json.dumps(album.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class OrderRejected(ValueError):
    """An order entry named an image from a different folder.

    Not the same as an image that has since been deleted, which is
    dropped: this is a client sending an id it was never handed for this
    folder, and silently ignoring it would save an order the operator
    didn't ask for."""


def order_to_filenames(folder: str, order: list[str]) -> list[str]:
    """Opaque image ids to the filenames the album model stores.

    Ids for images deleted since the client prepared the request are
    dropped, which is why a response order can differ from the submitted
    one. Ids belonging to another folder raise."""
    gallery = companion_gallery.gallery_module()
    out: list[str] = []
    for value in order:
        ref = companion_gallery.image_ref_from_id(value)
        if ref is None:
            raise OrderRejected(f"{value!r} is not a Gallery image id")
        ref_folder, filename = ref
        if ref_folder != folder:
            raise OrderRejected(f"{value!r} belongs to a different Gallery folder")
        if gallery.resolve_image_path(folder, filename) is None:
            continue
        if filename not in out:
            out.append(filename)
    return out


def order_to_image_ids(folder: str, order: list[str]) -> list[str]:
    """The stored filename order back as opaque image ids.

    Only the explicit order, never the expanded playback list. Returning
    every frame for an album saved with an empty order would read as
    "these were pinned" when what was saved was "use natural order", and
    the model treats those two as different things."""
    return [companion_gallery.image_id(folder, name) for name in order]


# -- projection ----------------------------------------------------------


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def album_frames(album: Album) -> list[tuple[str, str]]:
    """The album's ordered ``(frame_id, filename)`` frames, resolved against
    the live folder. Same resolution the collection endpoints use."""
    try:
        gallery = companion_gallery.gallery_module()
    except companion_gallery.GalleryUnavailable:
        return []
    files = gallery.list_folder_files(album.source_folder)
    return collection_sync.ordered_frames(album, files)


def _frame_cache_caps(device_id: str) -> tuple[int | None, int | None]:
    """``(capacity_bytes, max_frames)`` the display advertised, or Nones.

    Read from the same current-state heartbeat entry the manifest endpoint
    sizes against, so a plan and the manifest that follows it agree."""
    entry = _status().get(device_id)
    advertised = entry.get("frame_cache") if isinstance(entry, dict) else None
    if not isinstance(advertised, dict):
        return None, None
    capacity = advertised.get("capacity_bytes")
    max_frames = advertised.get("max_frames")
    return (
        capacity if isinstance(capacity, int) and capacity > 0 else None,
        max_frames if isinstance(max_frames, int) and max_frames > 0 else None,
    )


def _projection(
    album: Album, device_id: str, frames: list[tuple[str, str]]
) -> dict[str, Any] | None:
    """The manifest this album would produce for this display right now,
    without warming anything or writing to the store.

    ``warm_missing=False`` with a loader that reads nothing is what makes
    a preflight cheap: cold frames report no digest and no bytes instead
    of rendering the whole folder inside a modal."""
    push = _push()
    renders_dir = _renders_dir()
    if push is None or renders_dir is None:
        return None
    capacity_bytes, max_frames = _frame_cache_caps(device_id)
    return collection_sync.build_manifest(
        album,
        device_id,
        push_mgr=push,
        renders_dir=Path(renders_dir),
        frames=frames,
        image_loader=lambda _filename: None,
        device_id_for_url=device_id,
        warm_missing=False,
        capacity_bytes=capacity_bytes,
        max_frames=max_frames,
        resync_token=_resync_token(device_id),
    )


def plan_for(
    album: Album, device_id: str, frames: list[tuple[str, str]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """The contract's per-target cache plan, from an unwarmed projection.

    Authoritative even when the display advertised no ``detail``: the
    server applies its own frame cap when it sizes a manifest, so the plan
    still describes what would happen. An absent capability detail means
    the device didn't advertise, not that the answer is unknown.

    Two cases, and the difference is which limits genuinely participated:

    *Every frame warmed* - the artifact sizes on disk are real, so both the
    frame cap and the byte budget applied, and the storage projection is
    exact.

    *Any frame cold* - a cold frame contributes no bytes, so the byte
    budget never bound the result and quoting it would be inventing a
    figure. The count comes from the frame cap alone, which is exactly as
    deterministic as the contract's ``exact`` requires, and storage is
    omitted."""
    views = manifest["frames"]
    total = len(views)
    warmed = all(view["digest"] for view in views)
    if warmed:
        cacheable = sum(1 for view in views if view["cache"])
        plan: dict[str, Any] = {
            "total_frames": total,
            "cacheable_frames": cacheable,
            "accuracy": "exact",
            "fully_offline": cacheable == total,
            "storage": {
                "bytes": sum(int(view["bytes"]) for view in views if view["cache"]),
                "accuracy": "exact",
            },
        }
        return plan
    _capacity, max_frames = _frame_cache_caps(device_id)
    cap = max_frames if max_frames else collection_sync.DEFAULT_MAX_FRAMES
    cacheable = min(total, cap)
    return {
        "total_frames": total,
        "cacheable_frames": cacheable,
        "accuracy": "exact",
        "fully_offline": cacheable == total,
    }


def _desired_version(manifest: dict[str, Any]) -> str | None:
    """The collection version, but only once every frame is warmed.

    A cold album's version moves as warming completes, so publishing it
    early would show an app drift against the device's reported version
    that isn't real. Absence is the honest answer until it settles."""
    views = manifest["frames"]
    if not views or not all(view["digest"] for view in views):
        return None
    return str(manifest["version"])


def _observation(album: Album, device_id: str) -> dict[str, Any] | None:
    """The display's last collection report, when it describes this album.

    Gated on the reported collection id the same way the Devices card is:
    a report left over from an album this display no longer plays is not
    an observation of this one. Progress counts and the synced version are
    carried only when the firmware actually sent them, since older builds
    report a state and nothing else and a zero would read as real."""
    entry = _status().get(device_id)
    report = entry.get("collection_report") if isinstance(entry, dict) else None
    if not isinstance(report, dict) or report.get("id") != f"album:{album.id}":
        return None
    state = report.get("state")
    received_at = report.get("received_at")
    if state not in _OBSERVED_STATES or not isinstance(received_at, (int, float)):
        return None
    observed: dict[str, Any] = {"state": str(state), "observed_at": _iso(received_at)}
    for key in ("cached", "total"):
        value = report.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            observed[key] = value
    version = report.get("version")
    if isinstance(version, str) and version:
        observed["version"] = version
    return observed


# -- views ---------------------------------------------------------------


def support_for(device_id: str) -> dict[str, Any] | None:
    """Computed ``frame_cache`` support for one display, or None when no
    such display is registered."""
    registry = _registry()
    device = registry.devices.get(device_id) if registry is not None else None
    if device is None or device.kind_of is None:
        return None
    return capability_support(device, _status().get(device_id), FRAME_CACHE)


def claims_for(album: Album) -> dict[str, dict[str, str]]:
    """``{device_id: {album_id, name}}`` this album would take over.

    The stored claim carries only an id, and the album holding the display
    lives in another folder the client can't look up, so the name is
    resolved here. A takeover prompt naming a raw id asks the operator to
    approve something they can't identify."""
    store = _store()
    if store is None:
        return {}
    return resolve_claims(store.conflicts_for(album))


def resolve_claims(claims: dict[str, str]) -> dict[str, dict[str, str]]:
    """Store claims (``{device_id: album_id}``) as contract claims."""
    store = _store()
    names = {a.id: a.name for a in store.all()} if store is not None else {}
    return {
        device_id: {"album_id": album_id(stored_id), "name": names.get(stored_id, stored_id)}
        for device_id, stored_id in claims.items()
    }


def target_view(
    album: Album,
    device_id: str,
    *,
    claims: dict[str, dict[str, str]],
    frames: list[tuple[str, str]],
    saved: bool,
) -> dict[str, Any]:
    """One ``OfflineAlbumTarget``.

    ``saved`` separates a stored album from a preflight draft: only a
    stored album has a version the display could be reconciling against,
    or a report describing it."""
    target: dict[str, Any] = {"device_id": device_id}
    support = support_for(device_id)
    target["support"] = support or {
        "state": "unknown",
        "reason_code": "no_usable_heartbeat",
        "observed_at": None,
    }
    claim = claims.get(device_id)
    if claim is not None:
        target["conflict"] = claim
    manifest = _projection(album, device_id, frames)
    if manifest is not None:
        target["plan"] = plan_for(album, device_id, frames, manifest)
        if saved:
            version = _desired_version(manifest)
            if version is not None:
                target["desired_version"] = version
    if saved:
        observed = _observation(album, device_id)
        if observed is not None:
            target["observed"] = observed
    return target


def album_view(album: Album, folder: str) -> dict[str, Any]:
    """One ``OfflineAlbum``, the normalized server result rather than an
    echo of whatever the last write sent."""
    return {
        "id": album_id(album.id),
        "folder_id": companion_gallery.folder_id(folder),
        "name": album.name,
        "enabled": album.enabled,
        "device_ids": list(album.device_ids),
        "order": order_to_image_ids(folder, album.order),
        "fit": album.fit,
        "playback": {
            "mode": album.playback.mode,
            "interval_s": album.playback.interval_s,
            "repeat": album.playback.repeat,
        },
    }


def album_response(album: Album, folder: str) -> dict[str, Any]:
    """The saved album and every target's computed state."""
    frames = album_frames(album)
    claims = claims_for(album)
    return {
        "album": album_view(album, folder),
        "targets": [
            target_view(album, device_id, claims=claims, frames=frames, saved=True)
            for device_id in album.device_ids
        ],
    }


def preflight_response(album: Album, folder: str) -> dict[str, Any]:
    """A draft's per-target plan, computed without saving anything.

    A conflict is reported here as information rather than a refusal; the
    write is where the operator decides whether to take a display over."""
    frames = album_frames(album)
    claims = claims_for(album)
    return {
        "folder_id": companion_gallery.folder_id(folder),
        "targets": [
            target_view(album, device_id, claims=claims, frames=frames, saved=False)
            for device_id in album.device_ids
        ],
    }


def clear_warmed_frames(device_ids: set[str]) -> None:
    """Drop cached frames for every affected display after a write.

    The same thing the Web form does, and for the same reason: a frame's
    producer id is derived from its filename, so an order, fit or
    membership change doesn't move it, and without this the next manifest
    fetch serves the previous render."""
    push = _push()
    if push is None:
        return
    for device_id in device_ids:
        try:
            push.clear_album_cache(device_id)
        except Exception:  # a cache drop must never fail a save
            logger.warning("offline album: could not clear frame cache for %s", device_id)
