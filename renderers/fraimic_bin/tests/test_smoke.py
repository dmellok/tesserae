"""fraimic_bin: controller byte order for both Fraimic panels.

The EL315 checks use the spec's own byte walkthrough and its coordinate
formula (section 5), written out independently of the reshape in the
renderer, so a plain-transpose mistake (which the spec warns still looks
plausible on a photo) fails here rather than on the wall.
"""

from __future__ import annotations

import io
import random

import numpy as np
import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.renderer_loader import discover
from app.state.page_store import Panel

CODES = (0x0, 0x1, 0x2, 0x3, 0x5, 0x6)


@pytest.fixture
def fraimic_bin(tmp_path):
    registry = discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    renderer = registry.get("fraimic_bin")
    assert renderer is not None
    return renderer


@pytest.fixture
def mod(fraimic_bin):
    return fraimic_bin.module


def _png(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _random_codes(h: int, w: int, seed: int = 7) -> np.ndarray:
    rng = random.Random(seed)
    return np.array([[rng.choice(CODES) for _ in range(w)] for _ in range(h)], dtype=np.uint8)


def test_manifest(fraimic_bin) -> None:
    assert fraimic_bin.extension == "bin"
    assert fraimic_bin.mime == "application/octet-stream"
    assert fraimic_bin.retain is False


def test_el315_spec_walkthrough(mod) -> None:
    # Spec "Minimal Example": bottom two rows of columns 0-1 are
    # y=2558 [Yellow, Red], y=2559 [Black, White] -> file starts 0x01 0x23.
    codes = np.ones((2560, 1440), dtype=np.uint8)
    codes[2559, 0], codes[2559, 1] = 0x0, 0x1
    codes[2558, 0], codes[2558, 1] = 0x2, 0x3
    out = mod.pack_el315(codes)
    assert len(out) == 2_304_000
    assert out[0] == 0x01 and out[1] == 0x23


def test_el315_matches_spec_formula(mod) -> None:
    codes = _random_codes(2560, 1440)
    out = np.frombuffer(mod.pack_el315(codes), dtype=np.uint8)
    rng = random.Random(3)
    for _ in range(4000):
        k = rng.randint(1, 8)
        g, j = (k - 1) // 4, (k - 1) % 4
        b = rng.randint(0, 719)
        p = rng.randint(0, 159 if j == 3 else 799)
        q = j * 800 + p
        x, y = 2 * b + (q % 2), 2559 - (g * 1280 + q // 2)
        byte = out[(k - 1) * 288_000 + b * 400 + p // 2]
        nibble = byte >> 4 if p % 2 == 0 else byte & 0x0F
        assert nibble == codes[y, x], (k, b, p)


def test_el315_padding(mod) -> None:
    out = np.frombuffer(mod.pack_el315(_random_codes(2560, 1440)), dtype=np.uint8)
    blocks = out.reshape(8, 720, 400)
    for ic in (3, 7):  # IC4, IC8
        assert (blocks[ic][:, 80:] == 0x11).all()


def test_el133_halves(mod) -> None:
    codes = _random_codes(1600, 1200)
    out = mod.pack_el133(codes)
    assert len(out) == 960_000
    # Row 5, columns 600/601 are the first byte of row 5 in the right half.
    assert out[480_000 + 5 * 300] == (codes[5, 600] << 4) | codes[5, 601]
    assert out[5 * 300 + 2] == (codes[5, 4] << 4) | codes[5, 5]


@pytest.mark.parametrize(
    ("colour", "code"),
    [((0, 0, 255), 0x5), ((0, 255, 0), 0x6), ((255, 0, 0), 0x3), ((255, 255, 0), 0x2)],
)
def test_transform_uses_fraimic_codes(fraimic_bin, colour, code) -> None:
    panel = Panel(w=1200, h=1600, gamut="spectra_6")
    settings = {**fraimic_bin.settings_defaults(), "dither": "none", "saturation": 1.0}
    out = fraimic_bin.transform(_png((1200, 1600), colour), panel=panel, settings=settings)
    assert len(out) == 960_000
    assert set(out) == {(code << 4) | code}


def test_transform_31_5_size_and_fit(fraimic_bin) -> None:
    panel = Panel(w=1440, h=2560, gamut="spectra_6")
    out = fraimic_bin.transform(
        _png((300, 400), (255, 255, 255)), panel=panel, settings=fraimic_bin.settings_defaults()
    )
    assert len(out) == 2_304_000


def test_transform_landscape_panel_rotates_to_portrait(fraimic_bin) -> None:
    # Left half red, right half blue on a landscape composition: turned to
    # portrait, red is the top half and blue the bottom.
    img = Image.new("RGB", (1600, 1200), (255, 0, 0))
    img.paste((0, 0, 255), (800, 0, 1600, 1200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    panel = Panel(w=1600, h=1200, gamut="spectra_6")
    settings = {**fraimic_bin.settings_defaults(), "dither": "none", "saturation": 1.0}
    out = fraimic_bin.transform(buf.getvalue(), panel=panel, settings=settings)
    left = out[:480_000]
    assert left[0] == 0x33  # top row
    assert left[-1] == 0x55  # bottom row


def test_other_panel_sizes_pack_as_31_5(fraimic_bin) -> None:
    # An unbound page renders every base renderer at its own panel size.
    out = fraimic_bin.transform(_png((800, 480), (0, 0, 0)), panel=Panel(w=800, h=480), settings={})
    assert len(out) == 2_304_000
