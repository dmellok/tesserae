"""esp32_bw_bin renderer smoke.

Locks down the firmware wire contract: exactly ``width*height/8`` bytes,
8 pixels per byte, MSB = leftmost, bit-set = white. A regression in
the packer would make every panel-paint silently corrupt.
"""

from __future__ import annotations

import io
import sys

import pytest
from PIL import Image

from app.device_loader import discover
from app.main import REPO_ROOT
from app.renderer_loader import discover as renderer_discover

# Wire format: 1 bit per pixel, 8 pixels per byte.
PANEL_W = 400
PANEL_H = 300
EXPECTED_BYTES = PANEL_W * PANEL_H // 8


# Tesserae's renderer_loader registers each module under the folder
# name, so the renderer module ends up importable as ``renderer`` once
# we add its dir to sys.path (mirrors how the package's own tests do
# it for plugins).
sys.path.insert(0, str(REPO_ROOT / "renderers" / "esp32_bw_bin"))
import renderer  # noqa: E402


@pytest.fixture
def panel(tmp_path):
    """A discovered esp32_bw_client device's Panel. Goes through the
    same resolution path the live renderer uses (device manifest +
    PANEL_PRESETS auto-match), so any drift between preset and
    renderer is caught here."""
    from app.panel import device_panel

    devices = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    d = devices.get("esp32_bw_client")
    assert d is not None
    p = device_panel(d)
    assert p is not None
    return p


def _solid_png(rgb: tuple[int, int, int], *, size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=rgb).save(buf, format="PNG")
    return buf.getvalue()


def test_renderer_is_registered(tmp_path) -> None:
    renderers = renderer_discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert renderers.errors == [], renderers.errors
    r = renderers.get("esp32_bw_bin")
    assert r is not None
    assert r.name == "ESP32 BW client (1-bpp .bin)"
    assert r.extension == "bin"
    assert r.retain is True


def test_transform_emits_exactly_15000_bytes(panel) -> None:
    png = _solid_png((128, 128, 128), size=(PANEL_W, PANEL_H))
    out = renderer.transform(png, panel=panel, settings={})
    assert len(out) == EXPECTED_BYTES


def test_all_white_input_packs_to_all_0xff(panel) -> None:
    """The firmware spec says bit-set = white. A pure-white frame
    must therefore pack to all 0xFF bytes; a regression that flipped
    the bit convention (or used MSB-last) would explode here."""
    png = _solid_png((255, 255, 255), size=(PANEL_W, PANEL_H))
    out = renderer.transform(png, panel=panel, settings={"dither": "none"})
    assert len(out) == EXPECTED_BYTES
    assert out == b"\xff" * EXPECTED_BYTES


def test_all_black_input_packs_to_all_0x00(panel) -> None:
    """Symmetric to the white test, locks down both ends."""
    png = _solid_png((0, 0, 0), size=(PANEL_W, PANEL_H))
    out = renderer.transform(png, panel=panel, settings={"dither": "none"})
    assert len(out) == EXPECTED_BYTES
    assert out == b"\x00" * EXPECTED_BYTES


def test_leftmost_pixel_is_msb(panel) -> None:
    """Construct a frame with only the leftmost column white. Each
    byte's MSB should set, so every byte is 0x80."""
    img = Image.new("RGB", (PANEL_W, PANEL_H), color=(0, 0, 0))
    # Paint a single 1-pixel-wide white stripe at x=0.
    pixels = img.load()
    for y in range(PANEL_H):
        pixels[0, y] = (255, 255, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = renderer.transform(buf.getvalue(), panel=panel, settings={"dither": "none"})
    # First byte of each row covers pixels 0..7; only x=0 is white, so
    # the byte should be 0b10000000 = 0x80. Bytes-per-row = width/8.
    bpr = PANEL_W // 8
    for row in range(PANEL_H):
        assert out[row * bpr] == 0x80, f"row {row} first byte = {out[row * bpr]:#x}"


def test_payload_url_uses_bin_extension(panel) -> None:
    out = renderer.payload("deadbeef", "http://example.local:8000/", settings={})
    assert out == {"url": "http://example.local:8000/renders/deadbeef.bin"}


def test_profile_native_colour_guard_reaches_the_packer(panel) -> None:
    """The renderer reads the palette profile's edge block. Without the
    passthrough the guard would look enabled in Settings and do nothing
    on a mono panel (discussion #227)."""
    img = Image.new("RGB", (PANEL_W, PANEL_H), color=(248, 246, 242))
    for y in range(PANEL_H):
        for x in range(PANEL_W // 2):
            img.putpixel((x, y), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    plain = renderer.transform(png, panel=panel, settings={})
    guarded = renderer.transform(
        png, panel=panel, settings={"_profile_edges": {"protect_native_colours": 24}}
    )
    assert plain != guarded
    # A profile that leaves the guard off must not move any bytes.
    off = renderer.transform(
        png, panel=panel, settings={"_profile_edges": {"protect_native_colours": 0}}
    )
    assert off == plain
