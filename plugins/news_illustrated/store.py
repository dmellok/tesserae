"""Everything the widget keeps in its data dir, written atomically.

* ``stories.json``: the stories a render shows, one per source, in source order.
* ``images/<source>-<hash>.jpg``: one illustration per story. The name carries
  a hash of the bytes, so a new picture never overwrites the one a panel may
  be loading, and its URL can be cached forever.
* ``state.json``: the last run (for the widget's page) and which day's
  edition is done.
* ``cursor.json``: which story each placement showed last, for rotation.
* ``frames.json``: the size of each placement that rendered lately, so the
  pictures are drawn the shape of the frames that show them.
* ``jobs.json``: a story whose run did not finish: its summary and any paid
  picture task still to collect, so the next run resumes it instead of
  paying for another.
* ``run.lock``: locked (by the OS) while a run is going, so two never overlap
  (a second server process, or a widget reloaded in place with the old run
  still going). ``cursor.lock`` does the same for the rotation cursors.

A render only ever reads these; the run is the only writer of stories and
images.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

STORIES = "stories.json"
STATE = "state.json"
CURSOR = "cursor.json"
LOCK = "run.lock"
CURSOR_LOCK = "cursor.lock"
FRAMES = "frames.json"
FRAMES_LOCK = "frames.lock"
JOBS = "jobs.json"
IMAGES = "images"
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,80}\.jpg")
# A rendering placement moves to the next story at most this often, so the
# renders around one send (a warm-ahead, the send, its follow-ups) agree.
ROTATE_MIN_S = 240
# Placements not seen for this long are forgotten.
CURSOR_KEEP_S = 30 * 86400


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file and a rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: Any) -> None:
    write_atomic(path, json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8"))


# ----------------------------------------------------------------------
# Stories and their pictures
# ----------------------------------------------------------------------


def load_stories(data_dir: Path) -> list[dict[str, Any]]:
    got = read_json(Path(data_dir) / STORIES, {})
    stories = got.get("stories") if isinstance(got, dict) else None
    if not isinstance(stories, list):
        return []
    return [s for s in stories if isinstance(s, dict) and s.get("title")]


def save_stories(data_dir: Path, stories: list[dict[str, Any]]) -> None:
    write_json(Path(data_dir) / STORIES, {"stories": stories, "saved": int(time.time())})


def save_image(data_dir: Path, source_key: str, jpeg: bytes) -> str:
    """Keep a picture; returns its file name."""
    digest = hashlib.sha1(jpeg, usedforsecurity=False).hexdigest()[:12]
    name = f"{source_key}-{digest}.jpg"
    write_atomic(Path(data_dir) / IMAGES / name, jpeg)
    return name


def image_path(data_dir: Path, name: str) -> Path | None:
    """The picture called ``name``, if that is a name this widget writes and it exists."""
    if not IMAGE_NAME.fullmatch(name):
        return None
    path = Path(data_dir) / IMAGES / name
    return path if path.is_file() else None


# The pictures folder stays under this; past it the oldest unused go first.
MAX_IMAGE_BYTES = 200 * 1024 * 1024


def prune_images(data_dir: Path, keep: set[str], *, now: float | None = None) -> int:
    """Delete pictures no story uses any more, once unused for an hour since
    they stopped being used (a render may still be loading the one a story
    just replaced), and while the
    folder is over MAX_IMAGE_BYTES the oldest unused first
    (media_lifecycle). A picture a story uses is never deleted. How
    many went."""
    from . import media_lifecycle

    gone = media_lifecycle.sweep(
        Path(data_dir) / IMAGES,
        keep=keep,
        max_bytes=MAX_IMAGE_BYTES,
        now=now,
        pattern="*.jpg",
        # The grace counts from when a picture stopped being used.
        ledger=Path(data_dir) / "retired-images.json",
    )
    return len(gone)


# ----------------------------------------------------------------------
# Run state
# ----------------------------------------------------------------------


def load_state(data_dir: Path) -> dict[str, Any]:
    got = read_json(Path(data_dir) / STATE, {})
    return got if isinstance(got, dict) else {}


def save_state(data_dir: Path, state: dict[str, Any]) -> None:
    write_json(Path(data_dir) / STATE, state)


# ----------------------------------------------------------------------
# Locks held by the operating system
# ----------------------------------------------------------------------


class FileLock:
    """An exclusive lock on a file, held by the OS for as long as this object
    keeps it: a crashed process loses it at once, so nothing has to judge a
    lock "stale" and two runs can never both decide they own it. The file
    itself stays; only the lock comes and goes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self, *, wait: bool) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if _lock_fd(fd, wait=wait):
                self._fd = fd
                return True
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
        return False

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            with contextlib.suppress(OSError):
                _unlock_fd(fd)
            os.close(fd)

    def __enter__(self) -> FileLock:
        self.acquire(wait=True)
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _lock_fd(fd: int, *, wait: bool) -> bool:
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not wait:
                    return False
                time.sleep(0.05)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_fd(fd: int, *, wait: bool) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            return False
        return True

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def run_lock(data_dir: Path) -> FileLock:
    """The lock a run holds for its whole life."""
    return FileLock(Path(data_dir) / LOCK)


def lock_held(data_dir: Path) -> bool:
    """True while a run (in this process or another) holds the run lock."""
    probe = run_lock(data_dir)
    if probe.acquire(wait=False):
        probe.release()
        return False
    return True


# ----------------------------------------------------------------------
# Rotation
# ----------------------------------------------------------------------


def pick(data_dir: Path, placement: str, count: int, *, advance: bool, now: float) -> int:
    """The story index a placement shows now. With ``advance`` the placement
    moves on to the next story, at most once per ROTATE_MIN_S. The
    read-modify-write holds a file lock: renders run on several threads, and
    a reloaded copy of this module shares nothing in memory with the old one."""
    if count <= 0:
        return 0
    path = Path(data_dir) / CURSOR
    with FileLock(Path(data_dir) / CURSOR_LOCK):
        got = read_json(path, {})
        cursors = got if isinstance(got, dict) else {}
        mine = cursors.get(placement)
        index = int(mine.get("i", -1)) if isinstance(mine, dict) else -1
        at = float(mine.get("at", 0)) if isinstance(mine, dict) else 0.0
        if index < 0 or (advance and now - at >= ROTATE_MIN_S):
            index += 1
            at = now
            cursors = {
                k: v
                for k, v in cursors.items()
                if isinstance(v, dict) and now - float(v.get("at", 0)) < CURSOR_KEEP_S
            }
            cursors[placement] = {"i": index % count, "at": at}
            with contextlib.suppress(OSError):
                write_json(path, cursors)
        return index % count


# ----------------------------------------------------------------------
# Frame sizes
# ----------------------------------------------------------------------

# A placement's size is written again at most this often when unchanged, to
# keep it from being forgotten; forgotten after FRAMES_KEEP_S unseen.
FRAME_TOUCH_S = 86400
FRAMES_KEEP_S = 30 * 86400
MAX_FRAMES = 50


def note_frame(data_dir: Path, placement: str, width: int, height: int, *, now: float) -> None:
    """Remember that ``placement`` renders ``width`` x ``height``. Cheap when
    nothing changed: one small read, no write."""
    if width <= 0 or height <= 0:
        return
    path = Path(data_dir) / FRAMES
    seen = _frames(path)
    mine = seen.get(placement)
    if (
        isinstance(mine, dict)
        and (mine.get("w"), mine.get("h")) == (width, height)
        and _age(mine, now) < FRAME_TOUCH_S
    ):
        return
    with FileLock(Path(data_dir) / FRAMES_LOCK):
        seen = {k: v for k, v in _frames(path).items() if _age(v, now) < FRAMES_KEEP_S}
        seen[placement] = {"w": int(width), "h": int(height), "at": now}
        newest = sorted(seen.items(), key=lambda kv: float(kv[1].get("at") or 0))[-MAX_FRAMES:]
        with contextlib.suppress(OSError):
            write_json(path, dict(newest))


def frame_sizes(data_dir: Path, now: float) -> list[tuple[int, int]]:
    """The (width, height) of every placement seen in the last FRAMES_KEEP_S."""
    out: list[tuple[int, int]] = []
    for value in _frames(Path(data_dir) / FRAMES).values():
        try:
            width, height = int(value["w"]), int(value["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if _age(value, now) < FRAMES_KEEP_S and width > 0 and height > 0:
            out.append((width, height))
    return out


def _age(value: Any, now: float) -> float:
    """Seconds since a frame entry was written; infinite for a broken one."""
    try:
        return now - float(value.get("at") or 0)
    except (AttributeError, TypeError, ValueError):
        return float("inf")


def _frames(path: Path) -> dict[str, Any]:
    got = read_json(path, {})
    return got if isinstance(got, dict) else {}


# ----------------------------------------------------------------------
# Unfinished stories
# ----------------------------------------------------------------------


def load_jobs(data_dir: Path) -> dict[str, dict[str, Any]]:
    got = read_json(Path(data_dir) / JOBS, {})
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items() if isinstance(v, dict)}


def keep_jobs(data_dir: Path, keep: Callable[[dict[str, Any]], bool]) -> None:
    """Forget the unfinished attempts ``keep`` says no to."""
    jobs = load_jobs(data_dir)
    kept = {k: v for k, v in jobs.items() if keep(v)}
    if kept != jobs:
        write_json(Path(data_dir) / JOBS, kept)


def load_job(data_dir: Path, story_id: str) -> dict[str, Any]:
    """What an earlier, unfinished attempt at one story left: the story (with
    its summary) and a paid picture task still to collect."""
    got = read_json(Path(data_dir) / JOBS, {})
    job = got.get(story_id) if isinstance(got, dict) else None
    return job if isinstance(job, dict) else {}


def save_job(data_dir: Path, story_id: str, job: dict[str, Any] | None) -> None:
    """Keep (or with None, forget) an unfinished attempt. Only a run writes
    this, and only one run goes at a time."""
    path = Path(data_dir) / JOBS
    got = read_json(path, {})
    jobs = got if isinstance(got, dict) else {}
    if job is None:
        if story_id not in jobs:
            return
        jobs.pop(story_id)
    else:
        jobs[story_id] = job
    write_json(path, jobs)
