"""Thumbnails keep the display's exact aspect ratio, so a card sized by that
ratio shows no strip of its background along the image."""

from __future__ import annotations

import pytest

from app.lineup_thumbs import exact_fit, exact_thumb_size


@pytest.mark.parametrize(
    ("size", "dim", "expected"),
    [
        ((3840, 2160), 360, (352, 198)),
        ((3840, 2160), 1200, (1200, 675)),
        ((2160, 3840), 360, (198, 352)),
        ((800, 480), 360, (360, 216)),
        ((1200, 1600), 360, (270, 360)),
        ((100, 50), 360, (100, 50)),
    ],
)
def test_exact_thumb_size(size: tuple[int, int], dim: int, expected: tuple[int, int]) -> None:
    assert exact_thumb_size(size, dim) == expected


@pytest.mark.parametrize(
    "size", [(3840, 2160), (1872, 1404), (1600, 1200), (758, 1024), (1440, 2560), (1304, 984)]
)
def test_exact_fit_keeps_the_ratio_within_bounds(size: tuple[int, int]) -> None:
    w, h = exact_fit(size, 800, 6400)
    assert w <= 800 and h <= 6400
    assert abs(w / h - size[0] / size[1]) < 0.01 * size[0] / size[1]
    assert abs(w * size[1] / size[0] - h) < 0.6
