"""Unit tests for app.collection_sync: capability parsing, frame ordering,
manifest building, the capacity + max_frames budget, version digests, and
digest-addressed frame lookup. The REST endpoints over these are covered in
tests/test_rest_collection.py."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app import collection_sync
from app.state.album_model import Album, AlbumPlayback


class FakeRenderSource:
    """Stands in for PushManager: album_render_for + warm_album_frame."""

    def __init__(self, renders_dir: Path) -> None:
        self.renders_dir = renders_dir
        self.renders: dict[tuple[str, str], dict[str, Any]] = {}
        self.warmed: list[tuple[str, str]] = []

    def album_render_for(self, device_id: str, frame_id: str) -> dict[str, Any] | None:
        info = self.renders.get((device_id, frame_id))
        return dict(info) if info is not None else None

    def warm_album_frame(
        self, frame_id: str, device_id: str, image_bytes: bytes, *, fit: str
    ) -> bool:
        self.warmed.append((device_id, frame_id))
        digest = hashlib.sha256(image_bytes).hexdigest()[:16]
        filename = f"{digest}.bin"
        (self.renders_dir / filename).write_bytes(image_bytes)
        self.renders[(device_id, frame_id)] = {
            "digest": digest,
            "ext": "bin",
            "filename": filename,
        }
        return True


def _album(**overrides: Any) -> Album:
    base: dict[str, Any] = {
        "id": "holidays",
        "name": "Holidays",
        "device_ids": ["frame01"],
        "source_folder": "holidays",
        "fit": "fill",
        "playback": {"mode": "shuffle", "interval_s": 1800, "repeat": "reshuffle"},
    }
    base.update(overrides)
    return Album.model_validate(base)


# -- capability parsing --------------------------------------------------


def test_advertised_frame_cache_parses_and_validates() -> None:
    cap = collection_sync.advertised_frame_cache(
        {"frame_cache": {"schema": 1, "capacity_bytes": 67_108_864, "max_frames": 32}}
    )
    assert cap == {"schema": 1, "capacity_bytes": 67_108_864, "max_frames": 32}


def test_advertised_frame_cache_max_frames_optional() -> None:
    cap = collection_sync.advertised_frame_cache(
        {"frame_cache": {"schema": 1, "capacity_bytes": 1000}}
    )
    assert cap == {"schema": 1, "capacity_bytes": 1000}


def test_advertised_frame_cache_rejects_bad_shapes() -> None:
    assert collection_sync.advertised_frame_cache({}) is None
    assert collection_sync.advertised_frame_cache({"frame_cache": {"schema": 0}}) is None
    assert (
        collection_sync.advertised_frame_cache({"frame_cache": {"schema": 1, "capacity_bytes": -1}})
        is None
    )
    # booleans are not ints here
    assert (
        collection_sync.advertised_frame_cache(
            {"frame_cache": {"schema": True, "capacity_bytes": 10}}
        )
        is None
    )


# -- ordering ------------------------------------------------------------


def test_ordered_frames_respects_order_then_appends_new() -> None:
    album = _album(order=["b.jpg", "a.jpg"])
    frames = collection_sync.ordered_frames(album, ["a.jpg", "b.jpg", "c.jpg"])
    # order leads; c.jpg (not in order) is appended in natural sort.
    assert [f for _fid, f in frames] == ["b.jpg", "a.jpg", "c.jpg"]
    # frame_id is stable per filename, independent of position.
    assert frames[1][0] == collection_sync.frame_id_for("a.jpg")


def test_ordered_frames_drops_missing_order_entries() -> None:
    album = _album(order=["gone.jpg", "a.jpg"])
    frames = collection_sync.ordered_frames(album, ["a.jpg"])
    assert [f for _fid, f in frames] == ["a.jpg"]


# -- manifest ------------------------------------------------------------


def test_build_manifest_shapes_frames_and_producer(tmp_path: Path) -> None:
    src = FakeRenderSource(tmp_path)
    album = _album()
    files = ["a.jpg", "b.jpg"]
    frames = collection_sync.ordered_frames(album, files)
    loaders = {"a.jpg": b"AAAA", "b.jpg": b"BBBBBB"}

    manifest = collection_sync.build_manifest(
        album,
        "frame01",
        push_mgr=src,
        renders_dir=tmp_path,
        frames=frames,
        image_loader=lambda f: loaders.get(f),
        device_id_for_url="frame01",
        warm_missing=True,
    )
    assert manifest["schema"] == 1
    assert manifest["collection_id"] == "album:holidays"
    assert manifest["kind"] == "album"
    assert manifest["total_frames"] == 2
    assert manifest["cursor"] is None
    assert manifest["next_cursor"] is None
    assert len(manifest["version"]) == 16
    assert manifest["producer"] == {
        "album": {"playback": {"mode": "shuffle", "interval_s": 1800, "repeat": "reshuffle"}}
    }
    f0 = manifest["frames"][0]
    assert f0["frame_id"] == collection_sync.frame_id_for("a.jpg")
    assert f0["position"] == 0
    assert f0["bytes"] == 4
    assert f0["ttl_s"] == 0
    assert f0["cache"] is True
    assert f0["url"] == f"/api/v1/device/frame01/collection/frame/{f0['digest']}"
    assert manifest["frames"][1]["bytes"] == 6


def test_build_manifest_warms_only_missing(tmp_path: Path) -> None:
    src = FakeRenderSource(tmp_path)
    album = _album()
    frames = collection_sync.ordered_frames(album, ["a.jpg", "b.jpg"])
    # Pre-warm a.jpg so only b.jpg needs rendering.
    src.warm_album_frame(frames[0][0], "frame01", b"AAAA", fit="fill")
    src.warmed.clear()

    collection_sync.build_manifest(
        album,
        "frame01",
        push_mgr=src,
        renders_dir=tmp_path,
        frames=frames,
        image_loader=lambda _f: b"BBBB",
        device_id_for_url="frame01",
        warm_missing=True,
    )
    assert src.warmed == [("frame01", frames[1][0])]


def test_capacity_and_max_frames_mark_overflow_cache_false(tmp_path: Path) -> None:
    src = FakeRenderSource(tmp_path)
    album = _album(order=[])
    files = [f"{i}.jpg" for i in range(4)]
    frames = collection_sync.ordered_frames(album, files)

    # Each frame is 100 bytes; a 250-byte budget fits the first two by position.
    manifest = collection_sync.build_manifest(
        album,
        "frame01",
        push_mgr=src,
        renders_dir=tmp_path,
        frames=frames,
        image_loader=lambda _f: b"x" * 100,
        device_id_for_url="frame01",
        warm_missing=True,
        capacity_bytes=250,
    )
    cache_flags = [f["cache"] for f in manifest["frames"]]
    assert cache_flags == [True, True, False, False]

    # max_frames caps the count regardless of bytes.
    manifest2 = collection_sync.build_manifest(
        album,
        "frame01",
        push_mgr=src,
        renders_dir=tmp_path,
        frames=frames,
        image_loader=lambda _f: b"x" * 100,
        device_id_for_url="frame01",
        warm_missing=True,
        max_frames=1,
    )
    assert [f["cache"] for f in manifest2["frames"]] == [True, False, False, False]


# -- version -------------------------------------------------------------


def test_version_excludes_volatile_fields() -> None:
    base = {
        "schema": 1,
        "collection_id": "album:x",
        "frames": [],
        "cursor": None,
        "next_cursor": None,
    }
    v1 = collection_sync.version_digest(base)
    v2 = collection_sync.version_digest(
        {**base, "cursor": "page-2", "next_cursor": "abc", "version": "zzz"}
    )
    assert v1 == v2


def test_version_changes_with_playback(tmp_path: Path) -> None:
    src = FakeRenderSource(tmp_path)
    frames = collection_sync.ordered_frames(_album(), ["a.jpg"])
    kwargs: dict[str, Any] = {
        "push_mgr": src,
        "renders_dir": tmp_path,
        "frames": frames,
        "image_loader": lambda _f: b"AAAA",
        "device_id_for_url": "frame01",
        "warm_missing": True,
    }
    m1 = collection_sync.build_manifest(_album(), "frame01", **kwargs)
    seq = _album(playback=AlbumPlayback(mode="sequential", interval_s=600, repeat="loop"))
    m2 = collection_sync.build_manifest(seq, "frame01", **kwargs)
    assert m1["version"] != m2["version"]


# -- frame lookup --------------------------------------------------------


def test_frame_entry_by_digest_scans_frames(tmp_path: Path) -> None:
    src = FakeRenderSource(tmp_path)
    frames = collection_sync.ordered_frames(_album(), ["a.jpg", "b.jpg"])
    src.warm_album_frame(frames[1][0], "frame01", b"BBBB", fit="fill")
    digest = hashlib.sha256(b"BBBB").hexdigest()[:16]

    info = collection_sync.frame_entry_by_digest("frame01", digest, push_mgr=src, frames=frames)
    assert info is not None and info["digest"] == digest
    assert (
        collection_sync.frame_entry_by_digest("frame01", "0" * 16, push_mgr=src, frames=frames)
        is None
    )
