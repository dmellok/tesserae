"""Settings › Devices redesign: the devices table with its quick panel,
the per-device page (``GET /settings/devices/<id>``) and the calibration
page (``GET /settings/devices/<id>/calibration``), plus where the POSTs
that used to land on the card now redirect."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

# The collapsible sections, in sub-nav order, for a device with every
# feature. The summary rail (#overview) heads the page beside them.
SECTIONS = (
    "display",
    "schedule",
    "controls",
    "power",
    "troubleshooting",
    "remove",
)
# Ids the older, finer sections had. They live on as groups inside the
# sections above, so old links (#lineups, #logs, ...) still land.
OLD_ANCHORS = {
    "identity": "display",
    "rendering": "display",
    "timing": "schedule",
    "lineups": "schedule",
    "connection": "troubleshooting",
    "logs": "troubleshooting",
}


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


def _full_device(app: Flask) -> tuple[object, str]:
    """A paired Pico (buttons, battery, REST token, log upload) that has
    checked in: the device that shows every section."""
    client = app.test_client()
    _sign_in(client)
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "kitchen",
                "kind": "pico_bin_client",
                "panel_w": 800,
                "panel_h": 480,
                "fw_version": "1.41.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    token = resp.get_json()["device_token"]
    resp = client.post(
        "/api/v1/device/kitchen/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 76, "logs": {"schema": 1, "ring_bytes": 3072}}),
    )
    assert resp.status_code == 200
    return client, "kitchen"


def _section_html(body: str, section_id: str) -> str:
    """The markup of one top-level section, from its opening tag to the
    next section."""
    start = body.index(f'id="{section_id}"', body.index('<section class="dx-dsec'))
    start = body.rindex("<section", 0, start)
    end = body.find('<section class="dx-dsec', start + 1)
    return body[start : end if end != -1 else len(body)]


def _rail_html(body: str) -> str:
    start = body.index('<aside class="dx-rail" id="overview"')
    return body[start : body.index("</aside>", start)]


def test_device_page_renders_every_section_anchor(app: Flask) -> None:
    client, dev = _full_device(app)
    resp = client.get(f"/settings/devices/{dev}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for anchor in SECTIONS:
        assert f'id="{anchor}"' in body, anchor
        assert f'href="#{anchor}"' in body, anchor
    # The rail is #overview (old links to the Overview section land on it).
    assert body.count('id="overview"') == 1
    # One layout wrapper: the page script wires each one it finds.
    assert body.count("data-device-page") == 1
    # Five sections and the danger zone, no more: the section strip is the list.
    nav = body[body.index("data-subnav>") : body.index("</nav>", body.index("data-subnav>"))]
    assert re.findall(r'data-subnav-link="([^"]+)"', nav) == list(SECTIONS)
    # Breadcrumb.
    assert "Devices" in body
    # The combined form is hoisted and every field associates to it.
    assert f'id="device-{dev}-combined"' in body
    assert f'action="/settings/devices/{dev}/save"' in body
    assert 'name="device_name"' in body
    # The rail's actions: More (mirror, raw PNG), Preview, Push now.
    rail = _rail_html(body)
    assert f"/preview/{dev}.png" in rail
    assert f"/mirror/{dev}" in rail
    assert f'form="device-{dev}-push-now"' in rail
    assert f'action="/settings/devices/{dev}/push-now"' in body
    # Calibrate heads the Rendering group.
    assert f"/settings/devices/{dev}/calibration" in _section_html(body, "display")


def test_old_section_anchors_still_resolve_inside_the_new_sections(app: Flask) -> None:
    client, dev = _full_device(app)
    body = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    for old, parent in OLD_ANCHORS.items():
        assert body.count(f'id="{old}"') == 1, old
        assert f'id="{old}"' in _section_html(body, parent), (old, parent)
    # The groups are marked up as such, with a group heading.
    assert 'class="dx-grp" id="lineups" data-devsub' in body
    assert '<h3 class="dx-grp-title">Lineups</h3>' in body


def test_each_section_is_a_collapsible_with_a_toggle_button(app: Flask) -> None:
    """The sections use the shared collapsible component."""
    client, dev = _full_device(app)
    body = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    assert "data-devsec" not in body  # the page's own collapse code is retired
    assert 'data-collapsible-scope="device-page"' in body
    for anchor in SECTIONS[:-1]:  # everything but the Remove danger zone
        html = _section_html(body, anchor)
        assert "data-collapsible " in html or "data-collapsible>" in html, anchor
        assert "data-collapsible-head" in html, anchor
        assert "data-collapsible-toggle" in html, anchor
        assert f'aria-controls="{anchor}-body"' in html, anchor
        assert 'aria-expanded="true"' in html, anchor
        assert f'id="{anchor}-body" data-collapsible-body' in html, anchor
        assert "ph-caret-down" in html, anchor
        # Every section starts expanded in the markup.
        assert f'id="{anchor}-body" hidden' not in html, anchor
    assert "data-collapsible" not in _section_html(body, "remove")


# Every field, button and form the page carried before the redesign (device
# with every feature): only their place changed.
_CONTROLS = {
    "battery_offset_mv",
    "battery_offset_pct",
    "button_map_json",
    "device_icon",
    "device_id_readonly",
    "device_name",
    "locale",
    "panel_h",
    "panel_orientation",
    "panel_underscan",
    "panel_w",
    "quiet_hours_all_day",
    "quiet_hours_days",
    "quiet_hours_enabled",
    "quiet_hours_end",
    "quiet_hours_sleep",
    "quiet_hours_start",
    "refresh_mode",
    "sleep_interval_s",
    "transport",
    "wake_align_anchor",
    "wake_align_apply_all",
    "wake_align_mode",
    "wake_align_times",
    "wakes",
}
_ACTIONS = ("delete", "logs/collect", "regenerate-token", "reveal-token", "set-transport")


def test_regrouping_loses_no_control(app: Flask) -> None:
    client, dev = _full_device(app)
    body = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    body = body[body.index("data-device-page") :]
    names = set(re.findall(r'<(?:input|select|textarea|button)[^>]*?name="([^"]+)"', body))
    assert names >= _CONTROLS, sorted(_CONTROLS - names)
    for action in _ACTIONS:
        assert f'action="/settings/devices/{dev}/{action}"' in body, action
    # Where the controls now live.
    placed = {
        "device_name": "display",
        "panel_w": "display",
        "locale": "display",
        "sleep_interval_s": "schedule",
        "quiet_hours_start": "schedule",
        "button_map_json": "controls",
        "battery_offset_mv": "power",
        "wakes": "troubleshooting",
    }
    page = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    for name, section in placed.items():
        assert f'name="{name}"' in _section_html(page, section), name
    assert "Calibrate" in _section_html(page, "display")
    assert "Nothing scheduled yet" in _section_html(page, "schedule")
    trouble = _section_html(page, "troubleshooting")
    for text in (
        "Regenerate token",
        "Firmware page",
        "Advanced: access token and transport",
        "Advanced: debug details and raw manifest",
        "Advanced: last status report",
    ):
        assert text in trouble, text


def test_display_fields_are_compact_rows_with_their_actions(app: Flask) -> None:
    """Identity, panel and rendering fields are compact field rows; the
    orientation card is a compass icon button beside Rotation, Calibrate heads
    the Rendering group, and underscan is a plain number box."""
    client, dev = _full_device(app)
    page = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    display = _section_html(page, "display")
    assert display.count('<div class="dx-fg">') == 3
    assert "dx-band" not in display
    panel = display[display.index('id="display-panel"') : display.index('id="rendering"')]
    assert 'type="number" id="device-kitchen-panel-underscan" name="panel_underscan"' in panel
    assert 'type="range"' not in panel
    assert f'form="device-{dev}-calibrate"' in panel
    assert 'aria-label="Send orientation card"' in panel
    assert "data-size-swap" in panel  # the swap button between width and height
    rendering = display[display.index('id="rendering"') :]
    head = rendering[: rendering.index("</div>")]
    assert f'href="/settings/devices/{dev}/calibration"' in head
    # While the numbered card is out, the answer band appears under the row
    # and the compass button steps aside.
    answering = _section_html(
        client.get(f"/settings/devices/{dev}?calibrating={dev}").get_data(as_text=True),
        "display",
    )
    assert 'id="orientation-card"' in answering
    assert answering.count(f'form="device-{dev}-calibrate-apply"') == 4
    assert 'aria-label="Send orientation card"' not in answering


def test_rail_vitals_link_to_the_settings_behind_them(app: Flask) -> None:
    client, dev = _full_device(app)
    rail = _rail_html(client.get(f"/settings/devices/{dev}").get_data(as_text=True))
    assert re.search(r'<a class="dx-now" href="#lineups"', rail)
    for label, anchor in (
        ("Battery", "#power"),
        ("Next update", "#timing"),
        ("Firmware", "#connection"),
        ("Checked in", "#connection"),
    ):
        m = re.search(
            rf'<a class="dx-vital" href="{anchor}"[^>]*>\s*<span class="dx-vital-k">{label}</span>',
            rail,
        )
        assert m, label


def test_sub_nav_only_links_to_sections_that_exist(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    registry = client.application.config["DEVICE_REGISTRY"]
    registry.get(dev).manifest["buttons"] = False
    registry.get(dev).manifest["touch"] = False
    body = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    assert 'id="controls"' not in body
    linked = re.findall(r'data-subnav-link="([^"]+)"', body)
    assert "controls" not in linked
    assert {"display", "schedule", "troubleshooting"} <= set(linked)
    for anchor in linked:
        assert f'id="{anchor}"' in body, anchor


def test_devices_quick_panel_tiles_link_into_the_device_page(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    body = client.get("/settings/devices").get_data(as_text=True)
    assert f'href="/settings/devices/{dev}#lineups"' in body
    assert f'href="/settings/devices/{dev}#timing"' in body
    assert f'href="/settings/devices/{dev}#connection"' in body


def test_device_page_unknown_device_redirects_to_the_list(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/devices/nope", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location.endswith("/settings/devices")


@pytest.mark.parametrize("source", ["ios_ble", "android_ble"])
def test_phone_mode_report_is_visible_and_wifi_removes_bluetooth_badge(
    app: Flask, source: str
) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    other = _add(client, "study", kind="pi_png_client", name="Study")
    before = client.get("/settings/devices").get_data(as_text=True)
    assert "data-device-mode-badge" not in before
    transport_before = re.findall(r'data-transport="([^"]+)"', before)
    token, _ = app.config["COMPANION_TOKENS"].issue(
        client={"name": "Phone", "platform": "ios", "version": "1"},
        scopes=["device_setup:write"],
    )
    headers = {"Authorization": f"Bearer {token}"}
    path = f"/api/app/v1/devices/{dev}/mode-observation"
    for mode, timestamp in [
        ("bluetooth", "2026-01-02T03:04:05Z"),
        ("wifi", "2026-01-02T03:04:06Z"),
    ]:
        assert (
            client.put(
                path,
                headers=headers,
                json={"mode": mode, "observed_at": timestamp, "source": source},
            ).status_code
            == 204
        )
        body = client.get("/settings/devices").get_data(as_text=True)
        assert re.findall(r'data-transport="([^"]+)"', body) == transport_before
        assert body.count("data-device-mode-badge") == (1 if mode == "bluetooth" else 0)
        detail = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
        assert "data-device-mode-observation" in detail
        assert "Phone last confirmed" in detail
        assert f'datetime="{timestamp}"' in detail
        assert ("data-device-mode-badge" in detail) == (mode == "bluetooth")
        assert ("Bluetooth" if mode == "bluetooth" else "Wi-Fi") in detail
        provenance = app.config["DEVICE_FACTS"].get(dev)["mode_observation"]["token_id"]
        assert provenance not in detail and token not in detail
        other_detail = client.get(f"/settings/devices/{other}").get_data(as_text=True)
        assert "data-device-mode-badge" not in other_detail
        assert "data-device-mode-observation" not in other_detail
        assert "Phone last confirmed" not in other_detail
        assert "No report yet" not in other_detail


def test_invalid_phone_observation_does_not_break_device_page(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    app.config["DEVICE_FACTS"].record_mode_observation(
        dev,
        {
            "mode": "bluetooth",
            "source": "ios_ble",
            "observed_at": "not-a-date",
        },
    )
    response = client.get(f"/settings/devices/{dev}")
    assert response.status_code == 200
    assert "data-device-mode-badge" not in response.get_data(as_text=True)
    assert "data-device-mode-observation" not in response.get_data(as_text=True)


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


def test_connection_chips_link_to_the_firmware_page(app: Flask, monkeypatch) -> None:
    """The Connection header always offers the Firmware page; a newer build
    makes the "available" pill go there too, with the GitHub release one
    step away as release notes (#280)."""
    from app.firmware_check import FirmwareInfo
    from app.settings import index_routes

    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    body = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    assert 'href="/settings/firmware" data-firmware-page-link' in body

    app.config["DEVICE_STATUS"][dev] = {
        "received_at": time.time(),
        "parsed": {"fw_version": "1.39.0"},
    }
    monkeypatch.setattr(index_routes, "_firmware_check_enabled", lambda: True)
    monkeypatch.setattr(
        index_routes.firmware_check_module,
        "latest_for_kind",
        lambda kind_id, current="", wait=True: FirmwareInfo(
            version="1.40.0",
            released_at="2026-09-18",
            url="https://github.com/example/firmware/releases/tag/v1.40.0",
            notes_headline="Faster wakes",
            assets=(),
        ),
    )
    body = client.get(f"/settings/devices/{dev}").get_data(as_text=True)
    # The rail says an update is out: a badge on the firmware vital and a
    # call-out whose Update link goes to the Firmware page.
    rail = _rail_html(body)
    assert "dx-v-badge" in rail
    assert re.search(
        r'<span>Firmware v1.40.0 available</span><a href="/settings/firmware" title="Faster wakes',
        rail,
    )
    # The Connection head keeps the release notes, left of the Firmware page.
    conn = body[body.index('id="connection"') :]
    head = conn[: conn.index('class="dx-kv"')]
    assert 'href="https://github.com/example/firmware/releases/tag/v1.40.0"' in head
    assert head.index("Release notes") < head.index("Firmware page")


@pytest.mark.parametrize("source", ["ios_ble", "android_ble"])
def test_phone_telemetry_fills_existing_columns_without_changing_heartbeat(
    app: Flask, source: str
) -> None:
    from app.state.device_facts import DeviceFactsStore

    client = app.test_client()
    _sign_in(client)
    dev = _add(client)
    report = {
        "mode": "bluetooth",
        "source": source,
        "observed_at": "2026-01-02T03:04:05Z",
        "battery": {"percent": 65, "observed_at": "2026-01-02T03:04:06Z"},
        "firmware": {"version": "0.9.11", "observed_at": "2026-01-02T03:04:04Z"},
    }
    app.config["DEVICE_FACTS"].record_mode_observation(dev, report)
    # Reopen persisted facts, as after container recreation.
    app.config["DEVICE_FACTS"] = DeviceFactsStore(app.config["DEVICE_FACTS"]._path)
    for path in ("/settings/devices", f"/settings/devices/{dev}"):
        body = client.get(path).get_data(as_text=True)
        assert "65%" in body and "v0.9.11" in body
    assert dev not in app.config["DEVICE_STATUS"]
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "no heartbeat yet" in body and 'title="Bluetooth · ' in body
    # New Wi-Fi heartbeats win without modifying the persisted phone evidence.
    app.config["DEVICE_STATUS"][dev] = {
        "received_at": time.time(),
        "parsed": {"battery_pct": 70, "fw_version": "0.9.12"},
    }
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "70%" in body and "v0.9.12" in body
    assert "65%" not in body and "v0.9.11" not in body
    # A newer phone reading supersedes older Wi-Fi data, including battery 0%.
    app.config["DEVICE_STATUS"][dev]["received_at"] = 1
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "65%" in body and "v0.9.11" in body
    report["battery"] = {"percent": 0, "observed_at": "2026-01-02T03:04:07Z"}
    app.config["DEVICE_FACTS"].record_mode_observation(dev, report)
    assert "0%" in client.get("/settings/devices").get_data(as_text=True)


def _thumb(app: Flask, panel: dict[str, int] | None, *, has_render: bool = True) -> str:
    section = {
        "overview": {"has_render": has_render},
        "preview_url": "/preview/lounge.png",
        "device_name": "Lounge",
        "panel": panel,
    }
    with app.test_request_context("/"):
        return app.jinja_env.from_string(
            '{% from "_device_parts.html" import render_thumb with context %}'
            '{{ render_thumb(section, "dx-thumb--quick") }}'
        ).render(section=section)


def test_the_thumbnail_keeps_the_screens_shape(app: Flask) -> None:
    """A portrait screen's latest render used to sit in a fixed 4:3 box and
    be cropped to its middle. The box now takes the panel's aspect ratio."""
    portrait = _thumb(app, {"w": 1440, "h": 2560})
    assert 'style="--thumb-ar: 1440 / 2560"' in portrait
    assert "dx-thumb--portrait" in portrait
    landscape = _thumb(app, {"w": 800, "h": 480})
    assert 'style="--thumb-ar: 800 / 480"' in landscape
    assert "dx-thumb--portrait" not in landscape


def test_the_thumbnail_falls_back_to_the_fixed_box_without_a_panel_or_render(
    app: Flask,
) -> None:
    assert "--thumb-ar" not in _thumb(app, None)
    assert "--thumb-ar" not in _thumb(app, {"w": 1440, "h": 2560}, has_render=False)


def test_an_opened_dashboard_preview_keeps_the_dashboards_shape(app: Flask) -> None:
    """The Dashboards list's opened row used to fix the preview at 260 px wide,
    so a portrait dashboard sat in a squarish box. The box now carries the
    dashboard's own aspect ratio, and a portrait one is marked for a taller
    box."""
    client = app.test_client()
    _sign_in(client)
    for name, w, h in (("Tall", 1440, 2560), ("Wide", 800, 480)):
        resp = client.post("/api/mcp/pages", json={"name": name, "w": w, "h": h})
        assert resp.status_code == 200
    body = client.get("/pages").get_data(as_text=True)
    previews = re.findall(r'<div class="dx-dashquick-preview([^"]*)"\s+style="([^"]*)"', body)
    by_ratio = {style: cls for cls, style in previews}
    assert "is-portrait" in by_ratio["aspect-ratio: 1440 / 2560"]
    assert "is-portrait" not in by_ratio["aspect-ratio: 800 / 480"]


def _tall_and_wide_pages(client) -> dict[str, str]:
    ids = {}
    for name, w, h in (("Tall", 1440, 2560), ("Wide", 800, 480)):
        resp = client.post("/api/mcp/pages", json={"name": name, "w": w, "h": h})
        assert resp.status_code == 200
        ids[name] = resp.get_json()["id"]
    return ids


def test_the_dashboards_row_thumbnail_keeps_the_dashboards_shape(app: Flask) -> None:
    """The small thumbnail on each Dashboards row was a fixed 64 x 40 box that
    cropped a portrait render to its middle. It now carries the dashboard's own
    aspect ratio, and a portrait one is marked."""
    client = app.test_client()
    _sign_in(client)
    _tall_and_wide_pages(client)
    body = client.get("/pages").get_data(as_text=True)
    thumbs = re.findall(r'<span class="dx-dash-thumb([^"]*)"\s+style="([^"]*)"', body)
    by_ratio = {style.split(";")[0]: cls for cls, style in thumbs}
    assert "is-portrait" in by_ratio["--thumb-ar: 1440 / 2560"]
    assert "is-portrait" not in by_ratio["--thumb-ar: 800 / 480"]
    # The width the box is clamped to needs the plain ratio too.
    assert any("--thumb-r: 0.5625" in style for _, style in thumbs)
    assert any("--thumb-r: 1.6667" in style for _, style in thumbs)


def _lineup_of(client, name: str, page_ids: list[str]) -> str:
    resp = client.post(
        "/decks/new/lineup",
        data={"intent": "cycle", "name": name, "page_ids": page_ids, "interval_minutes": "30"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def test_the_lineups_page_screens_keep_each_dashboards_shape(app: Flask) -> None:
    """Each dashboard card on the Lineups page was a fixed 150 x 92 box with
    object-fit: cover, so a portrait render was cropped to its middle. The
    frame now carries the dashboard's aspect ratio and a portrait one is
    marked for a taller card, while a landscape one stays landscape."""
    client = app.test_client()
    _sign_in(client)
    ids = _tall_and_wide_pages(client)
    _lineup_of(client, "Mixed", [ids["Tall"], ids["Wide"]])
    body = client.get("/decks").get_data(as_text=True)
    # Each step tile carries its dashboard's own ratio (the strip sizes it by
    # height from --arw/--arh), never a fixed crop box.
    ratios = set(
        re.findall(r'<span class="lu-thumb [pl]" style="--ar:(\d+/\d+);--arw:\d+;--arh:\d+"', body)
    )
    assert {"1440/2560", "800/480"} <= ratios


def test_the_lineup_editor_hands_the_page_cards_each_dashboards_size(app: Flask) -> None:
    """The editor's page cards are drawn client-side from this data; they need
    the dashboard's size to keep its shape instead of cropping it."""
    client = app.test_client()
    _sign_in(client)
    ids = _tall_and_wide_pages(client)
    lineup = _lineup_of(client, "Mixed", [ids["Tall"], ids["Wide"]])
    body = client.get(f"/decks/{lineup}/edit").get_data(as_text=True)
    assert re.search(r'"h":\s*2560', body) and re.search(r'"w":\s*1440', body)
    assert re.search(r'"h":\s*480', body) and re.search(r'"w":\s*800', body)
    js = (REPO_ROOT / "static" / "deck_editor.js").read_text()
    assert "--thumb-ar" in js and "is-portrait" in js


def test_every_thumbnail_style_fits_instead_of_cropping() -> None:
    """None of the thumbnails that carry a ratio may crop: their images fit
    (object-fit: contain) inside a box with the dashboard's shape."""
    css = REPO_ROOT / "static" / "style"
    decks = (css / "dx-decks.css").read_text()
    lineups = (css / "dx-lineups.css").read_text()
    dashboards = (css / "dx-dashboards.css").read_text()
    assert re.search(r"\.lu \.lu-thumb img\s*{[^}]*object-fit: contain", lineups)
    assert re.search(r"\.lu \.lu-thumb\s*{[^}]*aspect-ratio: var\(--ar\)", lineups)
    assert re.search(r'\.dxe-thumb\[style\*="--thumb-r"\] img\s*{[^}]*object-fit: contain', decks)
    assert re.search(
        r'\.dx-dash-thumb\[style\*="--thumb-r"\] img\s*{[^}]*object-fit: contain', dashboards
    )
