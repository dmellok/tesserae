"""Mode-agnostic dither region map (issue #86).

Dithering is the default on every panel; flattening a cell to nearest-colour
is opt-in via the cell's ``dither`` override ("none"). The producer collects
those opt-outs into positioned rectangles; the rasteriser paints them into a
nearest-colour mask in painter's order (so a future canvas mode with
overlapping elements resolves the same way as today's non-overlapping grid);
the transform keeps the mask aligned with the image through the .bin
renderers' geometric pipeline.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from PIL import Image

from app.dither_regions import (
    has_nearest_region,
    rasterize_region_mask,
    regions_from_page,
    transform_mask,
)
from app.main import REPO_ROOT
from app.state.page_store import Cell, Page, Panel


def _cell(
    cid: str,
    plugin: str | None,
    x: int,
    y: int,
    w: int,
    h: int,
    dither: str | None = None,
) -> Cell:
    return Cell(id=cid, plugin=plugin, x=x, y=y, w=w, h=h, dither=dither)


def test_only_flat_override_marks_a_cell_nearest() -> None:
    """Nearest is opt-in per cell: only ``dither == "none"`` flattens. No
    override (the default) and an explicit ``"auto"`` both keep the frame's
    dither, so nothing regresses on low-palette panels unless the user asks."""
    page = Page(
        id="p",
        name="P",
        cells=[
            _cell("a", "weather_now", 0, 0, 100, 100, dither="none"),  # flat opt-in
            _cell("b", "weather_now", 100, 0, 100, 100, dither="auto"),  # explicit dither
            _cell("c", "picture_apod", 200, 0, 100, 100),  # default -> dither
            _cell("d", None, 300, 0, 50, 50),  # placeholder, skipped
        ],
    )
    regions = regions_from_page(page)
    assert [r["nearest"] for r in regions] == [True, False, False]
    assert regions[0]["x"] == 0 and regions[0]["w"] == 100
    assert has_nearest_region(regions) is True


def test_page_with_no_overrides_has_no_nearest_regions() -> None:
    """A page where no cell opts out packs byte-identically to before, so the
    push side skips the mask entirely."""
    page = Page(
        id="p",
        name="P",
        cells=[
            _cell("a", "weather_now", 0, 0, 10, 10),
            _cell("b", "picture_apod", 10, 0, 10, 10),
        ],
    )
    regions = regions_from_page(page)
    assert all(r["nearest"] is False for r in regions)
    assert has_nearest_region(regions) is False


def test_rasterize_paints_only_nearest_regions() -> None:
    regions = [{"x": 2, "y": 1, "w": 3, "h": 2, "nearest": True}]
    mask = rasterize_region_mask(regions, 8, 6)
    px = mask.load()
    # Inside the rect -> 255 (nearest).
    assert px[2, 1] == 255 and px[4, 2] == 255
    # Just outside -> 0 (frame dither). Inclusive box is exactly 3x2.
    assert px[5, 1] == 0 and px[2, 3] == 0 and px[1, 1] == 0


def test_rasterize_painters_order_diffuse_over_nearest_wins() -> None:
    """A later (higher-z) diffuse rect laid over an earlier nearest rect
    clears the overlap back to frame-dither, the painter's-order rule that
    keeps a future overlapping-canvas mode consistent with the grid."""
    regions = [
        {"x": 0, "y": 0, "w": 8, "h": 8, "nearest": True},  # nearest base
        {"x": 2, "y": 2, "w": 4, "h": 4, "nearest": False},  # diffuse on top
    ]
    mask = rasterize_region_mask(regions, 8, 8)
    px = mask.load()
    assert px[0, 0] == 255  # base still nearest
    assert px[3, 3] == 0  # overlap cleared to frame-dither by the top rect


def test_transform_mask_portrait_rotate_and_underscan() -> None:
    # 8x16 portrait mask, a nearest strip down the left edge.
    mask = rasterize_region_mask([{"x": 0, "y": 0, "w": 4, "h": 16, "nearest": True}], 8, 16)
    out = transform_mask(mask, native_w=16, native_h=8, rotate=90, underscan=0)
    assert out.size == (16, 8)
    # 90 CCW sends the left strip to the bottom rows; check a bottom pixel is
    # nearest and a top pixel is frame-dither.
    px = out.load()
    assert px[0, 7] == 255
    assert px[0, 0] == 0


def test_transform_mask_underscan_border_is_nearest() -> None:
    mask = Image.new("L", (20, 20), 0)  # all frame-dither
    out = transform_mask(mask, native_w=20, native_h=20, underscan=3)
    px = out.load()
    # The 3px matting border is flat, so it snaps to nearest (255).
    assert px[0, 0] == 255 and px[19, 19] == 255
    # Interior stays frame-dither.
    assert px[10, 10] == 0


def _load_renderer(name: str) -> ModuleType:
    """Import a bundled renderer module straight from its file so the test
    exercises the real transform glue (settings -> mask -> packer)."""
    path = Path(REPO_ROOT) / "renderers" / name / "renderer.py"
    spec = importlib.util.spec_from_file_location(f"_test_rend_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gradient_png(width: int, height: int) -> bytes:
    ramp = np.linspace(0, 255, width, dtype=np.uint8)
    arr = np.repeat(np.repeat(ramp[None, :, None], height, axis=0), 3, axis=2)
    out = io.BytesIO()
    Image.fromarray(arr).save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    "name,gamut",
    [
        ("pi_bin", "waveshare_e6"),
        ("esp32_bin", "waveshare_e6"),
        ("esp32_bw_bin", "waveshare_e6"),
        ("esp32_gray_bin", "waveshare_e6"),
    ],
)
def test_renderer_consumes_dither_regions(name: str, gamut: str) -> None:
    """Each wired .bin renderer must actually act on ``_dither_regions`` in
    its settings: a full-frame nearest region changes the packed bytes vs no
    region, and an absent key leaves the bytes untouched."""
    mod = _load_renderer(name)
    png = _gradient_png(64, 16)
    panel = Panel(w=64, h=16, gamut=gamut)
    base = mod.transform(png, panel=panel, settings={})
    # Absent key is byte-identical to no region processing.
    absent = mod.transform(png, panel=panel, settings={"other": 1})
    assert absent == base
    # A full-frame nearest region changes the output (gradient stops
    # dithering, snaps to nearest colour).
    regions = [{"x": 0, "y": 0, "w": 64, "h": 16, "nearest": True}]
    masked = mod.transform(png, panel=panel, settings={"_dither_regions": regions})
    assert masked != base
