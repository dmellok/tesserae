"""REST touch-dispatch tests (issue #49): the ``touch_*`` query params
on ``GET /frame`` and the standalone ``POST /tap`` endpoint.

Same full-app fixture shape as ``test_rest_api``. Dispatched cases use a
``webhook:`` action so no real page push (and no Playwright render) is
triggered; the webhook itself fires into a closed local port from a
daemon thread and is logged-and-dropped by design."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.touch_regions import save_regions


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


def _register(app: Flask, client, device_id: str = "hall_panel") -> str:
    """Register a device instance and return its bearer token."""
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.1.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["device_token"]


WEBHOOK_REGION = {
    "x": 0,
    "y": 0,
    "w": 800,
    "h": 600,
    "depth": 1,
    "order": 0,
    "tap": "webhook:http://127.0.0.1:9/tesserae-test",
    "swipe": None,
    "slide": None,
    # Config origin: the provenance gate only honours side-effecting
    # actions (webhook/ha) from editor/MCP-authored config.
    "origin": "config",
    "dangling": [],
}


def _seed_frame(app: Flask, device_id: str, *, regions: list[dict] | None = None) -> None:
    """Fake a rendered frame + its touch region sidecar."""
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders[device_id] = {
        "digest": "art123",
        "ext": "bin",
        "filename": "art123.bin",
        "composition_digest": "comp456",
    }
    if regions is not None:
        save_regions(push_mgr._renders_dir, "comp456", regions)


# -- GET /frame touch params ---------------------------------------------


def test_frame_touch_wake_dispatches_and_serves_frame(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        "/api/v1/device/hall_panel/frame"
        "?touch_x0=100&touch_y0=100&touch_digest=art123&touch_event_id=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "art123"
    rows = list(app.config["EVENT_LOG"].list(type="touch", source="touch", limit=10))
    assert len(rows) == 1
    assert rows[0].status == "webhook_dispatched"
    assert rows[0].extra["gesture"] == "tap"


def test_frame_touch_wake_accepts_quoted_etag_digest(app: Flask) -> None:
    """Firmware that echoes the ETag header verbatim (with quotes) must
    not be treated as stale."""
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        '/api/v1/device/hall_panel/frame?touch_x0=5&touch_y0=5&touch_digest="art123"',
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = list(app.config["EVENT_LOG"].list(type="touch", source="touch", limit=10))
    assert rows and rows[0].status == "webhook_dispatched"


def test_frame_stale_touch_degrades_to_plain_poll(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        "/api/v1/device/hall_panel/frame?touch_x0=100&touch_y0=100&touch_digest=oldframe",
        headers={"Authorization": f"Bearer {token}"},
    )
    # The wake still serves the current frame.
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "art123"
    rows = list(app.config["EVENT_LOG"].list(type="touch", source="touch", limit=10))
    assert rows and rows[0].status == "stale"


def test_frame_touch_params_ignored_when_button_present(app: Flask) -> None:
    """A wake carrying both a button and touch params dispatches the
    button only (firmware shouldn't send both; button wins)."""
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        "/api/v1/device/hall_panel/frame"
        "?button=refresh&touch_x0=100&touch_y0=100&touch_digest=art123",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    touch_rows = list(app.config["EVENT_LOG"].list(type="touch", source="touch", limit=10))
    assert touch_rows == []


# -- POST /tap -----------------------------------------------------------


def _tap(client, token: str, body: dict, device_id: str = "hall_panel"):
    return client.post(
        f"/api/v1/device/{device_id}/tap",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(body),
    )


def test_tap_requires_auth(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register(app, client)
    resp = client.post(
        "/api/v1/device/hall_panel/tap",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"x0": 1, "y0": 1, "digest": "art123"}),
    )
    assert resp.status_code == 401


def test_tap_validates_body(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel")

    assert _tap(app.test_client(), token, {"y0": 1, "digest": "d"}).status_code == 400
    assert _tap(app.test_client(), token, {"x0": 1, "y0": 1}).status_code == 400
    assert _tap(app.test_client(), token, {"x0": -5, "y0": 1, "digest": "d"}).status_code == 400


def test_tap_dispatches_webhook_action(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = _tap(client, token, {"x": 50, "y": 60, "digest": "art123"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["outcome"] == "webhook_dispatched"
    assert body["gesture"] == "tap"
    assert body["action_spec"] == "webhook:http://127.0.0.1:9/tesserae-test"


def test_tap_swipe_stroke_classifies_server_side(app: Flask) -> None:
    region = dict(WEBHOOK_REGION, tap=None, swipe={"left": "webhook:http://127.0.0.1:9/left"})
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[region])

    resp = _tap(client, token, {"x0": 500, "y0": 100, "x1": 100, "y1": 120, "digest": "art123"})
    body = resp.get_json()
    assert body["gesture"] == "swipe_left"
    assert body["outcome"] == "webhook_dispatched"


def test_tap_stale_and_no_target_outcomes(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[dict(WEBHOOK_REGION, w=10, h=10)])

    stale = _tap(client, token, {"x0": 5, "y0": 5, "digest": "not-current"})
    assert stale.status_code == 200
    assert stale.get_json()["outcome"] == "stale"

    miss = _tap(client, token, {"x0": 500, "y0": 500, "digest": "art123"})
    assert miss.status_code == 200
    assert miss.get_json()["outcome"] == "no_target"


def test_tap_no_frame_outcome(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)

    resp = _tap(client, token, {"x0": 5, "y0": 5, "digest": "whatever"})
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] == "no_frame"


def test_touch_event_shows_on_events_page(app: Flask) -> None:
    """A dispatched touch lands as a type=touch row and renders on the
    Events page under the touch filter, with its summary + resolved action."""
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])
    client.get(
        "/api/v1/device/hall_panel/frame"
        "?touch_x0=100&touch_y0=100&touch_digest=art123&touch_event_id=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    page = client.get("/events?type=touch").get_data(as_text=True)
    assert "dx-filter-chip--touch" in page  # the chip
    assert "event-touch" in page  # the friendly summary block
    assert "webhook:http://127.0.0.1:9/tesserae-test" in page  # resolved action


# -- ETag stability on touch wakes (E1003 linger) -------------------------


def test_frame_noop_touch_returns_304_when_frame_unchanged(app: Flask) -> None:
    """Regression: a touch whose action does not change the canvas must
    304 against the client's If-None-Match, not 200. On the E1003 every
    false-positive 200 costs a 1.3 MB download and a ~30 s panel repaint,
    so the ETag has to survive the dispatch."""
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        "/api/v1/device/hall_panel/frame"
        "?touch_x0=100&touch_y0=100&touch_digest=art123&touch_event_id=3",
        headers={"Authorization": f"Bearer {token}", "If-None-Match": '"art123"'},
    )
    assert resp.status_code == 304
    assert resp.headers["ETag"] == '"art123"'
    # The action still dispatched; only the frame transfer was skipped.
    rows = list(app.config["EVENT_LOG"].list(type="touch", source="touch", limit=10))
    assert rows and rows[0].status == "webhook_dispatched"


def test_frame_guard_exit_touch_also_304s(app: Flask) -> None:
    """Guard-chain exits (here: no_target) must not perturb the ETag
    either; the wake degrades to a plain 304 poll."""
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[])

    resp = client.get(
        "/api/v1/device/hall_panel/frame?touch_x0=5&touch_y0=5&touch_digest=art123",
        headers={"Authorization": f"Bearer {token}", "If-None-Match": '"art123"'},
    )
    assert resp.status_code == 304


# -- touch linger config (E1003) ------------------------------------------


def test_e1003_status_config_carries_touch_linger_default(app: Flask) -> None:
    """The E1003 hardware entry defaults touch_linger_s to 30 so a touch
    session skips the ~2.7 s deep-sleep wake for follow-up taps. The
    value must reach the firmware through the /status response config
    block, same path as touch_enabled."""
    client = app.test_client()
    _sign_in(client)
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": "hall_e1003",
                "kind": "seeed_reterminal_e1003",
                "panel_w": 1872,
                "panel_h": 1404,
                "fw_version": "1.3.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    token = resp.get_json()["device_token"]

    status = client.post(
        "/api/v1/device/hall_e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 80}),
    )
    assert status.status_code == 200
    config = status.get_json()["config"]
    assert config.get("touch_linger_s") == 30
    # Touch input itself stays opt-in (battery cost through deep sleep).
    assert config.get("touch_enabled") is False


# -- buzzer feedback config (#258) ----------------------------------------


def _register_kind(app: Flask, client, device_id: str, kind: str) -> str:
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": kind, "fw_version": "1.21.0"}),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return str(resp.get_json()["device_token"])


def test_beep_config_reaches_the_firmware_off_by_default(app: Flask) -> None:
    """The buzzer fields ride the same /status config block as the touch
    ones, and start silent: a panel that begins beeping after an update
    nobody asked for is worse than a silent one."""
    client = app.test_client()
    _sign_in(client)
    token = _register_kind(app, client, "hall_e1003", "seeed_reterminal_e1003")

    status = client.post(
        "/api/v1/device/hall_e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 80}),
    )
    config = status.get_json()["config"]
    assert config.get("beep_enabled") is False
    assert config.get("beep_volume") == 60
    # The notes, not the name: the panel holds no tone table, so retuning one
    # is a settings change rather than a firmware release.
    assert config.get("beep_pattern") == "2000:60"


def test_beep_config_reaches_a_button_only_reterminal(app: Flask) -> None:
    """The E1001 has the same buzzer and no touchscreen, so it gets the
    fields too and sounds them on a button press."""
    client = app.test_client()
    _sign_in(client)
    token = _register_kind(app, client, "hall_e1001", "seeed_reterminal_e1001")

    status = client.post(
        "/api/v1/device/hall_e1001/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 80}),
    )
    config = status.get_json()["config"]
    assert config.get("beep_enabled") is False
    assert "touch_linger_s" not in config, "no touchscreen on this board"


def test_beep_settings_survive_to_the_config_block(app: Flask) -> None:
    """What an operator picks in Settings -> Devices is what the panel is
    told, unchanged."""
    client = app.test_client()
    _sign_in(client)
    token = _register_kind(app, client, "hall_e1003", "seeed_reterminal_e1003")
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    section["hall_e1003"] = {
        **section.get("hall_e1003", {}),
        "sleep_interval_s": 900,
        "beep_enabled": True,
        "beep_tone": "chirp",
        "beep_volume": 30,
    }
    app.config["SETTINGS_STORE"].patch_section("devices", section)

    status = client.post(
        "/api/v1/device/hall_e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    config = status.get_json()["config"]
    assert config.get("beep_enabled") is True
    assert config.get("beep_volume") == 30
    assert config.get("beep_pattern") == "1800:30,2600:40"


def test_a_board_without_a_buzzer_gets_no_beep_fields(app: Flask) -> None:
    """Only the hardware entries that declare the buzzer extend the schema
    with it, so nothing else is told about a beep it cannot make."""
    client = app.test_client()
    _sign_in(client)
    token = _register_kind(app, client, "study_xiao", "xiao_epaper_75")

    status = client.post(
        "/api/v1/device/study_xiao/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    config = status.get_json()["config"]
    assert "beep_enabled" not in config
    assert "beep_pattern" not in config


def test_a_custom_tone_goes_to_the_panel_verbatim(app: Flask) -> None:
    """``custom`` hands over what the operator typed. The panel plays notes
    and knows no tone names, so this is the only place the choice lives."""
    client = app.test_client()
    _sign_in(client)
    token = _register_kind(app, client, "hall_e1003", "seeed_reterminal_e1003")
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    section["hall_e1003"] = {
        **section.get("hall_e1003", {}),
        "sleep_interval_s": 900,
        "beep_tone": "custom",
        "beep_pattern": "440:80,0:40,880:80",
    }
    app.config["SETTINGS_STORE"].patch_section("devices", section)

    status = client.post(
        "/api/v1/device/hall_e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    assert status.get_json()["config"]["beep_pattern"] == "440:80,0:40,880:80"


def test_custom_with_nothing_written_falls_back_to_a_beep(app: Flask) -> None:
    """Picking Custom and leaving the box empty is a half-finished edit, not
    a request for silence: the panel still acknowledges the tap."""
    client = app.test_client()
    _sign_in(client)
    token = _register_kind(app, client, "hall_e1003", "seeed_reterminal_e1003")
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    section["hall_e1003"] = {
        **section.get("hall_e1003", {}),
        "sleep_interval_s": 900,
        "beep_tone": "custom",
        "beep_pattern": "   ",
    }
    app.config["SETTINGS_STORE"].patch_section("devices", section)

    status = client.post(
        "/api/v1/device/hall_e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    assert status.get_json()["config"]["beep_pattern"] == "2000:60"
