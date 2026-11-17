"""Decode the real renderer output without changing pixel decisions."""

import io

import pytest
from PIL import Image

from app.panel_preview import render_panel_preview
from app.state.page_store import Panel


@pytest.mark.parametrize(
    "renderer_id,gamut",
    [
        ("esp32_bw_bin", "mono"),
        ("esp32_gray2_bin", "gray_4"),
        ("esp32_gray_bin", "gray_16"),
        ("esp32_bin", "waveshare_e6"),
        ("esp32_bin", "inky_7colour"),
        ("esp32_bin", "bwry_4"),
        ("pi_bin", "waveshare_e6"),
        ("pico_bin", "waveshare_e6"),
        ("trmnl_png_gray16", "gray_16"),
        ("trmnl_png", "mono"),
    ],
)
@pytest.mark.parametrize("size", [(32, 24), (24, 32)])
def test_preview_roundtrips_panel_pixel_values(app, renderer_id, gamut, size):
    renderer = app.config["RENDERER_REGISTRY"].get(renderer_id)
    image = Image.new("RGB", size)
    for x in range(size[0]):
        image.paste((x * 7, 100, 220 - x * 5), (x, 0, x + 1, size[1]))
    out = io.BytesIO()
    image.save(out, "PNG")
    panel = Panel(w=size[0], h=size[1], gamut=gamut)
    settings = {"dither": "floyd-steinberg", "saturation": 1.0, "contrast": 1.0}
    preview = render_panel_preview(
        out.getvalue(), renderer=renderer, panel=panel, settings=settings
    )
    with Image.open(io.BytesIO(preview)) as decoded:
        assert decoded.size == size
    original = renderer.transform(out.getvalue(), panel=panel, settings=settings)
    repacked = renderer.transform(preview, panel=panel, settings={**settings, "dither": "none"})
    assert repacked == original


def test_preview_paints_a_calibrated_device_in_its_measured_inks(app):
    """A device with a palette profile is previewed in the profile's colours, as the glass shows
    them; the pixel decisions (which ink goes where) stay the packer's."""
    from app.palette_profiles.bundled import bundled_profile

    profile = bundled_profile("paperlesspaper-spectra6")
    assert profile is not None
    renderer = app.config["RENDERER_REGISTRY"].get("esp32_bin")
    image = Image.new("RGB", (16, 8), (255, 255, 255))
    image.paste((0, 0, 0), (0, 0, 8, 8))
    out = io.BytesIO()
    image.save(out, "PNG")
    panel = Panel(w=16, h=8, gamut="waveshare_e6")
    settings = {"dither": "floyd-steinberg", "_palette_override": profile.palette.as_tuples()}
    preview = render_panel_preview(
        out.getvalue(), renderer=renderer, panel=panel, settings=settings
    )
    with Image.open(io.BytesIO(preview)) as decoded:
        assert decoded.convert("RGB").getpixel((2, 2)) == (0x1F, 0x22, 0x26)
        assert decoded.convert("RGB").getpixel((12, 2)) == (0xB9, 0xC7, 0xC9)


@pytest.mark.parametrize(
    "renderer_id,gamut,bits,profile",
    [
        ("esp32_bin", "waveshare_e6", 4, None),
        ("esp32_bin", "waveshare_e6", 4, "paperlesspaper-spectra6"),
        ("esp32_gray_bin", "gray_16", 4, None),
        ("esp32_bw_bin", "mono", 1, None),
    ],
)
@pytest.mark.parametrize("rotated,flipped", [(False, False), (True, False), (False, True)])
def test_preview_pixels_match_the_real_push_artifact(
    app, monkeypatch, renderer_id, gamut, bits, profile, rotated, flipped
):
    """Compare decoded preview pixels with the bytes written by a real push.

    The transport is silenced; settings, transform and artifact writing are real.
    """
    from dataclasses import replace

    import numpy as np

    from app.palette_profiles.bundled import bundled_profile
    from app.quantizer import align_palette_override, palette_for_gamut

    registry = app.config["RENDERER_REGISTRY"]
    renderer = registry.get(renderer_id)
    renderer = replace(renderer, manifest={**renderer.manifest, "device": "target"})
    registry.renderers[renderer_id] = renderer
    if profile:
        app.config["SETTINGS_STORE"].patch_section(
            "devices", {"target": {"palette_profile_slug": profile}}
        )
        app.config["SETTINGS_STORE"].patch_section("renderers", {renderer_id: {"calibrated": True}})
    panel = Panel(
        w=32,
        h=24,
        gamut=gamut,
        native_w=24 if rotated else 32,
        native_h=32 if rotated else 24,
        flip=flipped,
    )
    image = Image.new("RGB", (32, 24))
    image.putdata([(x * 7, y * 9, (x * 13 + y * 7) % 256) for y in range(24) for x in range(32)])
    source = io.BytesIO()
    image.save(source, "PNG")
    from types import SimpleNamespace

    from app.composer import page_preview_token
    from app.dither_regions import regions_from_page
    from app.state.page_store import Cell, Page

    page = Page(
        id="parity",
        name="Parity",
        panel=panel,
        device_ids=["target"],
        cells=[Cell(id="flat", plugin="webpage", x=0, y=0, w=16, h=24, dither="none")],
    )
    app.config["PAGE_STORE"].save(page)
    device = SimpleNamespace(
        id="target",
        panel={
            **panel.model_dump(),
            "orientation": "landscape_flipped" if flipped else "landscape",
        },
        renderer_ids=[renderer_id],
    )
    app.config["DEVICE_REGISTRY"] = SimpleNamespace(
        get=lambda key: device, devices={"target": device}
    )
    token = page_preview_token(page, (32, 24))
    cache = app.config["DATA_ROOT"] / "core" / "previews"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"parity~panel32x24__{token}.png").write_bytes(source.getvalue())
    manager = app.config["PUSH_MANAGER"]
    monkeypatch.setattr(manager._transport, "publish", lambda *args, **kwargs: None)
    settings = {
        **manager._resolve_render_settings(renderer),
        "_dither_regions": regions_from_page(page),
    }
    result = manager._publish_artifact(renderer, source.getvalue(), panel, settings=settings)
    artifact = (manager._renders_dir / f"{result.digest}.bin").read_bytes()
    packed = np.frombuffer(artifact, dtype=np.uint8)
    values = ((packed[:, None] >> np.arange(8 - bits, -1, -bits)) & ((1 << bits) - 1)).ravel()
    palette = [(0, 0, 0)] * (1 << bits)
    colours = palette_for_gamut(gamut)
    if profile:
        colours = align_palette_override(bundled_profile(profile).palette.as_tuples(), colours)
    wires = (0, 1, 2, 3, 5, 6) if gamut == "waveshare_e6" else range(len(colours))
    for wire, colour in zip(wires, colours, strict=True):
        palette[wire] = colour
    expected = Image.fromarray(
        np.asarray(palette, dtype=np.uint8)[values].reshape((panel.native_h, panel.native_w, 3))
    )
    if flipped:
        expected = expected.rotate(180)
    if rotated:
        expected = expected.rotate(90, expand=True)
    response = app.test_client().get("/compose/parity/panel.png?w=32&h=24&device=target")
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.data)) as actual:
        assert actual.size == expected.size
        assert actual.convert("RGB").tobytes() == expected.tobytes()


def test_grey_panel_preview_uses_measured_grey_inks(app):
    renderer = app.config["RENDERER_REGISTRY"].get("esp32_gray_bin")
    source = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(source, "PNG")
    preview = render_panel_preview(
        source.getvalue(),
        renderer=renderer,
        panel=Panel(w=32, h=24, gamut="gray_16"),
        settings={"dither": "none", "_gray_ramp": ["#242424", "#ddddcc"]},
    )
    with Image.open(io.BytesIO(preview)) as image:
        assert image.convert("RGB").getpixel((0, 0)) == (221, 221, 204)
