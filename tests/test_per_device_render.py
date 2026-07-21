"""Per-device render detection (#125): a page with a per_device_id widget
(tesserae_status) must trigger per-device rendering, whether the widget is on a
GRID cell or a CANVAS element. Before the fix only grid cells were scanned, so a
canvas dashboard's status bar rendered once per panel (not per device) and
showed the wrong device's battery (the min across all devices)."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.push import _page_needs_per_device_render
from app.state.page_store import Cell, Page
from app.state.panel_store import CanvasLayout, CodeSource, Element


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


def _canvas(*els: Element) -> Page:
    return Page(id="p", name="P", canvas=CanvasLayout(w=800, h=480, els=list(els)))


def test_canvas_status_bar_triggers_per_device(app: Flask) -> None:
    page = _canvas(Element(id="e1", kind="widget", widget="tesserae_status", x=0, y=0, w=800, h=40))
    with app.app_context():
        assert _page_needs_per_device_render(page) is True


def test_canvas_status_bar_as_code_source_triggers_per_device(app: Flask) -> None:
    page = _canvas(
        Element(id="e1", kind="code", sources=[CodeSource(key="tesserae_status", name="s")])
    )
    with app.app_context():
        assert _page_needs_per_device_render(page) is True


def test_grid_status_bar_still_triggers_per_device(app: Flask) -> None:
    page = Page(
        id="p", name="P", cells=[Cell(id="c", x=0, y=0, w=12, h=1, plugin="tesserae_status")]
    )
    with app.app_context():
        assert _page_needs_per_device_render(page) is True


def test_canvas_plain_widget_does_not_trigger(app: Flask) -> None:
    page = _canvas(Element(id="e1", kind="widget", widget="weather_now", x=0, y=0, w=200, h=200))
    with app.app_context():
        assert _page_needs_per_device_render(page) is False


def test_page_with_no_widgets_does_not_trigger(app: Flask) -> None:
    with app.app_context():
        assert _page_needs_per_device_render(Page(id="p", name="P")) is False
