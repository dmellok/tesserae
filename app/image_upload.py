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
