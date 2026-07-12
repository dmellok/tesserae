"""Authored-widget push/install: the server side of Tesserae Studio's widget push.

Installs a single widget from an uploaded tarball into ``<data_root>/authored/<id>``,
isolated from catalog installs (different lifecycle: developer pushes, no
``InstalledRecord``). This is what lets an authoring client build against a Tesserae on
another machine or inside the Home Assistant Add-on / Docker, where there is no shared
filesystem.

Security posture:

* Extraction is capped (compressed body, uncompressed total, file count) and uses the
  PEP 706 ``data`` filter, which rejects absolute paths, ``..`` traversal, and device
  files, so a malicious tar cannot escape the temp dir.
* Only ``kind: "widget"`` manifests that validate against ``plugin.schema.json`` and do
  not collide with a bundled id are accepted.
* ``server.py`` is never imported during install; it runs only on ``fetch()``. A source
  scan detects an admin ``blueprint()`` so the caller can choose an in-process reload
  (safe) versus a restart (needed to register new Flask routes).
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)

# Extraction caps. Compressed bound is checked before reading; the uncompressed
# and file-count bounds are checked against the tar index before extracting.
MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_FILE_COUNT = 2000

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
# Detect an admin blueprint without importing the module (no install-time exec).
_BLUEPRINT_RE = re.compile(r"^\s*def\s+blueprint\s*\(", re.MULTILINE)


class InstallError(Exception):
    """A rejected push. ``status`` maps to the HTTP code; ``message`` is safe to
    return to the caller (never a stack trace)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def authored_dir(data_root: Path) -> Path:
    d = Path(data_root) / "authored"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return slug or "widget"


def _bundled_ids(plugins_dir: Path) -> set[str]:
    if not plugins_dir.exists():
        return set()
    return {
        p.name for p in plugins_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
    }


def _extract(tar_bytes: bytes, dest: Path) -> None:
    if len(tar_bytes) > MAX_COMPRESSED_BYTES:
        raise InstallError(
            f"tarball too large ({len(tar_bytes)} bytes > {MAX_COMPRESSED_BYTES})", 413
        )
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            members = tar.getmembers()
            if len(members) > MAX_FILE_COUNT:
                raise InstallError(
                    f"tarball has too many entries ({len(members)} > {MAX_FILE_COUNT})", 413
                )
            total = sum(m.size for m in members if m.isreg())
            if total > MAX_UNCOMPRESSED_BYTES:
                raise InstallError(
                    f"tarball uncompressed size too large ({total} > {MAX_UNCOMPRESSED_BYTES})",
                    413,
                )
            # PEP 706 data filter: rejects absolute paths, "..", and device
            # files, and refuses links that escape the destination.
            tar.extractall(dest, filter="data")
    except InstallError:
        raise
    except (tarfile.TarError, OSError, ValueError) as err:
        raise InstallError(f"could not extract tarball: {err}") from err


def _locate_widget_root(extract_dir: Path) -> Path:
    """The widget folder: ``plugin.json`` either at the extract root, or inside a
    single top-level directory (the GitHub / Studio envelope shape)."""
    if (extract_dir / "plugin.json").is_file():
        return extract_dir
    children = [p for p in extract_dir.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and (children[0] / "plugin.json").is_file():
        return children[0]
    raise InstallError("no plugin.json at the tarball root or in a single top-level folder", 422)


def _validate_manifest(manifest: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise InstallError("plugin.json must be a JSON object", 422)
    try:
        jsonschema.validate(manifest, schema)
    except jsonschema.ValidationError as err:
        field = ".".join(str(p) for p in err.absolute_path) or "<root>"
        raise InstallError(f"manifest schema [{field}]: {err.message}", 422) from err
    if manifest.get("kind") != "widget":
        raise InstallError(f"only kind:'widget' can be pushed (got {manifest.get('kind')!r})", 422)
    return manifest


def declares_blueprint(widget_root: Path) -> bool:
    """True when the widget's ``server.py`` defines ``blueprint()`` (an admin page),
    detected by scanning the source, never by importing it."""
    server = Path(widget_root) / "server.py"
    if not server.is_file():
        return False
    try:
        return bool(_BLUEPRINT_RE.search(server.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def install_tarball(
    tar_bytes: bytes,
    *,
    data_root: Path,
    plugins_dir: Path,
    schema_path: Path,
    id_override: str | None = None,
) -> dict[str, Any]:
    """Extract, validate, and atomically install a single widget into
    ``<data_root>/authored/<id>``. Returns ``{id, version, name, blueprint}``.
    Raises :class:`InstallError` (with an HTTP status) on any bad input, leaving no
    partial writes."""
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err:
        raise InstallError(f"could not load plugin schema: {err}", 500) from err

    authored = authored_dir(data_root)
    incoming = authored / ".incoming"
    shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir(parents=True)
    try:
        _extract(tar_bytes, incoming)
        root = _locate_widget_root(incoming)
        try:
            manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            raise InstallError(f"plugin.json invalid: {err}", 422) from err
        _validate_manifest(manifest, schema)

        # Id: explicit override, else the envelope folder name, else a slug of the
        # manifest name. Normalise and validate the shape.
        raw_id = id_override or (
            root.name if root != incoming else _slugify(manifest.get("name", ""))
        )
        widget_id = raw_id if _ID_RE.match(raw_id) else _slugify(raw_id)
        if not _ID_RE.match(widget_id):
            raise InstallError(f"could not derive a valid widget id from {raw_id!r}", 422)

        if widget_id in _bundled_ids(plugins_dir):
            raise InstallError(
                f"id {widget_id!r} collides with a bundled widget (bundled ids win, "
                "so the push would not take effect); choose a different id",
                409,
            )

        # Atomic replace: stage a copy alongside, swap in via rename.
        staging = authored / f".{widget_id}.staging"
        backup = authored / f".{widget_id}.old"
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        shutil.copytree(root, staging)
        target = authored / widget_id
        if target.exists():
            target.rename(backup)
        try:
            staging.rename(target)
        except OSError as err:
            if backup.exists() and not target.exists():
                backup.rename(target)  # restore the previous install
            raise InstallError("could not install widget (filesystem error)", 500) from err
        finally:
            shutil.rmtree(backup, ignore_errors=True)

        logger.info(
            "authored: installed %s v%s (blueprint=%s)",
            widget_id,
            manifest.get("version", "?"),
            declares_blueprint(target),
        )
        return {
            "id": widget_id,
            "version": str(manifest.get("version", "")),
            "name": str(manifest.get("name", widget_id)),
            "blueprint": declares_blueprint(target),
        }
    finally:
        shutil.rmtree(incoming, ignore_errors=True)


def uninstall(widget_id: str, *, data_root: Path) -> bool:
    """Remove a pushed widget. Scoped strictly to ``<data_root>/authored/`` (never
    touches bundled or marketplace widgets). Returns True when a folder was removed."""
    if not _ID_RE.match(widget_id or ""):
        return False
    authored = authored_dir(data_root).resolve()
    target = (authored / widget_id).resolve()
    if target.parent != authored or not target.is_dir():
        return False
    shutil.rmtree(target)
    logger.info("authored: uninstalled %s", widget_id)
    return True


def list_authored(data_root: Path, registry: Any = None) -> list[dict[str, Any]]:
    """List pushed widgets so a client can reconcile. ``active`` = present in the
    live registry."""
    authored = authored_dir(data_root)
    out: list[dict[str, Any]] = []
    for child in sorted(authored.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        manifest_path = child / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}
        out.append(
            {
                "id": child.name,
                "version": str(manifest.get("version", "")),
                "name": str(manifest.get("name", child.name)),
                "active": bool(registry is not None and registry.get(child.name) is not None),
            }
        )
    return out


def any_blueprint(data_root: Path) -> bool:
    """True when any pushed widget declares an admin ``blueprint()``, so a bare
    reload knows it needs a restart to register routes."""
    authored = authored_dir(data_root)
    return any(
        declares_blueprint(child)
        for child in authored.iterdir()
        if child.is_dir() and not child.name.startswith((".", "_"))
    )
