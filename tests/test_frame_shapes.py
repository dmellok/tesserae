"""``ctx["frame_shapes"]``: the shapes of the frames a page is on, so a photo
widget can match each frame's shape when they differ."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import composer, panel
from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _render(
    app: Flask, monkeypatch: pytest.MonkeyPatch, sizes: list[tuple[int, int]]
) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []
    plugin = app.config["PLUGIN_REGISTRY"].get("clock_analog")
    monkeypatch.setattr(
        plugin,
        "server_module",
        types.SimpleNamespace(fetch=lambda options, settings, ctx: seen.append(ctx) or {}),
    )
    monkeypatch.setattr(
        panel,
        "_selected_device_panels",
        lambda page, devices: [
            (types.SimpleNamespace(id=f"d{i}"), types.SimpleNamespace(w=w, h=h))
            for i, (w, h) in enumerate(sizes)
        ],
    )
    page = Page(
        id="pg",
        name="PG",
        device_ids=[f"d{i}" for i in range(len(sizes))],
        cells=[Cell(id="c1", plugin="clock_analog", x=0, y=0, w=400, h=300)],
    )
    app.config.setdefault("PREVIEW_CACHE", {})["pg"] = page
    page_dict = page.model_dump(mode="json", exclude_none=True)
    page_dict["panel"] = {"w": 400, "h": 300}
    with app.test_request_context("/compose/pg"):
        composer._hydrate_page(page_dict, preview=True)
    return seen[0]


def test_a_page_on_both_shapes_says_so(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _render(app, monkeypatch, [(1600, 1200), (1200, 1600), (1920, 1080)])
    assert ctx["frame_shapes"] == ["landscape", "portrait"]


def test_a_page_on_one_shape(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _render(app, monkeypatch, [(1600, 1200), (800, 800)])
    assert ctx["frame_shapes"] == ["landscape", "square"]


def test_a_page_on_no_frame_adds_nothing(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _render(app, monkeypatch, [])
    assert "frame_shapes" not in ctx


def test_lineups_ctx_carries_the_frame_shapes(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import step_items

    monkeypatch.setattr(
        panel,
        "_selected_device_panels",
        lambda page, devices: [
            (types.SimpleNamespace(id="d0"), types.SimpleNamespace(w=1600, h=1200)),
            (types.SimpleNamespace(id="d1"), types.SimpleNamespace(w=1200, h=1600)),
        ],
    )
    cell = Cell(id="c1", plugin="picture_gallery", x=0, y=0, w=400, h=300)
    page = Page(id="pg", name="PG", device_ids=["d0", "d1"], cells=[cell])
    with app.test_request_context("/"):
        ctx = step_items._item_ctx(page, page.cells[0], "picture_gallery", None)
    assert ctx["frame_shapes"] == ["landscape", "portrait"]


def test_a_gallery_lists_only_the_pictures_a_frame_is_shown(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from app import step_items

    folder = app.config["PLUGIN_REGISTRY"].get("picture_gallery").data_dir
    for name, size in (("wide.jpg", (90, 60)), ("tall.jpg", (60, 90)), ("sq.jpg", (70, 70))):
        Image.new("RGB", size).save(folder / name)
    monkeypatch.setattr(step_items, "_GALLERY_CACHE", {})
    cell = Cell(id="c1", plugin="picture_gallery", x=0, y=0, w=400, h=300, options={})
    mixed = {"frame_shapes": ["landscape", "portrait"]}
    with app.test_request_context("/"):
        wide = step_items._gallery_items(cell, {**mixed, "cell_w": 800, "cell_h": 480})
        tall = step_items._gallery_items(cell, {**mixed, "cell_w": 480, "cell_h": 800})
        one_shape = step_items._gallery_items(
            cell, {"frame_shapes": ["landscape"], "cell_w": 800, "cell_h": 480}
        )
    assert sorted(i.key for i in wide.items) == ["sq.jpg", "wide.jpg"]
    assert sorted(i.key for i in tall.items) == ["sq.jpg", "tall.jpg"]
    assert one_shape.count == 3
