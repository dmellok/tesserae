"""Mode-agnostic dither region map (issue #86).

Dithering is applied once, globally, to the whole composition at pack time
(per device gamut + calibration). On rich panels a flat-colour UI cell can
read cleaner mapped straight to the palette with nearest-colour and no error
diffusion. But on low-palette panels (1-bit mono, 6-colour Spectra) dither
is what renders anti-aliased text and subtle shading as apparent tone, so
flattening there loses detail. Flat is therefore strictly opt-in per cell
(the editor's Advanced pane), never inferred from a widget manifest; the
default keeps the frame's dither everywhere so no panel regresses.

This module turns the per-cell opt-outs into a list of positioned rectangles
plus a rasteriser the ``.bin`` packers consume as a per-pixel "force nearest
here" mask.

The list-of-rects contract is composition-mode agnostic on purpose. Grid
mode feeds it one rect per cell (:func:`regions_from_page`); a future
freeform canvas mode (issue #60) would feed it one rect per placed element,
resolved the same way, painter's order, the topmost rect wins on overlap.
The packer never learns which mode produced the mask, so the two modes stay
compatible: canvas mode is just a second producer of the same rect list.

A "region" is a plain dict ``{x, y, w, h, nearest}`` (pixel coords in the
composition's own orientation). Kept as dicts, not a dataclass, so the list
threads cleanly through the push pipeline's JSON render-signature without a
custom encoder.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw

if TYPE_CHECKING:
    from app.state.page_store import Page

# Value written into the "L" mask where a region wants nearest-colour. 0 is
# "use the frame's dither" (the pre-#86 behaviour). The packer thresholds at
# 128, so only these two extremes are ever painted.
_NEAREST = 255
_FRAME = 0


def regions_from_page(page: Page) -> list[dict[str, Any]]:
    """Per-cell dither regions for a dashboard page, in z-order (cell order).

    Each region is ``{x, y, w, h, nearest}``. ``nearest`` is True only when
    the cell's ``dither`` override is ``"none"`` (the editor's Advanced pane,
    "Flat colour"). Everything else, no override, ``"auto"``, or any cell
    with no explicit opt-out, keeps the frame's dither.

    Dithering is the default on purpose. On low-palette panels (1-bit mono,
    6-colour Spectra) error diffusion is what renders anti-aliased text and
    subtle shading as apparent tone; flattening a cell to nearest-colour
    there throws that detail away. So flat is strictly opt-in per cell, never
    inferred from the widget's manifest, a widget that looks "flat" still
    has anti-aliased edges that want dithering on those panels.

    Cells with no plugin (layout placeholders) are skipped. Coordinates are
    the cell's panel pixels, the composition's own orientation, the .bin
    renderer applies the same geometric transforms to the rasterised mask as
    it does to the image (see :func:`transform_mask`)."""
    regions: list[dict[str, Any]] = []
    for cell in page.cells:
        if not cell.plugin:
            continue
        regions.append(
            {
                "x": int(cell.x),
                "y": int(cell.y),
                "w": int(cell.w),
                "h": int(cell.h),
                "nearest": cell.dither == "none",
            }
        )
    return regions


# Renderers outside the ``.bin`` family that also quantise on the server and
# apply the region mask. The TRMNL PNG renderers and ``kaleido_png`` quantise
# on the server too (each calls the quantiser itself before it encodes the
# PNG), so a Flat cell can be honoured there as well (#601).
_INDEXED_REGION_RENDERERS = frozenset(
    {
        "circuitpython_png",
        "circuitpython_bmp",
        "trmnl_png",
        "trmnl_png_color",
        "trmnl_png_gray16",
        "trmnl_png_gray4",
        "kaleido_png",
    }
)


def renderer_takes_regions(renderer_id: str, extension: str) -> bool:
    """True when the renderer quantises on the server and so can honour a
    cell's Flat override. ``renderer_id`` may carry a ``__<device>`` suffix
    (per-device renderer instances); only the base id is compared."""
    return extension == "bin" or renderer_id.split("__", 1)[0] in _INDEXED_REGION_RENDERERS


def has_nearest_region(regions: list[dict[str, Any]]) -> bool:
    """True when at least one region forces nearest-colour, i.e. the mask
    would actually change the packed output. When False, callers skip the
    mask entirely and the frame quantises byte-identically to before, so
    existing all-diffuse dashboards pay nothing."""
    return any(bool(r.get("nearest")) for r in regions)


def rasterize_region_mask(regions: list[dict[str, Any]], width: int, height: int) -> Image.Image:
    """Paint ``regions`` into an ``"L"`` mask at ``(width, height)``: 255
    where the frame should snap to nearest-colour, 0 where it should keep the
    frame's dither.

    Regions paint in list order, so a later (higher-z) rectangle wins on
    overlap, including a diffuse rectangle laid over a nearest one, which
    clears the nearest flag back to frame-dither in the overlap. That
    painter's-order rule is what keeps grid mode and a future canvas mode
    (issue #60) consistent, even when canvas elements overlap."""
    mask = Image.new("L", (width, height), _FRAME)
    draw = ImageDraw.Draw(mask)
    for r in regions:
        w = int(r.get("w", 0))
        h = int(r.get("h", 0))
        if w <= 0 or h <= 0:
            continue
        x = int(r.get("x", 0))
        y = int(r.get("y", 0))
        fill = _NEAREST if r.get("nearest") else _FRAME
        # rectangle() is inclusive of both corners, so subtract 1 to land a
        # w-by-h box rather than (w+1)-by-(h+1).
        draw.rectangle((x, y, x + w - 1, y + h - 1), fill=fill)
    return mask


def transform_mask(
    mask: Image.Image,
    *,
    native_w: int,
    native_h: int,
    rotate: int = 0,
    flip: bool = False,
    underscan: int = 0,
    vflip: bool = False,
    col_offset: int | None = None,
) -> Image.Image:
    """Apply a ``.bin`` renderer's geometric pipeline to a dither mask so it
    stays pixel-aligned with the packed image.

    The .bin renderers all transform in the same fixed order, rotate ->
    180° flip -> fit to native dims -> underscan inset -> row vflip, they
    just differ in which steps fire (pi_bin pre-rotates 90° CCW for portrait
    and never vflips; the esp32 family rotates 90° CW on an orientation
    mismatch and vflips bottom-scanning panels). The caller passes the same
    values it computed for the image; this runs them on the mask.

    ``rotate`` is degrees counter-clockwise (PIL convention: pi_bin passes
    90, the esp32 family passes -90). Mask-safe throughout: stays mode
    ``"L"``, resamples nearest-neighbour (a mask must keep hard edges), and
    fills the underscan border with 255, the matting sits under a bezel and
    is flat colour, so snapping it to nearest is correct and cheap."""
    m = mask
    if rotate:
        m = m.rotate(rotate, expand=True)
    if flip:
        m = m.rotate(180, expand=True)
    if m.size != (native_w, native_h):
        if col_offset is not None and m.width + col_offset <= native_w and m.height == native_h:
            # Hidden-column panel: the image was pasted into a white
            # native-stride canvas at ``col_offset``, mirror that here
            # instead of stretching the mask over columns the image never
            # moved to. The padding is flat white, so nearest-snapping it
            # (same as the underscan border) is correct and cheap.
            canvas = Image.new("L", (native_w, native_h), _NEAREST)
            canvas.paste(m, (col_offset, 0))
            m = canvas
        else:
            m = m.resize((native_w, native_h), Image.Resampling.NEAREST)
    if underscan > 0:
        inner_w = native_w - 2 * underscan
        inner_h = native_h - 2 * underscan
        if inner_w > 0 and inner_h > 0:
            inner = m.resize((inner_w, inner_h), Image.Resampling.NEAREST)
            canvas = Image.new("L", (native_w, native_h), _NEAREST)
            canvas.paste(inner, (underscan, underscan))
            m = canvas
    if vflip:
        m = m.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return m


def buffer_flat_mask(
    settings: dict[str, Any],
    *,
    comp_w: int,
    comp_h: int,
    buffer_w: int,
    buffer_h: int,
    flip: bool,
    underscan: int,
) -> Image.Image | None:
    """The Flat-cell mask on the buffer a PNG renderer paints, or None.

    Follows :func:`app.quantizer.orient_onto_buffer` and ``underscan_image``,
    the geometry every TRMNL PNG renderer and ``kaleido_png`` applies: a 90°
    clockwise turn when the composition aspect disagrees with the buffer's,
    180° more for ``flip``, a stretch to the buffer dims, then the underscan
    inset. None when no region forces nearest colour, so a frame without a
    Flat cell is quantised exactly as before."""
    regions = settings.get("_dither_regions")
    if not isinstance(regions, list) or not has_nearest_region(regions):
        return None
    mask = rasterize_region_mask(regions, comp_w, comp_h)
    rotate = -90 if (buffer_w > buffer_h) != (comp_w > comp_h) else 0
    return transform_mask(
        mask,
        native_w=buffer_w,
        native_h=buffer_h,
        rotate=rotate,
        flip=flip,
        underscan=int(underscan or 0),
    )


def paste_flat(dithered: Image.Image, flat: Image.Image, mask: Image.Image | None) -> Image.Image:
    """``dithered`` with ``flat`` (the same frame quantised with no dither)
    inside ``mask``. Both images come from one quantiser and palette, so they
    share a mode and, for a palette image, the palette indices."""
    if mask is None:
        return dithered
    out = dithered.copy()
    out.paste(flat, (0, 0), mask)
    return out
