"""TRMNL panel adoption: the buffer a client reports on every poll
(png-width / png-height) becomes the device panel's native dims so the
trmnl renderers can turn the composition onto the real screen.

Root cause this locks down: a Kindle running KOReader's trmnl-display
plugin paints whatever frame it fetches across its full 758×1024
portrait screen. The device panel used to carry only the kind-default
800×480 landscape dims, so every frame was served at the wrong aspect
and the client stretched it into a squashed landscape-in-portrait;
selecting "Rotation: 90" did nothing because the renderer had no
signal about the real buffer to rotate onto.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app import device_service, trmnl_api
from app.main import REPO_ROOT, create_app


@pytest.fixture
def app_with_trmnl(tmp_path: Path) -> Flask:
    """App built at a tmp data root with one manually-added TRMNL
    instance (the token-typed KOReader path), panel still at the kind
    default 800x480 landscape."""
    app = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    devices = app.config["DEVICE_REGISTRY"]
    result = device_service.create_instance(
        devices=devices,
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="trmnl_kindle",
        kind_id="trmnl_client",
        name="Kindle",
        access_token="abcde",
        api_key_strength="typeable",
    )
    assert result.ok, result.error
    return app


def _instance(app: Flask, device_id: str):
    return app.config["DEVICE_REGISTRY"].devices[device_id]


def _panel_file(app: Flask, device_id: str) -> Path:
    return _instance(app, device_id).path


def _panel_block(app: Flask, device_id: str) -> dict:
    raw = json.loads(_panel_file(app, device_id).read_text(encoding="utf-8"))
    return dict(raw.get("panel") or {})


def test_first_report_adopts_native_dims_and_seeds_composition(
    app_with_trmnl: Flask,
) -> None:
    """A Paperwhite 2 reports 758x1024. First adoption stamps that as the
    native buffer, and because the kind-default canvas is still 800x480
    (landscape orientation), the composition is seeded to the swapped
    pair 1024x758: the "Rotation: 90" semantics of a landscape canvas
    turned onto a portrait screen."""
    app = app_with_trmnl
    with app.app_context():
        device = _instance(app, "trmnl_kindle")
        trmnl_api._adopt_reported_panel(device, 758, 1024)
    panel = _panel_block(app, "trmnl_kindle")
    assert panel["native_w"] == 758
    assert panel["native_h"] == 1024
    assert panel["w"] == 1024
    assert panel["h"] == 758
    assert panel["orientation"] == "landscape"
    # The registry entry must be the reloaded one, so device_panel() in
    # the push pipeline sees the native block.
    assert _instance(app, "trmnl_kindle").panel["native_w"] == 758


def test_repeat_report_is_a_noop(app_with_trmnl: Flask) -> None:
    """Steady-state polls report the same size every time; adoption must
    not rewrite the file (or the reload churn) once the values match."""
    app = app_with_trmnl
    with app.app_context():
        device = _instance(app, "trmnl_kindle")
        trmnl_api._adopt_reported_panel(device, 758, 1024)
    before = _panel_file(app, "trmnl_kindle").read_bytes()
    with app.app_context():
        device = _instance(app, "trmnl_kindle")
        trmnl_api._adopt_reported_panel(device, 758, 1024)
    assert _panel_file(app, "trmnl_kindle").read_bytes() == before


def test_landscape_og_buffer_keeps_wide_composition(app_with_trmnl: Flask) -> None:
    """A native TRMNL OG (800x480 landscape screen) reporting its own
    dims keeps the wide composition — the seed must not transpose a
    landscape-native buffer into a portrait canvas."""
    app = app_with_trmnl
    with app.app_context():
        device = _instance(app, "trmnl_kindle")
        trmnl_api._adopt_reported_panel(device, 800, 480)
    panel = _panel_block(app, "trmnl_kindle")
    assert panel["native_w"] == 800
    assert panel["native_h"] == 480
    assert panel["w"] == 800
    assert panel["h"] == 480


def test_later_size_change_only_refreshes_native(
    app_with_trmnl: Flask,
) -> None:
    """A client that later reports a different screen (user moves the
    token to another reader) updates the native block but never touches
    a composition the user has since configured."""
    app = app_with_trmnl
    with app.app_context():
        device = _instance(app, "trmnl_kindle")
        trmnl_api._adopt_reported_panel(device, 758, 1024)
    # User deliberately sets a custom composition canvas.
    result = device_service.update_instance_panel(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="trmnl_kindle",
        w=600,
        h=800,
        orientation="portrait",
    )
    assert result.ok
    with app.app_context():
        device = _instance(app, "trmnl_kindle")
        trmnl_api._adopt_reported_panel(device, 1072, 1448)
    panel = _panel_block(app, "trmnl_kindle")
    assert panel["native_w"] == 1072
    assert panel["native_h"] == 1448
    assert panel["w"] == 600
    assert panel["h"] == 800
    assert panel["orientation"] == "portrait"


def test_garbage_reported_dims_are_ignored(app_with_trmnl: Flask) -> None:
    """Zero / negative / oversized headers must never corrupt the stored
    panel."""
    app = app_with_trmnl
    with app.app_context():
        device = _instance(app, "trmnl_kindle")
        trmnl_api._adopt_reported_panel(device, 0, 1024)
        trmnl_api._adopt_reported_panel(device, 758, -1)
        trmnl_api._adopt_reported_panel(device, 5000, 5000)
    panel = _panel_block(app, "trmnl_kindle")
    assert "native_w" not in panel
    assert panel["w"] == 800
    assert panel["h"] == 480