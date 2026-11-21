"""Per-device button map (Settings -> Devices -> Buttons): the JSON
textarea must be associated with the device card's combined form, and
a submitted map must persist to the settings store."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page


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

    body = client.get("/settings/devices/kitchen").get_data(as_text=True)
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

    body = client.get("/settings/devices/kitchen").get_data(as_text=True)
    assert "webhook:http://example.test/hook" in body


def test_page_action_takes_a_dashboard_name_or_id(app: Flask) -> None:
    """``page:`` takes the dashboard name people see, or its id (#379).
    Anything else is refused at save instead of doing nothing on the press."""
    store = app.config["PAGE_STORE"]
    store.save(Page(id="3f9a1c2b7d4e", name="Morning Briefing"))
    client = app.test_client()
    _sign_in(client)
    _add_device(client)

    def save(spec: str) -> dict:
        client.post(
            "/settings/devices/kitchen/save", data={"button_map_json": f'{{"a": "{spec}"}}'}
        )
        return app.config["SETTINGS_STORE"].get_section("devices").get("kitchen", {})

    assert save("page:morning briefing").get("button_map") == {"a": "page:morning briefing"}
    assert save("page:3f9a1c2b7d4e").get("button_map") == {"a": "page:3f9a1c2b7d4e"}

    stored = save("page:Evening")
    assert stored.get("button_map") == {"a": "page:3f9a1c2b7d4e"}
    body = client.get("/settings/devices/kitchen").get_data(as_text=True).replace("&#39;", "'")
    assert "no dashboard has the id or name 'Evening'" in body

    store.save(Page(id="aaaaaaaaaaaa", name="morning briefing"))
    assert save("page:Morning Briefing").get("button_map") == {"a": "page:3f9a1c2b7d4e"}


def _textarea(body: str) -> str:
    match = re.search(r"<textarea[^>]*name=\"button_map_json\"[^>]*>(.*?)</textarea>", body, re.S)
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(
    "typed",
    [
        '{"left": "rotate_prev" "right": "rotate_next"}',  # missing comma
        '{"left": "no_such_action"}',  # parses, fails validation
    ],
)
def test_a_rejected_button_map_comes_back_as_typed(app: Flask, typed: str) -> None:
    """#588: a save that fails validation keeps what was typed in the
    textarea, so a long map can be fixed instead of retyped. The stored
    map is untouched, and a later visit shows it again."""
    client = app.test_client()
    _sign_in(client)
    _add_device(client)
    client.post(
        "/settings/devices/kitchen/save",
        data={"button_map_json": '{"left": "webhook:http://example.test/kept"}'},
    )

    resp = client.post("/settings/devices/kitchen/save", data={"button_map_json": typed})
    assert resp.status_code == 302
    stored = app.config["SETTINGS_STORE"].get_section("devices").get("kitchen", {})
    assert stored.get("button_map") == {"left": "webhook:http://example.test/kept"}

    body = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "button map:" in body  # the error is still reported
    shown = _textarea(body)
    assert shown.replace("&#34;", '"').replace("&quot;", '"') == typed

    again = client.get("/settings/devices/kitchen").get_data(as_text=True)
    assert "example.test/kept" in _textarea(again)


# What the device page posts for the sections an operator usually leaves
# alone. Each of these reports "saved" on every submit whether or not it
# changed, which is what made a rejected map's message confusing (#588).
_UNTOUCHED_SECTIONS = {
    "device_name": "Kitchen",
    "quiet_hours__present": "1",
    "battery_offset_mv": "0",
    "battery_offset_pct": "0",
    "locale": "",
}
_BAD_MAP = '{"left": "rotate_prev" "right": "rotate_next"}'


def test_a_rejected_map_alone_claims_no_save(app: Flask) -> None:
    """#588 follow-up: when the button map is the only thing changed and
    it fails, the page shows the error and no "saved" message."""
    client = app.test_client()
    _sign_in(client)
    _add_device(client)
    settled = client.post("/settings/devices/kitchen/save", data=dict(_UNTOUCHED_SECTIONS))
    client.get(settled.headers["Location"])  # read that save's own message

    resp = client.post(
        "/settings/devices/kitchen/save",
        data={**_UNTOUCHED_SECTIONS, "button_map_json": _BAD_MAP},
    )
    body = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "button map must be valid JSON" in body
    assert "quiet hours saved" not in body
    assert "except the button map" not in body


def test_a_rejected_map_beside_a_real_change_says_what_was_skipped(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_device(client)
    settled = client.post("/settings/devices/kitchen/save", data=dict(_UNTOUCHED_SECTIONS))
    client.get(settled.headers["Location"])  # read that save's own message

    resp = client.post(
        "/settings/devices/kitchen/save",
        data={**_UNTOUCHED_SECTIONS, "device_name": "Galley", "button_map_json": _BAD_MAP},
    )
    body = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "button map must be valid JSON" in body
    assert "saved, except the button map, which has an error." in body
    assert "quiet hours saved" not in body


def test_a_good_save_still_lists_what_it_saved(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _add_device(client)
    resp = client.post(
        "/settings/devices/kitchen/save",
        data={**_UNTOUCHED_SECTIONS, "button_map_json": '{"left": "rotate_prev"}'},
    )
    body = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "button map saved" in body
