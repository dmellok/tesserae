"""Show renderer output upright, without publishing it to a display."""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image

from app.quantizer import align_palette_override, canonicalise_gamut, palette_for_gamut
from app.renderer_loader import Renderer
from app.state.page_store import Panel


def render_panel_preview(
    source: bytes, *, renderer: Renderer, panel: Panel, settings: dict[str, Any]
) -> bytes:
    # Quantise in the real transport orientation. Error diffusion is directional:
    # removing the hardware rotation before transforming changes pixel decisions.
    artifact = renderer.transform(source, panel=panel, settings=settings)
    base_id = renderer.id.split("__", 1)[0]
    pi_family = base_id in ("pi_bin", "pico_bin", "pi_png")
    esp_family = base_id.startswith("esp32_") and renderer.extension == "bin"
    if pi_family:
        width, height = max(panel.w, panel.h), min(panel.w, panel.h)
        undo_rotation = -90 if panel.w < panel.h else 0
    else:
        width, height = (
            (panel.native_w or panel.w, panel.native_h or panel.h)
            if esp_family
            else (panel.declared_native or (panel.w, panel.h))
        )
        undo_rotation = 90 if (width > height) != (panel.w > panel.h) else 0
    if renderer.extension != "bin":
        with Image.open(io.BytesIO(artifact)) as image:
            preview = image.convert("RGB")
    else:
        pixels = width * height
        bits = len(artifact) * 8 // pixels
        if bits not in (1, 2, 4) or len(artifact) * 8 != pixels * bits:
            raise ValueError("Unsupported panel buffer size for preview")
        packed = np.frombuffer(artifact, dtype=np.uint8)
        shifts = np.arange(8 - bits, -1, -bits, dtype=np.uint8)
        values = ((packed[:, None] >> shifts) & ((1 << bits) - 1)).ravel()
        gamut = canonicalise_gamut(panel.gamut)
        if gamut.startswith("gray_"):
            # Eight-grey clients halve the sparse 4-bit wire values to 0..7.
            palette = [(round(index * 255 / ((1 << bits) - 1)),) * 3 for index in range(1 << bits)]
            colours = palette_for_gamut(gamut)
            anchors = settings.get("_gray_ramp")
            if anchors:
                from app.palette_profiles.schema import GrayRamp

                colours = (
                    GrayRamp(levels=tuple(str(v) for v in anchors)).as_tuples(len(colours))
                    or colours
                )
            for index, colour in enumerate(colours):
                wire = round(index * ((1 << bits) - 1) / (len(colours) - 1))
                palette[wire] = colour
        else:
            palette = [(0, 0, 0)] * (1 << bits)
            colours = palette_for_gamut(gamut)
            # A device with a Calibration-tab profile is shown in its measured inks, so the
            # preview looks like the glass (Spectra "white" is a pale grey-teal, not #FFF).
            override = settings.get("_palette_override")
            if override and len(override) >= len(colours):
                colours = align_palette_override(
                    tuple((int(c[0]), int(c[1]), int(c[2])) for c in override), colours
                )
            wire_values = (0, 1, 2, 3, 5, 6) if gamut == "waveshare_e6" else range(len(colours))
            for wire, colour in zip(wire_values, colours, strict=True):
                if wire < len(palette):
                    palette[wire] = colour
        rgb = np.asarray(palette, dtype=np.uint8)[values].reshape((height, width, 3))
        preview = Image.fromarray(rgb)
    if esp_family and base_id == "esp32_bin" and panel.vflip:
        preview = preview.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    # Hidden controller columns are not visible on the glass.
    if esp_family and base_id == "esp32_bin" and panel.col_offset is not None:
        visible_w, visible_h = (panel.h, panel.w) if undo_rotation else (panel.w, panel.h)
        if visible_w + panel.col_offset <= width and visible_h == height:
            preview = preview.crop((panel.col_offset, 0, panel.col_offset + visible_w, visible_h))
    if panel.flip:
        preview = preview.rotate(180)
    if undo_rotation:
        preview = preview.rotate(undo_rotation, expand=True)
    out = io.BytesIO()
    preview.save(out, "PNG")
    return out.getvalue()
