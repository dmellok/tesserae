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


def test_pi_png_transform_rotates(pi_png, composition_png) -> None:
    panel = Panel(w=200, h=100)
    artifact = pi_png.transform(composition_png, panel=panel, settings=pi_png.settings_defaults())
    img = Image.open(io.BytesIO(artifact))
    # Default transform_rotate_quarters=1 (90 CW): 200x100 -> 100x200.
    assert img.size == (100, 200)


def test_pi_png_transform_quarters_zero_is_identity(pi_png, composition_png) -> None:
    panel = Panel(w=200, h=100)
    artifact = pi_png.transform(
        composition_png,
        panel=panel,
        settings={**pi_png.settings_defaults(), "transform_rotate_quarters": 0},
    )
    img = Image.open(io.BytesIO(artifact))
    assert img.size == (200, 100)


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
