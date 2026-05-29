"""Snapshot + restore of the runtime ``data/`` directory.

Used by the maintenance UI (Settings → System → Backups) and by the
updater to take a snapshot before applying an update so the user can roll
the data back if a release breaks something.

A snapshot is a single ``.zip`` written under ``data/core/backups/`` with
files stored relative to the data root. SQLite databases (``events.db``)
go through ``sqlite3``'s online backup API so the snapshot is consistent
even while the app is writing to them; everything else is copied as bytes.

The backups directory itself is excluded from snapshots so a backup
doesn't recursively bundle prior backups.

**Backups contain real secrets** (API tokens, MQTT passwords, OAuth
tokens). Treat the ``.zip`` with the same care as ``data/`` itself.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

BACKUPS_SUBDIR = "core/backups"  # relative to data_root
META_NAME = ".tesserae-backup.json"
META_VERSION = 1

LABEL_MANUAL = "manual"
LABEL_PRE_UPDATE = "pre-update"


@dataclass(frozen=True)
class Backup:
    id: str  # filename stem, e.g. "20260530-082145-manual"
    path: Path
    bytes: int
    created_at: float  # unix seconds
    label: str  # "manual" / "pre-update" / user-set
    note: str  # optional context (e.g. SHA going from/to)


def _backups_dir(data_root: Path) -> Path:
    d = data_root / BACKUPS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_sqlite(src: Path, dst: Path) -> bool:
    """Copy a SQLite file via the online backup API so a live writer
    can't tear the snapshot. Returns False (caller should fall back to a
    byte copy) when ``src`` isn't a SQLite database."""
    try:
        src_db = sqlite3.connect(str(src))
        try:
            dst_db = sqlite3.connect(str(dst))
            try:
                with dst_db:
                    src_db.backup(dst_db)
            finally:
                dst_db.close()
        finally:
            src_db.close()
        return True
    except sqlite3.DatabaseError:
        return False


def create(data_root: Path, *, label: str = LABEL_MANUAL, note: str = "") -> Backup:
    """Snapshot ``data/`` into a single ``.zip`` under
    ``data/core/backups/`` and return its metadata. Writes to a ``.part``
    first and renames on success so a crash never leaves a partial."""
    data_root = Path(data_root)
    out_dir = _backups_dir(data_root)
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    safe_label = "".join(c if c.isalnum() else "-" for c in (label or "manual")).strip("-")
    bid = f"{ts}-{safe_label or 'manual'}"
    final = out_dir / f"{bid}.zip"
    tmp = out_dir / f"{bid}.zip.part"
    backups_resolved = out_dir.resolve()

    with (
        tempfile.TemporaryDirectory() as td,
        zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf,
    ):
        td_path = Path(td)
        for src in data_root.rglob("*"):
            if src.is_dir() or not src.exists():
                continue
            # Skip the backups dir entirely (no recursive nesting).
            try:
                src.resolve().relative_to(backups_resolved)
            except ValueError:
                pass  # not inside backups
            else:
                continue
            rel = src.relative_to(data_root)
            if src.suffix == ".db":
                staged = td_path / rel
                staged.parent.mkdir(parents=True, exist_ok=True)
                if _snapshot_sqlite(src, staged):
                    zf.write(staged, str(rel))
                else:
                    zf.write(src, str(rel))
            else:
                zf.write(src, str(rel))
        meta = {
            "tool": "tesserae",
            "version": META_VERSION,
            "created_at": time.time(),
            "label": label,
            "note": note,
        }
        zf.writestr(META_NAME, json.dumps(meta, indent=2))
    tmp.replace(final)
    return Backup(
        id=bid,
        path=final,
        bytes=final.stat().st_size,
        created_at=float(meta["created_at"]),
        label=label,
        note=note,
    )


def _read_meta(path: Path) -> dict | None:
    try:
        with zipfile.ZipFile(path) as zf:
            if META_NAME not in zf.namelist():
                return None
            return json.loads(zf.read(META_NAME))
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError):
        return None


def list_all(data_root: Path) -> list[Backup]:
    """Newest first."""
    out_dir = _backups_dir(Path(data_root))
    items: list[Backup] = []
    for f in sorted(out_dir.glob("*.zip"), reverse=True):
        meta = _read_meta(f) or {}
        items.append(
            Backup(
                id=f.stem,
                path=f,
                bytes=f.stat().st_size,
                created_at=float(meta.get("created_at") or f.stat().st_mtime),
                label=str(meta.get("label") or "?"),
                note=str(meta.get("note") or ""),
            )
        )
    return items


def get(data_root: Path, backup_id: str) -> Backup | None:
    return next((b for b in list_all(Path(data_root)) if b.id == backup_id), None)


def delete(data_root: Path, backup_id: str) -> bool:
    backup = get(Path(data_root), backup_id)
    if backup is None:
        return False
    with contextlib.suppress(OSError):
        backup.path.unlink()
    return not backup.path.exists()


def restore(data_root: Path, backup_id: str) -> None:
    """Replace ``data/`` contents with the snapshot's payload. Preserves
    the backups dir itself (don't want to delete the snapshot we're
    restoring from). The caller is expected to restart the server right
    after — open SQLite handles on the old ``events.db`` keep writing to
    the orphaned inode until the process restarts (Linux/macOS); on
    Windows the live handle blocks the replace, so do this with the
    server stopped or via the maintenance flow which restarts."""
    data_root = Path(data_root)
    backup = get(data_root, backup_id)
    if backup is None:
        raise FileNotFoundError(backup_id)
    with zipfile.ZipFile(backup.path) as zf:
        if META_NAME not in zf.namelist():
            raise ValueError("not a Tesserae backup (no meta)")
        # Stage to tmp, then swap.
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for member in zf.namelist():
                if member == META_NAME or member.endswith("/"):
                    continue
                target = td_path / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
            backups_dir = _backups_dir(data_root).resolve()
            # Wipe data_root contents except the backups dir.
            for child in list(data_root.iterdir()):
                if child.resolve() == backups_dir:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    with contextlib.suppress(OSError):
                        child.unlink()
            # Lay down the staged content.
            for src in td_path.rglob("*"):
                if src.is_dir():
                    continue
                rel = src.relative_to(td_path)
                dst = data_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
