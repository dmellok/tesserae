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
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.secret_box import SecretBox, SecretBoxError, is_wrapped

BACKUPS_SUBDIR = "core/backups"  # relative to data_root
META_NAME = ".tesserae-backup.json"
META_VERSION = 2  # added "excluded_subpaths" field

LABEL_MANUAL = "manual"
LABEL_PRE_UPDATE = "pre-update"
LABEL_PRE_IMPORT = "pre-import"

SETTINGS_REL = "core/settings.json"

# What belongs to *this server* rather than to the dashboards it serves.
# An import from another install (#349) keeps these from the current
# server unless the operator opts in to taking the export's: otherwise
# importing into a second, already set-up server replaces its admin
# password, signs everyone out, and makes it impersonate the first one
# (same relay identity, same MQTT client id, same companion pairings).
#
# Settings sections kept whole.
IDENTITY_SECTIONS: tuple[str, ...] = ("auth", "relay")
# Keys kept inside the ``app`` section. ``public_url`` is the address this
# server is reached at, so the other server's value would point back at it.
IDENTITY_APP_KEYS: tuple[str, ...] = (
    "session_secret_secret",
    "mcp_token_secret",
    "webhook_token_secret",
    "instance_name",
    "server_colour",
    "public_url",
)
# Files under data_root kept as they are.
IDENTITY_FILES: tuple[str, ...] = (
    "core/install_id.json",
    "core/companion_tokens.json",
    "core/companion_idempotency.json",
    "core/.mqtt_client_id_suffix",
)

# Subpaths under data_root whose **regular files are excluded** from a
# snapshot, dotfiles (config like ``.folders.json``) inside them are still
# included so plugin metadata survives a restore.
#
# - ``plugins/picture_gallery`` hosts user-uploaded photos (tens of MB each).
# - ``core/renders`` is the push pipeline's content-addressed cache of
#   composition PNGs and per-renderer ``.bin`` artifacts. These regenerate
#   the moment any dashboard is pushed again, and the push code already
#   handles the "PNG evicted from disk" case for old history entries.
# - ``core/companion_personal_data.json`` contains latest-only personal values
#   explicitly excluded from backups by the Companion privacy contract.
#
# Restoring a backup that excluded a subpath does NOT wipe the user's
# current files there, the photos / render cache persist across restores.
DEFAULT_EXCLUDED_SUBPATHS: tuple[str, ...] = (
    "plugins/picture_gallery",
    "core/renders",
    "core/companion_personal_data.json",
)


@dataclass(frozen=True)
class Backup:
    id: str  # filename stem, e.g. "20260530-082145-manual"
    path: Path
    bytes: int
    created_at: float  # unix seconds
    label: str  # "manual" / "pre-update" / user-set
    note: str  # optional context (e.g. SHA going from/to)


@dataclass(frozen=True)
class RestoreResult:
    # Dotted settings paths of imported secrets that could not be
    # re-encrypted for this server (only filled when identity is kept).
    # They are left as they were and read back empty until re-entered.
    unreadable_secrets: tuple[str, ...] = ()


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


def _is_excluded(rel: Path, excluded_subpaths: tuple[str, ...]) -> bool:
    """Whether ``rel`` (relative to data_root) is a regular file inside any
    excluded subpath. Dotfiles within an excluded subpath are kept so
    plugin config / metadata still rides along."""
    if rel.name.startswith("."):
        return False
    rel_posix = rel.as_posix()
    return any(
        rel_posix == prefix or rel_posix.startswith(prefix + "/") for prefix in excluded_subpaths
    )


def create(
    data_root: Path,
    *,
    label: str = LABEL_MANUAL,
    note: str = "",
    excluded_subpaths: tuple[str, ...] = DEFAULT_EXCLUDED_SUBPATHS,
) -> Backup:
    """Snapshot ``data/`` into a single ``.zip`` under
    ``data/core/backups/`` and return its metadata. Writes to a ``.part``
    first and renames on success so a crash never leaves a partial.

    Regular files under ``excluded_subpaths`` are skipped (picture gallery
    images by default), dotfiles inside those paths are still included
    so plugin config survives. The exclusion list is embedded in the
    backup's metadata so :func:`restore` knows to preserve the user's
    current files there rather than wiping them."""
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
            if _is_excluded(rel, excluded_subpaths):
                continue
            if src.suffix == ".db":
                staged = td_path / rel
                staged.parent.mkdir(parents=True, exist_ok=True)
                if _snapshot_sqlite(src, staged):
                    zf.write(staged, str(rel))
                else:
                    zf.write(src, str(rel))
            else:
                zf.write(src, str(rel))
        meta: dict[str, Any] = {
            "tool": "tesserae",
            "version": META_VERSION,
            "created_at": time.time(),
            "label": label,
            "note": note,
            "excluded_subpaths": list(excluded_subpaths),
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


def _read_meta(path: Path) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(path) as zf:
            if META_NAME not in zf.namelist():
                return None
            data = json.loads(zf.read(META_NAME))
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _rewrap_secrets(
    node: Any,
    target: SecretBox,
    candidates: list[SecretBox],
    path: str,
    unreadable: list[str],
) -> None:
    """Re-encrypt every wrapped ``*_secret`` value under ``node`` in place
    for ``target``, decrypting with the first of ``candidates`` that
    works. A value none of them opens is left as it was and its dotted
    path appended to ``unreadable``."""
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        here = f"{path}.{key}" if path else str(key)
        if isinstance(value, dict):
            _rewrap_secrets(value, target, candidates, here, unreadable)
            continue
        if not (isinstance(key, str) and key.endswith("_secret") and is_wrapped(value)):
            continue
        for box in candidates:
            try:
                plain = box.unwrap(str(value))
            except SecretBoxError:
                continue
            node[key] = target.wrap(plain)
            break
        else:
            unreadable.append(here)


def _session_box(hex_secret: Any) -> SecretBox | None:
    if not isinstance(hex_secret, str) or not hex_secret:
        return None
    try:
        return SecretBox.from_session_secret(bytes.fromhex(hex_secret))
    except ValueError:
        return None


def _keep_identity(staged: Path, data_root: Path) -> list[str]:
    """Rewrite the staged import so this server keeps its own identity
    (see :data:`IDENTITY_SECTIONS` and friends) and the imported
    connector secrets are re-encrypted with this server's key. Returns
    the dotted paths of secrets that could not be re-encrypted.

    Secrets are wrapped with ``TESSERAE_SECRET_KEY`` when set, otherwise
    with a key derived from ``app.session_secret_secret``. Keeping this
    server's session secret changes the derived key, so each imported
    secret is opened with the env key or the export's session-derived
    key, whichever works, and wrapped again with this server's key."""
    current = _read_settings(data_root / SETTINGS_REL)
    staged_settings = staged / SETTINGS_REL
    imported = _read_settings(staged_settings)

    cur_app = current.get("app") if isinstance(current.get("app"), dict) else {}
    imp_app = imported.get("app") if isinstance(imported.get("app"), dict) else {}
    assert isinstance(cur_app, dict) and isinstance(imp_app, dict)
    export_box = _session_box(imp_app.get("session_secret_secret"))

    # Take the identity out of the import first, so the re-wrap below
    # only touches what is actually being imported.
    for section in IDENTITY_SECTIONS:
        imported.pop(section, None)
    for key in IDENTITY_APP_KEYS:
        imp_app.pop(key, None)

    # The key this server will boot with. A server without a session
    # secret yet gets one now, so the key is known before the swap.
    if _session_box(cur_app.get("session_secret_secret")) is None:
        cur_app = {**cur_app, "session_secret_secret": secrets.token_bytes(32).hex()}
    env_box = SecretBox.from_env()
    target_box = env_box or _session_box(cur_app["session_secret_secret"])
    assert target_box is not None
    candidates = [b for b in (env_box, export_box) if b is not None]
    unreadable: list[str] = []
    _rewrap_secrets(imported, target_box, candidates, "", unreadable)

    for section in IDENTITY_SECTIONS:
        if section in current:
            imported[section] = current[section]
    for key in IDENTITY_APP_KEYS:
        if key in cur_app:
            imp_app[key] = cur_app[key]
    if imp_app:
        imported["app"] = imp_app
    if imported:
        staged_settings.parent.mkdir(parents=True, exist_ok=True)
        staged_settings.write_text(json.dumps(imported, indent=2, sort_keys=True), encoding="utf-8")

    for rel in IDENTITY_FILES:
        src = data_root / rel
        dst = staged / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        else:
            dst.unlink(missing_ok=True)
    return unreadable


def restore(data_root: Path, backup_id: str, *, keep_identity: bool = False) -> RestoreResult:
    """Replace ``data/`` contents with the snapshot's payload. Preserves:

    * the backups dir itself (deleting the snapshot we're restoring from
      mid-restore would be bad),
    * any file inside an excluded subpath the backup recorded, those
      represent on-disk data the snapshot deliberately skipped (e.g.
      gallery photos), so the user's current files stay put,
    * with ``keep_identity``, this server's password, login settings and
      identity (an import from another install, #349); imported secrets
      are re-encrypted for this server and any that can't be are listed
      in the result.

    The caller is expected to restart the server right after, open
    SQLite handles on the old ``events.db`` keep writing to the orphaned
    inode until the process restarts (Linux/macOS); on Windows the live
    handle blocks the replace, so do this with the server stopped or via
    the maintenance flow which restarts."""
    data_root = Path(data_root)
    backup = get(data_root, backup_id)
    if backup is None:
        raise FileNotFoundError(backup_id)
    unreadable: list[str] = []
    with zipfile.ZipFile(backup.path) as zf:
        if META_NAME not in zf.namelist():
            raise ValueError("not a Tesserae backup (no meta)")
        try:
            meta = json.loads(zf.read(META_NAME))
        except (json.JSONDecodeError, OSError):
            meta = {}
        excluded = tuple(meta.get("excluded_subpaths") or ())
        # Stage to tmp, then swap.
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for member in zf.namelist():
                if member == META_NAME or member.endswith("/"):
                    continue
                target = td_path / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
            if keep_identity:
                unreadable = _keep_identity(td_path, data_root)
            backups_dir = _backups_dir(data_root).resolve()
            # Wipe data_root contents except the backups dir and files
            # inside excluded subpaths.
            for src in data_root.rglob("*"):
                if not src.exists() or src.is_dir():
                    continue
                # Inside backups dir → skip.
                try:
                    src.resolve().relative_to(backups_dir)
                    continue
                except ValueError:
                    pass
                rel = src.relative_to(data_root)
                if _is_excluded(rel, excluded):
                    continue
                with contextlib.suppress(OSError):
                    src.unlink()
            # Clean up any now-empty dirs (best-effort).
            for d in sorted(
                (p for p in data_root.rglob("*") if p.is_dir()),
                key=lambda p: len(p.parts),
                reverse=True,
            ):
                try:
                    if d.resolve() == backups_dir:
                        continue
                    d.rmdir()
                except OSError:
                    pass
            # Lay down the staged content.
            for src in td_path.rglob("*"):
                if src.is_dir():
                    continue
                rel = src.relative_to(td_path)
                dst = data_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    return RestoreResult(unreadable_secrets=tuple(unreadable))
