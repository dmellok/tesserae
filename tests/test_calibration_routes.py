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


def test_preview_reflects_applied_profile_palette(app: Flask) -> None:
    # v0.67.5: when a profile is applied, the palette-swatch preview
    # should paint that profile's palette rather than the built-in
    # gamut default.
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "boeber-spectra6"},
    )
    resp = client.get(f"/settings/devices/{dev}/test-pattern/preview.png?pattern=palette_swatches")
    assert resp.status_code == 200
    from io import BytesIO

    img = Image.open(BytesIO(resp.data)).convert("RGB")
    # Boeber's red = #EA4843. Sample near the top of the red swatch
    # (fourth swatch on a 6-colour E6 layout).
    swatch_w = img.width // 6
    r = img.getpixel((3 * swatch_w + swatch_w // 4, 2))
    assert r == (0xEA, 0x48, 0x43)


def test_preview_exposure_query_shifts_grayscale(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    baseline = client.get(
        f"/settings/devices/{dev}/test-pattern/preview.png?pattern=grayscale_ramp"
    ).data
    bumped = client.get(
        f"/settings/devices/{dev}/test-pattern/preview.png?pattern=grayscale_ramp&exposure=60"
    ).data
    assert baseline != bumped


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
    # The footer Calibrate button is gone (moved to the tab) so it no
    # longer competes with the tab-hosted flow for muscle memory.
    assert 'data-tab-link="calibration"' not in body


# ---- v0.69.14 fixes -------------------------------------------------


def _upload_png(client, device_id: str, name: str = "test.png"):
    """Post a tiny PNG to the custom-image upload endpoint."""
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGB", (10, 10), (255, 0, 0)).save(buf, format="PNG")
    buf.seek(0)
    return client.post(
        f"/settings/devices/{device_id}/test-pattern/custom-image/upload",
        data={"image": (buf, name)},
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def test_custom_image_upload_redirect_keeps_card_expanded(app: Flask) -> None:
    """Bug A: after uploading, the redirect must carry ``opened=<id>``
    so the device card stays expanded. Previously only ``tab=calibration``
    was set and the card collapsed on every upload."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    resp = _upload_png(client, dev)
    assert resp.status_code == 302
    assert f"opened={dev}" in resp.location
    assert "tab=calibration" in resp.location
    assert f"#device-{dev}" in resp.location


def test_custom_image_delete_redirect_keeps_card_expanded(app: Flask) -> None:
    """Same fix applied to the delete endpoint: the card should stay
    open after removing a custom image."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    _upload_png(client, dev)
    resp = client.post(
        f"/settings/devices/{dev}/test-pattern/custom-image/delete",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert f"opened={dev}" in resp.location
    assert "tab=calibration" in resp.location


def test_send_test_pattern_redirect_keeps_card_expanded(app: Flask) -> None:
    """Same fix on the Send-to-panel POST: sending a pattern shouldn't
    collapse the calibration tab on the redirect."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    pm = MagicMock()
    pm.push_image.return_value = PushResult(
        status="sent", page_id="test-pattern:palette_swatches:esp32_demo", error=None
    )
    app.config["PUSH_MANAGER"] = pm
    resp = client.post(
        f"/settings/devices/{dev}/test-pattern",
        data={"pattern": "palette_swatches"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert f"opened={dev}" in resp.location
    assert "tab=calibration" in resp.location


def test_tab_state_is_scoped_to_opened_device(app: Flask) -> None:
    """Bug B: ``?tab=calibration`` only applies to the card whose
    device id matches ``?opened=``. Other cards on the page still
    render on their default (status) tab. Before this fix, ``?tab=``
    was shared so all cards followed one card's tab click."""
    import re

    client = app.test_client()
    _sign_in(client)
    _register_device(client, "dev_alpha")
    _register_device(client, "dev_beta")
    body = client.get("/settings/devices?tab=calibration&opened=dev_alpha").get_data(as_text=True)
    alpha_idx = body.index('id="device-dev_alpha"')
    beta_idx = body.index('id="device-dev_beta"')
    alpha_html = body[alpha_idx:beta_idx]
    beta_html = body[beta_idx:]

    def _panels(html: str) -> dict[str, bool]:
        # Map each ``data-panel="<name>"`` to whether it is the active
        # tab (``class=... is-active``). Uses regex to tolerate the
        # template's whitespace + attribute order.
        out: dict[str, bool] = {}
        for m in re.finditer(r'data-panel="([^"]+)"[^>]*class="([^"]*)"', html, flags=re.DOTALL):
            out[m.group(1)] = "is-active" in m.group(2)
        return out

    alpha_panels = _panels(alpha_html)
    beta_panels = _panels(beta_html)
    assert alpha_panels.get("calibration") is True
    assert beta_panels.get("calibration") is False
    assert beta_panels.get("status") is True


def test_preview_slug_query_previews_candidate_profile(app: Flask) -> None:
    """Bug C: the preview endpoint accepts ``?slug=<slug>`` to preview
    a profile before the user hits Apply. The saved slug on the device
    is irrelevant when the query overrides."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    # No profile applied on this device. Request a preview with an
    # explicit slug override; the palette-swatch preview should paint
    # the overridden profile's palette rather than the built-in gamut.
    resp = client.get(
        f"/settings/devices/{dev}/test-pattern/preview.png"
        "?pattern=palette_swatches&slug=boeber-spectra6"
    )
    assert resp.status_code == 200
    from io import BytesIO

    img = Image.open(BytesIO(resp.data)).convert("RGB")
    # Boeber's red = #EA4843. Same shape as the applied-profile test.
    swatch_w = img.width // 6
    r = img.getpixel((3 * swatch_w + swatch_w // 4, 2))
    assert r == (0xEA, 0x48, 0x43)


def test_preview_empty_slug_query_hides_applied_profile(app: Flask) -> None:
    """``?slug=`` (empty) explicitly asks for the built-in default so
    the user can preview 'no profile' without unapplying first."""
    client = app.test_client()
    _sign_in(client)
    dev = _register_device(client)
    client.post(
        f"/settings/devices/{dev}/palette/apply",
        data={"slug": "boeber-spectra6"},
    )
    # Applied Boeber; now preview with slug="" (explicit no-profile).
    resp = client.get(
        f"/settings/devices/{dev}/test-pattern/preview.png?pattern=palette_swatches&slug="
    )
    assert resp.status_code == 200
    from io import BytesIO

    from app.quantizer import WAVESHARE_E6_PALETTE

    img = Image.open(BytesIO(resp.data)).convert("RGB")
    # Should be the built-in E6 red (WAVESHARE_E6_PALETTE[3]), not
    # Boeber's #EA4843, because ?slug= explicitly overrode.
    swatch_w = img.width // 6
    r = img.getpixel((3 * swatch_w + swatch_w // 4, 2))
    assert r == WAVESHARE_E6_PALETTE[3]
