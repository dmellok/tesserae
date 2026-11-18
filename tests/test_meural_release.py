"""Releasing and deleting a Meural: what is left behind afterwards."""

from __future__ import annotations

import json

import pytest

from app import device_service
from app.meural_auth import HttpError
from app.meural_pending import PLAYBACK_NOTICE
from tests.test_meural_login import app as app
from tests.test_meural_login import linked_frame
from tests.test_meural_login import no_network as no_network
from tests.test_meural_login import provider as provider


def _settings(app):
    return app.config["SETTINGS_STORE"].get_section("devices").get("test_frame") or {}


def _paused_rotation(delivery):
    """The journal of a frame whose slideshow Tesserae switched off."""
    state = json.loads(delivery.path.read_text())
    state.update(
        original={"settings": {"galleryRotation": True, "imageDuration": 300}},
        paused=True,
        image_duration_paused=True,
    )
    delivery.path.write_text(json.dumps(state))


@pytest.fixture
def cloud(monkeypatch):
    """The Netgear boundary for a release: uploads delete, and the playback
    restore succeeds or is refused as an expired session would be."""
    calls = {"restore": [], "fail": False}

    def restore(self, target, original):
        calls["restore"].append((target, original))
        if calls["fail"]:
            raise HttpError(401, {})

    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.restore_playback", restore)
    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.delete_item", lambda *a: None)
    return calls


def test_deleted_cloud_frame_can_be_chosen_for_another_display(app, provider, cloud):
    """The settings entry outlives a deleted device and still named its
    frame, so the frame stayed "selected by another Tesserae display"."""
    from app.meural_cloud import ensure_frame_available

    client = app.test_client()
    linked_frame(app, client)
    store = app.config["SETTINGS_STORE"]
    with pytest.raises(ValueError):
        ensure_frame_available(store, "other_frame", "101")
    assert client.post("/settings/devices/test_frame/delete").status_code == 302
    assert app.config["DEVICE_REGISTRY"].devices.get("test_frame") is None
    ensure_frame_available(store, "other_frame", "101")
    left = _settings(app)
    assert "cloud_device_id" not in left
    assert "meural_management_paused" not in left
    assert "meural_refresh_token_secret" not in left


def test_failed_delete_does_not_leave_the_frame_paused(app, provider, cloud, monkeypatch):
    """Releasing the frame pauses delivery before the delete. When the
    delete then fails the device is still there, and a LAN-only frame has
    no control that resumes it."""
    client = app.test_client()
    linked_frame(app, client)
    assert not _settings(app).get("meural_management_paused")
    monkeypatch.setattr(
        device_service,
        "delete_instance",
        lambda **kwargs: device_service.InstanceResult(None, "The disk is read-only."),
    )
    response = client.post("/settings/devices/test_frame/delete", follow_redirects=True)
    assert "The disk is read-only." in response.text
    assert app.config["DEVICE_REGISTRY"].devices.get("test_frame") is not None
    assert _settings(app)["meural_management_paused"] is False
    assert _settings(app)["cloud_device_id"] == "101"


def test_stopping_delivery_restores_playback_without_a_notice(app, provider, cloud):
    client = app.test_client()
    _paused_rotation(linked_frame(app, client))
    response = client.post(
        "/settings/devices/test_frame/meural/manage",
        data={"action": "release"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert len(cloud["restore"]) == 1
    assert response.json["account"]["notice"] == ""
    assert _settings(app)["meural_playback_unrestored"] is False


def test_unrestored_playback_tells_the_user_to_use_the_meural_app(app, provider, cloud):
    """With an expired sign-in the restore can never succeed. It was only
    logged, so the frame's slideshow stayed off with nothing saying why."""
    cloud["fail"] = True
    client = app.test_client()
    _paused_rotation(linked_frame(app, client))
    response = client.post(
        "/settings/devices/test_frame/meural/manage",
        data={"action": "release"},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, "a failed restore must not block stopping delivery"
    assert response.json["account"]["notice"] == PLAYBACK_NOTICE
    assert response.json["account"]["paused"] is True
    page = client.get("/settings/devices/test_frame").text
    assert PLAYBACK_NOTICE.replace("'", "&#39;") in page
    # Delivering again clears it: Tesserae owns the playback settings once more.
    cloud["fail"] = False
    resumed = client.post(
        "/settings/devices/test_frame/meural/manage",
        data={"action": "manage"},
        headers={"Accept": "application/json"},
    )
    assert resumed.json["account"]["notice"] == ""


def test_delete_with_unrestored_playback_still_deletes_and_says_so(app, provider, cloud):
    cloud["fail"] = True
    client = app.test_client()
    _paused_rotation(linked_frame(app, client))
    response = client.post("/settings/devices/test_frame/delete", follow_redirects=True)
    assert response.status_code == 200
    assert app.config["DEVICE_REGISTRY"].devices.get("test_frame") is None
    assert PLAYBACK_NOTICE.replace("'", "&#39;") in response.text


def test_account_text_says_what_delivery_does_to_playback(app, provider):
    """The block said gallery rotation is not changed while delivery turns
    it off."""
    page = app.test_client().get("/settings/devices/test_frame").text
    assert "does not change gallery rotation" not in page
    assert "turns off the frame's gallery rotation" in page
