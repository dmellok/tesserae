"""Flat cells are honoured by the TRMNL PNG and Kaleido renderers (#601).

A cell set to Flat is drawn in the nearest ink with no dithering. The ``.bin`` and CircuitPython
renderers did that; ``trmnl_png*`` and ``kaleido_png`` ignored it, although each of them quantises
on the server. The flat part of a frame must equal the same frame quantised with no dither, and
the rest must equal the frame quantised with the device's dither.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.dither_regions import renderer_takes_regions
from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel

RENDERERS = ["trmnl_png", "trmnl_png_color", "trmnl_png_gray16", "trmnl_png_gray4", "kaleido_png"]
W, H = 120, 60
# The left half of the composition is a Flat cell.
FLAT = [{"x": 0, "y": 0, "w": W // 2, "h": H, "nearest": True}]


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> Any:
    reg = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=Path(tmp_path_factory.mktemp("renderers")),
    )
    assert reg.errors == [], reg.errors
    return reg


def _gradient() -> bytes:
    """A smooth colour gradient: any error-diffusion dither turns it into a dot pattern."""
    img = Image.new("RGB", (W, H))
    img.putdata(
        [(x * 255 // W, y * 255 // H, (x + y) * 127 // (W + H)) for y in range(H) for x in range(W)]
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render(renderer: Any, panel: Panel, **settings: Any) -> Image.Image:
    out = renderer.transform(
        _gradient(), panel=panel, settings={"dither": "floyd-steinberg", **settings}
    )
    return Image.open(io.BytesIO(out)).convert("RGB")


def _half(img: Image.Image, side: str, box: tuple[int, int, int, int] | None = None) -> bytes:
    w, h = img.size
    region = box or ((0, 0, w // 2, h) if side == "left" else (w // 2, 0, w, h))
    return img.crop(region).tobytes()


@pytest.mark.parametrize("renderer_id", RENDERERS)
def test_every_png_renderer_takes_the_regions(renderer_id: str) -> None:
    assert renderer_takes_regions(renderer_id, "png")
    assert renderer_takes_regions(f"{renderer_id}__frame", "png")


@pytest.mark.parametrize("renderer_id", RENDERERS)
def test_a_flat_cell_is_drawn_without_dither(registry: Any, renderer_id: str) -> None:
    renderer = registry.get(renderer_id)
    panel = Panel(w=W, h=H, gamut="spectra_6")
    dithered = _render(renderer, panel)
    flat_everywhere = _render(renderer, panel, dither="none")
    mixed = _render(renderer, panel, _dither_regions=FLAT)

    assert _half(dithered, "left") != _half(flat_everywhere, "left"), (
        "the fixture must dither visibly"
    )
    assert _half(mixed, "left") == _half(flat_everywhere, "left")
    assert _half(mixed, "right") == _half(dithered, "right")


@pytest.mark.parametrize("renderer_id", RENDERERS)
def test_the_flat_cell_follows_the_turn_onto_a_portrait_buffer(
    registry: Any, renderer_id: str
) -> None:
    renderer = registry.get(renderer_id)
    panel = Panel(w=W, h=H, native_w=H, native_h=W, native_declared=True, gamut="spectra_6")
    flat_everywhere = _render(renderer, panel, dither="none")
    mixed = _render(renderer, panel, _dither_regions=FLAT)

    # Turned 90° clockwise, the composition's left half lands on the top half of the buffer.
    assert mixed.size == (H, W)
    top = (0, 0, H, W // 2)
    assert _half(mixed, "", top) == _half(flat_everywhere, "", top)


@pytest.mark.parametrize("renderer_id", RENDERERS)
def test_no_flat_cell_keeps_the_output_byte_identical(registry: Any, renderer_id: str) -> None:
    renderer = registry.get(renderer_id)
    panel = Panel(w=W, h=H, gamut="spectra_6")
    plain = renderer.transform(_gradient(), panel=panel, settings={"dither": "floyd-steinberg"})
    unflagged = [{**FLAT[0], "nearest": False}]
    with_diffuse = renderer.transform(
        _gradient(),
        panel=panel,
        settings={"dither": "floyd-steinberg", "_dither_regions": unflagged},
    )
    assert with_diffuse == plain
