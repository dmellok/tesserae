"""esp32_bin smoke: manifest contract, payload shape, and the
orientation-matching pack pipeline.

The renderer packs at panel.w × panel.h directly. Two shipped
panel orientations:

  * 13.3" Waveshare: portrait native (1200, 1600)
  * 7.3" PhotoPainter: landscape native (800, 480)

When the composition's orientation doesn't match the panel's, the
input gets a 90° CW rotation before packing so the bytes land at the
firmware's expected row stride."""

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
        (1200, 1600),  # 13.3" Waveshare — portrait native
        (800, 480),  # 7.3" PhotoPainter — landscape native
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
        # Landscape panel — red occupies the LEFT half, blue the RIGHT.
        # Read mid-row from both halves to confirm orientation.
        assert _decode_pixel(out, panel_w // 4, panel_h // 2, panel_w) == red_nibble
        assert _decode_pixel(out, 3 * panel_w // 4, panel_h // 2, panel_w) == blue_nibble
    else:
        # Portrait panel — red on TOP, blue on BOTTOM.
        assert _decode_pixel(out, panel_w // 2, panel_h // 4, panel_w) == red_nibble
        assert _decode_pixel(out, panel_w // 2, 3 * panel_h // 4, panel_w) == blue_nibble


def test_landscape_source_to_portrait_panel_rotates_cw(registry) -> None:
    """13.3" Waveshare is portrait native. A landscape composition gets
    a 90° CW rotation so its left edge lands at the panel top. Solid
    red on the left and solid blue on the right of the 1600×400 input
    must end up as red TOP and blue BOTTOM of the 1200×1600 buffer."""
    img = Image.new("RGB", (1600, 400), "white")
    img.paste((255, 0, 0), (0, 0, 800, 400))
    img.paste((0, 0, 255), (800, 0, 1600, 400))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=1200, h=1600),  # portrait native
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    assert len(out) == 1200 * 1600 // 2

    red_nibble = 0x3
    blue_nibble = 0x5
    white_nibble = 0x1
    # After CW rotation + letterbox-fit to 1200×1600, the 400-wide CW-
    # rotated strip lands centred. Red (was left) is the top half of
    # the centre strip; blue (was right) is the bottom half.
    assert _decode_pixel(out, 600, 400, 1200) == red_nibble
    assert _decode_pixel(out, 600, 1200, 1200) == blue_nibble
    # Letterbox columns left + right of the strip stay white.
    assert _decode_pixel(out, 100, 800, 1200) == white_nibble
    assert _decode_pixel(out, 1100, 800, 1200) == white_nibble


def test_portrait_source_to_landscape_panel_rotates_cw(registry) -> None:
    """7.3" PhotoPainter is landscape native. A portrait composition
    gets a 90° CW rotation so its top edge lands at the panel's RIGHT
    edge (think of physically rotating the image clockwise: north →
    east). Red TOP + blue BOTTOM portrait input ends up as red on the
    RIGHT, blue on the LEFT of the 800×480 landscape buffer.

    Without this, the firmware would feed the panel a 400-byte/row
    stride against the renderer's 240-byte/row pack — paints garbled
    vertical ghosts (the reported PhotoPainter symptom)."""
    img = Image.new("RGB", (480, 800), "white")
    img.paste((255, 0, 0), (0, 0, 480, 400))
    img.paste((0, 0, 255), (0, 400, 480, 800))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=800, h=480),  # landscape native
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    assert len(out) == 800 * 480 // 2  # 192000 bytes — matches the firmware report

    red_nibble = 0x3
    blue_nibble = 0x5
    # CW rotation maps original top → right, original bottom → left.
    # Red (was on top) lands on the panel's RIGHT half; blue (was on
    # bottom) lands on the LEFT half.
    assert _decode_pixel(out, 200, 240, 800) == blue_nibble  # mid of left half
    assert _decode_pixel(out, 600, 240, 800) == red_nibble  # mid of right half


def test_portrait_calibration_on_landscape_native_panel(registry) -> None:
    """Reported bug: user runs the device calibration on a 7.3"
    PhotoPainter and picks the portrait option. The device record
    then has panel.w=480, panel.h=800 (portrait composition). The
    panel hardware is still landscape-native (800w × 480h fixed in
    firmware) — packing at the panel arg's 480×800 stride puts the
    bytes back at 240 bytes/row vs the firmware's 400, the ghosts
    return.

    Fix: the renderer resolves the firmware-native dims from the
    panel's pixel count (800 × 480 = 384000 → 800×480 landscape),
    rotates the portrait composition 90° CW to fit, and packs at
    800×480 regardless of which orientation the user calibrated
    for."""
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
        panel=Panel(w=480, h=800),  # PORTRAIT calibration on the PhotoPainter
        settings={"dither": "none", "saturation": 1.0, "contrast": 1.0},
    )
    # Output is the firmware-native 800×480 landscape buffer — 192000
    # bytes at 400 bytes/row — regardless of which calibration the
    # user picked.
    assert len(out) == 800 * 480 // 2

    red_nibble = 0x3
    blue_nibble = 0x5
    # CW rotation: red (top of portrait) → right of landscape; blue
    # (bottom of portrait) → left of landscape.
    assert _decode_pixel(out, 200, 240, 800) == blue_nibble
    assert _decode_pixel(out, 600, 240, 800) == red_nibble


def test_landscape_calibration_on_portrait_native_panel(registry) -> None:
    """Symmetric case: a 13.3" Waveshare Spectra 6 is portrait native
    (1200×1600). If the user calibrates landscape, panel arrives as
    (1600, 1200). The renderer must still pack at 1200×1600 portrait
    (firmware-fixed) and rotate the landscape composition to fit."""
    # Landscape composition at the panel.w × panel.h shape: red LEFT,
    # blue RIGHT.
    img = Image.new("RGB", (1600, 1200), "white")
    img.paste((255, 0, 0), (0, 0, 800, 1200))
    img.paste((0, 0, 255), (800, 0, 1600, 1200))

    esp = registry.get("esp32_bin")
    assert esp is not None
    out = esp.transform(
        _png_bytes(img),
        panel=Panel(w=1600, h=1200),  # landscape calibration on the 13.3"
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
