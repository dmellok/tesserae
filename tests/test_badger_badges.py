"""Pimoroni Badger badges pair with the panel geometry their app unpacks.

The tesserae-device-badger app announces its framebuffer plus a rotation
(issue #200 contract). The 2040 W declares 128x296 at 90 degrees: the
dashboard is 296x128 and the esp32_bw_bin frame arrives turned a quarter
clockwise onto the portrait buffer, which the app reorders into
PicoGraphics' column-major 1-bit layout. If the instance stored a landscape
native buffer instead, every frame would paint scrambled, so pin both the
stored panel and the frame the renderer then produces.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.state.page_store import Panel
from renderers.esp32_bw_bin import renderer as bw_renderer


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


def _register(app: Flask, body: dict) -> None:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(body),
    )
    assert resp.status_code in (200, 201), resp.get_json()


def _panel(app: Flask, device_id: str) -> dict:
    device = app.config["DEVICE_REGISTRY"].get(device_id)
    assert device is not None
    return dict(device.panel or {})


def test_badger_2040_w_announce_keeps_the_portrait_native_buffer(app: Flask) -> None:
    _register(
        app,
        {
            "device_id": "badger_010203",
            "kind": "pimoroni_badger_2040_w",
            "panel_w": 128,
            "panel_h": 296,
            "rotation": 90,
            "gamut": "mono",
            "fw_version": "0.1.0",
            "mac": "28:CD:C1:01:02:03",
        },
    )
    panel = _panel(app, "badger_010203")
    assert (panel["w"], panel["h"]) == (296, 128)
    assert (panel["native_w"], panel["native_h"]) == (128, 296)
    assert not panel.get("flip")

    # The frame for that panel is the dashboard turned clockwise: native
    # row r is dashboard column r, read bottom to top.
    img = Image.new("L", (296, 128), 255)
    img.putpixel((0, 127), 0)  # bottom-left dashboard pixel
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    fields = {k: panel[k] for k in ("w", "h", "native_w", "native_h", "gamut") if k in panel}
    out = bw_renderer.transform(buf.getvalue(), panel=Panel(**fields), settings={"dither": "none"})
    assert len(out) == 4736
    assert out[0] == 0x7F  # first native byte, MSB = that pixel, black
    assert all(b == 0xFF for b in out[1:])


def test_badger_2350_announce_matches_its_landscape_buffer(app: Flask) -> None:
    _register(
        app,
        {
            "device_id": "badger_0a0b0c",
            "kind": "pimoroni_badger_2350",
            "panel_w": 264,
            "panel_h": 176,
            "rotation": 0,
            "gamut": "gray_4",
            "fw_version": "0.1.0",
            "mac": "28:CD:C1:0A:0B:0C",
        },
    )
    panel = _panel(app, "badger_0a0b0c")
    assert (panel["w"], panel["h"], panel["native_w"], panel["native_h"]) == (264, 176, 264, 176)
    assert panel["gamut"] == "gray_4"
