"""Calibration tab: colour test-pattern routes on Settings → Devices.

Covers the two endpoints wired to the new Calibration tab:

* ``GET /settings/devices/<id>/test-pattern/preview.png``, returns raw
  PNG bytes for the pattern picker's inline preview;
* ``POST /settings/devices/<id>/test-pattern``, hands the same bytes
  to :meth:`PushManager.push_image` so the device's real renderer +
  transport paint the panel.

Orientation calibration lives in :mod:`test_calibration` (unit) and is
mounted at the same routes it always was; this suite covers the new
routes only.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask
from PIL import Image

from app.main import REPO_ROOT, create_app
from app.push import PushResult


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


def _register_device(client, device_id: str = "esp32_demo", kind: str = "esp32_client") -> str:
    resp = client.post(
        "/settings/devices/add",
        data={"id": device_id, "kind": kind},
        follow_redirects=False,
    )
    assert resp.status_code == 302, f"device add failed: {resp.status_code} {resp.data!r}"
    return device_id


def test_preview_returns_png_for_known_device(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = client.get(f"/settings/devices/{dev}/test-pattern/preview.png?pattern=palette_swatches")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.headers.get("Cache-Control") == "no-store"
    # Body decodes as a valid image at some sensible size.
    from io import BytesIO

    img = Image.open(BytesIO(resp.data))
    assert img.format == "PNG"
    assert img.size[0] > 0 and img.size[1] > 0


def test_preview_404s_for_unknown_device(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get(
        "/settings/devices/no_such_device/test-pattern/preview.png?pattern=palette_swatches"
    )
    assert resp.status_code == 404


def test_preview_400s_for_unknown_pattern(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = client.get(
        f"/settings/devices/{dev}/test-pattern/preview.png?pattern=not-a-real-pattern"
    )
    assert resp.status_code == 400


def test_preview_defaults_to_palette_swatches(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    # No ``pattern`` param falls back to palette_swatches instead of erroring.
    resp = client.get(f"/settings/devices/{dev}/test-pattern/preview.png")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"


def test_send_invokes_push_image(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    # Install the mock after device registration; devices_add rebuilds
    # the transport + push manager, so an earlier install gets clobbered.
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="tp.png", composition_digest="d")
    app.config["PUSH_MANAGER"] = pm
    resp = client.post(
        f"/settings/devices/{dev}/test-pattern",
        data={"pattern": "palette_swatches"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Redirects back to the Calibration tab so muscle memory holds.
    assert "tab=calibration" in resp.location
    # push_image was called with the device id + a test-pattern label.
    pm.push_image.assert_called_once()
    call_kwargs = pm.push_image.call_args.kwargs
    assert call_kwargs["device_id"] == dev
    assert call_kwargs["source_label"].startswith("test-pattern:palette_swatches:")
    # First positional arg is the PNG bytes.
    (payload,) = pm.push_image.call_args.args
    assert isinstance(payload, bytes) and payload[:8] == b"\x89PNG\r\n\x1a\n"


def test_send_solid_fill_forwards_color_index(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_image.return_value = PushResult(status="sent", page_id="tp.png", composition_digest="d")
    app.config["PUSH_MANAGER"] = pm
    resp = client.post(
        f"/settings/devices/{dev}/test-pattern",
        data={"pattern": "solid_fill", "color_index": "3"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    (payload,) = pm.push_image.call_args.args
    # Payload is the palette-red solid fill; sampling the top-left pixel
    # (i.e. anywhere) picks up the exact E6 red.
    from io import BytesIO

    from app.quantizer import WAVESHARE_E6_PALETTE

    img = Image.open(BytesIO(payload)).convert("RGB")
    assert img.getpixel((0, 0)) == WAVESHARE_E6_PALETTE[3]


def test_send_rejects_unknown_pattern(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    app.config["PUSH_MANAGER"] = pm
    resp = client.post(
        f"/settings/devices/{dev}/test-pattern",
        data={"pattern": "bogus"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    pm.push_image.assert_not_called()


def test_send_404s_for_unknown_device(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/no_such_device/test-pattern",
        data={"pattern": "palette_swatches"},
        follow_redirects=False,
    )
    # No 404 template on the settings side; the route flashes + redirects
    # to /settings/devices so the user gets the flash on the next render.
    assert resp.status_code == 302
    assert "/settings/devices" in resp.location


def test_calibration_tab_renders_in_devices_index(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register_device(client)
    body = client.get("/settings/devices").get_data(as_text=True)
    # Tab label + at least one pattern option landed in the DOM.
    assert 'data-tab="calibration"' in body
    assert "Calibration" in body
    assert "Palette swatches" in body
    assert "Send to panel" in body
    # The old footer Calibrate button is gone; a deep-link chip took its place.
    assert 'data-tab-link="calibration"' in body
