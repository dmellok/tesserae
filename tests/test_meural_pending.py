"""Account sign-in before creating a Meural device."""

import json
import re
import time

import pytest

from tests.test_meural_login import app as app
from tests.test_meural_login import payload
from tests.test_meural_login import provider as provider


def pending_signin(client):
    response = client.get("/settings/meural/login")
    assert response.status_code == 200
    match = re.search(
        r'<script[^>]*id="meural-auth-config"[^>]*>(.*?)</script>', response.text, re.S
    )
    data = payload(json.loads(match.group(1)))
    response = client.post("/settings/meural/login", json=data)
    assert response.status_code == 200
    return response


def add(client, device_id="new_frame", frame="101", headers=None):
    return client.post(
        "/settings/devices/add",
        data={"kind": "meural", "id": device_id, "name": "Example frame", "cloud_device_id": frame},
        headers=headers,
    )


def refused(response):
    """The Add device form is a plain post: a refusal goes back to the
    Devices page with the reason, never to a page of JSON."""
    return response.status_code == 302 and response.location == "/settings/devices#add-device"


def test_pending_signin_adds_device_account_and_frame_together(app, provider):
    client = app.test_client()
    response = pending_signin(client)
    state = response.json["account"]
    assert state["state"] == "signed_in"
    assert state["chosen_frame"] is None
    assert not state["saved"]
    assert app.config["DEVICE_REGISTRY"].get("new_frame") is None
    pending = app.config["MEURAL_PENDING_ADDS"]
    assert "private-access" not in repr(pending)
    assert "private-refresh" not in repr(pending)
    before = client.get("/settings/meural/account").json["account"]
    assert before["frames"][0]["id"] == "101"
    selected = client.post("/settings/meural/frame", json={"cloud_device_id": "101"})
    assert selected.json["account"]["chosen_frame"] == {"id": "101", "name": "Example frame"}
    assert selected.json["account"]["saved"] is False
    response = add(client)
    assert response.status_code == 302
    assert response.location == "/settings/devices/new_frame"
    cfg = app.config["SETTINGS_STORE"].get_section("devices")["new_frame"]
    assert cfg["cloud_device_id"] == "101"
    assert cfg["meural_refresh_token_secret"] == "private-refresh"
    assert not app.config["MEURAL_PENDING_ADDS"]
    state = client.get("/settings/devices/new_frame/meural/account").json["account"]
    assert state["saved"]
    assert state["chosen_frame"]["id"] == "101"
    assert "private-refresh" not in json.dumps(state)
    assert refused(add(client, "second_frame"))
    assert app.config["DEVICE_REGISTRY"].get("second_frame") is None


@pytest.mark.parametrize("end", ["cancel", "expire"])
def test_abandoning_pending_account_leaves_no_device_or_settings(app, provider, end):
    client = app.test_client()
    pending_signin(client)
    if end == "cancel":
        response = client.post("/settings/meural/logout")
        assert response.json["account"]["state"] == "signed_out"
    else:
        for entry in app.config["MEURAL_PENDING_ADDS"].values():
            entry["expires_at"] = time.monotonic() - 1
    assert refused(add(client))
    assert app.config["DEVICE_REGISTRY"].get("new_frame") is None
    assert "new_frame" not in app.config["SETTINGS_STORE"].get_section("devices")
    assert not app.config["MEURAL_PENDING_ADDS"]


def test_pending_account_is_bound_to_the_browser_session(app, provider):
    first, second = app.test_client(), app.test_client()
    pending_signin(first)
    assert second.get("/settings/meural/account").json["account"]["state"] == "signed_out"
    assert refused(add(second))
    assert add(first).status_code == 302


def test_invalid_add_keeps_pending_account_without_creating_orphan(app, provider):
    client = app.test_client()
    pending_signin(client)
    assert refused(add(client, "../invalid"))
    assert app.config["DEVICE_REGISTRY"].get("../invalid") is None
    assert client.get("/settings/meural/account").json["account"]["state"] == "signed_in"
    assert add(client).status_code == 302


def test_pending_frame_rejects_outside_inventory_and_duplicate(app, provider):
    client = app.test_client()
    pending_signin(client)
    assert refused(add(client, frame="999"))
    assert app.config["DEVICE_REGISTRY"].get("new_frame") is None
    app.config["SETTINGS_STORE"].update_item("devices", "test_frame", {"cloud_device_id": "101"})
    response = add(client, headers={"Accept": "application/json"})
    assert response.status_code == 400
    assert "another Tesserae display" in response.json["error"]
    assert app.config["DEVICE_REGISTRY"].get("new_frame") is None


def test_opening_signin_preserves_pending_account_and_state_does_not_expose_its_key(app, provider):
    client = app.test_client()
    pending_signin(client)
    key = next(iter(app.config["MEURAL_PENDING_ADDS"]))
    html = client.get("/settings/devices").text
    assert key not in html
    response = client.get("/settings/meural/login")
    assert key not in response.text
    assert key in app.config["MEURAL_PENDING_ADDS"]
    assert client.get("/settings/meural/account").json["account"]["state"] == "signed_in"


def test_cloud_add_cannot_bypass_pending_signin_with_whitespace_in_kind(app):
    response = app.test_client().post(
        "/settings/devices/add", data={"kind": " meural ", "id": "unlinked_frame"}
    )
    assert refused(response)
    assert app.config["DEVICE_REGISTRY"].get("unlinked_frame") is None


def test_opening_login_keeps_the_pending_account_and_selection(app, provider):
    client = app.test_client()
    pending_signin(client)
    client.post("/settings/meural/frame", json={"cloud_device_id": "101"})
    client.get("/settings/meural/login")
    assert client.get("/settings/meural/account").json["account"]["chosen_frame"]["id"] == "101"
    response = client.post(
        "/settings/devices/add",
        data={
            "kind": "meural",
            "id": "new_frame",
            "name": "Example frame",
        },
    )
    assert response.status_code == 302


def test_adding_pending_account_does_not_wait_for_poller_or_call_cloud(app, provider, monkeypatch):
    import threading

    client = app.test_client()
    pending_signin(client)
    entered, release = threading.Event(), threading.Event()
    app.config["MEURAL_IO_LOCK"] = threading.Lock()

    def hold():
        with app.config["MEURAL_IO_LOCK"]:
            entered.set()
            assert release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(1)

    def no_network(*args, **kwargs):
        raise AssertionError("Adding a pending account must use saved inventory only")

    monkeypatch.setattr("app.meural_auth.request_json", no_network)
    try:
        assert add(client).status_code == 302
        assert not release.is_set()
    finally:
        release.set()
        thread.join(3)


def test_adding_before_signing_in_returns_to_the_devices_page_with_the_reason(app):
    """The Add device form posts as a form. Without a pending sign-in it used
    to land on a page holding only a JSON error."""
    client = app.test_client()
    response = add(client, frame="")
    assert refused(response)
    assert app.config["DEVICE_REGISTRY"].get("new_frame") is None
    page = client.get("/settings/devices")
    assert "Device not added. Sign in to Netgear and choose a frame first" in page.text
    # A script that asks for JSON still gets it.
    scripted = add(client, frame="", headers={"Accept": "application/json"})
    assert scripted.status_code == 400
    assert "Sign in to Netgear" in scripted.json["error"]
