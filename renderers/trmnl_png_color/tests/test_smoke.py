"""trmnl_png_color renderer smoke: composition PNG in, palette-mode
PNG out on the same TRMNL /api/display topic as the mono variant.

Confirms the loader registers the renderer, the manifest fields match
the TRMNL BYOS wire contract, output is genuinely indexed (mode "P")
rather than RGB, the palette matches the panel's Spectra 6 gamut, and
mono / ACeP fall-throughs still land on palette-mode output rather
than accidentally 24-bit RGB.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def trmnl_png_color(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("trmnl_png_color")
    assert renderer is not None
    return renderer


@pytest.fixture
def composition_png() -> bytes:
    """A 200x100 colour-graded PNG. Diagonal gradients give the dither
    pass real work rather than a flat block, so a palette-projection
    regression shows up as a byte-level diff."""
    img = Image.new("RGB", (200, 100))
    for y in range(100):
        for x in range(200):
            img.putpixel((x, y), ((x + y) % 256, (x * 2) % 256, (y * 3) % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_trmnl_png_color_manifest_matches_byos_wire_contract(trmnl_png_color) -> None:
    """The colour variant must publish to the same /frame/trmnl topic
    as the 1-bit renderer so both routes through the same TRMNL BYOS
    /api/display path on the device side."""
    assert trmnl_png_color.device == "trmnl"
    assert trmnl_png_color.orientation == "composition"
    assert trmnl_png_color.extension == "png"
    assert trmnl_png_color.mime == "image/png"
    assert trmnl_png_color.retain is False
    assert trmnl_png_color.topic == "tesserae/trmnl/frame/trmnl"


def _colors_used(out: Image.Image) -> set[tuple[int, int, int]]:
    """Return the set of distinct (R, G, B) colours the indexed image
    actually renders to. Pillow's palette is 256 entries; unused
    slots are padded with (0, 0, 0), so mapping distinct indices
    back through the palette before dedup gives the true count."""
    palette = out.getpalette() or []
    indices = set(out.getdata())
    return {(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]) for i in indices}


def test_trmnl_png_color_spectra_6_output_is_indexed(trmnl_png_color, composition_png) -> None:
    """A Spectra 6 panel (the E1002 case) must produce palette-mode
    PNG whose colours come from the Waveshare E6 6-colour palette.
    Anything outside the palette means the quantiser regressed."""
    from app.quantizer import WAVESHARE_E6_PALETTE

    panel = Panel(w=200, h=100, gamut="spectra_6")
    artifact = trmnl_png_color.transform(
        composition_png, panel=panel, settings=trmnl_png_color.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P", f"expected palette mode, got {out.mode!r}"
    colors = _colors_used(out)
    assert colors <= set(WAVESHARE_E6_PALETTE)


def test_trmnl_png_color_waveshare_e6_alias_uses_same_palette(
    trmnl_png_color, composition_png
) -> None:
    from app.quantizer import WAVESHARE_E6_PALETTE

    panel = Panel(w=200, h=100, gamut="waveshare_e6")
    artifact = trmnl_png_color.transform(
        composition_png, panel=panel, settings=trmnl_png_color.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert _colors_used(out) <= set(WAVESHARE_E6_PALETTE)


def test_trmnl_png_color_unknown_gamut_falls_back_to_e6(trmnl_png_color, composition_png) -> None:
    """Unlabelled panels should fall through to Waveshare E6 rather
    than mono (colour panels + colour renderer + no gamut = still
    colour output). Belt-and-braces so a maintainer who adds a new
    colour SKU without a declared gamut still gets a sensible frame."""
    from app.quantizer import WAVESHARE_E6_PALETTE

    panel = Panel(w=200, h=100, gamut="")
    artifact = trmnl_png_color.transform(
        composition_png, panel=panel, settings=trmnl_png_color.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P"
    assert _colors_used(out) <= set(WAVESHARE_E6_PALETTE)


def test_trmnl_png_color_acep_7colour_uses_inky_palette(trmnl_png_color, composition_png) -> None:
    """A future 7-colour ACeP panel over the same wire protocol should
    get the Inky 7-colour palette. The renderer is deliberately
    gamut-driven so it works across colour panel families, not just
    E1002-shaped ones."""
    from app.quantizer import INKY_7COLOUR_PALETTE

    panel = Panel(w=200, h=100, gamut="acep_7colour")
    artifact = trmnl_png_color.transform(
        composition_png, panel=panel, settings=trmnl_png_color.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert _colors_used(out) <= set(INKY_7COLOUR_PALETTE)


def test_trmnl_png_color_payload_is_selfcontained_url(trmnl_png_color) -> None:
    """The wire payload is just the URL; rotation / scale / bg were
    baked in at transform time. Client just fetches and paints."""
    p = trmnl_png_color.payload("abc123def456", "http://tesserae.local:8765", settings={})
    assert p == {"url": "http://tesserae.local:8765/renders/abc123def456.png"}
