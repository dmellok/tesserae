"""esp32_bin smoke: confirms retain=True (the bit that distinguishes it
from pi_bin) and that it produces byte-identical output to pi_bin for
the same input — both pack against WAVESHARE_E6_PALETTE."""

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


@pytest.fixture
def panel_png() -> bytes:
    img = Image.new("RGB", (200, 100), (255, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_esp32_bin_manifest_fields(registry) -> None:
    renderer = registry.get("esp32_bin")
    assert renderer is not None
    assert renderer.device == "esp32"
    assert renderer.extension == "bin"
    assert renderer.retain is True  # the key distinguishing bit
    assert renderer.topic == "tesserae/esp32/frame/bin"


def test_esp32_bin_matches_pi_bin_bytes(registry, panel_png) -> None:
    pi_bin = registry.get("pi_bin")
    esp = registry.get("esp32_bin")
    assert pi_bin is not None and esp is not None
    panel = Panel(w=200, h=100)
    a = pi_bin.transform(panel_png, panel=panel, settings=pi_bin.settings_defaults())
    b = esp.transform(panel_png, panel=panel, settings=esp.settings_defaults())
    # Same packer + same palette + same input -> same output. This is what
    # lets the two renderers share a single on-disk artifact via the
    # content-addressed digest.
    assert a == b


def test_esp32_bin_payload(registry) -> None:
    renderer = registry.get("esp32_bin")
    assert renderer is not None
    assert renderer.payload("zzz", "http://x/", settings={}) == {"url": "http://x/renders/zzz.bin"}
