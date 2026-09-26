"""trmnl_png renderer smoke: composition PNG in, 1-bit dithered PNG out,
mapped onto the client's reported buffer.

Covers the rotation contract the renderer shares with ``esp32_bin`` /
``pi_png``: the composition is produced at ``panel.w × panel.h``, the
client paints its own buffer (``native_w × native_h``, reported by the
device via png-width/png-height). When the two disagree on aspect the
finished composition is turned 90° CW before the client's scaler can
stretch it out of shape — the squashed-landscape-on-a-portrait-Kindle
bug. Panels with no native block fall back to composition dims and
stay byte-compatible with the pre-rotation output.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def trmnl_png(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("trmnl_png")
    assert renderer is not None
    return renderer


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _landscape_comp(width: int = 200, height: int = 100) -> bytes:
    """A landscape composition: white on the left, black on the right.
    After a 90° CW turn onto a portrait buffer the white half (original
    left edge) lands on top and the black half at the bottom."""
    img = Image.new("RGB", (width, height), "white")
    # Right half black.
    img.paste((0, 0, 0), (width // 2, 0, width, height))
    return _png(img)


def _portrait_comp(width: int = 100, height: int = 200) -> bytes:
    """A portrait composition: white on top, black on the bottom. After
    a 90° CW turn onto a landscape buffer white lands on the right and
    black on the left (same mapping esp32_bin tests assert)."""
    img = Image.new("RGB", (width, height), "white")
    img.paste((0, 0, 0), (0, height // 2, width, height))
    return _png(img)


def test_manifest_matches_byos_wire_contract(trmnl_png) -> None:
    assert trmnl_png.device == "trmnl"
    assert trmnl_png.orientation == "composition"
    assert trmnl_png.extension == "png"
    assert trmnl_png.mime == "image/png"
    assert trmnl_png.retain is False
    assert trmnl_png.topic == "tesserae/trmnl/frame/trmnl"


def test_no_native_block_passes_composition_through_unchanged(trmnl_png) -> None:
    """Legacy / custom panels with no native block: output stays at the
    composition dims with no rotation, matching pre-fix behaviour."""
    panel = Panel(w=200, h=100, gamut="gray_16")
    out = Image.open(
        io.BytesIO(trmnl_png.transform(_landscape_comp(), panel=panel, settings={}))
    )
    assert out.size == (200, 100)
    assert out.mode == "1"


def test_landscape_composition_rotated_onto_portrait_buffer(trmnl_png) -> None:
    """The Kindle case: a landscape dashboard composed at 200×100 for a
    portrait 100×200 screen. Selecting "Rotation: 90" means the frame is
    turned so it fills the portrait screen; without this the client's
    scaler squashes the landscape into portrait."""
    panel = Panel(w=200, h=100, native_w=100, native_h=200)
    out = Image.open(
        io.BytesIO(trmnl_png.transform(_landscape_comp(), panel=panel, settings={}))
    )
    assert out.size == (100, 200)
    # Mode "1" pixels are 0 or 255. CW turn maps composition left (white)
    # → output top, right (black) → output bottom.
    assert out.getpixel((50, 10)) == 255
    assert out.getpixel((50, 190)) == 0


def test_portrait_composition_rotated_onto_landscape_buffer(trmnl_png) -> None:
    """Mirror case: portrait composition on a landscape buffer, the same
    mapping esp32_bin asserts (white top → right edge after CW turn)."""
    panel = Panel(w=100, h=200, native_w=200, native_h=100)
    out = Image.open(
        io.BytesIO(trmnl_png.transform(_portrait_comp(), panel=panel, settings={}))
    )
    assert out.size == (200, 100)
    assert out.getpixel((180, 50)) == 255  # white (original top) → right
    assert out.getpixel((20, 50)) == 0  # black (original bottom) → left


def test_flip_adds_180_after_rotation(trmnl_png) -> None:
    """panel.flip composes on top of the 90° turn: same dims, content
    turned the other way (white half ends up at the bottom)."""
    panel = Panel(w=200, h=100, native_w=100, native_h=200, flip=True)
    out = Image.open(
        io.BytesIO(trmnl_png.transform(_landscape_comp(), panel=panel, settings={}))
    )
    assert out.size == (100, 200)
    assert out.getpixel((50, 10)) == 0
    assert out.getpixel((50, 190)) == 255


def test_matching_aspects_render_without_rotation(trmnl_png) -> None:
    """A portrait composition on a portrait buffer with matching native
    dims: no turn (this is the KOReader default, 758×1024 both ways)."""
    comp = _portrait_comp(100, 200)
    panel = Panel(w=100, h=200, native_w=100, native_h=200)
    out = Image.open(
        io.BytesIO(trmnl_png.transform(comp, panel=panel, settings={}))
    )
    assert out.size == (100, 200)
    assert out.getpixel((50, 10)) == 255  # top still white
    assert out.getpixel((50, 190)) == 0  # bottom still black


def test_payload_is_selfcontained_url(trmnl_png) -> None:
    p = trmnl_png.payload("abc123def456", "http://tesserae.local:8765", settings={})
    assert p == {"url": "http://tesserae.local:8765/renders/abc123def456.png"}