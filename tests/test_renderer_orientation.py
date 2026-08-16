"""Which way a renderer turns an upload (discussion #231).

The rule every bundled renderer has to follow: a turn belongs to the
*panel*, never to the picture. An uploaded image is fitted into the
display's composition first, and only then rotated to the firmware's
native row stride if those two disagree. Deciding the turn from the
source image's own aspect instead sent every portrait picture to a
landscape panel on its side.

The packers are stubbed out so the assertions read against the image
each renderer hands them, rather than against packed bits.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from PIL import Image

from app.main import REPO_ROOT
from app.state.page_store import Panel

# renderer id -> the packer name in its module namespace.
PACKERS: dict[str, str] = {
    "esp32_bin": "pack_to_panel_bin",
    "esp32_bw_bin": "pack_to_panel_bin_1bpp",
    "esp32_gray_bin": "pack_to_panel_bin_4bpp_gray",
    "esp32_gray2_bin": "pack_to_panel_bin_2bpp_gray",
    "pi_bin": "pack_to_panel_bin",
    "pico_bin": "pack_to_panel_bin",
}

RED = (220, 30, 30)
BLUE = (30, 30, 220)


def _load(name: str) -> ModuleType:
    path = Path(REPO_ROOT) / "renderers" / name / "renderer.py"
    spec = importlib.util.spec_from_file_location(f"_test_orient_{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _portrait_png() -> bytes:
    """A tall picture, red on top and blue underneath, so a quarter turn
    is visible as the split moving from horizontal to vertical."""
    img = Image.new("RGB", (200, 400), RED)
    img.paste(Image.new("RGB", (200, 200), BLUE), (0, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _packed_image(name: str, panel: Panel, png: bytes, **settings: Any) -> Image.Image:
    """Run a renderer's transform, returning the image it went to pack."""
    mod = _load(name)
    seen: list[Image.Image] = []

    def fake_pack(img: Image.Image, **kw: Any) -> bytes:
        seen.append(img.convert("RGB"))
        return b""

    setattr(mod, PACKERS[name], fake_pack)
    mod.transform(png, panel=panel, settings=dict(settings))
    assert seen, "renderer should have packed something"
    return seen[0]


@pytest.mark.parametrize("name", sorted(PACKERS))
def test_portrait_upload_is_not_turned_on_a_landscape_panel(name: str) -> None:
    """The regression: a portrait picture keeps its orientation on a
    landscape panel, letterboxed down the middle rather than laid on its
    side. Three renderers read the turn off the source image's aspect,
    so every webcomic and phone photo arrived rotated."""
    panel = Panel(w=400, h=200, native_w=400, native_h=200)
    packed = _packed_image(name, panel, _portrait_png(), image_fit="fit")

    assert packed.size == (400, 200)
    # Centre column: red above the midpoint, blue below. A quarter turn
    # would put the split across the columns instead.
    assert packed.getpixel((200, 40))[0] > packed.getpixel((200, 40))[2]
    assert packed.getpixel((200, 160))[2] > packed.getpixel((200, 160))[0]


@pytest.mark.parametrize("name", sorted(PACKERS))
def test_portrait_upload_fills_a_portrait_panel(name: str) -> None:
    """A portrait panel (composition taller than wide, landscape-native
    buffer) takes the same picture the other way round: it fills the
    composition, and the turn that follows belongs to the panel's stride,
    not to the picture."""
    panel = Panel(w=200, h=400, native_w=400, native_h=200)
    packed = _packed_image(name, panel, _portrait_png(), image_fit="fit")

    assert packed.size == (400, 200)
    # The source is the panel's aspect, so it fills the composition edge
    # to edge: every column carries both colours, none is letterbox white.
    colours = {packed.getpixel((x, y)) for x in (10, 200, 390) for y in (10, 190)}
    assert (255, 255, 255) not in colours


def test_pi_png_turns_the_panel_not_the_picture() -> None:
    """pi_png hands the client the image plus a ``scale`` and lets it fit,
    so the server only contributes the panel's turn. Turning before the
    client's fit is safe here: an aspect-preserving fit into the swapped
    rectangle commutes with a quarter turn, so the picture lands the same
    either way."""
    mod = _load("pi_png")
    out = mod.transform(_portrait_png(), panel=Panel(w=200, h=400), settings={"image_fit": "fit"})

    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (400, 200)


def test_pi_png_leaves_a_landscape_panel_alone() -> None:
    """No turn, no change: a landscape panel passes the bytes through for
    the client to fit, as the v3 payload contract has always described."""
    mod = _load("pi_png")
    png = _portrait_png()
    out = mod.transform(png, panel=Panel(w=400, h=200), settings={"image_fit": "fit"})
    assert out == png
