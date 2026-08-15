"""picture_gallery, folder-based image rotation + admin.

Ported from inky-dash's gallery plugin. A "folder" is either:
  * **Internal**, a subdirectory inside the plugin's data_dir. Images
    are uploaded via the admin UI.
  * **External**, a metadata pointer to an arbitrary host path. The
    plugin reads + serves images from there but never writes to it;
    uploads / deletes are rejected for external folders so we can't
    accidentally trash a user's actual photo library.

Folder metadata (label, external path) lives in ``.folders.json``
inside data_dir. Internal folders are real directories on disk.

Thumbnails are JPEG-cached under ``.thumb_cache/`` keyed by
``folder + filename + mtime`` so re-uploading the same name busts the
cache automatically.

Orientation classification is cached in ``.orientation_cache.json``
(same key shape) so the portrait/landscape/square filter doesn't
re-open every image on every render.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import re
import shutil
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from PIL import Image, ImageOps
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename
from werkzeug.wrappers import Response

ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_FOLDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ROOT_FOLDER_VALUE = "_root"
META_FILE = ".folders.json"
THUMB_DIR = ".thumb_cache"
THUMB_SIZE = (320, 240)
RENDITION_DIR = ".render_cache"
ORIENT_CACHE_FILE = ".orientation_cache.json"
SQUARE_TOLERANCE = 0.05


# ----- data_dir + metadata --------------------------------------------


def _data_dir() -> Path:
    registry = current_app.config["PLUGIN_REGISTRY"]
    plugin = registry.get("picture_gallery")
    if plugin is None:
        raise RuntimeError("picture_gallery plugin not registered")
    path: Path = plugin.data_dir
    return path


def _meta_path(data_dir: Path) -> Path:
    return data_dir / META_FILE


def _load_meta(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = _meta_path(data_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_meta(data_dir: Path, meta: dict[str, dict[str, Any]]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = _meta_path(data_dir).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, _meta_path(data_dir))


# ----- folder resolution ----------------------------------------------


def _folder_path(folder_name: str, data_dir: Path) -> Path | None:
    if not folder_name or folder_name == ROOT_FOLDER_VALUE:
        return data_dir
    if not _FOLDER_NAME_RE.match(folder_name):
        return None
    meta = _load_meta(data_dir).get(folder_name, {})
    if meta.get("external_path"):
        try:
            return Path(meta["external_path"]).expanduser().resolve()
        except OSError:
            return None
    return data_dir / folder_name


def _is_external(folder_name: str, data_dir: Path) -> bool:
    if not folder_name or folder_name == ROOT_FOLDER_VALUE:
        return False
    return bool(_load_meta(data_dir).get(folder_name, {}).get("external_path"))


def _list_images(folder: Path | None) -> list[Path]:
    if folder is None or not folder.exists() or not folder.is_dir():
        return []
    try:
        return sorted(
            p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES
        )
    except (PermissionError, OSError):
        return []


def _list_internal_folders(data_dir: Path) -> list[str]:
    if not data_dir.exists():
        return []
    return sorted(
        p.name
        for p in data_dir.iterdir()
        if p.is_dir() and _FOLDER_NAME_RE.match(p.name) and not p.name.startswith(".")
    )


def _all_folder_names(data_dir: Path) -> list[str]:
    internal = set(_list_internal_folders(data_dir))
    meta = _load_meta(data_dir)
    external = {n for n, m in meta.items() if m.get("external_path")}
    return sorted(internal | external)


# ----- thumbnails -----------------------------------------------------


def _thumb_path(data_dir: Path, folder_name: str, filename: str, source: Path) -> Path:
    try:
        mtime = int(source.stat().st_mtime)
    except OSError:
        mtime = 0
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    return data_dir / THUMB_DIR / f"{folder_name}__{safe}__{mtime}.jpg"


def _ensure_thumbnail(source: Path, dest: Path) -> Path | None:
    if dest.exists():
        return dest
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            img.save(tmp, format="JPEG", quality=78, optimize=True)
            os.replace(tmp, dest)
        return dest
    except (OSError, Image.UnidentifiedImageError, ValueError):
        return None


# ----- full-size renditions -------------------------------------------
#
# A PNG copy of an image whose stored format a consumer can't take. The
# Gallery accepts BMP and GIF (pre-dithered artwork arrives as BMP, see
# #117) and the Web UI serves them as-is, but the Companion contract's
# media types don't cover either. Rather than hide those files from the
# app, it gets a rendition: same pixels, a format everything reads, and
# the stored file untouched.
#
# Cached beside the thumbnails and keyed the same way (folder + filename
# + mtime), so re-saving an image busts it automatically.


def _rendition_path(data_dir: Path, folder_name: str, filename: str, source: Path) -> Path:
    try:
        mtime = int(source.stat().st_mtime)
    except OSError:
        mtime = 0
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    return data_dir / RENDITION_DIR / f"{folder_name}__{safe}__{mtime}.png"


def _ensure_rendition(source: Path, dest: Path) -> Path | None:
    if dest.exists():
        return dest
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as img:
            # First frame only for an animated GIF: the contract has no
            # notion of an animation, and a still is what a panel renders.
            oriented = ImageOps.exif_transpose(img) or img
            if oriented.mode in ("P", "PA"):
                oriented = oriented.convert("RGBA" if "transparency" in oriented.info else "RGB")
            elif oriented.mode not in ("RGB", "RGBA", "L", "LA"):
                oriented = oriented.convert("RGB")
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            oriented.save(tmp, format="PNG", optimize=True)
            os.replace(tmp, dest)
        return dest
    except (OSError, Image.UnidentifiedImageError, ValueError):
        return None


# ----- orientation cache ---------------------------------------------


def _orient_cache_path(data_dir: Path) -> Path:
    return data_dir / ORIENT_CACHE_FILE


def _load_orient_cache(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = _orient_cache_path(data_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_orient_cache(data_dir: Path, cache: dict[str, dict[str, Any]]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = _orient_cache_path(data_dir).with_suffix(".json.tmp")
    with contextlib.suppress(OSError):
        tmp.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, _orient_cache_path(data_dir))


def _orientation_of(source: Path) -> tuple[int, int, str] | None:
    try:
        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            w, h = img.size
    except (OSError, Image.UnidentifiedImageError, ValueError):
        return None
    if h == 0:
        return None
    ratio = w / h
    if abs(ratio - 1.0) <= SQUARE_TOLERANCE:
        orient = "square"
    elif ratio > 1:
        orient = "landscape"
    else:
        orient = "portrait"
    return w, h, orient


def _filter_by_orientation(
    images: list[Path],
    folder_segment: str,
    desired: str,
    data_dir: Path,
) -> list[Path]:
    cache = _load_orient_cache(data_dir)
    dirty = False
    kept: list[Path] = []
    for p in images:
        try:
            mtime = int(p.stat().st_mtime)
        except OSError:
            continue
        key = f"{folder_segment}/{p.name}"
        entry = cache.get(key)
        if not entry or entry.get("mtime") != mtime:
            measured = _orientation_of(p)
            if measured is None:
                continue
            w, h, orient = measured
            entry = {"mtime": mtime, "w": w, "h": h, "orientation": orient}
            cache[key] = entry
            dirty = True
        if entry["orientation"] == desired:
            kept.append(p)
    if dirty:
        _save_orient_cache(data_dir, cache)
    return kept


# ----- widget contract: fetch + choices ------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    data_dir = Path(ctx["data_dir"])
    folder_name = options.get("folder") or ""
    folder = _folder_path(folder_name, data_dir)
    images = _list_images(folder)
    if not images:
        return {
            "error": f"No images in '{folder_name or ROOT_FOLDER_VALUE}'. "
            "Add some at Settings → Widgets → Gallery.",
            "url": None,
        }

    folder_segment = folder_name if folder_name else ROOT_FOLDER_VALUE
    orientation = (options.get("orientation") or "any").lower()
    if orientation in ("portrait", "landscape", "square"):
        images = _filter_by_orientation(images, folder_segment, orientation, data_dir)
        if not images:
            return {
                "error": f"No {orientation} images in '{folder_name or ROOT_FOLDER_VALUE}'.",
                "url": None,
            }

    mode = options.get("mode", "random")
    if mode == "sequential":
        suffix = f"_{orientation}" if orientation != "any" else ""
        idx_file = data_dir / f".sequential_index_{folder_segment}{suffix}"
        try:
            current = int(idx_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            current = -1
        next_idx = (current + 1) % len(images)
        # Only a render headed for a panel moves the album on. Opening the
        # editor, hovering a card on the dashboards list, or probing the
        # widget all call fetch() too, and each one used to consume a photo
        # the panel then never showed (#209). A preview shows what the panel
        # will paint next, which is the same image the position already
        # points at.
        if ctx.get("preview"):
            chosen = images[next_idx]
        else:
            with contextlib.suppress(OSError):
                idx_file.write_text(str(next_idx), encoding="utf-8")
            chosen = images[next_idx]
    else:
        chosen = random.choice(images)

    return {
        "url": f"/plugins/picture_gallery/folders/{folder_segment}/{chosen.name}",
        "filename": chosen.name,
        "folder": folder_segment,
        "count": len(images),
    }


def choices(name: str) -> list[dict[str, Any]]:
    if name != "folders":
        return []
    data_dir = _data_dir()
    out: list[dict[str, Any]] = []
    # Root pseudo-folder: only surface it when there are images loose in data_dir.
    root_has_images = any(
        p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES
        for p in (data_dir.iterdir() if data_dir.exists() else [])
    )
    if root_has_images:
        out.append({"value": ROOT_FOLDER_VALUE, "label": "(root)"})
    meta = _load_meta(data_dir)
    for folder_name in _all_folder_names(data_dir):
        external = meta.get(folder_name, {}).get("external_path")
        count = len(_list_images(_folder_path(folder_name, data_dir)))
        suffix = " ↗" if external else ""
        out.append({"value": folder_name, "label": f"{folder_name} ({count}){suffix}"})
    if not out:
        out.append({"value": "", "label": "(no folders yet)"})
    return out


def resolve_image_path(folder: str, filename: str) -> Path | None:
    """Map (folder, filename) to an on-disk image path, or None. Public so
    the Send page can resolve a gallery image to push. ``safe_join`` lets
    spaces / unicode round-trip while still rejecting traversal attempts."""
    target_dir = _folder_path(folder, _data_dir())
    if target_dir is None or not target_dir.exists() or not target_dir.is_dir():
        return None
    joined = safe_join(str(target_dir), filename)
    if joined is None:
        return None
    path = Path(joined)
    if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
        return None
    return path


def list_folder_files(folder: str) -> list[str]:
    """Public: a folder's image filenames in natural (filename) sort. Used by
    the offline-album producer (#177) to enumerate an album's frames. Empty for
    an unknown / empty folder."""
    data_dir = _data_dir()
    if folder and folder != ROOT_FOLDER_VALUE and not _FOLDER_NAME_RE.match(folder):
        return []
    return [p.name for p in _list_images(_folder_path(folder, data_dir))]


# ----- storage adapter (public, for the Companion API) ----------------
#
# The Companion Gallery surface (#225) reads and writes the same storage
# the admin pages do, but must not call the admin routes: those are
# browser forms with redirects, flash messages, and filename identity,
# and they change shape as the plugin does. These functions are the
# contract between the plugin's storage and anything outside it.


def folder_names() -> list[str]:
    """Every addressable folder, root first then internal + external by
    name. Root is the plugin's own data_dir, always present."""
    return [ROOT_FOLDER_VALUE, *_all_folder_names(_data_dir())]


def folder_exists(folder: str) -> bool:
    # An empty name is not root. ``_folder_path`` treats the two the same
    # for the render path's convenience, which would let an outside caller
    # address root by a second name it never minted.
    if not folder:
        return False
    if folder == ROOT_FOLDER_VALUE:
        return True
    path = _folder_path(folder, _data_dir())
    return path is not None and path.is_dir()


def folder_is_external(folder: str) -> bool:
    """External folders point at an arbitrary host directory. Read-only:
    the plugin serves from them but never writes, so an upload can't
    scribble on somebody's actual photo library."""
    return _is_external(folder, _data_dir())


def folder_label(folder: str) -> str:
    if folder == ROOT_FOLDER_VALUE:
        return "(root)"
    meta = _load_meta(_data_dir()).get(folder, {})
    label = meta.get("label")
    return str(label) if isinstance(label, str) and label.strip() else folder


def normalize_folder_name(raw: str) -> str | None:
    """Fold a user-supplied display name into a storage folder name, or
    None when nothing usable survives.

    Folder names are the on-disk directory names, so the plugin's own
    regex is the authority: lowercase, digits, ``-`` and ``_``, starting
    on an alphanumeric. Normalising here rather than rejecting means a
    phone keyboard's "Summer 2026" lands as ``summer-2026`` instead of a
    validation error the operator has to guess their way out of."""
    folded = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-_")
    folded = re.sub(r"-{2,}", "-", folded)[:64].strip("-_")
    return folded if folded and _FOLDER_NAME_RE.match(folded) else None


def create_internal_folder(folder: str) -> bool:
    """Create an internal folder. False when it already exists (internal
    or external) or the name isn't a legal folder name. Never links an
    external path: that stays an admin-only action, since it hands the
    plugin a host directory to read."""
    if not _FOLDER_NAME_RE.match(folder):
        return False
    data_dir = _data_dir()
    meta = _load_meta(data_dir)
    if folder in meta or (data_dir / folder).exists():
        return False
    (data_dir / folder).mkdir(parents=True)
    meta[folder] = {"label": folder, "external_path": None}
    _save_meta(data_dir, meta)
    return True


def image_target_dir(folder: str) -> Path | None:
    """Writable directory for a folder, or None when it doesn't exist or
    doesn't accept writes."""
    if folder_is_external(folder):
        return None
    target = _folder_path(folder, _data_dir())
    if target is None or not target.is_dir():
        return None
    return target


def thumbnail_path(folder: str, filename: str) -> Path | None:
    """Cached JPEG thumbnail for one image, generating it on first ask.
    None when the image is unreadable."""
    source = resolve_image_path(folder, filename)
    if source is None:
        return None
    return _ensure_thumbnail(source, _thumb_path(_data_dir(), folder, filename, source))


def rendition_path(folder: str, filename: str) -> Path | None:
    """Cached full-size PNG copy of one image, generating it on first
    ask. None when the image is unreadable.

    For consumers that can't take the stored format. The stored file is
    never touched, so the Web UI keeps serving the original."""
    source = resolve_image_path(folder, filename)
    if source is None:
        return None
    return _ensure_rendition(source, _rendition_path(_data_dir(), folder, filename, source))


def allowed_suffixes() -> frozenset[str]:
    return ALLOWED_SUFFIXES


# ----- offline album authoring (#177) ---------------------------------


def _bindable_devices() -> list[dict[str, Any]]:
    """Device instances an offline album can be bound to, each carrying
    the computed frame-cache support state that decides whether it can
    actually play one.

    Every registered instance is listed rather than filtered: a target
    that has simply not checked in yet is not the same as one that
    reported no storage, and hiding either leaves an operator wondering
    where their display went. Only ``unsupported`` is refused, since
    that's the one state we have evidence for; ``unknown`` stays
    selectable with a caveat, so a display that happens to be asleep
    during setup can still be bound (#225)."""
    reg = current_app.config.get("DEVICE_REGISTRY")
    if reg is None:
        return []
    from app.device_capability import FRAME_CACHE, capability_support, support_note

    status_cache = current_app.config.get("DEVICE_STATUS") or {}
    out: list[dict[str, Any]] = []
    for dev in sorted(reg.devices.values(), key=lambda d: d.name.lower()):
        if dev.kind_of is None:
            continue
        support = capability_support(dev, status_cache.get(dev.id), FRAME_CACHE)
        out.append(
            {
                "id": dev.id,
                "label": dev.display_name,
                "state": support["state"],
                "selectable": support["state"] != "unsupported",
                "note": support_note(support),
            }
        )
    return out


def _album_for_folder(folder: str) -> Any | None:
    """The saved album whose id is this folder, or None. One album per folder."""
    store = current_app.config.get("ALBUM_STORE")
    return store.get(folder) if store is not None else None


# ----- admin blueprint ------------------------------------------------


def blueprint() -> Blueprint:
    bp = Blueprint("picture_gallery_admin", __name__, template_folder="templates")

    # ----- image serving routes -----

    @bp.get("/folders/<folder>/<path:filename>")
    def serve_image(folder: str, filename: str) -> Any:
        data_dir = _data_dir()
        target_dir = _folder_path(folder, data_dir)
        if target_dir is None or not target_dir.exists() or not target_dir.is_dir():
            abort(404)
        return send_from_directory(target_dir, filename)

    @bp.get("/folders/<folder>/<path:filename>/thumb")
    def serve_thumbnail(folder: str, filename: str) -> Any:
        data_dir = _data_dir()
        target_dir = _folder_path(folder, data_dir)
        if target_dir is None or not target_dir.exists() or not target_dir.is_dir():
            abort(404)
        source_str = safe_join(str(target_dir), filename)
        if source_str is None:
            abort(404)
        source = Path(source_str)
        if not source.is_file() or source.suffix.lower() not in ALLOWED_SUFFIXES:
            abort(404)
        thumb = _thumb_path(data_dir, folder, filename, source)
        result = _ensure_thumbnail(source, thumb)
        if result is None:
            # Thumbnail generation failed, unreadable file (locked, or a
            # OneDrive/cloud "online-only" placeholder), or a format Pillow
            # won't decode. Try the original, but never 500 on a single bad
            # image: a thumbnail that won't load is fine, the gallery just
            # skips it.
            try:
                return send_from_directory(target_dir, filename)
            except OSError:
                abort(404)
        response = send_file(result, mimetype="image/jpeg", conditional=True)
        # The thumbnail URL embeds the source mtime, so the browser can
        # cache aggressively, a re-upload will hit a different URL.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    # ----- admin pages -----

    @bp.get("/")
    def index() -> str:
        data_dir = _data_dir()
        folders = []
        meta = _load_meta(data_dir)
        # Surface a root pseudo-folder when there are loose images.
        root_imgs = [
            p
            for p in (data_dir.iterdir() if data_dir.exists() else [])
            if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES
        ]
        if root_imgs:
            folders.append(
                {
                    "id": ROOT_FOLDER_VALUE,
                    "name": "(root)",
                    "image_count": len(root_imgs),
                    "external_path": None,
                }
            )
        for fname in _all_folder_names(data_dir):
            entry = meta.get(fname, {})
            folders.append(
                {
                    "id": fname,
                    "name": fname,
                    "image_count": len(_list_images(_folder_path(fname, data_dir))),
                    "external_path": entry.get("external_path"),
                }
            )
        return render_template("picture_gallery/index.html", folders=folders)

    @bp.post("/folders")
    def create_folder() -> Response:
        name = (request.form.get("name") or "").strip().lower()
        external_raw = (request.form.get("external_path") or "").strip()
        if not _FOLDER_NAME_RE.match(name):
            flash("Folder names must be lowercase letters, digits, hyphens or underscores.", "warn")
            return redirect(url_for("picture_gallery_admin.index"))
        data_dir = _data_dir()
        meta = _load_meta(data_dir)
        if name in meta or (data_dir / name).exists():
            flash(f"Folder '{name}' already exists.", "warn")
            return redirect(url_for("picture_gallery_admin.index"))
        if external_raw:
            ext = Path(external_raw).expanduser()
            try:
                ext_resolved = ext.resolve()
            except OSError:
                flash(f"Could not resolve external path: {external_raw}", "warn")
                return redirect(url_for("picture_gallery_admin.index"))
            if not ext_resolved.exists() or not ext_resolved.is_dir():
                flash(f"External path is not an existing directory: {ext_resolved}", "warn")
                return redirect(url_for("picture_gallery_admin.index"))
            meta[name] = {"label": name, "external_path": str(ext_resolved)}
            _save_meta(data_dir, meta)
            flash(f"Linked external folder '{name}' to {ext_resolved}.", "ok")
        else:
            (data_dir / name).mkdir(parents=True)
            meta[name] = {"label": name, "external_path": None}
            _save_meta(data_dir, meta)
            flash(f"Created folder '{name}'.", "ok")
        return redirect(url_for("picture_gallery_admin.show_folder", folder=name))

    @bp.get("/folders/<folder>")
    def show_folder(folder: str) -> str:
        data_dir = _data_dir()
        if folder != ROOT_FOLDER_VALUE and not _FOLDER_NAME_RE.match(folder):
            abort(404)
        target = _folder_path(folder, data_dir)
        if target is None:
            abort(404)
        images = _list_images(target)
        external = _is_external(folder, data_dir) if folder != ROOT_FOLDER_VALUE else False
        external_path = None
        if external:
            external_path = _load_meta(data_dir).get(folder, {}).get("external_path")
        album = _album_for_folder(folder)
        return render_template(
            "picture_gallery/folder.html",
            folder_id=folder,
            folder_name="(root)" if folder == ROOT_FOLDER_VALUE else folder,
            external=external,
            external_path=external_path,
            images=[{"name": p.name} for p in images],
            allowed_exts=sorted(ALLOWED_SUFFIXES),
            max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
            devices=_bindable_devices(),
            album=album,
        )

    @bp.post("/folders/<folder>/upload")
    def upload(folder: str) -> Response:
        data_dir = _data_dir()
        if folder != ROOT_FOLDER_VALUE and _is_external(folder, data_dir):
            flash("Uploads to external folders aren't allowed.", "warn")
            return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))
        target_dir = _folder_path(folder, data_dir)
        if target_dir is None or not target_dir.exists() or not target_dir.is_dir():
            abort(404)
        files = request.files.getlist("file")
        if not files:
            flash("No files in upload.", "warn")
            return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))
        saved, skipped = [], []
        for upload_file in files:
            if not upload_file or not upload_file.filename:
                continue
            safe = secure_filename(upload_file.filename)
            if not safe or Path(safe).suffix.lower() not in ALLOWED_SUFFIXES:
                skipped.append(upload_file.filename)
                continue
            blob = upload_file.read(MAX_UPLOAD_BYTES + 1)
            if len(blob) > MAX_UPLOAD_BYTES:
                skipped.append(f"{safe} (too large)")
                continue
            (target_dir / safe).write_bytes(blob)
            saved.append(safe)
        if saved:
            flash(f"Uploaded {len(saved)} image{'s' if len(saved) != 1 else ''}.", "ok")
        if skipped:
            flash(f"Skipped: {', '.join(skipped)}", "warn")
        return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))

    def _resolve_existing_image(folder: str, filename: str) -> Path | None:
        return resolve_image_path(folder, filename)

    @bp.post("/folders/<folder>/images/<path:filename>/send")
    def send_image(folder: str, filename: str) -> Response:
        """Hand off to the Send page with this image pre-loaded so the user
        can pick which displays + fit mode to push with (rather than a blind
        push to every renderer). Kept as a redirect for back-compat; the
        gallery tiles now link straight to /send."""
        if _resolve_existing_image(folder, filename) is None:
            abort(404)
        return redirect(url_for("send.index", tab="gallery", g_folder=folder, g_file=filename))

    @bp.post("/folders/<folder>/images/<path:filename>/delete")
    def delete_image(folder: str, filename: str) -> Response:
        if folder != ROOT_FOLDER_VALUE and _is_external(folder, _data_dir()):
            flash("Deletes for external folders aren't allowed.", "warn")
            return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))
        path = _resolve_existing_image(folder, filename)
        if path is None:
            abort(404)
        path.unlink()
        return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))

    @bp.post("/folders/<folder>/delete")
    def delete_folder(folder: str) -> Response:
        data_dir = _data_dir()
        if folder == ROOT_FOLDER_VALUE:
            flash("Cannot delete the root folder.", "warn")
            return redirect(url_for("picture_gallery_admin.index"))
        meta = _load_meta(data_dir)
        if folder in meta and meta[folder].get("external_path"):
            del meta[folder]
            _save_meta(data_dir, meta)
            flash(f"Unlinked external folder '{folder}' (host files untouched).", "ok")
            return redirect(url_for("picture_gallery_admin.index"))
        target = data_dir / folder
        if not target.exists() or not target.is_dir() or target == data_dir:
            if folder in meta:
                del meta[folder]
                _save_meta(data_dir, meta)
            flash(f"Folder '{folder}' was already gone.", "warn")
            return redirect(url_for("picture_gallery_admin.index"))
        shutil.rmtree(target, ignore_errors=True)
        if folder in meta:
            del meta[folder]
            _save_meta(data_dir, meta)
        flash(f"Deleted folder '{folder}'.", "ok")
        return redirect(url_for("picture_gallery_admin.index"))

    @bp.post("/folders/<folder>/use-as-album")
    def use_as_album(folder: str) -> Response:
        """Turn this folder into an offline photo album (#177): save an Album
        bound to the picked device(s) so a storage-capable panel caches the
        rendered frames and plays them back locally. Contract:
        docs/dev/frame-cache.md. Re-submitting updates the folder's album."""
        from pydantic import ValidationError

        from app.state.album_model import Album

        if folder != ROOT_FOLDER_VALUE and not _FOLDER_NAME_RE.match(folder):
            abort(404)
        store = current_app.config.get("ALBUM_STORE")
        if store is None:
            flash("Album store is unavailable.", "warn")
            return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))
        if not list_folder_files(folder):
            flash("This folder has no images to make an album from.", "warn")
            return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))

        default_name = "Root album" if folder == ROOT_FOLDER_VALUE else folder
        name = (request.form.get("name") or "").strip() or default_name
        # A submitted target the form disabled (an open tab from before the
        # display last reported, a hand-crafted POST) is dropped rather than
        # saved: binding an album a device can never receive looks like it
        # worked and then silently does nothing (#225).
        selectable = {d["id"] for d in _bindable_devices() if d["selectable"]}
        submitted = [d for d in request.form.getlist("device_ids") if d]
        device_ids = [d for d in submitted if d in selectable]
        refused = len(submitted) - len(device_ids)
        fit = request.form.get("fit") or "fill"
        mode = request.form.get("mode") or "sequential"
        repeat = request.form.get("repeat") or "loop"
        try:
            interval_min = int(request.form.get("interval_min") or 30)
        except ValueError:
            interval_min = 30
        interval_s = max(60, min(interval_min * 60, 86_400))

        prev = store.get(folder)
        # A save replaces the whole record, so fields this form doesn't edit
        # are carried across rather than left to fall back to the model
        # defaults. An album can also be authored elsewhere (MCP, and the
        # Companion API once it lands), and those surfaces can set an explicit
        # frame order, disable an album, or pick an interval that isn't a whole
        # number of minutes. Renaming a folder's album here shouldn't silently
        # drop any of that.
        order = list(prev.order) if prev is not None else []
        enabled = prev.enabled if prev is not None else True
        # The field can only show whole minutes, so an unchanged one means "as
        # it was", not "round it to what I can see".
        if prev is not None and interval_min == prev.playback.interval_s // 60:
            interval_s = prev.playback.interval_s
        try:
            album = Album.model_validate(
                {
                    "id": folder,
                    "name": name,
                    "enabled": enabled,
                    "device_ids": device_ids,
                    "source_folder": folder,
                    "order": order,
                    "fit": fit,
                    "playback": {"mode": mode, "interval_s": interval_s, "repeat": repeat},
                }
            )
        except ValidationError:
            flash("Could not save the album: invalid playback settings.", "warn")
            return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))
        # A display plays one cached collection at a time. Taking it off
        # whatever it's showing is a decision, so the form says which album
        # has it and lets the operator resubmit with "take over" rather than
        # quietly winning (discussion #230).
        from app.state.album_store import AlbumConflict

        try:
            store.upsert(album, replace=bool(request.form.get("replace_conflicts")))
        except AlbumConflict as conflict:
            names = {a.id: a.name for a in store.all()}
            listed = ", ".join(
                f"{did} is playing '{names.get(aid, aid)}'"
                for did, aid in sorted(conflict.claims.items())
            )
            flash(
                f"Not saved: {listed}. Tick 'Take over' to move those displays to this album.",
                "warn",
            )
            return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))

        # Drop cached frames for every affected device (newly bound, still
        # bound, or just unbound) so the next manifest fetch re-renders with the
        # current order / fit / membership rather than serving stale frames.
        push = current_app.config.get("PUSH_MANAGER")
        if push is not None:
            affected = set(device_ids) | set(prev.device_ids if prev is not None else [])
            for dev_id in affected:
                with contextlib.suppress(Exception):
                    push.clear_album_cache(dev_id)

        count = len(device_ids)
        flash(
            f"Saved album '{name}', bound to {count} device{'' if count == 1 else 's'}.",
            "ok",
        )
        if refused:
            flash(
                f"Skipped {refused} display{'' if refused == 1 else 's'} that reported "
                "no frame cache; an offline album can't play there.",
                "warn",
            )
        if not enabled:
            # Nothing here can disable an album, so this one was turned off
            # elsewhere. Saying so beats a success message about displays it
            # isn't playing on.
            flash(
                "This album is disabled, so it isn't playing on the displays it's bound to.",
                "warn",
            )
        return redirect(url_for("picture_gallery_admin.show_folder", folder=folder))

    return bp
