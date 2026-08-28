"""Per-device button map (Settings -> Devices -> Buttons): the JSON
textarea must be associated with the device card's combined form, and
a submitted map must persist to the settings store."""

from __future__ import annotations

import re
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


def _add_device(client, instance_id: str = "kitchen") -> None:
    client.post(
        "/settings/devices/add",
        data={"kind": "circuitpython_generic", "id": instance_id, "name": "Kitchen"},
    )


def test_button_map_textarea_is_associated_with_the_combined_form(app: Flask) -> None:
    """The device card's combined <form> is an empty element that fields
    join via the ``form`` attribute. A textarea without it is orphaned:
    edits never mark the form dirty (so no Save bar appears) and the
    value is dropped on submit."""
    client = app.test_client()
    _sign_in(client)
    _add_device(client)

    body = client.get("/settings/devices").get_data(as_text=True)
    match = re.search(r"<textarea[^>]*name=\"button_map_json\"[^>]*>", body)
    assert match is not None, "button map textarea missing from the devices page"
    textarea = match.group(0)
    form_attr = re.search(r"form=\"([^\"]+)\"", textarea)
    assert form_attr is not None, "button map textarea is not associated with any form"
    assert f'id="{form_attr.group(1)}"' in body, "textarea points at a form id not on the page"


def test_saving_a_button_map_persists_and_renders_back(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_device(client)

    resp = client.post(
        "/settings/devices/kitchen/save",
        data={"button_map_json": '{"left": "webhook:http://example.test/hook"}'},
    )
    assert resp.status_code == 302

    stored = app.config["SETTINGS_STORE"].get_section("devices").get("kitchen", {})
    assert stored.get("button_map") == {"left": "webhook:http://example.test/hook"}

    body = client.get("/settings/devices").get_data(as_text=True)
    assert "webhook:http://example.test/hook" in body
