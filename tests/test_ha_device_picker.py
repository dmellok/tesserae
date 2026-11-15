"""HA device picker for config forms (OpenDisplay support).

Pure-logic tests for parsing HA's template output, plus the settings
endpoint that lists an integration's devices (degrading to an empty list
with a message when HA isn't configured).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.ha_device_picker import (
    build_template,
    list_integration_devices,
    parse_devices,
    parse_resolution,
)
from app.main import REPO_ROOT, create_app


def test_parse_resolution_from_model() -> None:
    assert parse_resolution("296x128 E Ink") == (296, 128)
    assert parse_resolution("800 x 480") == (800, 480)
    assert parse_resolution("480×800 colour") == (480, 800)  # unicode times
    # A diagonal-only model carries no pixels.
    assert parse_resolution('2.9" E Ink') is None
    assert parse_resolution(None) is None
    assert parse_resolution("") is None


def test_parse_devices_adds_resolution_and_sorts() -> None:
    rendered = (
        '[{"device_id": "b2", "name": "Zebra", "model": "296x128 E Ink"},'
        ' {"device_id": "a1", "name": "apple", "model": "2.9\\" E Ink"}]'
    )
    out = parse_devices(rendered)
    # Sorted case-insensitively by name: apple before Zebra.
    assert [d["device_id"] for d in out] == ["a1", "b2"]
    # Zebra's model carries pixels -> w/h present; apple's diagonal -> absent.
    zebra = next(d for d in out if d["device_id"] == "b2")
    assert zebra["w"] == 296 and zebra["h"] == 128
    apple = next(d for d in out if d["device_id"] == "a1")
    assert "w" not in apple


def test_parse_devices_tolerates_garbage() -> None:
    assert parse_devices("not json") == []
    assert parse_devices('{"not": "a list"}') == []
    # Rows without a device_id are dropped.
    assert parse_devices('[{"name": "x"}]') == []


def test_build_template_validates_and_embeds_integration() -> None:
    assert "integration_entities('opendisplay')" in build_template("opendisplay")


def test_list_integration_devices_rejects_bad_domain() -> None:
    found, err = list_integration_devices(object(), "not a domain!")
    assert found == [] and err == "invalid integration"


def test_list_integration_devices_soft_without_ha() -> None:
    # A module with no render_template (ha_core absent / old) -> empty + msg.
    found, err = list_integration_devices(object(), "opendisplay")
    assert found == [] and err is not None


def test_list_integration_devices_parses_from_mod() -> None:
    class FakeMod:
        @staticmethod
        def render_template(_template: str, *, timeout: int = 10) -> str:
            return '[{"device_id": "abc", "name": "Kitchen", "model": "296x128 E Ink"}]'

    found, err = list_integration_devices(FakeMod(), "opendisplay")
    assert err is None
    assert found == [
        {"device_id": "abc", "name": "Kitchen", "model": "296x128 E Ink", "w": 296, "h": 128}
    ]


def test_list_integration_devices_surfaces_render_error() -> None:
    class BoomMod:
        @staticmethod
        def render_template(_template: str, *, timeout: int = 10) -> str:
            raise RuntimeError("Home Assistant is not configured")

    found, err = list_integration_devices(BoomMod(), "opendisplay")
    assert found == [] and err == "Home Assistant is not configured"


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_endpoint_soft_when_ha_unconfigured(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/devices/ha-devices.json?integration=opendisplay")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["devices"] == []
    assert body["error"]  # a message so the UI can fall back to manual entry


def test_endpoint_returns_devices_when_ha_answers(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    monkeypatch.setattr(
        core,
        "render_template",
        lambda _t, timeout=10: '[{"device_id":"abc","name":"Kitchen","model":"296x128 E Ink"}]',
        raising=False,
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/devices/ha-devices.json?integration=opendisplay")
    body = resp.get_json()
    assert body["error"] is None
    assert body["devices"][0]["device_id"] == "abc"
    assert body["devices"][0]["w"] == 296 and body["devices"][0]["h"] == 128


def test_add_form_persists_picked_ha_device_id(app: Flask) -> None:
    """Adding an OpenDisplay tag from the Add-device form stores the picked
    HA device id as config, so it's usable without opening the card."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/add",
        data={
            "kind": "opendisplay_ha",
            "id": "kitchen_tag",
            "name": "Kitchen tag",
            "panel_w": "296",
            "panel_h": "128",
            "panel_orientation": "landscape",
            "ha_device_id": "4ab9c7f0e2",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    assert section.get("kitchen_tag", {}).get("ha_device_id") == "4ab9c7f0e2"


def _devices_page(app: Flask) -> str:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/devices")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_add_form_points_at_the_bridge_without_a_ha_connection(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No add-on and no Home Assistant Core connection: there is nothing to
    push through, so the OpenDisplay tab explains the bridge instead."""
    monkeypatch.delenv("TESSERAE_HA_INGRESS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    html = _devices_page(app)
    assert 'name="kind" value="opendisplay_ha"' not in html
    assert "has no Home\n          Assistant connection" in html


def test_add_form_offers_ha_push_outside_the_addon_once_ha_core_is_set(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Tesserae on Docker / Proxmox with the Home Assistant Core plugin
    configured can push through HA (frames are uploaded over its API), so
    the form is no longer reserved for the add-on."""
    monkeypatch.delenv("TESSERAE_HA_INGRESS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    app.config["SETTINGS_STORE"].update_section(
        "plugins", {"ha_core": {"base_url": "http://ha", "token_secret": "t"}}
    )
    html = _devices_page(app)
    assert 'name="kind" value="opendisplay_ha"' in html
    assert "uploads each frame to Home Assistant" in html


def _add_tag(client, kind: str = "opendisplay_ha", device_id: str = "kitchen_tag") -> None:
    resp = client.post(
        "/settings/devices/add",
        data={
            "kind": kind,
            "id": device_id,
            "name": "Kitchen tag",
            "panel_w": "296",
            "panel_h": "128",
            "panel_orientation": "landscape",
        },
    )
    assert resp.status_code in (302, 303)


def test_tag_page_offers_one_rotation_and_a_palette(app: Flask) -> None:
    """An OpenDisplay tag cannot report its colours, so the operator picks
    the palette. The page shows one Rotation: not the pi_png client options
    HA never sees, and not the older HA turn on a tag that never set it."""
    client = app.test_client()
    _sign_in(client)
    _add_tag(client)
    html = client.get("/settings/devices/kitchen_tag").get_data(as_text=True)

    assert 'name="panel_gamut"' in html
    assert "Colour palette" in html
    for label in ("Black and white", "Black, white and red", "Six colours (Spectra 6)"):
        assert label in html
    # The kind's spectra_6 default shows as the selected six-colour choice.
    assert "Unavailable" not in html
    assert 'name="panel_orientation"' in html
    assert "Client rotation" not in html
    assert "Letterbox colour" not in html
    assert 'name="rotate"' not in html


def test_tag_page_keeps_the_older_ha_turn_while_it_is_set(app: Flask) -> None:
    """A tag that still relies on HA's rotation shows the setting, so it can
    be seen and cleared."""
    client = app.test_client()
    _sign_in(client)
    _add_tag(client)
    app.config["SETTINGS_STORE"].patch_section("devices", {"kitchen_tag": {"rotate": "90"}})
    html = client.get("/settings/devices/kitchen_tag").get_data(as_text=True)
    assert 'name="rotate"' in html
    assert "Turn in Home Assistant (older setup)" in html


def test_bridge_tag_page_offers_a_palette(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_tag(client, kind="opendisplay", device_id="bridge_tag")
    html = client.get("/settings/devices/bridge_tag").get_data(as_text=True)
    assert 'name="panel_gamut"' in html
    assert "Colour palette" in html


def test_picked_palette_is_saved_on_the_panel(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_tag(client)
    resp = client.post(
        "/settings/devices/kitchen_tag/panel",
        data={
            "panel_w": "296",
            "panel_h": "128",
            "panel_orientation": "landscape",
            "panel_gamut": "bwr_3",
        },
    )
    assert resp.status_code in (302, 303)
    device = app.config["DEVICE_REGISTRY"].get("kitchen_tag")
    assert device.panel["gamut"] == "bwr_3"
    html = client.get("/settings/devices/kitchen_tag").get_data(as_text=True)
    assert 'value="bwr_3" selected' in html


_SOLUM = {
    "device_id": "hadev1",
    "entry_id": "01M7CWG65MJNEDEK6KNCG9SX8E",
    "name": "Office tag",
    "model": '3.5" BWRY',
}


def _mock_tags(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Home Assistant that knows one Solum 3.5" tag (184x384, BWRY)."""
    import json

    from app import opendisplay_tags

    opendisplay_tags.reset_cache()
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module

    def render_template(template: str, timeout: int = 10) -> str:
        return json.dumps([_SOLUM])

    def request_json(path: str, timeout: int = 10) -> dict:
        assert path.endswith(_SOLUM["entry_id"])
        display = {"pixel_width": 184, "pixel_height": 384, "rotation": 0, "color_scheme": 3}
        return {"data": {"device_config": {"displays": [display]}}}

    monkeypatch.setattr(core, "render_template", render_template, raising=False)
    monkeypatch.setattr(core, "request_json", request_json, raising=False)


def test_picker_reports_what_the_tag_itself_says(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The picker rows carry the tag's real size and palette, a device id to
    start from, and whether a Tesserae device already drives the tag."""
    _mock_tags(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)
    row = client.get("/settings/devices/ha-devices.json?integration=opendisplay").get_json()[
        "devices"
    ][0]
    assert (row["w"], row["h"]) == (184, 384)
    assert row["gamut"] == "bwry_4"
    assert row["palette"] == "Black, white, red and yellow"
    assert row["suggested_id"] == "office_tag"
    assert row["registered"] == ""


def test_adding_a_found_tag_takes_its_size_palette_and_surface(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-click Add posts what the picker reported. The device comes
    out at the tag's size and palette, with the surface stored for the
    publisher, and the picker then shows the tag as taken."""
    _mock_tags(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/settings/devices/add",
        data={
            "kind": "opendisplay_ha",
            "ha_device_id": "hadev1",
            "id": "office_tag",
            "name": "Office tag",
            "panel_w": "184",
            "panel_h": "384",
            "panel_orientation": "portrait",
            "panel_gamut": "bwry_4",
        },
    )
    assert resp.status_code in (302, 303)
    device = app.config["DEVICE_REGISTRY"].get("office_tag")
    assert (device.panel["w"], device.panel["h"]) == (184, 384)
    assert device.panel["orientation"] == "portrait"
    assert device.panel["gamut"] == "bwry_4"
    stored = app.config["SETTINGS_STORE"].get_section("devices")["office_tag"]
    assert stored["ha_device_id"] == "hadev1"
    assert (stored["tag_w"], stored["tag_h"]) == (184, 384)

    rows = client.get("/settings/devices/ha-devices.json?integration=opendisplay").get_json()
    assert rows["devices"][0]["registered"] == "office_tag"
    assert rows["devices"][0]["suggested_id"] == "office_tag_2"

    # A tall tag hung upright reads 0 degrees, not 90.
    html = client.get("/settings/devices/office_tag").get_data(as_text=True)
    assert '<option value="portrait" selected>0°</option>' in html.replace("\n", "")


def test_detected_palette_wins_over_the_forms_default(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding by hand with the palette left at its six-colour default still
    ends on the palette the tag reports."""
    _mock_tags(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={
            "kind": "opendisplay_ha",
            "ha_device_id": "hadev1",
            "id": "office_tag",
            "name": "Office tag",
            "panel_w": "184",
            "panel_h": "384",
            "panel_orientation": "portrait",
            "panel_gamut": "waveshare_e6",
        },
    )
    assert app.config["DEVICE_REGISTRY"].get("office_tag").panel["gamut"] == "bwry_4"


def test_add_form_offers_found_tags_models_and_palette(app: Flask) -> None:
    app.config["SETTINGS_STORE"].update_section(
        "plugins", {"ha_core": {"base_url": "http://ha", "token_secret": "t"}}
    )
    html = _devices_page(app)
    assert "data-od-found" in html
    assert "data-od-model" in html
    assert "OpenDisplay 4.26&#34; Mono Kit (800 × 480)" in html
    assert 'name="panel_gamut"' in html
    assert "Black, white and yellow" in html


def test_tag_page_shows_delivery_and_send_again(app: Flask, tmp_path: Path) -> None:
    """The tag's page says whether the last picture arrived, with Home
    Assistant's reason when it did not, and offers to send it again."""
    client = app.test_client()
    _sign_in(client)
    _add_tag(client)
    html = client.get("/settings/devices/kitchen_tag").get_data(as_text=True)
    assert "Nothing sent yet" in html
    assert "Colour conversion" in html and "Refresh" in html

    pub = app.config["OPENDISPLAY_HA_PUBLISHER"]
    pub._record_failure("kitchen_tag", "k", "The tag is not reachable over Bluetooth.")
    html = client.get("/settings/devices/kitchen_tag").get_data(as_text=True)
    assert "The last picture did not reach the tag" in html
    assert "The tag is not reachable over Bluetooth." in html
    assert "Trying again in about 1 min" in html
    assert "Send again" in html

    pub._record_sent("kitchen_tag", "k")
    html = client.get("/settings/devices/kitchen_tag").get_data(as_text=True)
    assert "Picture delivered" in html


def test_send_again_route(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_tag(client)
    # Nothing rendered yet: refused with a message, no crash.
    resp = client.post("/settings/devices/kitchen_tag/opendisplay/resend")
    assert resp.status_code in (302, 303)

    pub = app.config["OPENDISPLAY_HA_PUBLISHER"]
    asked: list[str] = []
    pub.resend = asked.append  # type: ignore[method-assign]
    app.config["PUSH_MANAGER"]._latest_renders["kitchen_tag"] = {"composition_digest": "abc"}
    resp = client.post("/settings/devices/kitchen_tag/opendisplay/resend")
    assert resp.status_code in (302, 303)
    assert asked == ["kitchen_tag"]

    # Not an OpenDisplay tag: nothing to resend.
    resp = client.post("/settings/devices/nope/opendisplay/resend")
    assert resp.status_code in (302, 303)
    assert asked == ["kitchen_tag"]
