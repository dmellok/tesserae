"""Settings › Devices redesign: the devices table with its quick panel,
the per-device page (``GET /settings/devices/<id>``) and the calibration
page (``GET /settings/devices/<id>/calibration``), plus where the POSTs
that used to land on the card now redirect."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

SECTIONS = (
    "overview",
    "identity",
    "display",
    "timing",
    "controls",
    "rendering",
    "power",
    "lineups",
    "connection",
    "remove",
)


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


def _add(
    client, device_id: str = "lounge", kind: str = "esp32_client", name: str = "Lounge"
) -> str:
    resp = client.post(
        "/settings/devices/add",
        data={"id": device_id, "kind": kind, "name": name},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    return device_id


def test_device_page_renders_every_section_anchor(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    resp = client.get(f"/settings/devices/{dev}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for anchor in SECTIONS:
        assert f'id="{anchor}"' in body, anchor
        assert f'href="#{anchor}"' in body, anchor
    # Breadcrumb + header.
    assert "Devices" in body
    assert "Lounge" in body
    # The combined form is hoisted and every field associates to it.
    assert f'id="device-{dev}-combined"' in body
    assert f'action="/settings/devices/{dev}/save"' in body
    assert 'name="device_name"' in body
    # Header actions.
    assert f"/preview/{dev}.png" in body
    assert f"/mirror/{dev}" in body
    assert f"/settings/devices/{dev}/push-now" in body
    assert f"/settings/devices/{dev}/calibration" in body


def test_device_page_unknown_device_redirects_to_the_list(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/devices/nope", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("/settings/devices")


def test_calibration_page_renders(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    resp = client.get(f"/settings/devices/{dev}/calibration")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Colour test patterns" in body
    assert "Palette recalibration" in body
    assert f'action="/settings/devices/{dev}/test-pattern"' in body
    # Per-renderer tone fields post through the combined form, flagged so
    # the save comes back here.
    assert 'name="_active_tab" value="calibration"' in body
    assert f'name="esp32_bin__{dev}:dither"' in body


def test_combined_save_redirects_to_the_device_page(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    resp = client.post(
        f"/settings/devices/{dev}/save",
        data={"device_name": "Lounge", "_section": "timing"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.location.endswith(f"/settings/devices/{dev}#timing")

    resp = client.post(
        f"/settings/devices/{dev}/save",
        data={"device_name": "Lounge", "_active_tab": "calibration"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.location.endswith(f"/settings/devices/{dev}/calibration")

    # An unknown section falls back to the top of the device page.
    resp = client.post(
        f"/settings/devices/{dev}/save",
        data={"device_name": "Lounge", "_section": "bogus"},
        follow_redirects=False,
    )
    assert resp.location.endswith(f"/settings/devices/{dev}")


def test_card_era_posts_redirect_to_their_section(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    resp = client.post(f"/settings/devices/{dev}/calibrate", follow_redirects=False)
    assert f"calibrating={dev}" in resp.location
    assert resp.location.endswith("#display")
    resp = client.post(
        f"/settings/devices/{dev}/calibrate/apply", data={"top_left": "4"}, follow_redirects=False
    )
    assert resp.location.endswith(f"/settings/devices/{dev}#display")
    resp = client.post(
        f"/settings/devices/{dev}/battery-offset",
        data={"battery_offset_mv": "10", "battery_offset_pct": "0"},
        follow_redirects=False,
    )
    assert resp.location.endswith(f"/settings/devices/{dev}#power")


def test_devices_table_quick_panel_opens_via_query(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    _add(client, "study", name="Study")
    body = client.get("/settings/devices").get_data(as_text=True)
    # Both rows render, both panels closed.
    assert f'id="device-{dev}"' in body
    assert 'id="device-study"' in body
    assert re.search(rf'id="quick-{dev}"[^>]*\bhidden\b', body)
    body = client.get(f"/settings/devices?opened={dev}").get_data(as_text=True)
    assert not re.search(rf'id="quick-{dev}"[^>]*\bhidden\b', body)
    assert re.search(r'id="quick-study"[^>]*\bhidden\b', body)
    # The quick panel links to the device page.
    assert f'href="/settings/devices/{dev}"' in body
    # Toolbar + hidden Add device panel.
    assert "Add device" in body
    assert re.search(r'id="add-device-panel"[^>]*\bhidden\b', body)
    assert not re.search(
        r'id="add-device-panel"[^>]*\bhidden\b',
        client.get("/settings/devices?add=1").get_data(as_text=True),
    )


def test_devices_table_row_shows_battery_firmware_and_freshness(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    app.config["DEVICE_STATUS"][dev] = {
        "received_at": time.time(),
        "parsed": {"battery_mv": 3500, "battery_pct": 12, "fw_version": "1.39.0"},
    }
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "12%" in body
    assert "v1.39.0" in body
    assert "dx-devrow-batt is-warn" in body
    assert "is-ok" in body


def test_push_now_without_a_page_explains_itself(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    resp = client.post(f"/settings/devices/{dev}/push-now", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith(f"/settings/devices/{dev}#overview")
    body = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    assert "has not been sent a dashboard yet" in body
