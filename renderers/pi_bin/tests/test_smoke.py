"""pi_bin smoke: composition PNG packs to the expected byte count, manifest
fields are wired right, payload matches the .bin contract."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel


@pytest.fixture
def pi_bin(tmp_path):
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


def test_pi_bin_manifest_fields(pi_bin) -> None:
    assert pi_bin.device == "pi_bin"
    assert pi_bin.orientation == "composition"
    assert pi_bin.extension == "bin"
    assert pi_bin.mime == "application/octet-stream"
    assert pi_bin.retain is False
    assert pi_bin.topic == "tesserae/pi_bin/frame/bin"


def test_pi_bin_transform_produces_4bpp_buffer(pi_bin, panel_png) -> None:
    panel = Panel(w=100, h=80)
    artifact = pi_bin.transform(panel_png, panel=panel, settings=pi_bin.settings_defaults())
    # Two pixels per byte, scanline order — width * height / 2 bytes total.
    assert len(artifact) == 100 * 80 // 2


def test_pi_bin_transform_resizes_mismatched_input(pi_bin) -> None:
    # An off-panel upload (e.g. Send-page image) must be fit, not rejected.
    img = Image.new("RGB", (50, 50), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    panel = Panel(w=100, h=80)
    artifact = pi_bin.transform(buf.getvalue(), panel=panel, settings=pi_bin.settings_defaults())
    assert len(artifact) == 100 * 80 // 2


def test_pi_bin_payload_is_just_url(pi_bin) -> None:
    payload = pi_bin.payload("abc123", "http://192.168.1.10:8000", settings={})
    assert payload == {"url": "http://192.168.1.10:8000/renders/abc123.bin"}
