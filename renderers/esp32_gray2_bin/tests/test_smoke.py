"""esp32_gray2_bin renderer smoke: composition PNG in, raw 2-bpp
grayscale packed buffer out. Byte-contract checks so a firmware update
doesn't get surprised by a silent format drift."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def esp32_gray2_bin(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("esp32_gray2_bin")
    assert renderer is not None
    return renderer


@pytest.fixture
def composition_png() -> bytes:
    """An 800x480 horizontal gradient at the E1001's native dims so the
    renderer skips the resize path and we exercise the pack step
    directly."""
    img = Image.new("RGB", (800, 480))
    for x in range(800):
        g = (x * 255) // 800
        for y in range(480):
            img.putpixel((x, y), (g, g, g))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_esp32_gray2_bin_manifest_fields(esp32_gray2_bin) -> None:
    assert esp32_gray2_bin.device == "esp32"
    assert esp32_gray2_bin.orientation == "composition"
    assert esp32_gray2_bin.extension == "bin"
    assert esp32_gray2_bin.mime == "application/octet-stream"
    assert esp32_gray2_bin.retain is True
    assert esp32_gray2_bin.topic == "tesserae/esp32/frame/bin"


def test_esp32_gray2_bin_e1001_byte_count(esp32_gray2_bin, composition_png) -> None:
    """The E1001 case: 800x480 panel -> exactly 96000 bytes. The
    firmware's image decoder demands this exact count and refuses to
    paint on any mismatch."""
    panel = Panel(w=800, h=480, gamut="mono", native_w=800, native_h=480)
    artifact = esp32_gray2_bin.transform(
        composition_png, panel=panel, settings=esp32_gray2_bin.settings_defaults()
    )
    assert len(artifact) == 800 * 480 // 4


def test_esp32_gray2_bin_black_and_white_extremes(esp32_gray2_bin) -> None:
    """Solid black packs to all 0x00, solid white to all 0xFF: the
    2-bit fields are 0b00 = black and 0b11 = white, MSB first."""
    panel = Panel(w=800, h=480, gamut="mono", native_w=800, native_h=480)
    for colour, expected_byte in (((0, 0, 0), 0x00), ((255, 255, 255), 0xFF)):
        img = Image.new("RGB", (800, 480), colour)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        artifact = esp32_gray2_bin.transform(
            buf.getvalue(), panel=panel, settings=esp32_gray2_bin.settings_defaults()
        )
        assert artifact == bytes([expected_byte]) * (800 * 480 // 4)


def test_esp32_gray2_bin_msb_first_pixel_order(esp32_gray2_bin) -> None:
    """A single white pixel at the top-left corner must land in bits
    7-6 of byte 0 (leftmost pixel of the first 4-pixel group)."""
    img = Image.new("RGB", (800, 480), (0, 0, 0))
    img.putpixel((0, 0), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    panel = Panel(w=800, h=480, gamut="mono", native_w=800, native_h=480)
    settings = dict(esp32_gray2_bin.settings_defaults())
    settings["dither"] = "none"
    artifact = esp32_gray2_bin.transform(buf.getvalue(), panel=panel, settings=settings)
    assert artifact[0] == 0b11000000
    assert artifact[1] == 0x00


def test_esp32_gray2_bin_portrait_composition_rotates(esp32_gray2_bin) -> None:
    """A portrait composition rotates to the landscape-native panel and
    still emits exactly 96000 bytes."""
    img = Image.new("RGB", (480, 800), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    panel = Panel(w=480, h=800, gamut="mono", native_w=800, native_h=480)
    artifact = esp32_gray2_bin.transform(
        buf.getvalue(), panel=panel, settings=esp32_gray2_bin.settings_defaults()
    )
    assert len(artifact) == 96000
