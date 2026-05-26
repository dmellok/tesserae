"""esp32_bin smoke: manifest contract, payload shape, and the critical
landscape→portrait rotation that keeps the firmware happy (it reads the
buffer as native portrait, period — passing landscape-stride bytes paints
3x2 tiles)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def registry(tmp_path):
    r = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert r.errors == [], r.errors
    return r


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode_pixel(buf: bytes, fx: int, fy: int, width: int) -> int:
    """Read the palette nibble at panel pixel (fx, fy) from a packed
    4-bpp buffer. High nibble = even col, low nibble = odd col."""
    byte_idx = fy * (width // 2) + fx // 2
    b = buf[byte_idx]
    return (b >> 4) & 0xF if fx % 2 == 0 else b & 0xF


def test_esp32_bin_manifest_fields(registry) -> None:
    renderer = registry.get("esp32_bin")
    assert renderer is not None
    assert renderer.device == "esp32"
    assert renderer.extension == "bin"
    assert renderer.retain is True  # the key distinguishing bit
    assert renderer.topic == "tesserae/esp32/frame/bin"


def test_esp32_bin_payload(registry) -> None:
    renderer = registry.get("esp32_bin")
    assert renderer is not None
    assert renderer.payload("zzz", "http://x/", settings={}) == {"url": "http://x/renders/zzz.bin"}


def test_landscape_source_is_rotated_to_portrait(registry) -> None:
    """Bug fingerprint: with the panel configured landscape (1600x1200
    via inky_13_3 preset, default orientation), the composer used to
    emit a 1600x1200 landscape PNG and the renderer packed it at
    landscape stride. The firmware then read the 960000-byte buffer as
    1200x1600 portrait and painted 3x2 tiles of the source.

    The fix forces native-portrait output. This test feeds a landscape
    input with red on the left half and blue on the right half, then
    decodes the resulting buffer as if it were portrait — red must now
    be in the top half, blue in the bottom half."""
    img = Image.new("RGB", (1600, 400), "white")
    img.paste((255, 0, 0), (0, 0, 800, 400))
    img.paste((0, 0, 255), (800, 0, 1600, 400))

    esp = registry.get("esp32_bin")
    assert esp is not None
    # Panel arg is the user's setting; could be landscape (1600x1200) or
    # portrait (1200x1600). Either way the renderer must emit 1200x1600.
    panel = Panel(w=1600, h=1200)
    out = esp.transform(
        _png_bytes(img),
        panel=panel,
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    assert len(out) == 1200 * 1600 // 2  # 960000 bytes, exact

    # After CW rotation + fit_to_panel, the 1600x400 input becomes a
    # 400x1600 portrait strip letterboxed centred on the 1200x1600 panel.
    # The original left half (red) now occupies the panel's TOP half
    # (rows 0..799) within the centre strip; the original right half
    # (blue) lands in the BOTTOM half (rows 800..1599).
    red_nibble = 0x3
    blue_nibble = 0x5
    white_nibble = 0x1
    assert _decode_pixel(out, 600, 400, 1200) == red_nibble
    assert _decode_pixel(out, 600, 1200, 1200) == blue_nibble
    # Letterbox sides on either column stay white nibble 0x1.
    assert _decode_pixel(out, 100, 800, 1200) == white_nibble
    assert _decode_pixel(out, 1100, 800, 1200) == white_nibble


def test_portrait_source_passes_through_unchanged(registry) -> None:
    """Regression guard: portrait input must NOT get rotated. A solid
    red top half + solid blue bottom half should stay that way."""
    img = Image.new("RGB", (1200, 1600), "white")
    img.paste((255, 0, 0), (0, 0, 1200, 800))
    img.paste((0, 0, 255), (0, 800, 1200, 1600))

    esp = registry.get("esp32_bin")
    assert esp is not None
    panel = Panel(w=1200, h=1600)
    out = esp.transform(
        _png_bytes(img),
        panel=panel,
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    assert len(out) == 1200 * 1600 // 2

    assert _decode_pixel(out, 600, 400, 1200) == 0x3  # red, top half
    assert _decode_pixel(out, 600, 1200, 1200) == 0x5  # blue, bottom half


def test_landscape_panel_setting_still_emits_portrait(registry) -> None:
    """The panel arg arrives from app settings. If the user has the
    Inky 13.3" preset selected with default landscape orientation, panel
    comes in as (1600, 1200). The renderer must still emit a 1200x1600
    portrait buffer because the firmware contract is fixed."""
    img = Image.new("RGB", (1200, 1600), "white")  # portrait composition
    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=1600, h=1200),  # user has it as landscape
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    assert len(out) == 1200 * 1600 // 2  # native portrait, regardless
