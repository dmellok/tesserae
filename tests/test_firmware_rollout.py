"""Firmware update admin UI (#121): the guard-railed wrapper over app.ota.release.

Covers the shared verify service, the import / queue / withdraw routes (queue
rides the release store's per-device offer set), the auto-import of published
descriptors on queue, and capability awareness (non-OTA devices aren't offered
anything)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

VALID = REPO_ROOT / "tests" / "fixtures" / "ota" / "valid.json"
WRONG_KEY = REPO_ROOT / "tests" / "fixtures" / "ota" / "wrong_key.json"
KIND = "esp32_client"  # the valid fixture's device_kind
REL_FW = "1.4.0"  # the valid fixture's fw_version


# -- service ------------------------------------------------------------------


def test_verify_descriptor_accepts_valid_and_names_the_reason_on_failure() -> None:
    import json

    from app.ota.service import manifest_summary, verify_descriptor
    from app.ota.verify import OtaVerificationError

    manifest = verify_descriptor(json.loads(VALID.read_text()))
    assert manifest["device_kind"] == KIND
    summary = manifest_summary(manifest)
    assert summary["fw_version"] == REL_FW
    assert summary["sha256_short"] == manifest["sha256"][:12]
    assert summary["image_host"]  # host extracted from image_url

    with pytest.raises(OtaVerificationError) as ei:
        verify_descriptor(json.loads(WRONG_KEY.read_text()))
    assert ei.value.reason in ("bad_signature", "unknown_key")


# -- routes -------------------------------------------------------------------


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


def _authed_client(app: Flask):  # type: ignore[no-untyped-def]
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


def _mk_device(app: Flask, instance_id: str) -> None:
    from app import device_service

    device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id=instance_id,
        kind_id=KIND,
        orientation="landscape",
    )


def _set_status(
    app: Flask, device_id: str, *, fw: str, capable: bool = True, phase: str | None = None
) -> None:
    entry: dict = {"received_at": time.time(), "parsed": {"fw_version": fw}}
    if capable:
        entry["ota_schema"] = 1
    if phase is not None:
        entry["ota"] = {"phase": phase, "target_fw": REL_FW, "received_at": time.time()}
    app.config["DEVICE_STATUS"][device_id] = entry


def _import_valid(client) -> None:  # type: ignore[no-untyped-def]
    client.post(
        "/settings/firmware/import",
        data={"descriptor": (VALID.open("rb"), "valid.json")},
        content_type="multipart/form-data",
    )


def test_import_valid_sets_release(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
    client = _authed_client(app)
    _import_valid(client)
    rel = app.config["OTA_RELEASE"].get(KIND)
    assert rel is not None
    assert rel["fw_version"] == REL_FW
    assert rel["state"] == "canary"
    assert rel["canary_device_ids"] == []  # nothing offered until a device is queued


def test_import_bad_signature_rejected(app: Flask) -> None:
    client = _authed_client(app)
    client.post(
        "/settings/firmware/import",
        data={"descriptor": (WRONG_KEY.open("rb"), "wrong_key.json")},
        content_type="multipart/form-data",
    )
    assert app.config["OTA_RELEASE"].get(KIND) is None
    # The rejection is event-logged.
    log = app.config["EVENT_LOG"]
    assert any(e.status == "error" for e in log.list(type="ota"))


def test_queue_offers_release_to_device(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.3.0")
    client = _authed_client(app)
    _import_valid(client)
    client.post("/settings/firmware/queue", data={"device_id": "dev_a"})
    rel = app.config["OTA_RELEASE"].get(KIND)
    assert rel["state"] == "canary"
    assert rel["canary_device_ids"] == ["dev_a"]
    # The offer machinery now serves this device the descriptor.
    assert app.config["OTA_RELEASE"].descriptor_for(KIND, "dev_a") is not None
    assert app.config["OTA_RELEASE"].descriptor_for(KIND, "dev_other") is None


def test_queue_rejects_non_capable_device(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_usb")
    _set_status(app, "dev_usb", fw="1.3.0", capable=False)
    client = _authed_client(app)
    _import_valid(client)
    client.post("/settings/firmware/queue", data={"device_id": "dev_usb"})
    assert app.config["OTA_RELEASE"].get(KIND)["canary_device_ids"] == []


def test_queue_without_release_flashes_error(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.3.0")
    client = _authed_client(app)
    resp = client.post(
        "/settings/firmware/queue", data={"device_id": "dev_a"}, follow_redirects=True
    )
    assert "No release to offer yet" in resp.get_data(as_text=True)
    assert app.config["OTA_RELEASE"].get(KIND) is None


def test_withdraw_removes_device_from_offer_set(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.3.0")
    client = _authed_client(app)
    _import_valid(client)
    client.post("/settings/firmware/queue", data={"device_id": "dev_a"})
    client.post("/settings/firmware/withdraw", data={"device_id": "dev_a"})
    rel = app.config["OTA_RELEASE"].get(KIND)
    assert rel["canary_device_ids"] == []
    assert app.config["OTA_RELEASE"].descriptor_for(KIND, "dev_a") is None


def test_withdraw_from_promoted_release_materialises_remaining_set(app: Flask) -> None:
    """A CLI-promoted release offers to every device; withdrawing one from the
    UI converts that to an explicit per-device list minus the withdrawn one."""
    with app.app_context():
        _mk_device(app, "dev_a")
        _mk_device(app, "dev_b")
    _set_status(app, "dev_a", fw="1.3.0")
    _set_status(app, "dev_b", fw="1.3.0")
    client = _authed_client(app)
    _import_valid(client)
    app.config["OTA_RELEASE"].promote(KIND)
    client.post("/settings/firmware/withdraw", data={"device_id": "dev_a"})
    rel = app.config["OTA_RELEASE"].get(KIND)
    assert rel["state"] == "canary"
    assert rel["canary_device_ids"] == ["dev_b"]


def test_reimport_preserves_queued_devices(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.3.0")
    client = _authed_client(app)
    _import_valid(client)
    client.post("/settings/firmware/queue", data={"device_id": "dev_a"})
    _import_valid(client)  # e.g. re-importing the same or a newer descriptor
    assert app.config["OTA_RELEASE"].get(KIND)["canary_device_ids"] == ["dev_a"]


def test_promote_blocked_until_canary_confirmed(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
    # dev_a is capable and behind the release, but has NOT confirmed.
    _set_status(app, "dev_a", fw="1.3.0", phase="downloading")
    client = _authed_client(app)
    _import_valid(client)
    client.post("/settings/firmware/canary", data={"kind_id": KIND, "device_ids": ["dev_a"]})
    assert app.config["OTA_RELEASE"].get(KIND)["canary_device_ids"] == ["dev_a"]

    client.post("/settings/firmware/promote", data={"kind_id": KIND})
    assert app.config["OTA_RELEASE"].get(KIND)["state"] == "canary"  # still gated

    _set_status(app, "dev_a", fw=REL_FW, phase="confirmed")
    client.post("/settings/firmware/promote", data={"kind_id": KIND})
    assert app.config["OTA_RELEASE"].get(KIND)["state"] == "promoted"


def test_canary_rejects_non_capable_device(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_usb")
    _set_status(app, "dev_usb", fw="1.3.0", capable=False)
    client = _authed_client(app)
    _import_valid(client)
    client.post("/settings/firmware/canary", data={"kind_id": KIND, "device_ids": ["dev_usb"]})
    assert app.config["OTA_RELEASE"].get(KIND)["canary_device_ids"] == []


def test_pause_withdraws_offer(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.3.0")
    client = _authed_client(app)
    _import_valid(client)
    client.post("/settings/firmware/pause", data={"kind_id": KIND})
    assert app.config["OTA_RELEASE"].get(KIND)["state"] == "paused"


def test_offline_shows_disclosure_and_skips_check(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.firmware_check as fwc
    import app.settings.firmware_routes as fr

    monkeypatch.setattr(fr, "online_enabled", lambda _s: False)

    def _boom(*a, **k):  # the outbound check must not run when online is off
        raise AssertionError("api.tesserae.ink was contacted while offline")

    monkeypatch.setattr(fwc, "latest_for_kind", _boom)
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.4.0")
    client = _authed_client(app)
    html = client.get("/settings/firmware").get_data(as_text=True)
    assert "Automatic update checks are off" in html
    assert "api.tesserae.ink" in html  # the disclosure names what would be pinged


def test_online_shows_update_available(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.firmware_check as fwc
    import app.settings.firmware_routes as fr
    from app.firmware_check import FirmwareInfo

    monkeypatch.setattr(fr, "online_enabled", lambda _s: True)
    monkeypatch.setattr(
        fwc,
        "latest_for_kind",
        lambda kind, current="": FirmwareInfo(
            version="1.9.0",
            released_at="",
            url="https://example.test/r",
            notes_headline="",
            assets=(),
            descriptor_url="https://api.tesserae.ink/d.json",
        ),
    )
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.4.0")
    client = _authed_client(app)
    html = client.get("/settings/firmware").get_data(as_text=True)
    assert "Automatic update checks are off" not in html
    assert "v1.9.0" in html  # the Available column shows the published version
    assert "update available" in html
    assert "Queue update" in html


class _UrlResp:
    status = 200

    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self) -> _UrlResp:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def read(self, n: int = -1) -> bytes:
        return self._data


def test_queue_auto_imports_published_descriptor(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """With online mode on and no imported release, queueing fetches the
    published descriptor, verifies it, and offers it in one click."""
    import urllib.request

    import app.firmware_check as fwc
    import app.settings.firmware_routes as fr
    from app.firmware_check import FirmwareInfo

    monkeypatch.setattr(fr, "online_enabled", lambda _s: True)
    monkeypatch.setattr(
        fwc,
        "latest_for_kind",
        lambda kind, current="": FirmwareInfo(
            version=REL_FW,
            released_at="",
            url="https://example.test/r",
            notes_headline="",
            assets=(),
            descriptor_url="https://api.tesserae.ink/d.json",
        ),
    )
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=0: _UrlResp(VALID.read_bytes())
    )
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.3.0")
    client = _authed_client(app)
    client.post("/settings/firmware/queue", data={"device_id": "dev_a"})
    rel = app.config["OTA_RELEASE"].get(KIND)
    assert rel is not None
    assert rel["fw_version"] == REL_FW
    assert rel["canary_device_ids"] == ["dev_a"]


def test_queue_refuses_untrusted_descriptor_host(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.firmware_check as fwc
    import app.settings.firmware_routes as fr
    from app.firmware_check import FirmwareInfo

    monkeypatch.setattr(fr, "online_enabled", lambda _s: True)
    monkeypatch.setattr(
        fwc,
        "latest_for_kind",
        lambda kind, current="": FirmwareInfo(
            version=REL_FW,
            released_at="",
            url="https://example.test/r",
            notes_headline="",
            assets=(),
            descriptor_url="https://evil.example/d.json",
        ),
    )
    with app.app_context():
        _mk_device(app, "dev_a")
    _set_status(app, "dev_a", fw="1.3.0")
    client = _authed_client(app)
    client.post("/settings/firmware/queue", data={"device_id": "dev_a"})
    assert app.config["OTA_RELEASE"].get(KIND) is None


def test_page_renders_with_capability_and_chip(app: Flask) -> None:
    with app.app_context():
        _mk_device(app, "dev_a")
        _mk_device(app, "dev_usb")
    _set_status(app, "dev_a", fw="1.4.0", phase="confirmed")
    _set_status(app, "dev_usb", fw="1.2.0", capable=False)
    client = _authed_client(app)
    _import_valid(client)
    html = client.get("/settings/firmware").get_data(as_text=True)
    assert "USB update only" in html  # non-capable device flagged
    assert "confirmed" in html  # capable device's OTA chip
    assert "dev_a" in html and "dev_usb" in html


def test_check_now_clears_cache_and_button_renders(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.firmware_check as fwc
    import app.settings.firmware_routes as fr

    monkeypatch.setattr(fr, "online_enabled", lambda _s: True)
    monkeypatch.setattr(fwc, "latest_for_kind", lambda kind, current="": None)
    client = _authed_client(app)
    html = client.get("/settings/firmware").get_data(as_text=True)
    assert "Check now" in html

    fwc._cache["some_kind"] = (time.time(), None)
    resp = client.post("/settings/firmware/check")
    assert resp.status_code == 302
    assert fwc._cache == {}


def test_check_now_blocked_when_offline(app: Flask, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.firmware_check as fwc
    import app.settings.firmware_routes as fr

    monkeypatch.setattr(fr, "online_enabled", lambda _s: False)
    client = _authed_client(app)
    html = client.get("/settings/firmware").get_data(as_text=True)
    assert "Check now" not in html

    stale = (time.time(), None)
    fwc._cache["some_kind"] = stale
    client.post("/settings/firmware/check")
    assert fwc._cache.get("some_kind") == stale
    del fwc._cache["some_kind"]
