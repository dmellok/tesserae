"""circuitpython_bmp renderer smoke: composition PNG in, uncompressed
indexed BMP out. Confirms the loader registers the renderer, the
manifest fields are wired for BMP, output is a genuinely indexed BMP
(mode "P" with the panel's palette) that carries no zlib compression
(so adafruit_imageload can stream it without a decompress buffer), and
the shared pixel pipeline matches circuitpython_png's palette choices.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def circuitpython_bmp(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("circuitpython_bmp")
    assert renderer is not None
    return renderer


@pytest.fixture
def composition_png() -> bytes:
    """A 200x100 colour-graded PNG to exercise the dither + quantize
    path. Diagonal gradient stripes give the dither pass real work
    rather than a flat block."""
    img = Image.new("RGB", (200, 100))
    for y in range(100):
        for x in range(200):
            img.putpixel((x, y), ((x + y) % 256, (x * 2) % 256, (y * 3) % 256))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _colors_used(out: Image.Image) -> set[tuple[int, int, int]]:
    palette = out.getpalette() or []
    indices = set(out.getdata())
    return {(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]) for i in indices}


def test_circuitpython_bmp_manifest_fields(circuitpython_bmp) -> None:
    assert circuitpython_bmp.device == "circuitpython"
    assert circuitpython_bmp.orientation == "composition"
    assert circuitpython_bmp.extension == "bmp"
    assert circuitpython_bmp.mime == "image/bmp"
    assert circuitpython_bmp.retain is False
    assert circuitpython_bmp.topic == "tesserae/circuitpython/frame/bmp"


def test_circuitpython_bmp_output_is_bmp(circuitpython_bmp, composition_png) -> None:
    # The whole point of this renderer: emit a real BMP (not a PNG the
    # client would then have to zlib-decompress). Pillow tags the format
    # on open, so a genuine BMP round-trips as "BMP".
    panel = Panel(w=200, h=100, gamut="bwr_3")
    artifact = circuitpython_bmp.transform(
        composition_png, panel=panel, settings=circuitpython_bmp.settings_defaults()
    )
    assert artifact[:2] == b"BM", "BMP magic missing"
    out = Image.open(io.BytesIO(artifact))
    assert out.format == "BMP"


def test_circuitpython_bmp_is_uncompressed(circuitpython_bmp, composition_png) -> None:
    # adafruit_imageload rejects RLE-compressed BMP; the client needs
    # BI_RGB (compression == 0). The 40-byte BITMAPINFOHEADER stores the
    # compression method as a little-endian uint32 at byte offset 30.
    panel = Panel(w=200, h=100, gamut="bwr_3")
    artifact = circuitpython_bmp.transform(
        composition_png, panel=panel, settings=circuitpython_bmp.settings_defaults()
    )
    compression = int.from_bytes(artifact[30:34], "little")
    assert compression == 0, f"expected BI_RGB (0), got {compression}"


def test_circuitpython_bmp_bwr_3_uses_tricolour_palette(circuitpython_bmp, composition_png) -> None:
    from app.quantizer import BWR_3_PALETTE

    panel = Panel(w=200, h=100, gamut="bwr_3")
    artifact = circuitpython_bmp.transform(
        composition_png, panel=panel, settings=circuitpython_bmp.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert out.mode == "P"
    assert _colors_used(out) <= set(BWR_3_PALETTE)


def test_circuitpython_bmp_matches_png_pixels(circuitpython_bmp, composition_png) -> None:
    # BMP and PNG share the pixel pipeline (circuitpython_indexed_image);
    # only the container differs. The decoded pixels must be identical so
    # a client sees the same frame whichever format it asked for.
    from app.quantizer import circuitpython_indexed_image

    panel = Panel(w=200, h=100, gamut="spectra_6")
    expected = circuitpython_indexed_image(
        composition_png,
        width=panel.w,
        height=panel.h,
        gamut=panel.gamut,
        settings=circuitpython_bmp.settings_defaults(),
    )
    artifact = circuitpython_bmp.transform(
        composition_png, panel=panel, settings=circuitpython_bmp.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    assert list(out.convert("RGB").getdata()) == list(expected.convert("RGB").getdata())


def test_circuitpython_bmp_spectra_6_has_no_orange(circuitpython_bmp, composition_png) -> None:
    # #118: a spectra_6 panel is 6-colour (black/white/red/yellow/blue/green).
    # The BMP must not carry the 7th colour (orange) that the Pi-side inky
    # palette uses; the CircuitPython client paints exactly what arrives.
    from app.quantizer import WAVESHARE_E6_PALETTE

    panel = Panel(w=200, h=100, gamut="spectra_6")
    artifact = circuitpython_bmp.transform(
        composition_png, panel=panel, settings=circuitpython_bmp.settings_defaults()
    )
    out = Image.open(io.BytesIO(artifact))
    colors = _colors_used(out)
    assert colors <= set(WAVESHARE_E6_PALETTE)
    assert (255, 140, 0) not in colors


def test_circuitpython_bmp_payload_points_at_bmp(circuitpython_bmp) -> None:
    payload = circuitpython_bmp.payload(
        "deadbeef", "http://host:8080", settings=circuitpython_bmp.settings_defaults()
    )
    assert payload["url"] == "http://host:8080/renders/deadbeef.bmp"
