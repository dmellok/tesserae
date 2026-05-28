"""pi_png renderer smoke: composition PNG in, rotated PNG out, payload
matches the v3-frozen contract."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def pi_png(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("pi_png")
    assert renderer is not None
    return renderer


@pytest.fixture
def composition_png() -> bytes:
    """A 200x100 portrait-ish PNG to exercise the rotate-to-landscape path."""
    img = Image.new("RGB", (200, 100), (220, 119, 87))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_pi_png_manifest_fields(pi_png) -> None:
    assert pi_png.device == "pi_png"
    assert pi_png.orientation == "landscape"
    assert pi_png.extension == "png"
    assert pi_png.mime == "image/png"
    assert pi_png.retain is False
    assert pi_png.topic == "tesserae/pi_png/frame/png"


def test_pi_png_landscape_panel_is_identity(pi_png, composition_png) -> None:
    # Landscape panel (w > h): no rotation — matches pi_bin, which only
    # turns when the panel is portrait. (Was a fixed 270° before; that
    # left landscape pages sideways.)
    panel = Panel(w=200, h=100)
    artifact = pi_png.transform(composition_png, panel=panel, settings=pi_png.settings_defaults())
    assert Image.open(io.BytesIO(artifact)).size == (200, 100)


def test_pi_png_portrait_panel_rotates_to_landscape(pi_png, composition_png) -> None:
    # Portrait panel (w < h): 90° CCW turn (3 CW quarters), same as
    # pi_bin, mapping the tall composition onto the client's landscape buffer.
    panel = Panel(w=100, h=200)
    artifact = pi_png.transform(composition_png, panel=panel, settings=pi_png.settings_defaults())
    # composition_png is 200x100; a 90° turn makes it 100x200.
    assert Image.open(io.BytesIO(artifact)).size == (100, 200)


def test_pi_png_flip_adds_180(pi_png) -> None:
    # flip adds 2 quarter-turns (180°) on top of the renderer's base, so
    # output dims match the un-flipped frame but the content is turned
    # the opposite way. Use a non-uniform image (top half red, bottom
    # half blue) so the 180° turn is detectable byte-wise.
    src = Image.new("RGB", (200, 100), (220, 119, 87))
    for y in range(50):
        for x in range(200):
            src.putpixel((x, y), (0, 0, 255))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    comp = buf.getvalue()
    settings = pi_png.settings_defaults()
    normal = pi_png.transform(comp, panel=Panel(w=200, h=100, flip=False), settings=settings)
    flipped = pi_png.transform(comp, panel=Panel(w=200, h=100, flip=True), settings=settings)
    assert Image.open(io.BytesIO(normal)).size == Image.open(io.BytesIO(flipped)).size
    assert normal != flipped  # 180° apart


def test_pi_png_payload_matches_v3_contract(pi_png) -> None:
    payload = pi_png.payload(
        "abc123", "http://192.168.1.10:8000", settings=pi_png.settings_defaults()
    )
    assert payload == {
        "url": "http://192.168.1.10:8000/renders/abc123.png",
        "rotate": 0,
        "scale": "fit",
        "bg": "white",
        "saturation": 0.5,
    }
