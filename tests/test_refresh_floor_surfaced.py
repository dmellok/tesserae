"""A declared ``refresh_floor_s`` is shown, not enforced (#250).

41 hardware profiles list a repaint floor for their glass, and the server
read the field nowhere, while the schema claimed it was "surfaced in the
Settings UI" and the hardware guide called it a poll cadence "enforced on
the always-on path", an enforcement removed in v0.332.0. Three different
stories, none of them true.

The server's answer is to state the number as the profile's declaration
and gate nothing. It names no guarantor, because there is none to name:
firmware that holds repaints at all does so against its own compile-time
constant, only in the always-on loop, and several clients hold none. The
values are authored by protocol anyway (the Sticky lists 60 while its own
notes put a full paint at 1.2 s).

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

FLOORED_KIND = "seeed_reterminal_e1003"  # lists 60
# A TRMNL-client kind that lists a floor. Its cadence field is
# `refresh_rate_s`, not `sleep_interval_s`, so it needs its own check.
FLOORED_TRMNL_KIND = "trmnl_x"  # lists 60
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
        assert "60s repaint floor" in help_text, name
        # A declaration, sourced to the profile. Naming the firmware as the
        # guarantor was wrong: E1003 firmware never reads this field.
        assert "profile lists" in help_text, name
        assert "firmware" not in help_text.lower(), name


def test_a_trmnl_client_kind_gets_the_note_on_its_own_cadence_field(app: Flask) -> None:
    """The two TRMNL-client profiles that list a floor set their cadence with
    `refresh_rate_s`. Without that field in the cadence tuple, the note never
    reached them."""
    kind = _kind(app, FLOORED_TRMNL_KIND)
    fields = note_refresh_floor(config_fields_from_schema(kind.config_schema), kind)

    help_text = _field(fields, "refresh_rate_s").get("help") or ""
    assert "60s repaint floor" in help_text


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
            # A kind that lists a floor. `esp32_client` lists none, so a
            # reintroduced clamp would have passed this test untouched.
            {
                "device_id": "e1003",
                "kind": FLOORED_KIND,
                "panel_w": 1872,
                "panel_h": 1404,
            }
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


def test_the_device_reports_the_floor_its_profile_lists(app: Flask) -> None:
    """One property, read by both the settings note and the Companion API."""
    assert _kind(app, FLOORED_KIND).refresh_floor_s == 60
    assert _kind(app, FLOORED_TRMNL_KIND).refresh_floor_s == 60
    assert _kind(app, FLOORLESS_KIND).refresh_floor_s is None


def test_the_schema_and_the_guide_agree_with_the_code(app: Flask) -> None:
    """Both described the field as a poll-cadence bound, which it is not,
    and the schema claimed a Settings surface that did not exist."""
    schema = json.loads((REPO_ROOT / "schema" / "hardware.schema.json").read_text(encoding="utf-8"))
    described = schema["properties"]["refresh_floor_s"]["description"]
    assert "lower bound on the device's poll cadence" not in described.lower()
    assert "declaration" in described.lower()
    assert "firmware" not in described.lower()

    guide = (REPO_ROOT / "docs" / "dev" / "adding-hardware.md").read_text(encoding="utf-8")
    row = next(line for line in guide.splitlines() if line.startswith("| `refresh_floor_s`"))
    assert "declaration" in row
    assert "firmware" not in row.lower()
    assert "Enforced on the always-on path" not in row


@pytest.mark.parametrize("floor", [60, None], ids=["declared", "none"])
def test_the_companion_panel_schema_accepts_the_floor_in_both_forms(floor: int | None) -> None:
    """Panel is additionalProperties: false, so a device view carrying the
    field is only valid once the schema erratum adds it. Both forms the
    server serves: a number where the profile lists one, null where it
    lists none.

    Checked against a local Panel rather than by editing the shared
    fixtures under ``tests/companion/contract/``, which are part of the
    vendored copy and stay verbatim."""
    import jsonschema

    from tests.companion._schema import schema_for

    panel = {
        "width": 1872,
        "height": 1404,
        "gamut": "gray16",
        "orientation": "landscape",
        "refresh_floor_s": floor,
    }
    jsonschema.validate(panel, schema_for("Panel"))


def test_the_companion_panel_schema_refuses_a_zero_floor() -> None:
    """``minimum: 1``: a floor of zero is not a declaration, and the property
    returns None rather than 0 for exactly that reason."""
    import jsonschema

    from tests.companion._schema import schema_for

    panel = {
        "width": 1872,
        "height": 1404,
        "gamut": "gray16",
        "orientation": "landscape",
        "refresh_floor_s": 0,
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(panel, schema_for("Panel"))
