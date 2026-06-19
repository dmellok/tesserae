"""pico_bin smoke: composition PNG packs to the expected byte count,
manifest fields are wired right (retain True, landscape packing, topic
pattern resolved against device pico_bin), payload matches the .bin
contract. Mirrors renderers/pi_bin/tests/test_smoke.py with the retain
flag flipped + the device id swapped.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def pico_bin(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("pico_bin")
    assert renderer is not None
    return renderer


@pytest.fixture
def pi_bin(tmp_path):
    """Cross-check fixture: pi_bin must pack to the same bytes as
    pico_bin for the same input, so the content-addressed disk store
    can share one file when both renderers are active."""
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("pi_bin")
    assert renderer is not None
    return renderer


@pytest.fixture
def panel_png() -> bytes:
    img = Image.new("RGB", (100, 80), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_pico_bin_manifest_fields(pico_bin) -> None:
    assert pico_bin.device == "pico_bin"
    assert pico_bin.orientation == "composition"
    assert pico_bin.extension == "bin"
    assert pico_bin.mime == "application/octet-stream"
    # The point of pico_bin existing instead of being a pi_bin clone:
    # retain=True so a freshly-woken deep-sleep client sees the current
    # frame on first wake.
    assert pico_bin.retain is True
    assert pico_bin.topic == "tesserae/pico_bin/frame/bin"


def test_pico_bin_transform_produces_4bpp_landscape_buffer(pico_bin, panel_png) -> None:
    panel = Panel(w=100, h=80)
    artifact = pico_bin.transform(panel_png, panel=panel, settings=pico_bin.settings_defaults())
    # Two pixels per byte, scanline order, max(w,h) * min(w,h) / 2 bytes
    # total. Landscape-native packing means a portrait panel of (80, 100)
    # produces the same byte count as a landscape (100, 80) one.
    assert len(artifact) == 100 * 80 // 2


def test_pico_bin_transform_resizes_mismatched_input(pico_bin) -> None:
    # Send-page uploads aren't panel-sized; must be fit, not rejected.
    img = Image.new("RGB", (50, 50), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    panel = Panel(w=100, h=80)
    artifact = pico_bin.transform(
        buf.getvalue(), panel=panel, settings=pico_bin.settings_defaults()
    )
    assert len(artifact) == 100 * 80 // 2


def test_pico_bin_packs_identical_bytes_to_pi_bin(pico_bin, pi_bin, panel_png) -> None:
    """The whole point of pico_bin reusing pi_bin's transform() verbatim:
    same input + same panel + same settings produces byte-identical
    output, so content-addressed disk storage shares a single .bin file
    when both renderers are active in the same install."""
    panel = Panel(w=100, h=80)
    settings = pi_bin.settings_defaults()
    pi = pi_bin.transform(panel_png, panel=panel, settings=settings)
    pico = pico_bin.transform(panel_png, panel=panel, settings=settings)
    assert pi == pico


def test_pico_bin_packs_portrait_input_to_landscape_native(pico_bin) -> None:
    """A portrait-oriented panel (w < h) must still produce a landscape
    buffer: the renderer pre-rotates the composition 90 deg, the
    firmware does the final on-device rotation to the panel's mount
    orientation. Without the pre-rotate the firmware would receive a
    transposed row stride and the panel would paint ghosted scanlines."""
    img = Image.new("RGB", (80, 100), (0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    panel = Panel(w=80, h=100)
    artifact = pico_bin.transform(
        buf.getvalue(), panel=panel, settings=pico_bin.settings_defaults()
    )
    # max * min / 2 regardless of orientation; landscape packing.
    assert len(artifact) == 100 * 80 // 2


def test_pico_bin_payload_is_just_url(pico_bin) -> None:
    payload = pico_bin.payload("abc123", "http://192.168.1.10:8000", settings={})
    assert payload == {"url": "http://192.168.1.10:8000/renders/abc123.bin"}
