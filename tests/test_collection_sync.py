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


# -- paging --------------------------------------------------------------


def _built_manifest(tmp_path: Path, count: int) -> dict[str, Any]:
    src = FakeRenderSource(tmp_path)
    album = _album(order=[])
    files = [f"{i:02d}.jpg" for i in range(count)]
    frames = collection_sync.ordered_frames(album, files)
    return collection_sync.build_manifest(
        album,
        "frame01",
        push_mgr=src,
        renders_dir=tmp_path,
        frames=frames,
        image_loader=lambda f: f.encode(),
        device_id_for_url="frame01",
        warm_missing=True,
    )


def test_paged_manifest_bounds_pages_and_walks(tmp_path: Path) -> None:
    full = _built_manifest(tmp_path, 5)
    p1 = collection_sync.paged_manifest(full, None, page_size=2)
    assert [f["position"] for f in p1["frames"]] == [0, 1]
    assert p1["cursor"] is None
    # total_frames and version describe the whole collection on every page.
    assert p1["total_frames"] == 5
    assert p1["version"] == full["version"]

    p2 = collection_sync.paged_manifest(full, p1["next_cursor"], page_size=2)
    assert [f["position"] for f in p2["frames"]] == [2, 3]
    assert p2["cursor"] == p1["next_cursor"]

    p3 = collection_sync.paged_manifest(full, p2["next_cursor"], page_size=2)
    assert [f["position"] for f in p3["frames"]] == [4]
    assert p3["next_cursor"] is None
    assert p3["version"] == full["version"]


def test_paged_manifest_stale_or_malformed_cursor_restarts(tmp_path: Path) -> None:
    full = _built_manifest(tmp_path, 3)
    bad_cursors = (
        "deadbeef00000000.1",  # other version: collection changed mid-walk
        "not-a-cursor",
        f"{full['version']}.x",
        f"{full['version']}.-1",
    )
    for bad in bad_cursors:
        page = collection_sync.paged_manifest(full, bad, page_size=2)
        assert page["cursor"] is None
        assert [f["position"] for f in page["frames"]] == [0, 1]


def test_default_page_size_holds_all_cacheable_frames() -> None:
    # Slice-1 firmware reads a single page; every cache:true frame (bounded by
    # DEFAULT_MAX_FRAMES) must therefore fit in page one.
    assert collection_sync.PAGE_MAX_FRAMES >= collection_sync.DEFAULT_MAX_FRAMES


# -- device playback report ----------------------------------------------


def test_reported_collection_parses_and_validates() -> None:
    report = collection_sync.reported_collection(
        {
            "collection": {
                "id": "album:holidays",
                "version": "a" * 16,
                "cached": 32,
                "total": 127,
                "state": "playing",
            }
        }
    )
    assert report == {
        "id": "album:holidays",
        "version": "a" * 16,
        "cached": 32,
        "total": 127,
        "state": "playing",
    }


def test_reported_collection_rejects_bad_shapes() -> None:
    assert collection_sync.reported_collection({}) is None
    assert collection_sync.reported_collection({"collection": {"id": "x"}}) is None
    assert (
        collection_sync.reported_collection({"collection": {"id": "", "state": "playing"}}) is None
    )
    assert collection_sync.reported_collection({"collection": {"id": "x", "state": "nope"}}) is None
    # Bad optional fields are dropped, not fatal.
    report = collection_sync.reported_collection(
        {"collection": {"id": "x", "state": "error", "cached": -1, "total": True, "version": 7}}
    )
    assert report == {"id": "x", "state": "error"}


def test_report_changed_ignores_count_churn() -> None:
    prev = {"id": "album:x", "state": "syncing", "version": "v1", "cached": 3, "total": 40}
    assert collection_sync.report_changed(None, prev)
    assert not collection_sync.report_changed(prev, {**prev, "cached": 9, "total": 41})
    assert collection_sync.report_changed(prev, {**prev, "state": "playing"})
    assert collection_sync.report_changed(prev, {**prev, "version": "v2"})


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


# -- unwarmable frames (#247) --------------------------------------------


class _FailingRenderSource(FakeRenderSource):
    """A render source whose warm never succeeds, e.g. an image the
    renderer cannot open."""

    def warm_album_frame(
        self, frame_id: str, device_id: str, image_bytes: bytes, *, fit: str
    ) -> bool:
        self.warmed.append((device_id, frame_id))
        return False


def _manifest_with(src: FakeRenderSource, tmp_path: Path, files: list[str]) -> dict[str, Any]:
    album = _album(order=[])
    return collection_sync.build_manifest(
        album,
        "frame01",
        push_mgr=src,
        renders_dir=tmp_path,
        frames=collection_sync.ordered_frames(album, files),
        image_loader=lambda f: f.encode(),
        device_id_for_url="frame01",
        warm_missing=True,
    )


def test_a_frame_that_cannot_be_rendered_is_never_offered_for_caching(tmp_path: Path) -> None:
    """Frames are fetched by digest. Offering one with no digest tells the
    device to cache something it cannot address, so it caches nothing and
    reports 0 of N (#247)."""
    manifest = _manifest_with(_FailingRenderSource(tmp_path), tmp_path, ["a.jpg", "b.jpg"])
    assert [f["digest"] for f in manifest["frames"]] == ["", ""]
    assert [f["cache"] for f in manifest["frames"]] == [False, False]


def test_an_unwarmable_frame_does_not_consume_the_budget(tmp_path: Path) -> None:
    """It measures zero bytes, so it always fit and was always offered,
    which is how every frame ended up cache: true and uncacheable."""
    src = FakeRenderSource(tmp_path)
    # One good frame, then one the renderer refuses.
    good = _manifest_with(src, tmp_path, ["a.jpg"])
    assert good["frames"][0]["cache"] is True

    mixed_src = FakeRenderSource(tmp_path)
    original = mixed_src.warm_album_frame

    def _selective(frame_id: str, device_id: str, image_bytes: bytes, *, fit: str) -> bool:
        if image_bytes == b"bad.jpg":
            return False
        return original(frame_id, device_id, image_bytes, fit=fit)

    mixed_src.warm_album_frame = _selective  # type: ignore[method-assign]
    manifest = _manifest_with(mixed_src, tmp_path, ["a.jpg", "bad.jpg", "c.jpg"])
    caches = [f["cache"] for f in manifest["frames"]]
    assert caches == [True, False, True]


def test_cacheable_count_reflects_only_addressable_frames(tmp_path: Path) -> None:
    manifest = _manifest_with(_FailingRenderSource(tmp_path), tmp_path, ["a.jpg"])
    assert sum(1 for f in manifest["frames"] if f["cache"]) == 0


def test_unrendered_frames_are_reported_separately_from_over_caps(tmp_path, caplog) -> None:
    """A frame over the device's caps is expected on a big album; a frame
    with no render is a fault. Reporting both as "exceeds caps" is what
    made the second invisible in a journal (#247)."""
    import logging

    with caplog.at_level(logging.INFO, logger="app.collection_sync"):
        _manifest_with(_FailingRenderSource(tmp_path), tmp_path, ["a.jpg", "b.jpg"])
    text = caplog.text
    assert "have no render" in text
    assert "exceed device caps" not in text


def test_a_healthy_manifest_still_says_it_ran(tmp_path, caplog) -> None:
    """Silence on the happy path meant a device that never asked and one
    that asked and got nothing looked identical."""
    import logging

    with caplog.at_level(logging.INFO, logger="app.collection_sync"):
        _manifest_with(FakeRenderSource(tmp_path), tmp_path, ["a.jpg"])
    assert "frames=1 cacheable=1 unrendered=0" in caplog.text
