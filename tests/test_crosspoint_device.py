"""The ``crosspoint_gray`` device kind.

A CrossPoint e-reader paints the sleep image twice, through its
GRAYSCALE_LSB and GRAYSCALE_MSB planes, quantising each pixel to 0-3. That
is the ``gray_4`` gamut. Registering as a generic CircuitPython client left
the panel on a mono default, and the extra levels were discarded server-side
before the file was ever written, so the reader painted a 1-bit image on a
4-level panel.

The renderer needed no change: it selects its palette from the bound panel's
gamut, so declaring ``gray_4`` is what makes it emit four levels. What was
wrong was the kind, not the pixel pipeline.
"""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app

PANEL_W = 480
PANEL_H = 800


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


def _register(app: Flask, client) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "reader",
                "kind": "crosspoint_gray",
                "panel_w": PANEL_W,
                "panel_h": PANEL_H,
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["device_token"]


def _seed_gradient(app: Flask) -> None:
    """A horizontal ramp: a mono pipeline collapses this to two values, a
    four-level one keeps four. That difference is the whole point."""
    renders_dir = Path(app.config["RENDERS_DIR"])
    renders_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("L", (PANEL_W, PANEL_H))
    for x in range(PANEL_W):
        shade = int(x / PANEL_W * 255)
        for y in range(PANEL_H):
            img.putpixel((x, y), shade)
    img.convert("RGB").save(renders_dir / "gradient.png")
    app.config["PUSH_MANAGER"]._latest_renders["reader"] = {
        "digest": "art0001",
        "ext": "bmp",
        "filename": "art0001.bmp",
        "composition_digest": "gradient",
    }


def test_the_kind_registers_with_a_four_level_grey_panel(app: Flask) -> None:
    client = app.test_client()
    _register(app, client)
    device = app.config["DEVICE_REGISTRY"].get("reader")

    assert device is not None
    assert device.panel["gamut"] == "gray_4"
    assert device.panel["w"] == PANEL_W
    assert device.panel["h"] == PANEL_H


def test_the_frame_is_greyscale_capable_bmp_not_mono(app: Flask) -> None:
    """``hasGreyscale()`` on the reader is ``bpp > 1``, so a 1-bit frame takes
    the mono path however many levels the panel has."""
    client = app.test_client()
    token = _register(app, client)
    _seed_gradient(app)

    resp = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.get_data()

    assert body[:2] == b"BM"
    bpp = struct.unpack_from("<H", body, 28)[0]
    assert bpp > 1, "1-bit output takes the reader's mono path"


def test_the_frame_is_uncompressed(app: Flask) -> None:
    """The reader's BMP decoder rejects any compression except BI_BITFIELDS
    on 32bpp."""
    client = app.test_client()
    token = _register(app, client)
    _seed_gradient(app)

    body = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    ).get_data()
    compression = struct.unpack_from("<I", body, 30)[0]
    assert compression == 0, "BI_RGB (uncompressed) is the only safe choice here"


def test_the_frame_carries_no_more_than_four_grey_levels(app: Flask) -> None:
    """Quantised server-side to the panel's own 0-3, rather than handing over
    a smooth ramp and letting the device decide."""
    client = app.test_client()
    token = _register(app, client)
    _seed_gradient(app)

    body = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    ).get_data()
    levels = {p for p in Image.open(io.BytesIO(body)).convert("L").getdata()}

    assert len(levels) <= 4, f"expected at most four grey levels, got {sorted(levels)}"
    assert len(levels) > 2, "a gradient collapsing to two levels means the mono path ran"


def test_the_frame_matches_the_portrait_panel(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_gradient(app)

    body = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    ).get_data()
    width, height = struct.unpack_from("<ii", body, 18)

    assert (width, abs(height)) == (PANEL_W, PANEL_H)


def test_the_frame_fits_the_readers_download_cap(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed_gradient(app)

    body = client.get(
        "/api/v1/device/reader/frame.bmp", headers={"Authorization": f"Bearer {token}"}
    ).get_data()
    assert len(body) < 1_000_000, f"{len(body)} bytes is past the event-download cap"
