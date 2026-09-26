"""trmnl_png_gray16 renderer smoke: composition PNG in, 16-level
greyscale PNG out on the same TRMNL /api/display topic as the 1-bit
variant.

Confirms the loader registers the renderer, the manifest fields match
the TRMNL BYOS wire contract, output is genuinely 8-bit greyscale with
at most 16 distinct levels (not RGB, not 1-bit), the nominal ramp is
used by default, and a calibration ``_gray_ramp`` override is honoured.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def trmnl_png_gray16(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("trmnl_png_gray16")
    assert renderer is not None
    return renderer


@pytest.fixture
def composition_png() -> bytes:
    """A 200x100 greyscale-graded PNG. A diagonal luminance gradient
    gives the dither pass real work rather than a flat block, so a
    ramp-projection regression shows up as a byte-level diff."""
    img = Image.new("RGB", (200, 100))
    for y in range(100):
        for x in range(200):
            v = (x + y) % 256
            img.putpixel((x, y), (v, v, v))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _levels(out: Image.Image) -> set[int]:
    assert out.mode == "L", f"expected 8-bit greyscale, got {out.mode!r}"
    return set(out.getdata())


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_manifest_matches_byos_wire_contract(trmnl_png_gray16) -> None:
    """The greyscale variant publishes to the same /frame/trmnl topic as
    the 1-bit renderer so both route through the same TRMNL BYOS
    /api/display path on the device side."""
    assert trmnl_png_gray16.device == "trmnl"
    assert trmnl_png_gray16.orientation == "composition"
    assert trmnl_png_gray16.extension == "png"
    assert trmnl_png_gray16.mime == "image/png"
    assert trmnl_png_gray16.retain is False
    assert trmnl_png_gray16.topic == "tesserae/trmnl/frame/trmnl"


def test_output_is_8bit_grey_with_at_most_16_levels(trmnl_png_gray16, composition_png) -> None:
    panel = Panel(w=200, h=100, gamut="gray_16")
    artifact = trmnl_png_gray16.transform(
        composition_png, panel=panel, settings=trmnl_png_gray16.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.size == (200, 100)
    levels = _levels(out)
    assert len(levels) <= 16
    # A full-range gradient through a 16-level ramp should actually use
    # most of the ramp, not collapse to black + white.
    assert len(levels) >= 8, f"only {len(levels)} levels; dither may have regressed to 1-bit"
    assert levels <= {v for v, _, _ in _NOMINAL}


def test_none_dither_snaps_to_nominal_ramp(trmnl_png_gray16, composition_png) -> None:
    """dither='none' is nearest-level with no diffusion, so every output
    value must be an exact nominal ramp entry."""
    panel = Panel(w=200, h=100, gamut="gray_16")
    settings = {**trmnl_png_gray16.settings_defaults(), "dither": "none"}
    artifact = trmnl_png_gray16.transform(composition_png, panel=panel, settings=settings)
    out = Image.open(io.BytesIO(artifact))
    assert _levels(out) <= {v for v, _, _ in _NOMINAL}


def test_calibration_gray_ramp_override_is_honoured(trmnl_png_gray16, composition_png) -> None:
    """When app.push injects a device's measured ramp under
    ``_gray_ramp``, output values come from that ramp, not the nominal
    one. Use a deliberately compressed ramp (nothing below 0x20, nothing
    above 0xE0) so the override is unmistakable."""
    panel = Panel(w=200, h=100, gamut="gray_16")
    settings = {
        **trmnl_png_gray16.settings_defaults(),
        "dither": "none",
        "_gray_ramp": ("#202020", "#e0e0e0"),
    }
    artifact = trmnl_png_gray16.transform(composition_png, panel=panel, settings=settings)
    out = Image.open(io.BytesIO(artifact))
    levels = _levels(out)
    assert min(levels) >= 0x20
    assert max(levels) <= 0xE0


def test_landscape_composition_rotated_onto_portrait_buffer(trmnl_png_gray16) -> None:
    """The Kindle case: a landscape dashboard composed at 200×100 for a
    portrait 100×200 screen. Selecting "Rotation: 90" must turn the
    frame onto the client's buffer, or the client's scaler squashes
    the landscape into a portrait (issue reported against TRMNL on
    Kindle)."""
    img = Image.new("RGB", (200, 100), "white")
    img.paste((0, 0, 0), (100, 0, 200, 100))
    panel = Panel(w=200, h=100, native_w=100, native_h=200, gamut="gray_16")
    artifact = trmnl_png_gray16.transform(
        _png_bytes(img), panel=panel, settings=trmnl_png_gray16.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.size == (100, 200)
    assert out.mode == "L"
    # CW turn maps composition left (white) → output top, right (black)
    # → output bottom. 16-level ramp keeps white ≈ 255 and black ≈ 0.
    assert out.getpixel((50, 10)) >= 240
    assert out.getpixel((50, 190)) <= 15


def test_no_native_block_keeps_composition_dims(trmnl_png_gray16, composition_png) -> None:
    """Legacy / custom panels with no native block keep the old
    pass-through behaviour byte-for-byte: same dims, no rotation."""
    panel = Panel(w=200, h=100, gamut="gray_16")
    artifact = trmnl_png_gray16.transform(
        composition_png, panel=panel, settings=trmnl_png_gray16.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.size == (200, 100)


def test_payload_is_selfcontained_url(trmnl_png_gray16) -> None:
    p = trmnl_png_gray16.payload("abc123def456", "http://tesserae.local:8765", settings={})
    assert p == {"url": "http://tesserae.local:8765/renders/abc123def456.png"}


_NOMINAL = tuple((v, v, v) for v in range(0, 256, 17))
