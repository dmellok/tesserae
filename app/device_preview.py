"""Logical, device-specific previews for Companion display cards.

Renderer artifacts are device wire formats.  Some are viewable PNG/BMP files,
while others are packed binary buffers whose orientation includes native row
stride and mount compensation.  Companion previews instead represent the
logical on-wall view: the selected image-fit mode at ``panel.w x panel.h``,
with logical underscan, but without exposing hardware-only rotation/flip.

The generated PNG is content-addressed and retained beside renderer artifacts.
"""

from __future__ import annotations

import hashlib
import io
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from app.quantizer import fit_to_panel, underscan_image
from app.state.page_store import Panel

_DIGEST_RE = re.compile(r"^[0-9a-f]{16}$")
_IMAGE_FIT_MODES = frozenset(("fit", "fill", "blur", "stretch", "center"))
_BACKGROUND_COLOURS = frozenset(("white", "black", "red", "green", "blue", "yellow", "orange"))


@dataclass(frozen=True)
class RetainedDevicePreview:
    path: Path
    etag: str


def build_device_preview_png(
    composition_png: bytes,
    *,
    panel: Panel,
    settings: dict[str, Any],
) -> bytes:
    """Return an upright logical-screen PNG for one device render.

    ``composition_png`` is the same source the renderer receives, including
    any server-side low-battery overlay.  Fit and underscan intentionally run
    in logical panel coordinates.  Physical mount compensation (``flip`` /
    ``vflip``) and firmware-native row-stride rotation belong only in the wire
    artifact and must not make the phone preview sideways or upside down.
    """

    with Image.open(io.BytesIO(composition_png)) as source:
        image = source.convert("RGB")

    fit_value = settings.get("image_fit", settings.get("scale", "fit"))
    fit = fit_value if isinstance(fit_value, str) and fit_value in _IMAGE_FIT_MODES else "fit"
    bg_value = settings.get("bg", "white")
    background = (
        bg_value if isinstance(bg_value, str) and bg_value in _BACKGROUND_COLOURS else "white"
    )
    if image.size != (panel.w, panel.h):
        image = fit_to_panel(
            image,
            target_w=panel.w,
            target_h=panel.h,
            scale=fit,
            bg=background,
        )
    if panel.underscan:
        image = underscan_image(image, underscan=panel.underscan)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def write_device_preview(
    renders_dir: Path,
    composition_png: bytes,
    *,
    panel: Panel,
    settings: dict[str, Any],
) -> str:
    """Atomically retain a logical preview and return its content digest."""

    preview_png = build_device_preview_png(composition_png, panel=panel, settings=settings)
    digest = hashlib.sha256(preview_png).hexdigest()[:16]
    renders_dir.mkdir(parents=True, exist_ok=True)
    destination = renders_dir / f"{digest}.png"
    if destination.exists():
        destination.touch()
        return digest

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=renders_dir,
            prefix=f".{digest}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(preview_png)
            temporary = Path(handle.name)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return digest


def retained_device_preview(
    renders_dir: Path,
    digest: object,
) -> RetainedDevicePreview | None:
    """Safely resolve a retained ``<digest>.png`` without symlink escape."""

    if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
        return None

    root = renders_dir.resolve()
    candidate = root / f"{digest}.png"
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return RetainedDevicePreview(path=resolved, etag=digest)
