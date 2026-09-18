"""Page-level wake cadence (#144).

How often a device wakes has always been a device property, but the
content is what knows how often it changes: a once-daily agenda on a
five-minute panel woke roughly 288 times a day to collect 287 304s. A
page may now declare its own ``sleep_interval_s``, and a sleeping device
showing that page wakes on it.

What these pin: the resolution order, the clamp to what the kind's
firmware accepts, the cases that must fall back rather than guess, and
that an always-on panel is left alone.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page

# A profile whose schema declares min 5 / max 604800 for the wake interval.
KIND = "esp32_client"


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


def _register(app: Flask, client, device_id: str) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": KIND, "panel_w": 800, "panel_h": 480}),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _show(app: Flask, device_id: str, page: Page) -> None:
    """Put a page on the device's glass: save it, and point the latest
    render at it, which is what names the displayed page."""
    app.config["PAGE_STORE"].save(page)
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = {
        "digest": "abc",
        "ext": "bin",
        "filename": "abc.bin",
        "page_id": page.id,
        "timestamp": time.time(),
    }


def _next_poll_s(client, token: str, device_id: str) -> int:
    resp = client.post(
        f"/api/v1/device/{device_id}/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    assert resp.status_code == 200
    return int(resp.get_json()["next_poll_s"])


def test_a_page_can_sleep_longer_than_the_device_interval(app: Flask) -> None:
    """The case the issue is about: a daily dashboard on a panel set to
    wake every five minutes."""
    client = app.test_client()
    token = _register(app, client, "panel")
    app.config["SETTINGS_STORE"].update_section("devices", {"panel": {"sleep_interval_s": 300}})
    assert _next_poll_s(client, token, "panel") == 300

    _show(app, "panel", Page(id="agenda", name="Agenda", sleep_interval_s=86400))
    assert _next_poll_s(client, token, "panel") == 86400


def test_a_page_can_also_wake_faster(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "panel")
    app.config["SETTINGS_STORE"].update_section("devices", {"panel": {"sleep_interval_s": 3600}})
    _show(app, "panel", Page(id="transit", name="Transit", sleep_interval_s=120))
    assert _next_poll_s(client, token, "panel") == 120


def test_the_page_interval_is_clamped_to_what_the_firmware_accepts(app: Flask) -> None:
    """Only the device knows what its firmware takes, so a dashboard
    asking for a second is given the kind's floor rather than refused."""
    client = app.test_client()
    token = _register(app, client, "panel")
    _show(app, "panel", Page(id="hot", name="Hot", sleep_interval_s=1))
    # esp32_client declares min 5.
    assert _next_poll_s(client, token, "panel") == 5


def test_a_page_that_declares_nothing_leaves_the_device_alone(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "panel")
    app.config["SETTINGS_STORE"].update_section("devices", {"panel": {"sleep_interval_s": 300}})
    _show(app, "panel", Page(id="plain", name="Plain"))
    assert _next_poll_s(client, token, "panel") == 300


def test_a_device_with_no_render_yet_keeps_its_own_interval(app: Flask) -> None:
    """No render means no page on the glass, and nothing to read a
    cadence from."""
    client = app.test_client()
    token = _register(app, client, "panel")
    app.config["SETTINGS_STORE"].update_section("devices", {"panel": {"sleep_interval_s": 300}})
    app.config["PAGE_STORE"].save(Page(id="agenda", name="Agenda", sleep_interval_s=86400))
    assert _next_poll_s(client, token, "panel") == 300


def test_a_deleted_page_falls_back_rather_than_stranding_the_device(app: Flask) -> None:
    """The render outlives the page it was made from; a device must not
    inherit an interval from a dashboard that no longer exists."""
    client = app.test_client()
    token = _register(app, client, "panel")
    app.config["SETTINGS_STORE"].update_section("devices", {"panel": {"sleep_interval_s": 300}})
    _show(app, "panel", Page(id="gone", name="Gone", sleep_interval_s=86400))
    app.config["PAGE_STORE"].delete("gone")
    assert _next_poll_s(client, token, "panel") == 300


def test_an_always_on_panel_is_not_put_on_a_page_cadence(app: Flask) -> None:
    """An always-on device is not on the sleep grid at all, so a page's
    cadence says nothing about when it comes back — and honouring one
    would slow the manual Send that always-on mode exists for."""
    client = app.test_client()
    token = _register(app, client, "panel")
    app.config["SETTINGS_STORE"].update_section(
        "devices", {"panel": {"always_on": True, "awake_poll_s": 10}}
    )
    _show(app, "panel", Page(id="agenda", name="Agenda", sleep_interval_s=86400))
    assert _next_poll_s(client, token, "panel") == 10


# -- the surfaces that set it -------------------------------------------


def test_the_dashboards_form_sets_and_clears_the_interval(app: Flask) -> None:
    client = app.test_client()
    _register(app, client, "panel")
    app.config["PAGE_STORE"].save(Page(id="agenda", name="Agenda"))

    client.post("/pages/agenda", data={"sleep_interval_s": "86400"})
    assert app.config["PAGE_STORE"].get("agenda").sleep_interval_s == 86400

    # Empty is the meaningful value: hand the device back its own interval.
    client.post("/pages/agenda", data={"sleep_interval_s": ""})
    assert app.config["PAGE_STORE"].get("agenda").sleep_interval_s is None

    # "custom" with nothing typed is a stray submit, not a reset.
    client.post("/pages/agenda", data={"sleep_interval_s": "3600"})
    client.post(
        "/pages/agenda",
        data={"sleep_interval_s": "custom", "sleep_interval_s_custom": ""},
    )
    assert app.config["PAGE_STORE"].get("agenda").sleep_interval_s == 3600


def test_an_agent_can_set_and_read_the_interval(app: Flask) -> None:
    client = app.test_client()
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True, "composer": True})
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    created = client.post("/api/mcp/pages", json={"name": "Agenda", "w": 800, "h": 480})
    assert created.status_code in (200, 201)
    page_id = created.get_json()["id"]

    assert (
        client.patch(
            f"/api/mcp/pages/{page_id}/canvas", json={"sleep_interval_s": 86400}
        ).status_code
        == 200
    )
    assert client.get(f"/api/mcp/pages/{page_id}/canvas").get_json()["sleep_interval_s"] == 86400
    listed = client.get("/api/mcp/pages").get_json()["pages"]
    assert any(p["id"] == page_id and p["sleep_interval_s"] == 86400 for p in listed)

    # null hands the device back its own interval; nonsense is refused rather
    # than silently stored.
    assert (
        client.patch(
            f"/api/mcp/pages/{page_id}/canvas", json={"sleep_interval_s": None}
        ).status_code
        == 200
    )
    assert client.get(f"/api/mcp/pages/{page_id}/canvas").get_json()["sleep_interval_s"] is None
    assert (
        client.patch(
            f"/api/mcp/pages/{page_id}/canvas", json={"sleep_interval_s": "daily"}
        ).status_code
        == 422
    )


def test_the_clamp_reads_the_trmnl_kind_bounds_too(app: Flask) -> None:
    """The TRMNL kind declares its wake bounds under ``refresh_rate_s`` rather
    than ``sleep_interval_s``; the clamp has to find them there, or a page
    asking for one second reaches a TRMNL as ``refresh_rate: 1``."""
    from app import page_cadence

    kind = app.config["DEVICE_REGISTRY"].get("trmnl_client")
    assert kind is not None
    assert page_cadence._schema_bounds(kind) == (5, 86400)

    _show(app, kind.id, Page(id="hot", name="Hot", sleep_interval_s=1))
    with app.test_request_context():
        assert page_cadence.page_sleep_interval_s(kind) == 5


def test_an_unparseable_form_value_keeps_what_is_stored(app: Flask) -> None:
    """Garbage in the custom box must not invent a one-second interval on a
    page that had none; the stored value (or its absence) stays."""
    client = app.test_client()
    _register(app, client, "panel")
    app.config["PAGE_STORE"].save(Page(id="agenda", name="Agenda"))

    client.post(
        "/pages/agenda", data={"sleep_interval_s": "custom", "sleep_interval_s_custom": "soon"}
    )
    assert app.config["PAGE_STORE"].get("agenda").sleep_interval_s is None

    client.post("/pages/agenda", data={"sleep_interval_s": "3600"})
    client.post("/pages/agenda", data={"sleep_interval_s": "soon"})
    assert app.config["PAGE_STORE"].get("agenda").sleep_interval_s == 3600
