"""Companion Gallery adapter over the Picture Gallery plugin's storage.

The phone is where photos originate, so the Companion API (#225) needs to
browse the server's photo library, create a folder, add to it, and hand an
image back into the existing Send flow. It does that over the same files
the Picture Gallery admin pages use, without going anywhere near their
routes: those are browser forms with redirects, flash messages, and
filename-as-identity, and they change shape as the plugin does.

Two rules shape everything here.

**Identity is opaque and never a path.** A folder or image id is an
encoded reference the server mints and the client only ever echoes back.
A client cannot construct one from a name, cannot learn where an external
folder lives on the host, and cannot walk out of the gallery with a
crafted id: every decode is re-validated against the plugin's own path
rules before it touches disk.

**Normalisation is the server's job.** An uploaded photo is decoded,
re-encoded from raw pixels, and published: EXIF (all of it, GPS
included) is gone because the stored file is built from pixel data with
no metadata to carry, source orientation is baked into those pixels
rather than left as a tag for renderers to disagree over, and the ICC
profile is re-attached deliberately. The client sends a photo; what
lands is the server's file.

Content types: the contract's Gallery enum covers JPEG, PNG, HEIC/HEIF
and WebP. The plugin also accepts GIF and BMP, which it has served
through the Web UI since it was ported, so this surface serves those as
a cached PNG rendition rather than either hiding them or labelling them
with a media type the client can't decode. See
``notes/companion-gallery.md``.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import current_app, url_for
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

# -- advertised limits ---------------------------------------------------
#
# Server-advertised so the client never hard-codes them. The byte cap
# matches the plugin's own upload ceiling; the batch size is advice about
# how many Photos selections to queue at once, not a request shape (every
# request carries exactly one image).
GALLERY_UPLOAD_BYTES = 20_971_520  # 20 MiB
GALLERY_UPLOAD_BATCH_SIZE = 20
GALLERY_IMAGE_CONTENT_TYPES: tuple[str, ...] = (
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "image/webp",
)

# Stored suffix -> the media type served straight from the stored file.
_NATIVE_CONTENT_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# Stored suffixes the Gallery reads but the contract's media types don't
# cover. Served as a cached PNG rendition rather than hidden: the Web UI
# has always accepted both, and a folder of pre-dithered BMPs reading as
# empty over the API would be a worse answer than a converted copy.
_RENDITION_SUFFIXES: frozenset[str] = frozenset({".bmp", ".gif"})
_RENDITION_CONTENT_TYPE = "image/png"

# Decoded format -> (stored suffix, media type). HEIC/HEIF is transcoded
# to JPEG on the way in: nothing else in Tesserae reads HEIF, so leaving
# it in its source format would put a file in the Gallery that the
# widgets, renderers and thumbnailer all skip.
_TARGET_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": (".jpg", "image/jpeg"),
    "MPO": (".jpg", "image/jpeg"),
    "HEIF": (".jpg", "image/jpeg"),
    "HEIC": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}

# Longest edge accepted, matching the Companion image-push ceiling.
GALLERY_MAX_EDGE = 8192

_FOLDER_PREFIX = "fld_"
_IMAGE_PREFIX = "img_"
_STEM_RE = re.compile(r"[^a-z0-9]+")


class GalleryUnavailable(RuntimeError):
    """The Picture Gallery plugin isn't installed or didn't load."""


def gallery_module() -> Any:
    """The plugin's ``server.py``, or raise. Resolved per call rather than
    imported: the plugin registry is built at app start and a plugin can
    be removed from disk between runs."""
    registry = current_app.config.get("PLUGIN_REGISTRY")
    plugin = registry.get("picture_gallery") if registry is not None else None
    module = getattr(plugin, "server_module", None) if plugin is not None else None
    if module is None:
        raise GalleryUnavailable("picture_gallery plugin is not available")
    return module


def gallery_available() -> bool:
    """Whether the Gallery surface can be advertised at all."""
    try:
        gallery_module()
    except (GalleryUnavailable, KeyError, RuntimeError):
        return False
    return True


# -- opaque identifiers --------------------------------------------------


def encode_opaque(prefix: str, raw: str) -> str:
    """Mint one opaque id. Public because the Offline Album adapter mints its
    own ids over the same scheme, and two encoders that have to agree are
    better as one function than as a copied five lines."""
    packed = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    return prefix + packed.rstrip("=")


def decode_opaque(prefix: str, value: str) -> str | None:
    if not value.startswith(prefix):
        return None
    body = value[len(prefix) :]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        return raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def folder_id(folder: str) -> str:
    return encode_opaque(_FOLDER_PREFIX, folder)


def folder_from_id(value: str) -> str | None:
    """The plugin folder name an id refers to, or None. Re-validated
    against the plugin rather than trusted: a decoded id is client input,
    not a capability."""
    name = decode_opaque(_FOLDER_PREFIX, value)
    if name is None:
        return None
    try:
        gallery = gallery_module()
    except GalleryUnavailable:
        return None
    return name if gallery.folder_exists(name) else None


def image_id(folder: str, filename: str) -> str:
    return encode_opaque(_IMAGE_PREFIX, f"{folder}\n{filename}")


def image_ref_from_id(value: str) -> tuple[str, str] | None:
    """``(folder, filename)`` an image id names, without checking that the
    image still exists.

    Separate from :func:`image_from_id` because the Offline Album order has
    to tell "this id belongs to a different folder", which is the client
    sending something it never should have, apart from "this image was
    deleted while the form was open", which is ordinary and gets dropped
    (discussion #230)."""
    raw = decode_opaque(_IMAGE_PREFIX, value)
    if raw is None or "\n" not in raw:
        return None
    folder, filename = raw.split("\n", 1)
    if not folder or not filename:
        return None
    return folder, filename


def image_from_id(value: str) -> tuple[str, str] | None:
    """``(folder, filename)`` for an image id, or None when it doesn't
    decode or doesn't resolve to a readable image."""
    ref = image_ref_from_id(value)
    if ref is None:
        return None
    folder, filename = ref
    try:
        gallery = gallery_module()
    except GalleryUnavailable:
        return None
    if gallery.resolve_image_path(folder, filename) is None:
        return None
    return folder, filename


# -- read model ----------------------------------------------------------


def _listable(gallery: Any, folder: str) -> list[str]:
    """A folder's filenames this surface can serve, natively or as a
    rendition. Everything the plugin will read, in other words."""
    return [
        name
        for name in gallery.list_folder_files(folder)
        if Path(name).suffix.lower() in _NATIVE_CONTENT_TYPES
        or Path(name).suffix.lower() in _RENDITION_SUFFIXES
    ]


@dataclass(frozen=True)
class ServedImage:
    """The bytes this surface hands back for one stored image.

    ``path`` is the stored file for a format the contract defines, and a
    cached PNG rendition of it otherwise. ``name`` follows: a client that
    saves what it downloaded ends up with a file whose extension matches
    its contents."""

    path: Path
    content_type: str
    name: str
    is_rendition: bool


def served_image(folder: str, filename: str) -> ServedImage | None:
    """What to serve for one stored image, or None when it can't be read.

    BMP and GIF are the case this exists for. The Gallery has accepted
    both since it was ported (#117: pre-dithered artwork arrives as BMP)
    and the Web UI serves them unchanged, but the Companion contract's
    media types cover neither. Skipping them would make a folder of
    pre-dithered BMPs read as empty over the API, so they're rendered to
    PNG on first ask instead, cached beside the thumbnails, with the
    stored file untouched."""
    gallery = gallery_module()
    source = gallery.resolve_image_path(folder, filename)
    if source is None:
        return None
    suffix = source.suffix.lower()
    native = _NATIVE_CONTENT_TYPES.get(suffix)
    if native is not None:
        return ServedImage(path=source, content_type=native, name=filename, is_rendition=False)
    if suffix not in _RENDITION_SUFFIXES:
        return None
    rendition = gallery.rendition_path(folder, filename)
    if rendition is None:
        return None
    return ServedImage(
        path=Path(rendition),
        content_type=_RENDITION_CONTENT_TYPE,
        name=f"{Path(filename).stem}.png",
        is_rendition=True,
    )


def etag_for(path: Path) -> str:
    """Cache validator for an image's content and its derived thumbnail.

    Derived from the file's identity rather than a content hash: a folder
    listing would otherwise read every byte of every photo to answer, and
    the thumbnail cache is already keyed on the same mtime."""
    try:
        stat = path.stat()
        raw = f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        raw = path.name
    return '"' + hashlib.blake2b(raw.encode("utf-8"), digest_size=12).hexdigest() + '"'


def _created_at(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return None


def image_view(folder: str, filename: str) -> dict[str, Any] | None:
    """One image as the contract's ``GalleryImage``, or None when it has
    gone missing or can't be decoded for its dimensions.

    Every field describes what a client will actually receive, so for a
    rendition the type, size and validator are the rendition's, not the
    stored file's. ``created_at`` stays the stored file's, since that is
    when the photo entered the Gallery; a cache rebuild is not a new
    import."""
    served = served_image(folder, filename)
    if served is None:
        return None
    try:
        with Image.open(served.path) as img:
            width, height = ImageOps.exif_transpose(img).size
        size_bytes = served.path.stat().st_size
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    source = gallery_module().resolve_image_path(folder, filename)
    ident = image_id(folder, filename)
    return {
        "id": ident,
        "folder_id": folder_id(folder),
        "name": served.name,
        "content_type": served.content_type,
        "bytes": max(int(size_bytes), 1),
        "width": int(width),
        "height": int(height),
        "etag": etag_for(served.path),
        "thumbnail_url": url_for("companion_api.gallery_image_thumbnail", image_id=ident),
        "content_url": url_for("companion_api.gallery_image_content", image_id=ident),
        "created_at": _created_at(source if source is not None else served.path),
    }


def folder_view(folder: str) -> dict[str, Any]:
    """One folder as the contract's ``GalleryFolder``.

    ``writable`` is computed, never inferred from ``kind`` by the client:
    external folders are read-only today because the plugin refuses to
    write into a host directory it was merely pointed at, and that's a
    policy that can change without the client's model of it changing."""
    gallery = gallery_module()
    external = gallery.folder_is_external(folder)
    names = _listable(gallery, folder)
    cover: str | None = None
    if names:
        cover = url_for(
            "companion_api.gallery_image_thumbnail", image_id=image_id(folder, names[0])
        )
    return {
        "id": folder_id(folder),
        "name": gallery.folder_label(folder)[:80],
        "kind": "external" if external else "internal",
        "writable": not external,
        "image_count": len(names),
        "cover_thumbnail_url": cover,
    }


def list_folders() -> list[dict[str, Any]]:
    gallery = gallery_module()
    return [folder_view(name) for name in gallery.folder_names()]


def folder_detail(folder: str) -> dict[str, Any]:
    """One folder and its images.

    The count is corrected here to the images actually returned. The
    listing endpoint takes it from the directory, because opening every
    image in every folder to answer would be a slow list, so a file that
    turns out to be truncated or corrupt is counted there and dropped
    here. Opening the folder is what resolves the difference."""
    gallery = gallery_module()
    views = [image_view(folder, name) for name in _listable(gallery, folder)]
    images = [view for view in views if view is not None]
    view = folder_view(folder)
    view["image_count"] = len(images)
    return {"folder": view, "images": images}


# -- upload --------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedImage:
    """An uploaded photo after the server has taken ownership of it."""

    data: bytes
    suffix: str
    content_type: str
    width: int
    height: int


def _strip_metadata(image: Image.Image) -> Image.Image:
    """A pixel-identical copy with no metadata attached.

    Rebuilt from raw bytes rather than filtered key by key. Pillow's
    savers read several metadata blocks straight off ``Image.info`` when
    the caller doesn't override them (WebP picks up ``exif`` and ``xmp``
    that way), so removing the known keys is a list that goes stale
    against a Pillow upgrade. An image with nothing in ``info`` cannot
    leak a block nobody thought to pop."""
    mode = image.mode
    if mode == "P":
        mode = "RGBA" if "transparency" in image.info else "RGB"
    elif mode not in {"RGB", "RGBA", "L", "LA"}:
        mode = "RGB"
    converted = image.convert(mode) if image.mode != mode else image
    return Image.frombytes(mode, converted.size, converted.tobytes())


def _flatten(image: Image.Image) -> Image.Image:
    """Composite alpha onto white for a format that can't carry it."""
    if image.mode not in {"RGBA", "LA"}:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    canvas = Image.new("RGB", rgba.size, (255, 255, 255))
    canvas.paste(rgba, mask=rgba.split()[-1])
    return canvas


def normalize_upload(blob: bytes) -> NormalizedImage | str:
    """Decode, strip, orient and re-encode an upload.

    Returns the normalized image, or an error code (``unsupported_image``
    / ``image_too_large``) for the caller to map onto the contract's
    error envelope."""
    if len(blob) > GALLERY_UPLOAD_BYTES:
        return "image_too_large"
    try:
        with Image.open(io.BytesIO(blob)) as src:
            src.load()
            source_format = (src.format or "").upper()
            target = _TARGET_FORMATS.get(source_format)
            if target is None:
                return "unsupported_image"
            suffix, content_type = target
            # Bake the source orientation into the pixels. A stored tag is
            # something each renderer, thumbnailer and firmware path gets
            # to interpret differently; a rotated buffer is not.
            oriented = ImageOps.exif_transpose(src) or src
            icc = oriented.info.get("icc_profile")
            clean = _strip_metadata(oriented)
    except Image.DecompressionBombError:
        return "image_too_large"
    except (OSError, ValueError, TypeError, SyntaxError):
        return "unsupported_image"

    if max(clean.size) > GALLERY_MAX_EDGE:
        return "image_too_large"

    out = io.BytesIO()
    save_args: dict[str, Any] = {}
    if icc:
        save_args["icc_profile"] = icc
    try:
        if content_type == "image/jpeg":
            _flatten(clean).save(out, format="JPEG", quality=90, optimize=True, **save_args)
        elif content_type == "image/png":
            clean.save(out, format="PNG", optimize=True, **save_args)
        else:
            clean.save(out, format="WEBP", quality=90, method=4, exif=b"", **save_args)
    except (OSError, ValueError) as exc:
        logger.warning("gallery upload re-encode failed: %s", exc)
        return "unsupported_image"

    return NormalizedImage(
        data=out.getvalue(),
        suffix=suffix,
        content_type=content_type,
        width=int(clean.size[0]),
        height=int(clean.size[1]),
    )


def stored_filename(original: str | None, image: NormalizedImage) -> str:
    """The filename the server picks for an upload.

    Keeps a readable trace of what the operator sent, so the Web UI shows
    ``beach-3f2a1c9d0e77.jpg`` rather than an opaque token, and appends a
    digest of the stored bytes: the same photo retried after a dropped
    connection resolves to the same path instead of accumulating
    ``beach (1).jpg``."""
    stem = secure_filename(Path(original or "").stem).lower()
    stem = _STEM_RE.sub("-", stem).strip("-")[:48] or "photo"
    digest = hashlib.sha256(image.data).hexdigest()[:12]
    return f"{stem}-{digest}{image.suffix}"


def publish_image(folder: str, filename: str, image: NormalizedImage) -> bool:
    """Write an upload into a folder, atomically. False when the folder
    doesn't accept writes or the write failed.

    Idempotent by construction: the filename carries a digest of these
    exact bytes, so a repeat writes the file that's already there."""
    gallery = gallery_module()
    target_dir = gallery.image_target_dir(folder)
    if target_dir is None:
        return False
    destination = target_dir / filename
    if destination.exists():
        return True
    tmp = destination.with_name(destination.name + ".part")
    try:
        tmp.write_bytes(image.data)
        tmp.replace(destination)
    except OSError as exc:
        logger.warning("gallery upload could not be stored: %s", exc)
        tmp.unlink(missing_ok=True)
        return False
    return True
