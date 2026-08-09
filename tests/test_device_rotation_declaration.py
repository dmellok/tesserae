"""A client can declare its framebuffer + rotation at registration (issue #200).

Before this, ``panel_w`` / ``panel_h`` were the only geometry a client could
send, and they were read as the dashboard canvas. That leaves a panel whose
native buffer is portrait but which the user wants a landscape dashboard on
inexpressible: picking landscape in Settings rewrites the dims, and the
CircuitPython renderers then emit a landscape file the client's portrait buffer
can't take.

Declaring ``rotation`` splits the two apart. The reported dims become the
framebuffer, the rotation is the turn from that buffer to the canvas, and the
renderer turns the finished composition back onto the buffer so the downloaded
file is always the shape the client asked for. Clients that send no rotation
keep the older reading, which this file also pins.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from app.device_service import panel_geometry_from_report, parse_rotation
from app.main import REPO_ROOT, create_app
from app.state.page_store import Panel

# The two CircuitPython renderers share a pixel pipeline, so both are
# driven through the same cases.
from renderers.circuitpython_bmp import renderer as bmp_renderer
from renderers.circuitpython_png import renderer as png_renderer


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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _register(client, app: Flask, *, device_id: str, **body: object):
    code = app.config["PAIRING_STORE"].issue(note="test").code
    payload = {
        "device_id": device_id,
        "kind": "circuitpython_generic",
        "panel_w": 1200,
        "panel_h": 1920,
        "fw_version": "0.1.0",
    }
    payload.update(body)
    return client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(payload),
    )


def _panel_block(app: Flask, device_id: str) -> dict:
    device = app.config["DEVICE_REGISTRY"].get(device_id)
    assert device is not None, f"no device {device_id!r}"
    return dict(device.panel or {})


# -- the mapping itself ------------------------------------------------


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, "landscape"),
        (90, "portrait"),
        (180, "landscape_flipped"),
        (270, "portrait_flipped"),
    ],
)
def test_rotation_turns_a_landscape_buffer(rotation: int, expected: str) -> None:
    overrides, orientation = panel_geometry_from_report(w=800, h=480, rotation=rotation)
    assert orientation == expected
    assert overrides == {"native_w": 800, "native_h": 480}


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, "portrait"),
        (90, "landscape"),
        (180, "portrait_flipped"),
        (270, "landscape_flipped"),
    ],
)
def test_rotation_turns_a_portrait_buffer(rotation: int, expected: str) -> None:
    """Bernhard's Pi Touch Panel 2 case: the panel always autodetects as
    1200x1920, so rotation is the only way to ask for a landscape
    dashboard on it."""
    overrides, orientation = panel_geometry_from_report(w=1200, h=1920, rotation=rotation)
    assert orientation == expected
    assert overrides == {"native_w": 1200, "native_h": 1920}


def test_no_rotation_keeps_the_older_reading() -> None:
    """Undeclared means the dims are the canvas: infer the aspect, claim
    nothing about the framebuffer."""
    overrides, orientation = panel_geometry_from_report(w=1200, h=1920, rotation=None)
    assert orientation == "portrait"
    assert overrides == {}


@pytest.mark.parametrize("value", [None, "", "ninety", 45, 12.5, True, [90]])
def test_junk_rotation_reads_as_undeclared(value: object) -> None:
    """A firmware sending nonsense lands where one sending nothing does,
    rather than failing to pair."""
    assert parse_rotation(value) is None


def test_rotation_is_taken_modulo_a_full_turn() -> None:
    assert parse_rotation(450) == 90
    assert parse_rotation(360) == 0


def test_missing_dims_yield_nothing_to_stamp() -> None:
    assert panel_geometry_from_report(w=0, h=0, rotation=90) == ({}, None)
    assert panel_geometry_from_report(w=None, h=None, rotation=None) == ({}, None)


# -- registration ------------------------------------------------------


def test_register_with_rotation_90_stores_landscape_canvas_and_portrait_buffer(
    app: Flask,
) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = _register(client, app, device_id="pi_touch_2", rotation=90)
    assert resp.status_code == 201, resp.get_json()
    panel = _panel_block(app, "pi_touch_2")
    assert (panel["w"], panel["h"]) == (1920, 1200)
    assert (panel["native_w"], panel["native_h"]) == (1200, 1920)
    assert panel["orientation"] == "landscape"


def test_register_with_rotation_0_composes_at_the_reported_dims(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert _register(client, app, device_id="pi_touch_0", rotation=0).status_code == 201
    panel = _panel_block(app, "pi_touch_0")
    assert (panel["w"], panel["h"]) == (1200, 1920)
    assert (panel["native_w"], panel["native_h"]) == (1200, 1920)
    assert panel["orientation"] == "portrait"


def test_register_without_rotation_keeps_the_dims_and_derives_the_aspect(app: Flask) -> None:
    """The half of issue #200 that the REST path was missing: a portrait
    client used to self-register with portrait dims and the kind's
    landscape orientation, and the first save of the panel form resolved
    that by swapping the dims."""
    client = app.test_client()
    _sign_in(client)
    assert _register(client, app, device_id="pi_touch_none").status_code == 201
    panel = _panel_block(app, "pi_touch_none")
    assert (panel["w"], panel["h"]) == (1200, 1920)
    assert panel["orientation"] == "portrait"
    # No claim about the framebuffer, so no stride is written.
    assert "native_w" not in panel


def test_register_with_rotation_180_flips_without_reshaping(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert _register(client, app, device_id="pi_touch_180", rotation=180).status_code == 201
    panel = _panel_block(app, "pi_touch_180")
    assert (panel["w"], panel["h"]) == (1200, 1920)
    assert panel["orientation"] == "portrait_flipped"


# -- renderers ---------------------------------------------------------


def _composition(w: int, h: int) -> bytes:
    """A composition PNG with a marker block in its top-left corner, so a
    rotation is visible in the pixels and not just the dims."""
    img = Image.new("RGB", (w, h), "white")
    for x in range(40):
        for y in range(40):
            img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _size(blob: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(blob)).size


@pytest.mark.parametrize("renderer", [bmp_renderer, png_renderer])
def test_declared_buffer_receives_the_dashboard_rotated_onto_it(renderer) -> None:
    """rotation 90 on a portrait-native panel: the dashboard is composed
    landscape, the file lands portrait."""
    panel = Panel(
        w=1920,
        h=1200,
        gamut="mono",
        native_w=1200,
        native_h=1920,
        native_declared=True,
    )
    out = renderer.transform(_composition(1920, 1200), panel=panel, settings={})
    assert _size(out) == (1200, 1920)


@pytest.mark.parametrize("renderer", [bmp_renderer, png_renderer])
def test_matching_buffer_is_left_alone(renderer) -> None:
    panel = Panel(
        w=1200,
        h=1920,
        gamut="mono",
        native_w=1200,
        native_h=1920,
        native_declared=True,
    )
    out = renderer.transform(_composition(1200, 1920), panel=panel, settings={})
    assert _size(out) == (1200, 1920)


@pytest.mark.parametrize("renderer", [bmp_renderer, png_renderer])
def test_undeclared_buffer_keeps_the_composition_shape(renderer) -> None:
    """The back-compat guard. ``device_panel`` fills native dims from the
    preset table on a dims match, and a device already painting
    composition-shaped frames (rotating on-device) must not start
    receiving them turned 90° after an upgrade."""
    panel = Panel(w=480, h=800, gamut="mono", native_w=800, native_h=480)
    out = renderer.transform(_composition(480, 800), panel=panel, settings={})
    assert _size(out) == (480, 800)


@pytest.mark.parametrize("renderer", [bmp_renderer, png_renderer])
def test_rotation_carries_the_content_not_just_the_frame(renderer) -> None:
    """The composition's top-left marker has to land on the buffer's
    top-right after a 90° CW turn; a bare resize would pass the dims
    assertions above while painting a squashed dashboard."""
    panel = Panel(
        w=800,
        h=480,
        gamut="mono",
        native_w=480,
        native_h=800,
        native_declared=True,
    )
    out = renderer.transform(_composition(800, 480), panel=panel, settings={})
    img = Image.open(io.BytesIO(out)).convert("L")
    assert img.size == (480, 800)
    assert img.getpixel((470, 10)) < 128, "marker should have landed top-right"
    assert img.getpixel((10, 10)) > 128, "top-left should be blank after the turn"
