"""Panel dims must survive a save, and a portrait panel must be enterable
(issue #200).

Firmware reports panel dims but never an orientation, so a client painting a
tall panel used to land with portrait dims and the device kind's landscape
orientation. Nothing read that contradiction until the first save of the panel
form, which resolved it by rewriting the dims to match the orientation: a
1200x1920 panel silently became 1920x1200, and re-typing the dims swapped them
straight back. Two halves to the fix, both covered here: registration derives
the aspect from the reported dims, and the save prefers whichever of
dims/rotation the user actually changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


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


def _panel(app: Flask, instance_id: str) -> dict:
    device = app.config["DEVICE_REGISTRY"].get(instance_id)
    assert device is not None, f"no device {instance_id!r}"
    return dict(device.panel or {})


def _add_portrait_device(client, instance_id: str = "pi5_tall") -> None:
    """A device whose panel is taller than it is wide, stored inconsistently the
    way a firmware discovery used to leave it: portrait dims, landscape
    orientation."""
    client.post(
        "/settings/devices/add",
        data={
            "kind": "circuitpython_generic",
            "id": instance_id,
            "name": "Pi5 tall",
            "panel_w": "1200",
            "panel_h": "1920",
            "panel_orientation": "landscape",
        },
    )


def test_saving_an_unrelated_field_leaves_the_dims_alone(app: Flask, tmp_path: Path) -> None:
    """Bernhard's report: edit the sleep interval, and width/height swap."""
    client = app.test_client()
    _sign_in(client)
    _add_portrait_device(client)
    # The add form normalises to the submitted orientation, so force the
    # inconsistent state the bug depends on: portrait dims, landscape
    # orientation. On disk (what the save rewrites) and in the loaded manifest
    # (what the save compares against), as a discovery used to leave it.
    path = tmp_path / "devices" / "pi5_tall.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["panel"]["w"], raw["panel"]["h"] = 1200, 1920
    raw["panel"]["orientation"] = "landscape"
    path.write_text(json.dumps(raw), encoding="utf-8")
    live = app.config["DEVICE_REGISTRY"].get("pi5_tall").manifest["panel"]
    live["w"], live["h"], live["orientation"] = 1200, 1920, "landscape"
    assert (_panel(app, "pi5_tall")["w"], _panel(app, "pi5_tall")["h"]) == (1200, 1920)

    # Now save the card with the dims unchanged, as editing sleep interval does.
    client.post(
        "/settings/devices/pi5_tall/save",
        data={
            "panel_w": "1200",
            "panel_h": "1920",
            "panel_orientation": "landscape",
            "sleep_interval_s": "600",
        },
    )
    panel = _panel(app, "pi5_tall")
    assert (panel["w"], panel["h"]) == (1200, 1920)
    # The contradiction is repaired on the orientation side, not the dims.
    assert panel["orientation"] == "portrait"


def test_typing_portrait_dims_sticks(app: Flask) -> None:
    """Entering tall dims with the rotation dropdown untouched keeps them."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={
            "kind": "circuitpython_generic",
            "id": "wide",
            "name": "Wide",
            "panel_w": "1920",
            "panel_h": "1200",
            "panel_orientation": "landscape",
        },
    )
    assert (_panel(app, "wide")["w"], _panel(app, "wide")["h"]) == (1920, 1200)

    client.post(
        "/settings/devices/wide/save",
        data={"panel_w": "1200", "panel_h": "1920", "panel_orientation": "landscape"},
    )
    panel = _panel(app, "wide")
    assert (panel["w"], panel["h"]) == (1200, 1920)
    assert panel["orientation"] == "portrait"


def test_moving_the_rotation_dropdown_still_wins(app: Flask) -> None:
    """When the rotation is what changed, the dims follow it. That's the
    existing contract (the form's JS swaps the visible inputs live, and a
    hand-crafted POST should still land consistent)."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={
            "kind": "circuitpython_generic",
            "id": "rotate_me",
            "name": "Rotate",
            "panel_w": "1920",
            "panel_h": "1200",
            "panel_orientation": "landscape",
        },
    )
    # Dropdown moves to portrait while the dims still read landscape (JS didn't
    # fire): the orientation is the authority, so the dims get swapped.
    client.post(
        "/settings/devices/rotate_me/save",
        data={"panel_w": "1920", "panel_h": "1200", "panel_orientation": "portrait"},
    )
    panel = _panel(app, "rotate_me")
    assert (panel["w"], panel["h"]) == (1200, 1920)
    assert panel["orientation"] == "portrait"


def test_flipped_rotation_is_preserved(app: Flask) -> None:
    """The 180 degree half of the orientation is the user's mount choice; only
    the landscape/portrait half follows the dims."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={
            "kind": "circuitpython_generic",
            "id": "flipped",
            "name": "Flipped",
            "panel_w": "1920",
            "panel_h": "1200",
            "panel_orientation": "landscape_flipped",
        },
    )
    client.post(
        "/settings/devices/flipped/save",
        data={
            "panel_w": "1200",
            "panel_h": "1920",
            "panel_orientation": "landscape_flipped",
        },
    )
    panel = _panel(app, "flipped")
    assert (panel["w"], panel["h"]) == (1200, 1920)
    assert panel["orientation"] == "portrait_flipped"
