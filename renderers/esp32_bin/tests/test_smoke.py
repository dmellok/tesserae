"""esp32_bin smoke: manifest contract, payload shape, and the
orientation-matching pack pipeline.

The renderer packs at panel.w × panel.h directly. Two shipped
panel orientations:

  * 13.3" Waveshare: portrait native (1200, 1600)
  * 7.3" PhotoPainter: landscape native (800, 480)

Uploaded images are first fitted into the panel's composition dimensions.
Only a mismatch between that composition orientation and the firmware's
native row stride gets a 90° CW rotation before packing."""

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


@pytest.mark.parametrize(
    "panel_w,panel_h",
    [
        (1200, 1600),  # 13.3" Waveshare, portrait native
        (800, 480),  # 7.3" PhotoPainter, landscape native
    ],
)
def test_matching_orientation_round_trip(registry, panel_w: int, panel_h: int) -> None:
    """An input whose orientation already matches the panel passes
    straight through (no rotation surprise) and unpacks at panel.w ×
    panel.h to reproduce the input. Paints two coloured halves
    horizontally for landscape panels and vertically for portrait so
    the test's expected positions follow the natural reading order."""
    if panel_w > panel_h:
        # Landscape input matches landscape panel.
        img = Image.new("RGB", (panel_w, panel_h), "white")
        img.paste((255, 0, 0), (0, 0, panel_w // 2, panel_h))
        img.paste((0, 0, 255), (panel_w // 2, 0, panel_w, panel_h))
    else:
        # Portrait input matches portrait panel.
        img = Image.new("RGB", (panel_w, panel_h), "white")
        img.paste((255, 0, 0), (0, 0, panel_w, panel_h // 2))
        img.paste((0, 0, 255), (0, panel_h // 2, panel_w, panel_h))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=panel_w, h=panel_h),
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    assert len(out) == panel_w * panel_h // 2  # exact byte count

    red_nibble = 0x3
    blue_nibble = 0x5
    if panel_w > panel_h:
        # Landscape panel, red occupies the LEFT half, blue the RIGHT.
        # Read mid-row from both halves to confirm orientation.
        assert _decode_pixel(out, panel_w // 4, panel_h // 2, panel_w) == red_nibble
        assert _decode_pixel(out, 3 * panel_w // 4, panel_h // 2, panel_w) == blue_nibble
    else:
        # Portrait panel, red on TOP, blue on BOTTOM.
        assert _decode_pixel(out, panel_w // 2, panel_h // 4, panel_w) == red_nibble
        assert _decode_pixel(out, panel_w // 2, 3 * panel_h // 4, panel_w) == blue_nibble


def test_landscape_photo_to_portrait_panel_fits_without_rotation(registry) -> None:
    """Regression: a 4:3 landscape photo sent to a portrait-native E1004
    is source media, not a landscape-calibrated composition. Fit must
    letterbox it without turning the photo 90°."""
    img = Image.new("RGB", (400, 300), "white")
    img.paste((255, 0, 0), (0, 0, 200, 300))
    img.paste((0, 0, 255), (200, 0, 400, 300))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=120, h=160, native_w=120, native_h=160),
        settings={
            "dither": "none",
            "saturation": 1.0,
            "contrast": 1.0,
            "image_fit": "fit",
        },
    )
    assert len(out) == 120 * 160 // 2

    red_nibble = 0x3
    blue_nibble = 0x5
    white_nibble = 0x1
    # The landscape photo stays left-to-right, centred vertically.
    assert _decode_pixel(out, 30, 80, 120) == red_nibble
    assert _decode_pixel(out, 90, 80, 120) == blue_nibble
    assert _decode_pixel(out, 60, 10, 120) == white_nibble
    assert _decode_pixel(out, 60, 150, 120) == white_nibble


def test_landscape_photo_to_portrait_panel_fills_without_rotation(registry) -> None:
    """Fill crops a 4:3 photo to the portrait panel while preserving
    its visual orientation. This is the Companion app's reported path."""
    img = Image.new("RGB", (400, 300), "white")
    img.paste((255, 0, 0), (0, 0, 200, 300))
    img.paste((0, 0, 255), (200, 0, 400, 300))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=120, h=160, native_w=120, native_h=160),
        settings={
            "dither": "none",
            "saturation": 1.0,
            "contrast": 1.0,
            "image_fit": "fill",
        },
    )
    assert len(out) == 120 * 160 // 2

    red_nibble = 0x3
    blue_nibble = 0x5
    assert _decode_pixel(out, 10, 80, 120) == red_nibble
    assert _decode_pixel(out, 110, 80, 120) == blue_nibble


def test_portrait_calibration_on_landscape_native_panel(registry) -> None:
    """Reported bug: user runs device calibration on a 7.3"
    PhotoPainter and picks the portrait option. The device record
    then has panel.w=480, panel.h=800 (portrait composition). The
    panel hardware is still landscape-native (800w × 480h fixed in
    firmware), packing at the panel arg's 480×800 stride puts the
    bytes back at 240 bytes/row vs the firmware's 400, the ghosts
    return.

    Fix: ``resolve_settings_panel`` populates panel.native_w /
    panel.native_h from the PhotoPainter preset's firmware-native
    stride (800w × 480h). The renderer reads those directly and
    rotates the portrait composition 90° CW to fit."""
    # Portrait composition at the panel.w × panel.h shape: red TOP,
    # blue BOTTOM. Composer paints this for a user with portrait
    # calibration on the PhotoPainter.
    img = Image.new("RGB", (480, 800), "white")
    img.paste((255, 0, 0), (0, 0, 480, 400))
    img.paste((0, 0, 255), (0, 400, 480, 800))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        # PORTRAIT calibration on the PhotoPainter, native dims still
        # carry the landscape (800, 480) firmware stride.
        panel=Panel(w=480, h=800, native_w=800, native_h=480),
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    # Output is the firmware-native 800×480 landscape buffer, 192000
    # bytes at 400 bytes/row, regardless of which calibration the
    # user picked.
    assert len(out) == 800 * 480 // 2

    red_nibble = 0x3
    blue_nibble = 0x5
    # CW rotation: red (top of portrait) → right of landscape; blue
    # (bottom of portrait) → left of landscape.
    assert _decode_pixel(out, 200, 240, 800) == blue_nibble
    assert _decode_pixel(out, 600, 240, 800) == red_nibble


def test_bwry_panel_gets_native_2bpp_pack(registry) -> None:
    """v0.69.5: ``panel.gamut`` routes into ``pack_to_panel_bin`` so a
    BWRY-declared panel gets the native 2-bpp buffer (four pixels per
    byte), not the 4-bpp Spectra 6 default. Pre-v0.69.5 this test
    would fail because ``esp32_bin`` never passed ``gamut=``.
    Solid red on a BWRY panel packs to 0xFF everywhere (palette index
    3 in all four 2-bit slots)."""
    img = Image.new("RGB", (400, 300), (255, 0, 0))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=400, h=300, gamut="bwry_4"),
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    # 2-bpp buffer, not 4-bpp: 30_000 bytes for a 400x300 PicPak frame.
    assert len(out) == 400 * 300 // 4
    assert all(b == 0xFF for b in out), "solid red on BWRY should be 0xFF everywhere"


def test_vflip_reverses_row_order_before_pack(registry) -> None:
    """v0.69.16: ``panel.vflip`` reverses row order before pack so panels
    whose hardware scans bottom-to-top (PicPak 4-colour BWRY) paint the
    right way up. Split-image with white on top / red on the bottom of
    the composition; with vflip set, the FIRST packed byte carries the
    RED palette index (0x03 in each 2-bit slot = 0xFF), because the
    bottom of the composition lands at the top of the wire buffer."""
    img = Image.new("RGB", (400, 300), "white")
    # Bottom half red (palette index 3), top half white (palette index 1)
    img.paste((255, 0, 0), (0, 150, 400, 300))

    esp = registry.get("esp32_bin")
    assert esp is not None
    panel = Panel(w=400, h=300, gamut="bwry_4", vflip=True)
    out = esp.transform(
        _png_bytes(img),
        panel=panel,
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    # 2-bpp buffer: 30_000 bytes for a 400x300 PicPak frame.
    assert len(out) == 400 * 300 // 4
    # The top-of-wire row is what the panel scans first. With vflip on,
    # that should be the RED half (bottom of the composition). Red is
    # palette index 3 = 0b11; four reds per byte = 0xFF.
    row_bytes = 400 // 4
    assert out[:row_bytes] == b"\xff" * row_bytes
    # And the bottom-of-wire row is the WHITE half (top of the
    # composition). White is palette index 1 = 0b01; four whites per
    # byte = 0b01010101 = 0x55.
    assert out[-row_bytes:] == b"\x55" * row_bytes


def test_vflip_off_preserves_row_order(registry) -> None:
    """The vflip default (False) leaves rows alone; existing panels
    (Spectra 6, and BWRY panels that don't need the flip) stay
    byte-identical to pre-v0.69.16."""
    img = Image.new("RGB", (400, 300), "white")
    img.paste((255, 0, 0), (0, 150, 400, 300))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=400, h=300, gamut="bwry_4"),  # vflip defaults False
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    row_bytes = 400 // 4
    # Top-of-wire = top-of-composition = WHITE (0x55 for four whites/byte).
    assert out[:row_bytes] == b"\x55" * row_bytes
    # Bottom-of-wire = bottom-of-composition = RED (0xFF).
    assert out[-row_bytes:] == b"\xff" * row_bytes


def test_landscape_calibration_on_portrait_native_panel(registry) -> None:
    """Symmetric case: a 13.3" Waveshare Spectra 6 is portrait native
    (1200×1600). If the user calibrates landscape, panel arrives as
    (1600, 1200) with native dims still (1200, 1600). The renderer
    must still pack at 1200×1600 portrait and rotate the landscape
    composition to fit."""
    # Landscape composition at the panel.w × panel.h shape: red LEFT,
    # blue RIGHT.
    img = Image.new("RGB", (1600, 1200), "white")
    img.paste((255, 0, 0), (0, 0, 800, 1200))
    img.paste((0, 0, 255), (800, 0, 1600, 1200))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        # Landscape calibration on the Waveshare 13.3", native dims
        # carry the portrait (1200, 1600) firmware stride.
        panel=Panel(w=1600, h=1200, native_w=1200, native_h=1600),
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    # Output is the firmware-native 1200×1600 portrait buffer.
    assert len(out) == 1200 * 1600 // 2

    red_nibble = 0x3
    blue_nibble = 0x5
    # CW rotation: red (left of landscape) → top of portrait; blue
    # (right of landscape) → bottom of portrait.
    assert _decode_pixel(out, 600, 400, 1200) == red_nibble
    assert _decode_pixel(out, 600, 1200, 1200) == blue_nibble


def test_bwr_panel_gets_native_2bpp_pack(registry) -> None:
    """The XIAO 7.5" BWR class: ``gamut = "bwr_3"`` routes into the same
    native 2-bpp pack as BWRY, with the tri-colour wire values 0b00
    black, 0b01 white, 0b10 red. Solid frames pin all three, and the
    reserved 0b11 can never appear (the palette has three entries)."""
    esp = registry.get("esp32_bin")
    assert esp is not None
    settings = {"dither": "none", "saturation": 1.0, "contrast": 1.0}
    panel = Panel(w=800, h=480, gamut="bwr_3")
    for colour, expected_byte in (
        ((0, 0, 0), 0x00),  # black -> 0b00 in all four slots
        ((255, 255, 255), 0x55),  # white -> 0b01
        ((255, 0, 0), 0xAA),  # red -> 0b10
    ):
        img = Image.new("RGB", (800, 480), colour)
        out = esp.transform(_png_bytes(img), panel=panel, settings=settings)
        assert len(out) == 800 * 480 // 4
        assert all(b == expected_byte for b in out)


def test_bwr_msb_first_pixel_order_and_no_reserved_value(registry) -> None:
    """A single red pixel at the top-left corner lands in bits 7-6 of
    byte 0; a busy gradient never emits the reserved 0b11 field."""
    esp = registry.get("esp32_bin")
    assert esp is not None
    settings = {"dither": "none", "saturation": 1.0, "contrast": 1.0}
    panel = Panel(w=800, h=480, gamut="bwr_3")

    img = Image.new("RGB", (800, 480), (0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0))
    out = esp.transform(_png_bytes(img), panel=panel, settings=settings)
    assert out[0] == 0b10000000
    assert out[1] == 0x00

    # Gradient through greys and reds, dithered: every 2-bit field must
    # stay within {0b00, 0b01, 0b10}.
    grad = Image.new("RGB", (800, 480))
    for x in range(800):
        g = (x * 255) // 800
        for y in range(0, 480, 2):
            grad.putpixel((x, y), (g, g, g))
            grad.putpixel((x, y + 1), (255, g, g))
    dithered = esp.transform(
        _png_bytes(grad),
        panel=panel,
        settings={"dither": "floyd-steinberg", "saturation": 1.0, "contrast": 1.0},
    )
    for byte in dithered:
        for shift in (6, 4, 2, 0):
            assert (byte >> shift) & 0b11 != 0b11
