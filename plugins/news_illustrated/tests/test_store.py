"""The data dir: rotation cursors, the run lock, pictures and atomic writes."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from plugins.news_illustrated import store

T0 = 1_800_000_000.0


def test_rotation_moves_on_at_most_once_per_dwell(tmp_path: Path) -> None:
    assert store.pick(tmp_path, "cell", 3, advance=True, now=T0) == 0
    assert store.pick(tmp_path, "cell", 3, advance=True, now=T0 + 10) == 0, "same send"
    later = T0 + store.ROTATE_MIN_S
    assert store.pick(tmp_path, "cell", 3, advance=True, now=later) == 1
    assert store.pick(tmp_path, "cell", 3, advance=True, now=later + store.ROTATE_MIN_S) == 2
    assert store.pick(tmp_path, "cell", 3, advance=True, now=later + 2 * store.ROTATE_MIN_S) == 0


def test_previews_do_not_move_the_cursor(tmp_path: Path) -> None:
    store.pick(tmp_path, "cell", 3, advance=True, now=T0)
    for step in range(1, 4):
        assert store.pick(tmp_path, "cell", 3, advance=False, now=T0 + step * 1000) == 0


def test_placements_rotate_independently_and_survive_fewer_stories(tmp_path: Path) -> None:
    store.pick(tmp_path, "a", 3, advance=True, now=T0)
    store.pick(tmp_path, "a", 3, advance=True, now=T0 + store.ROTATE_MIN_S)
    assert store.pick(tmp_path, "b", 3, advance=True, now=T0 + store.ROTATE_MIN_S) == 0
    assert store.pick(tmp_path, "a", 1, advance=False, now=T0) == 0
    assert store.pick(tmp_path, "a", 0, advance=True, now=T0) == 0


def test_forgotten_placements_are_dropped(tmp_path: Path) -> None:
    store.pick(tmp_path, "old", 3, advance=True, now=T0)
    store.pick(tmp_path, "new", 3, advance=True, now=T0 + store.CURSOR_KEEP_S + 1)
    assert set(store.read_json(tmp_path / store.CURSOR, {})) == {"new"}


def test_run_lock_is_exclusive_and_needs_no_stale_guess(tmp_path: Path) -> None:
    first = store.run_lock(tmp_path)
    assert first.acquire(wait=False)
    assert store.lock_held(tmp_path)
    assert not store.run_lock(tmp_path).acquire(wait=False)
    first.release()
    assert not store.lock_held(tmp_path)
    # The file left behind is not a lock: only a live holder is.
    assert (tmp_path / store.LOCK).exists()
    second = store.run_lock(tmp_path)
    assert second.acquire(wait=False)
    second.release()


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX child process")
def test_a_lock_held_by_another_process_dies_with_it(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(30)",
            str(tmp_path / store.LOCK),
        ],
        stdout=subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == b"locked"
        assert store.lock_held(tmp_path)
        assert not store.run_lock(tmp_path).acquire(wait=False)
    finally:
        holder.kill()
        holder.wait()
        if holder.stdout is not None:
            holder.stdout.close()
    assert not store.lock_held(tmp_path)


def test_cursor_updates_from_two_module_copies_are_not_lost(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "news_illustrated_store_copy", Path(store.__file__)
    )
    assert spec and spec.loader
    copy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = copy
    try:
        spec.loader.exec_module(copy)

        def advance(n: int) -> None:
            module = store if n % 2 else copy
            module.pick(tmp_path, f"cell-{n}", 3, advance=True, now=T0)

        threads = [threading.Thread(target=advance, args=(n,)) for n in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.modules.pop(spec.name, None)
    assert len(store.read_json(tmp_path / store.CURSOR, {})) == 24


def test_unfinished_jobs_round_trip(tmp_path: Path) -> None:
    assert store.load_job(tmp_path, "tech") == {}
    store.save_job(tmp_path, "tech", {"summary": "S", "kie": {"task_id": "t"}})
    store.save_job(tmp_path, "world", {"summary": "W"})
    assert store.load_job(tmp_path, "tech")["kie"] == {"task_id": "t"}
    store.save_job(tmp_path, "tech", None)
    assert store.load_job(tmp_path, "tech") == {}
    assert store.load_job(tmp_path, "world") == {"summary": "W"}


def test_pictures_are_named_by_content_and_only_ours_are_served(tmp_path: Path) -> None:
    name = store.save_image(tmp_path, "tech-1234abcd", b"\xff\xd8\xffjpeg")
    assert name.startswith("tech-1234abcd-") and name.endswith(".jpg")
    assert store.image_path(tmp_path, name) == tmp_path / "images" / name
    assert store.image_path(tmp_path, "../stories.json") is None
    assert store.image_path(tmp_path, "missing-1.jpg") is None
    # Unused, but just written: it stays for the grace period, then goes.
    assert store.prune_images(tmp_path, set()) == 0
    assert store.prune_images(tmp_path, set(), now=time.time() + 2 * 3600) == 1


def test_stories_round_trip_and_bad_files_read_as_empty(tmp_path: Path) -> None:
    store.save_stories(tmp_path, [{"title": "A"}, {"title": ""}, "junk"])  # type: ignore[list-item]
    assert store.load_stories(tmp_path) == [{"title": "A"}]
    (tmp_path / store.STORIES).write_text("{not json", encoding="utf-8")
    assert store.load_stories(tmp_path) == []
    assert not list(tmp_path.glob(".*.tmp")), "no temp files left behind"


def test_frames_are_remembered_cheaply_and_forgotten(tmp_path: Path) -> None:
    store.note_frame(tmp_path, "hall", 1440, 2560, now=T0)
    store.note_frame(tmp_path, "desk", 800, 480, now=T0)
    assert sorted(store.frame_sizes(tmp_path, T0 + 60)) == [(800, 480), (1440, 2560)]
    path = tmp_path / store.FRAMES
    before = path.stat().st_mtime_ns
    time.sleep(0.01)
    store.note_frame(tmp_path, "hall", 1440, 2560, now=T0 + 60)
    assert path.stat().st_mtime_ns == before, "unchanged within a day: no write"
    store.note_frame(tmp_path, "hall", 1600, 1200, now=T0 + 120)
    assert (1600, 1200) in store.frame_sizes(tmp_path, T0 + 120)
    # The desk frame not seen for a month is forgotten; the hall was seen since.
    store.note_frame(tmp_path, "hall", 1600, 1200, now=T0 + store.FRAMES_KEEP_S - 10)
    assert store.frame_sizes(tmp_path, T0 + store.FRAMES_KEEP_S + 1) == [(1600, 1200)]
    store.note_frame(tmp_path, "zero", 0, 0, now=T0)
    assert "zero" not in path.read_text(encoding="utf-8")


def test_broken_frames_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / store.FRAMES).write_text(
        '{"a": {"w": "x", "h": 1}, "b": [], "c": {"w": 10, "h": 20, "at": "soon"},'
        f' "d": {{"w": 300, "h": 200, "at": {T0}}}}}',
        encoding="utf-8",
    )
    assert store.frame_sizes(tmp_path, T0) == [(300, 200)]
    store.note_frame(tmp_path, "e", 100, 100, now=T0)
    assert sorted(store.frame_sizes(tmp_path, T0)) == [(100, 100), (300, 200)]
    (tmp_path / store.FRAMES).write_text("[1, 2]", encoding="utf-8")
    assert store.frame_sizes(tmp_path, T0) == []
