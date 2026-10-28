"""The last good comics on disk, and the per-source record of how they got there.

Layout inside the widget's ``data_dir``::

    state.json          per source: pictures, edition, last success / attempt, error
    log.json            the newest LOG_LINES fetches, uploads and imports, for the admin page
    gocomics-cookie-status.json  when the pasted GoComics cookies were set and how they did
    images/<name>       the pictures, named by content hash
    gocomics-cookies.txt  the Bunny Shield session (written by server.py)

A strip's manual picture (uploaded, or imported from an address on the admin
page) is a source of its own, ``manual:<strip>``, stored like a download and
never forgotten for age: it stays until the strip goes back to Automatic.

A refresh writes a new picture under a new name, then points ``state.json``
at it, then prunes, so a render reading the old state mid-refresh still
finds its files. Nothing is ever overwritten in place, which is what keeps
the last good strip on screen when a fetch fails.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from PIL import Image, ImageOps

_HERE = Path(__file__).resolve().parent


def _lifecycle() -> ModuleType:
    """media_lifecycle.py beside this file: the one server.py loaded, else
    loaded here (a direct import of this module)."""
    module_name = f"_tesserae_plugins.{_HERE.name}.media_lifecycle"
    found = sys.modules.get(module_name)
    if found is not None:
        return found
    spec = importlib.util.spec_from_file_location(module_name, _HERE / "media_lifecycle.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


lifecycle = _lifecycle()

STATE_FILE = "state.json"
LOG_FILE = "log.json"
COOKIE_STATUS_FILE = "gocomics-cookie-status.json"
ROTATION_FILE = "turns.json"
# When each unused picture was first seen unused (media_lifecycle.py).
RETIRED_FILE = "retired.json"
# When the cleanup last ran outside a fetch, and how often it may.
CLEANUP_FILE = "cleanup.json"
CLEANUP_EVERY_S = 3600
IMAGE_DIR = "images"
# The admin page's log keeps this many lines.
LOG_LINES = 150
# A picture uploaded or imported by hand: at most this many bytes, and this
# many pixels once decoded (checked before decoding, so a small file that
# expands into a huge bitmap is refused rather than decoded).
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 50_000_000
MANUAL_PREFIX = "manual:"
# The widest source today is a 1200 px Dilbert; panels are 1440 px across.
# Anything larger is only bytes for the renderer to wait on.
MAX_EDGE_PX = 2400
JPEG_QUALITY = 90
# A picture no longer referenced stays this long, in case a render that read
# the previous state is still loading it.
PRUNE_GRACE_S = 3600
# A source nobody has asked for in this long is forgotten with its pictures.
FORGET_AFTER_S = 30 * 86400
# The pictures folder stays under this; past it the oldest unreferenced go.
MAX_BYTES = 200 * 1024 * 1024

_NAME_RE = re.compile(r"^[a-z0-9-]{1,80}-[0-9a-f]{12}\.(?:png|jpg)$")
_KEY_SAFE_RE = re.compile(r"[^a-z0-9]+")

_RUNTIME = f"_tesserae_plugins.{_HERE.name}.runtime"


def shared(name: str, factory: Callable[[], Any]) -> Any:
    """Process-wide state that outlives a reload of the plugin's modules.

    An in-process widget reload imports these files again; module globals
    would then give the new code its own lock and worker registry while the
    old worker thread still writes to the same folder. Keeping them on one
    holder module, created once, gives every version the same objects.
    """
    holder = sys.modules.setdefault(_RUNTIME, ModuleType(_RUNTIME))
    return holder.__dict__.setdefault(name, factory())


# One writer at a time per process, across reloads; readers rely on the
# atomic replace.
_LOCK: threading.Lock = shared("store_lock", threading.Lock)


@dataclass(frozen=True)
class Picture:
    """A picture ready for the browser renderer."""

    data: bytes
    ext: str
    width: int
    height: int


def _flatten(img: Image.Image) -> Image.Image:
    """Bring any decoded mode down to L or RGB on white.

    Transparent pixels would otherwise show the theme's background through
    the strip, which on a dark theme is black ink on black.
    """
    if img.mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    if img.mode in ("RGBA", "LA", "PA"):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    if img.mode in ("1", "I", "I;16", "F"):
        return img.convert("L")
    if img.mode not in ("L", "RGB"):
        return img.convert("RGB")
    return img


def header_size(raw: bytes) -> tuple[int, int] | None:
    """A PNG, GIF, WebP or JPEG's pixel size read from its header bytes, or
    None for anything else. Read before Pillow opens the file: Pillow sets
    up some decoders (WebP's animation decoder and its canvas) inside
    ``Image.open``, so a size check after it comes too late."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR" and len(raw) >= 24:
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if raw[:6] in (b"GIF87a", b"GIF89a") and len(raw) >= 10:
        return int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" and len(raw) >= 30:
        chunk = raw[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(raw[24:27], "little"), 1 + int.from_bytes(
                raw[27:30], "little"
            )
        if chunk == b"VP8 ":
            return (
                int.from_bytes(raw[26:28], "little") & 0x3FFF,
                int.from_bytes(raw[28:30], "little") & 0x3FFF,
            )
        if chunk == b"VP8L" and raw[20] == 0x2F:
            bits = int.from_bytes(raw[21:25], "little")
            return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
        return None
    if raw[:2] == b"\xff\xd8":
        at = 2
        while at + 9 < len(raw):
            if raw[at] != 0xFF:
                return None
            marker = raw[at + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                at += 2
                continue
            length = int.from_bytes(raw[at + 2 : at + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return (
                    int.from_bytes(raw[at + 7 : at + 9], "big"),
                    int.from_bytes(raw[at + 5 : at + 7], "big"),
                )
            at += 2 + length
    return None


def normalise(raw: bytes, max_edge: int = MAX_EDGE_PX) -> Picture:
    """Decode a downloaded picture and re-encode it for the renderer.

    Takes the first frame of an animation, applies EXIF orientation, flattens
    transparency, caps the size, and writes JPEG for photographic sources
    (the Far Side scans) and PNG for everything else (GIF line art keeps its
    hard edges). Raises ValueError when ``raw`` is not a picture at all,
    the usual sign of an error or challenge page served in its place.
    """
    size = header_size(raw)
    if size is None or size[0] < 1 or size[1] < 1:
        raise ValueError("not a PNG, GIF, WebP or JPEG picture")
    if size[0] * size[1] > MAX_PIXELS:
        raise ValueError("too many pixels")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ("PNG", "GIF", "WEBP", "JPEG"):
                raise ValueError("not a PNG, GIF, WebP or JPEG picture")
            if img.width * img.height > MAX_PIXELS:
                raise ValueError("too many pixels")
            img.load()
            source_format = img.format
            out = _flatten(ImageOps.exif_transpose(img) or img)
            if max(out.size) > max_edge:
                out.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            if source_format == "JPEG":
                out.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                ext = "jpg"
            else:
                out.save(buf, format="PNG", optimize=True)
                ext = "png"
            width, height = out.size
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as err:
        raise ValueError("not a picture") from err
    if width < 1 or height < 1:
        raise ValueError("empty picture")
    return Picture(buf.getvalue(), ext, width, height)


def validate_upload(raw: bytes) -> Picture:
    """A picture uploaded or imported by hand, checked and converted the way
    a downloaded strip is (:func:`normalise`). Raises ValueError with a
    message for the page: too big, or not a picture."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"That picture is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        return normalise(raw)
    except ValueError as err:
        raise ValueError(
            "That isn't a picture this widget can use (PNG, JPEG, GIF or WebP, "
            f"at most {MAX_PIXELS // 1_000_000} megapixels)."
        ) from err


def _write_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class Store:
    """``state.json`` plus the picture files it points at."""

    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir)
        self.image_dir = self.root / IMAGE_DIR
        self.state_path = self.root / STATE_FILE

    # -- state ------------------------------------------------------------

    def sources(self) -> dict[str, dict[str, Any]]:
        """Every source's record. A missing or corrupt file reads as empty,
        which only means the next render fetches again."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        sources = data.get("sources") if isinstance(data, dict) else None
        if not isinstance(sources, dict):
            return {}
        return {k: v for k, v in sources.items() if isinstance(v, dict)}

    def update(self, key: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Read-modify-write one source's record under the store lock."""
        with _LOCK:
            sources = self.sources()
            entry = dict(sources.get(key) or {})
            change(entry)
            sources[key] = entry
            self._save(sources)
            return entry

    def _save(self, sources: dict[str, dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"version": 1, "sources": sources}, indent=1, sort_keys=True)
        _write_atomic(self.state_path, body.encode("utf-8"))

    def forget(self, key: str) -> None:
        """Drop one source's record (its pictures go at the next prune)."""
        with _LOCK:
            sources = self.sources()
            if sources.pop(key, None) is not None:
                self._save(sources)

    # -- whose turn it is, per placement (screen.py) --------------------------

    def take_turn(
        self, placement: str, count: int, decide: Callable[[int], Any], *, advance: bool, now: float
    ) -> Any:
        """The plan ``decide(start)`` gives for this placement's turn, under
        the store lock. With ``advance``, a placement whose comics have been
        up for HOLD_S moves on past the ones it showed; a preview never
        moves it. Placements unseen for FORGET_AFTER_S are dropped."""
        from_hold = 4 * 60
        with _LOCK:
            path = self.root / ROTATION_FILE
            try:
                turns = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                turns = {}
            turns = turns if isinstance(turns, dict) else {}
            mine = turns.get(placement) if isinstance(turns.get(placement), dict) else {}
            start = int(mine.get("start") or 0) if mine else 0
            shown = int(mine.get("shown") or 0) if mine else 0
            at = float(mine.get("at") or 0) if mine else 0.0
            moved = False
            if advance and mine and shown and now - at >= from_hold:
                start = (start + shown) % max(1, count)
                moved = True
            chosen = decide(start)
            if not mine or moved or len(chosen.show) != shown:
                turns = {
                    k: v
                    for k, v in turns.items()
                    if isinstance(v, dict) and now - float(v.get("at") or 0) < FORGET_AFTER_S
                }
                turns[placement] = {
                    "start": start,
                    "shown": len(chosen.show),
                    "at": now if (moved or not mine) else at,
                }
                self.root.mkdir(parents=True, exist_ok=True)
                _write_atomic(path, json.dumps(turns).encode("utf-8"))
            return chosen

    # -- the pasted GoComics cookies' status --------------------------------
    # When they were pasted, when they expire, how many, and how the last
    # GoComics session that used them did. Never the cookies themselves.

    def cookie_status(self) -> dict[str, Any]:
        try:
            data = json.loads((self.root / COOKIE_STATUS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def update_cookie_status(self, change: Callable[[dict[str, Any]], None] | None) -> None:
        """Change the status under the store lock; ``None`` forgets it."""
        with _LOCK:
            path = self.root / COOKIE_STATUS_FILE
            if change is None:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                return
            status = self.cookie_status()
            change(status)
            self.root.mkdir(parents=True, exist_ok=True)
            _write_atomic(path, json.dumps(status, indent=1).encode("utf-8"))

    # -- the log --------------------------------------------------------------

    def log(self) -> list[dict[str, Any]]:
        """The log's entries, oldest first; a missing or corrupt file is empty."""
        try:
            data = json.loads((self.root / LOG_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = data.get("log") if isinstance(data, dict) else None
        return [
            e
            for e in entries or []
            if isinstance(e, dict)
            and isinstance(e.get("at"), (int, float))
            and isinstance(e.get("text"), str)
        ]

    def append_log(self, entry: dict[str, Any]) -> None:
        """Add one entry, keeping the newest LOG_LINES."""
        with _LOCK:
            entries = [*self.log(), entry][-LOG_LINES:]
            self.root.mkdir(parents=True, exist_ok=True)
            body = json.dumps({"log": entries}, indent=1)
            _write_atomic(self.root / LOG_FILE, body.encode("utf-8"))

    # -- pictures -----------------------------------------------------------

    def add(self, key: str, picture: Picture) -> str:
        """Write ``picture`` and return its file name. Same bytes, same name,
        so an unchanged strip refetched tomorrow costs no new file."""
        prefix = _KEY_SAFE_RE.sub("-", key.lower()).strip("-")[:60] or "comic"
        digest = hashlib.sha256(picture.data).hexdigest()[:12]
        name = f"{prefix}-{digest}.{picture.ext}"
        path = self.image_dir / name
        # Under the cleanup's lock: a picture that comes back (same bytes,
        # same name) after it was left unused starts its grace over, so the
        # cleanup can't delete it between this write and the record that
        # points at it.
        with _LOCK:
            self.image_dir.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                _write_atomic(path, picture.data)
            lifecycle.retire_reset(self.root / RETIRED_FILE, name)
        return name

    def image_path(self, name: str) -> Path | None:
        """The file behind a served name, or None. Names are validated
        against the shape :meth:`add` writes, so no path escapes the folder."""
        if not _NAME_RE.match(name):
            return None
        path = self.image_dir / name
        return path if path.is_file() else None

    def prune(
        self,
        now: float | None = None,
        wanted: set[str] | None = None,
        max_bytes: int = MAX_BYTES,
    ) -> list[str]:
        """Forget sources nothing shows any more and delete the pictures
        nothing points at (media_lifecycle.py's policy).

        ``wanted`` is every source the settings still name (each strip in
        any mode, its manual picture, the Far Side), when the caller knows
        them: a record outside it (a removed or renamed strip, the manual
        picture of a strip that left the list) is forgotten once untouched
        for PRUNE_GRACE_S. Without it, a record is forgotten after
        FORGET_AFTER_S without a try, manual pictures excepted. Then the
        pictures no record points at go after PRUNE_GRACE_S, and older
        unreferenced ones first while the folder is over ``max_bytes``. A
        picture a record points at (the one on screen) is never deleted.
        Returns the files deleted."""
        now = time.time() if now is None else now
        with _LOCK:
            sources = self.sources()
            stale = []
            for key, entry in sources.items():
                touched = max(
                    float(entry.get("last_attempt") or 0), float(entry.get("last_success") or 0)
                )
                if wanted is not None and key not in wanted:
                    if now - touched > PRUNE_GRACE_S:
                        stale.append(key)
                elif not key.startswith(MANUAL_PREFIX) and now - touched > FORGET_AFTER_S:
                    stale.append(key)
            for key in stale:
                del sources[key]
            if stale:
                self._save(sources)
            if not self.image_dir.is_dir():
                return []
            # Still under the lock: no record can point at a picture between
            # the keep set and the deletes, and each delete checks again.
            return lifecycle.sweep(
                self.image_dir,
                keep=self._referenced(sources),
                max_bytes=max_bytes,
                grace_s=PRUNE_GRACE_S,
                now=now,
                ledger=self.root / RETIRED_FILE,
                still_unused=lambda name: name not in self._referenced(self.sources()),
            )

    def prune_if_due(
        self, wanted: set[str] | None, now: float | None = None, every: float = CLEANUP_EVERY_S
    ) -> list[str] | None:
        """Run :meth:`prune` if it hasn't run for ``every`` seconds; None when
        not due. Renders and the admin page call this, so a widget whose
        strips are all manual (no fetch worker ever runs) still cleans up."""
        now = time.time() if now is None else now
        path = self.root / CLEANUP_FILE
        try:
            last = float(json.loads(path.read_text(encoding="utf-8")).get("at") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            last = 0.0
        if 0 <= now - last < every:
            return None
        with _LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            _write_atomic(path, json.dumps({"at": now}).encode("utf-8"))
        return self.prune(now=now, wanted=wanted)

    @staticmethod
    def _referenced(sources: dict[str, dict[str, Any]]) -> set[str]:
        return {
            str(pic.get("file"))
            for entry in sources.values()
            for pic in entry.get("pictures") or []
            if isinstance(pic, dict)
        }
