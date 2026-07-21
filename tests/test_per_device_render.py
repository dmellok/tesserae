"""Per-device render for the status bar (#125).

Two halves:

1. Detection: a page with a per_device_id widget (tesserae_status) must trigger
   per-device rendering, whether the widget is on a GRID cell or a CANVAS
   element. Before the fix only grid cells were scanned, so a canvas dashboard
   pushed once per panel, not per device.

2. Threading: the resolved ``target_device_id`` must actually reach the widget
   fetch. The grid path carries it via ``page_dict``; the canvas path
   (``_render_canvas`` -> ``_build_canvas_els`` -> ``_fetch_plugin_data``) has to
   pass it explicitly, or every canvas render shows a min-across-all-devices
   battery even when the push fans out per device. And a non-push preview (no
   ``?device_id``) defaults to the page's first bound device so the hover
   thumbnail / live iframe show a real device rather than the aggregate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _two_devices(app: Flask) -> None:
    """Create dev_a (91% battery) and dev_b (12%), min-across-all is 12."""
    from app import device_service

    for did in ("dev_a", "dev_b"):
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DEVICE_DATA_ROOT"],
            instance_id=did,
            kind_id="esp32_client",
            orientation="landscape",
        )
    app.config["DEVICE_STATUS"] = {
        "dev_a": {"parsed": {"battery_pct": 91, "rssi": -40}},
        "dev_b": {"parsed": {"battery_pct": 12, "rssi": -80}},
    }


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


# -- threading: the resolved device must reach the widget fetch ---------------


def _status_battery(app: Flask, target_device_id: str) -> Any:
    """Battery the canvas status bar resolves to for a given target device."""
    from app.composer import _build_canvas_els

    el = Element(id="e1", kind="widget", widget="tesserae_status", x=0, y=0, w=800, h=40)
    with app.test_request_context("/compose/cv"):
        els = _build_canvas_els([el], 800, 480, target_device_id=target_device_id)
    return (els[0].get("data") or {}).get("battery_pct")


def test_canvas_render_threads_target_device(app: Flask) -> None:
    _two_devices(app)
    assert _status_battery(app, "dev_a") == 91
    assert _status_battery(app, "dev_b") == 12


def test_canvas_render_without_target_uses_aggregate(app: Flask) -> None:
    _two_devices(app)
    # No target: min-across-all-devices (the pre-fix behaviour, still correct
    # when genuinely no device is targeted, e.g. an unbound page).
    assert _status_battery(app, "") == 12


def test_preview_defaults_to_first_bound_device(app: Flask) -> None:
    _two_devices(app)
    from app.composer import _preview_target_device

    devices = app.config["DEVICE_REGISTRY"]
    # Missing ids are skipped; the first *present* bound device wins.
    bound = Page(id="p", name="P", device_ids=["ghost", "dev_b"])
    assert _preview_target_device(bound, devices) == "dev_b"
    assert _preview_target_device(Page(id="u", name="U"), devices) == ""


def _captured_target(app: Flask, page: Page, query: str) -> str:
    """The target_device_id the compose route hands the canvas renderer."""
    import app.composer as composer

    app.config["PAGE_STORE"].save(page)
    seen: dict[str, str] = {}

    def _spy(layout: Any, *, target_w: int, target_h: int, target_device_id: str = "") -> str:
        seen["t"] = target_device_id
        return ""

    orig = composer._render_canvas
    composer._render_canvas = _spy  # type: ignore[assignment]
    try:
        app.test_client().get(f"/compose/{page.id}{query}")
    finally:
        composer._render_canvas = orig  # type: ignore[assignment]
    return seen.get("t", "<uncalled>")


def test_compose_route_wires_target_for_canvas(app: Flask) -> None:
    _two_devices(app)
    page = Page(
        id="cv",
        name="CV",
        device_ids=["dev_a", "dev_b"],
        layout_kind="canvas",
        canvas=CanvasLayout(w=800, h=480, els=[]),
    )
    # Explicit device_id wins (the push fan-out).
    assert _captured_target(app, page, "?w=800&h=480&device_id=dev_b") == "dev_b"
    # Preview (no device_id, not a push): default to the first bound device.
    assert _captured_target(app, page, "?w=800&h=480") == "dev_a"
    # A push with no device_id stays empty (unbound / non-per-device render).
    assert _captured_target(app, page, "?w=800&h=480&for_push=1") == ""
