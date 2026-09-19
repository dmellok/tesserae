"""A declared ``refresh_floor_s`` is shown, not enforced (#250).

41 hardware profiles say how fast their glass can be repainted, and the
server read the field nowhere — while the schema claimed it was
"surfaced in the Settings UI" and the hardware guide called it a poll
cadence "enforced on the always-on path", an enforcement removed in
v0.332.0. Three different stories, none of them true.

The server's answer is to state the number and gate nothing: the
firmware holds its own repaints, timed from the last paint rather than
from a handover the server cannot see land, and the profile values are
authored by protocol anyway (the Sticky declares 60 while its own notes
put a full paint at 1.2 s).

What these pin: the note reaches the cadence fields, it changes no
stored value, the Companion API reports the number, and a kind that
declares no floor says nothing at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.settings._shared import config_fields_from_schema, note_refresh_floor

FLOORED_KIND = "seeed_reterminal_e1003"  # declares 60
FLOORLESS_KIND = "pico_bin_client"


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


def _kind(app: Flask, kind_id: str):
    return next(k for k in app.config["DEVICE_REGISTRY"].all() if k.id == kind_id)


def _field(fields: list[dict], name: str) -> dict:
    return next(f for f in fields if f["name"] == name)


def test_the_floor_is_mentioned_beside_the_cadence_fields(app: Flask) -> None:
    kind = _kind(app, FLOORED_KIND)
    fields = note_refresh_floor(config_fields_from_schema(kind.config_schema), kind)

    for name in ("sleep_interval_s", "awake_poll_s"):
        help_text = _field(fields, name).get("help") or ""
        assert "60s refresh floor" in help_text, name
        # Whose guarantee it is, since it is not the server's.
        assert "firmware" in help_text, name


def test_the_note_keeps_the_help_the_field_already_had(app: Flask) -> None:
    kind = _kind(app, FLOORED_KIND)
    before = _field(config_fields_from_schema(kind.config_schema), "sleep_interval_s")
    after = _field(
        note_refresh_floor(config_fields_from_schema(kind.config_schema), kind),
        "sleep_interval_s",
    )
    assert before.get("help")  # the premise: it had help to keep
    assert str(before["help"]) in str(after["help"])


def test_a_kind_with_no_floor_says_nothing(app: Flask) -> None:
    kind = _kind(app, FLOORLESS_KIND)
    fields = note_refresh_floor(config_fields_from_schema(kind.config_schema), kind)
    assert fields == config_fields_from_schema(kind.config_schema)


def test_the_note_is_presentation_only(app: Flask) -> None:
    """The stored value is whatever the operator typed. A floor that
    quietly clamped a setting would be the v0.332.0 bug again."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "e1003", "kind": "esp32_client", "panel_w": 1872, "panel_h": 1404}
        ),
    )
    token = resp.get_json()["device_token"]

    app.config["SETTINGS_STORE"].update_section("devices", {"e1003": {"sleep_interval_s": 5}})
    status = client.post(
        "/api/v1/device/e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    assert status.get_json()["config"]["sleep_interval_s"] == 5


def test_the_companion_api_reports_the_floor(app: Flask) -> None:
    from app.companion_api import _panel_refresh_floor_s

    assert _panel_refresh_floor_s(_kind(app, FLOORED_KIND)) == 60
    assert _panel_refresh_floor_s(_kind(app, FLOORLESS_KIND)) is None


def test_the_schema_and_the_guide_agree_with_the_code(app: Flask) -> None:
    """Both described the field as a poll-cadence bound, which it is not,
    and the schema claimed a Settings surface that did not exist."""
    schema = json.loads((REPO_ROOT / "schema" / "hardware.schema.json").read_text())
    described = schema["properties"]["refresh_floor_s"]["description"]
    assert "lower bound on the device's poll cadence" not in described.lower()
    assert "advisory" in described.lower()
    # It used to promise a Settings surface that did not exist; now one does.
    assert "settings" in described.lower()

    guide = (REPO_ROOT / "docs" / "dev" / "adding-hardware.md").read_text()
    row = next(line for line in guide.splitlines() if line.startswith("| `refresh_floor_s`"))
    assert "Advisory" in row
    assert "Enforced on the always-on path" not in row
