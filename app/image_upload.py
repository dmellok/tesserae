"""Shared limits and validation for an image pushed straight to a panel.

Two surfaces accept a photo and fan it out without a saved dashboard
behind it: the Companion API's ``POST /api/app/v1/images`` (a paired
client) and the webhook API's ``POST /api/v1/push/image`` (an operator's
script, one global token). They differ in auth and in what they answer
with, but they take the same bytes and must judge them the same way, so
the limits live here rather than in whichever route grew them first.

The caps are the ones the Companion contract already advertises through
its capability probe, which means a client reading the advertised
``limits`` and a script reading the OpenAPI spec are told the same
numbers.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger(__name__)

# Register the HEIF/HEIC opener so Pillow can decode iPhone photos, both
# for the edge/size validation here and downstream in the renderer.
# pillow-heif is a hard dependency (pyproject.toml); the import is
# guarded only so a broken wheel degrades to "HEIC unsupported" rather
# than failing app import.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    logger.warning("pillow-heif unavailable; HEIC/HEIF image uploads will not decode")


IMAGE_UPLOAD_BYTES = 26_214_400  # 25 MiB
IMAGE_MAX_EDGE = 8192
IMAGE_CONTENT_TYPES: tuple[str, ...] = (
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "image/webp",
)
IMAGE_FIT_MODES: tuple[str, ...] = ("fit", "fill", "blur", "stretch", "center")
# Turns a caller can ask for, clockwise degrees plus ``auto``. ``auto``
# turns the image a quarter when its aspect is the opposite of the
# panel's, so a portrait photo lands filling a landscape panel rather
# than as a letterboxed strip. Opt-in rather than the default: an image
# carrying text (a poster, a rendered chart) is worse rotated than
# letterboxed, and the server can't tell the two apart.
IMAGE_ROTATE_MODES: tuple[str, ...] = ("auto", "0", "90", "180", "270")

# EXIF tag 0x0112. Phone cameras store a landscape pixel buffer plus this
# tag rather than rotating the pixels; Pillow doesn't apply it on open().
_ORIENTATION_TAG = 274

# Error codes the callers map onto their own envelopes. Named rather than
# returned as prose so both surfaces can answer in their own vocabulary
# while agreeing on what went wrong.
UNSUPPORTED = "unsupported_image"
TOO_LARGE = "image_too_large"


def decode_edge(image_bytes: bytes) -> int | None:
    """Longest edge of the decoded image, or None if it can't be decoded."""
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            return max(int(img.width), int(img.height))
    except Exception:
        return None


def _quarters(
    rotate: str | None,
    *,
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
) -> int:
    """Clockwise quarter-turns for a ``rotate`` mode, 0 when there is none.

    ``auto`` compares the (already EXIF-normalized) image aspect with the
    panel's: opposite aspects get one turn, matching or square aspects get
    none. Unknown values are treated as "no turn", the routes validate the
    vocabulary and answer 400 before reaching here."""
    mode = (rotate or "").strip().lower()
    if mode == "auto":
        if min(src_w, src_h, target_w, target_h) <= 0:
            return 0
        return 1 if (src_w > src_h) != (target_w > target_h) else 0
    return {"90": 1, "180": 2, "270": 3}.get(mode, 0)


def orient_for_panel(
    image_bytes: bytes,
    *,
    rotate: str | None = None,
    target_w: int = 0,
    target_h: int = 0,
) -> bytes:
    """Normalize EXIF orientation, then apply the caller's turn.

    Both steps happen once, on ingest, rather than inside each renderer's
    fit. ``fit_to_panel`` already EXIF-transposes, but the renderers that
    fit client-side (``pi_png`` hands its panel the composition plus a
    ``scale`` field) never call it, so a phone photo reached those panels
    on its side: the pixel buffer is landscape and the orientation tag
    that makes it portrait was dropped on re-encode (discussion #231).
    Doing it here means every renderer sees upright pixels, and the
    History thumbnail matches what the panel paints.

    Returns the input bytes untouched when there is nothing to apply, so
    a dashboard composition (no EXIF, no requested turn) doesn't pay a
    decode + re-encode on its way to the panel."""
    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            tag = int(img.getexif().get(_ORIENTATION_TAG, 1) or 1)
            oriented = ImageOps.exif_transpose(img) or img
            quarters = _quarters(
                rotate,
                src_w=oriented.width,
                src_h=oriented.height,
                target_w=target_w,
                target_h=target_h,
            )
            if quarters == 0 and tag in (0, 1):
                return image_bytes
            if quarters:
                # PIL rotates counter-clockwise; negate for clockwise.
                oriented = oriented.rotate(-90 * quarters, expand=True)
            out = BytesIO()
            oriented.convert("RGB").save(out, format="PNG")
            return out.getvalue()
    except Exception:
        # Undecodable bytes are the validator's problem, not this one's.
        # Passing them through keeps the failure where it's diagnosed.
        logger.warning("could not orient uploaded image; passing bytes through", exc_info=True)
        return image_bytes


def validate_image(image_bytes: bytes, content_type: str | None) -> str | None:
    """``None`` when the image is acceptable, else :data:`UNSUPPORTED` or
    :data:`TOO_LARGE`.

    Declared media type first (cheap, and rejects an obvious mismatch
    without decoding), then the encoded size, then a real decode for the
    edge check. The decode is what catches a file that claims to be a
    JPEG and isn't, so it runs even when the declared type is fine."""
    if (content_type or "").lower() not in IMAGE_CONTENT_TYPES:
        return UNSUPPORTED
    if len(image_bytes) > IMAGE_UPLOAD_BYTES:
        return TOO_LARGE
    edge = decode_edge(image_bytes)
    if edge is None:
        return UNSUPPORTED
    if edge > IMAGE_MAX_EDGE:
        return TOO_LARGE
    return None
