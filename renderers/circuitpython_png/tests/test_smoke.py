"""circuitpython_png renderer smoke: composition PNG in, palette-mode
PNG out. Confirms the loader registers the renderer, the manifest
fields are wired correctly, output is genuinely indexed (mode "P")
rather than RGB, the palette length matches the panel's gamut, and
the flip path produces visibly different bytes."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def circuitpython_png(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("circuitpython_png")
    assert renderer is not None
    return renderer


@pytest.fixture
def composition_png() -> bytes:
    """A 200x100 colour-graded PNG to exercise the dither + quantize
    path. Diagonal gradient stripes give the dither pass real work
    rather than a flat block, so any palette-projection regression
    shows up as a byte-level diff."""
    img = Image.new("RGB", (200, 100))
    for y in range(100):
        for x in range(200):
            img.putpixel((x, y), ((x + y) % 256, (x * 2) % 256, (y * 3) % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_circuitpython_png_manifest_fields(circuitpython_png) -> None:
    assert circuitpython_png.device == "circuitpython"
    assert circuitpython_png.orientation == "composition"
    assert circuitpython_png.extension == "png"
    assert circuitpython_png.mime == "image/png"
    assert circuitpython_png.retain is False
    assert circuitpython_png.topic == "tesserae/circuitpython/frame/png"


def _colors_used(out: Image.Image) -> set[tuple[int, int, int]]:
    """Return the set of distinct (R, G, B) colours the indexed image
    actually renders to. Pillow's palette table is 256 entries; we pad
    the unused slots with (0, 0, 0), which means counting distinct
    palette indices overcounts (duplicate black entries can each get
    selected by dither). Mapping back through the palette before
    de-duplicating gives the true colour count."""
    palette = out.getpalette() or []
    indices = set(out.getdata())
    return {(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]) for i in indices}


def test_circuitpython_png_mono_output_is_indexed(circuitpython_png, composition_png) -> None:
    # A 1-bit mono panel should produce a palette-mode PNG with the
    # two mono palette entries actually used. Verifies the renderer
    # skips the .convert("RGB") step the bin-packer feed path uses,
    # which is the whole point of this renderer.
    panel = Panel(w=200, h=100, gamut="mono")
    artifact = circuitpython_png.transform(
        composition_png, panel=panel, settings=circuitpython_png.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P", f"expected palette mode, got {out.mode!r}"
    colors = _colors_used(out)
    assert colors <= {(0, 0, 0), (255, 255, 255)}


def test_circuitpython_png_bwr_3_uses_tricolour_palette(circuitpython_png, composition_png) -> None:
    # Discussion #24: black/white/red tri-colour e-ink. Output must be
    # an indexed PNG drawn only from the 3-entry BWR palette so
    # adafruit_imageload mounts it without an on-device quantise.
    from app.quantizer import BWR_3_PALETTE

    panel = Panel(w=200, h=100, gamut="bwr_3")
    artifact = circuitpython_png.transform(
        composition_png, panel=panel, settings=circuitpython_png.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P"
    assert _colors_used(out) <= set(BWR_3_PALETTE)


def test_circuitpython_png_gray_4_uses_grey_ramp(circuitpython_png, composition_png) -> None:
    # Discussion #24: 2-bit greyscale panel. Output must quantise to the
    # 4-level grey ramp, not a highlight palette.
    from app.quantizer import GRAY_4_PALETTE

    panel = Panel(w=200, h=100, gamut="gray_4")
    artifact = circuitpython_png.transform(
        composition_png, panel=panel, settings=circuitpython_png.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P"
    assert _colors_used(out) <= set(GRAY_4_PALETTE)


def test_circuitpython_png_spectra_6_uses_palette_colors(
    circuitpython_png, composition_png
) -> None:
    # Spectra 6 panel: every distinct colour in the output must come
    # from the 6-entry Spectra 6 palette. The image quantises against
    # that palette, so anything outside the palette is a regression.
    from app.quantizer import SPECTRA_6_PALETTE

    panel = Panel(w=200, h=100, gamut="spectra_6")
    artifact = circuitpython_png.transform(
        composition_png, panel=panel, settings=circuitpython_png.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P"
    colors = _colors_used(out)
    assert colors <= set(SPECTRA_6_PALETTE)


def test_circuitpython_png_unknown_gamut_falls_back_to_spectra_6(
    circuitpython_png, composition_png
) -> None:
    # Empty / custom gamut should fall through to Spectra 6 nominal
    # rather than erroring or emitting RGB.
    from app.quantizer import SPECTRA_6_PALETTE

    panel = Panel(w=200, h=100, gamut="")
    artifact = circuitpython_png.transform(
        composition_png, panel=panel, settings=circuitpython_png.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P"
    colors = _colors_used(out)
    assert colors <= set(SPECTRA_6_PALETTE)


def test_circuitpython_png_rgb24_output_is_full_colour(circuitpython_png, composition_png) -> None:
    # v0.69.1 (issue #41): rgb24 panels get a plain 24-bit RGB PNG
    # rather than an indexed palette output, so a full-colour LCD
    # hybrid can paint the composition at its native depth.
    panel = Panel(w=200, h=100, gamut="rgb24")
    artifact = circuitpython_png.transform(
        composition_png, panel=panel, settings=circuitpython_png.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "RGB", f"expected RGB mode, got {out.mode!r}"
    # No palette, so ``getpalette()`` returns None.
    assert out.getpalette() is None


def test_circuitpython_png_rgb16_output_is_full_colour(circuitpython_png, composition_png) -> None:
    # rgb16 shares the rgb24 wire format (24-bit RGB PNG); the
    # firmware packs to RGB565 on-device. Same shape assertion as
    # the rgb24 test.
    panel = Panel(w=200, h=100, gamut="rgb16")
    artifact = circuitpython_png.transform(
        composition_png, panel=panel, settings=circuitpython_png.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "RGB"
    assert out.getpalette() is None


def test_circuitpython_png_rgb24_preserves_source_colours(
    circuitpython_png,
) -> None:
    # A colourful gradient survives the rgb24 path with more distinct
    # colours than the 6-entry Spectra 6 palette would allow. Uses a
    # 128-shade horizontal ramp so the assertion is unambiguous.
    from PIL import Image as PILImage

    ramp = PILImage.new("RGB", (128, 32))
    for x in range(128):
        for y in range(32):
            ramp.putpixel((x, y), (x * 2, 128, 255 - x * 2))
    buf = io.BytesIO()
    ramp.save(buf, format="PNG")
    artifact = circuitpython_png.transform(
        buf.getvalue(),
        panel=Panel(w=128, h=32, gamut="rgb24"),
        settings=circuitpython_png.settings_defaults(),
    )
    out = PILImage.open(io.BytesIO(artifact)).convert("RGB")
    # Distinct colour count comfortably exceeds the 6-colour Spectra 6
    # palette that would apply on any of the other gamut paths.
    assert len({out.getpixel((x, 0)) for x in range(128)}) > 20


def test_circuitpython_png_flip_changes_bytes(circuitpython_png) -> None:
    # flip=True applies a 180° rotation before quantise, so the
    # resulting indexed PNG bytes differ from the un-flipped version.
    # Use a black + white asymmetric image (black left, white right)
    # because mono quantise collapses similar luminances; black vs
    # white survives the projection.
    src = Image.new("RGB", (200, 100), (0, 0, 0))
    for y in range(100):
        for x in range(100, 200):
            src.putpixel((x, y), (255, 255, 255))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    comp = buf.getvalue()
    settings = circuitpython_png.settings_defaults()
    normal = circuitpython_png.transform(
        comp, panel=Panel(w=200, h=100, gamut="mono", flip=False), settings=settings
    )
    flipped = circuitpython_png.transform(
        comp, panel=Panel(w=200, h=100, gamut="mono", flip=True), settings=settings
    )
    assert Image.open(io.BytesIO(normal)).size == Image.open(io.BytesIO(flipped)).size
    assert normal != flipped


def test_circuitpython_png_payload_shape(circuitpython_png) -> None:
    payload = circuitpython_png.payload(
        "abc123", "http://192.168.1.10:8000", settings=circuitpython_png.settings_defaults()
    )
    assert payload == {"url": "http://192.168.1.10:8000/renders/abc123.png"}
