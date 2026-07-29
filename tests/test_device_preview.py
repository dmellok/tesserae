"""Logical device-preview generation and retention."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.device_preview import (
    build_device_preview_png,
    retained_device_preview,
    write_device_preview,
)
from app.state.page_store import Panel


def _source_png() -> bytes:
    image = Image.new("RGB", (4, 2), "red")
    for y in range(image.height):
        for x in range(2, image.width):
            image.putpixel((x, y), (0, 0, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _preview(*, fit: str, panel: Panel | None = None) -> Image.Image:
    raw = build_device_preview_png(
        _source_png(),
        panel=panel or Panel(w=8, h=8),
        settings={"image_fit": fit},
    )
    with Image.open(io.BytesIO(raw)) as image:
        return image.convert("RGB")


@pytest.mark.parametrize("fit", ["fit", "fill", "blur", "stretch", "center"])
def test_all_photo_fit_modes_produce_exact_logical_panel_size(fit: str) -> None:
    assert _preview(fit=fit).size == (8, 8)


def test_fit_modes_preserve_their_distinct_layout_semantics() -> None:
    fit = _preview(fit="fit")
    fill = _preview(fit="fill")
    blur = _preview(fit="blur")
    stretch = _preview(fit="stretch")
    center = _preview(fit="center")

    assert fit.getpixel((0, 0)) == (255, 255, 255)
    assert center.getpixel((0, 0)) == (255, 255, 255)
    assert fill.getpixel((0, 0)) != (255, 255, 255)
    assert stretch.getpixel((0, 0)) != (255, 255, 255)
    assert blur.getpixel((0, 0)) != (255, 255, 255)

    # Fit scales the full source up; Center keeps the original 4x2 pixels.
    fit_nonwhite = sum(pixel != (255, 255, 255) for pixel in fit.getdata())
    center_nonwhite = sum(pixel != (255, 255, 255) for pixel in center.getdata())
    assert fit_nonwhite > center_nonwhite


def test_preview_stays_upright_when_wire_artifact_needs_mount_compensation() -> None:
    normal = _preview(fit="stretch", panel=Panel(w=3, h=5))
    compensated = _preview(
        fit="stretch",
        panel=Panel(
            w=3,
            h=5,
            flip=True,
            vflip=True,
            native_w=5,
            native_h=3,
        ),
    )

    assert compensated.size == (3, 5)
    assert list(compensated.getdata()) == list(normal.getdata())
    assert compensated.getpixel((0, 0))[0] > compensated.getpixel((0, 0))[2]
    assert compensated.getpixel((2, 0))[2] > compensated.getpixel((2, 0))[0]


def test_preview_applies_logical_underscan() -> None:
    preview = _preview(fit="stretch", panel=Panel(w=8, h=8, underscan=2))

    assert preview.getpixel((0, 0)) == (255, 255, 255)
    assert preview.getpixel((2, 2)) != (255, 255, 255)


def test_preview_uses_renderer_letterbox_background() -> None:
    raw = build_device_preview_png(
        _source_png(),
        panel=Panel(w=8, h=8),
        settings={"scale": "fit", "bg": "black"},
    )
    with Image.open(io.BytesIO(raw)) as image:
        assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 0)


def test_content_addressed_preview_round_trip_and_safe_lookup(tmp_path: Path) -> None:
    digest = write_device_preview(
        tmp_path,
        _source_png(),
        panel=Panel(w=8, h=8),
        settings={"image_fit": "fill"},
    )
    retained = retained_device_preview(tmp_path, digest)

    assert len(digest) == 16
    assert retained is not None
    assert retained.etag == digest
    assert retained.path == (tmp_path / f"{digest}.png").resolve()
    assert retained_device_preview(tmp_path, f"../{digest}") is None
