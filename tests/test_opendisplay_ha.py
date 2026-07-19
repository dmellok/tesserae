"""OpenDisplay-via-HA publisher (issue: OpenDisplay support).

Renders reach OpenDisplay tags by writing the frame into HA's media folder
and calling ``opendisplay.upload_image``. These tests mock the ha_core
service call and use a temp media root, so no HA or Bluetooth is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import device_service
from app.main import REPO_ROOT, create_app
from app.opendisplay_ha import OpenDisplayHaPublisher


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


def _make_tag(app: Flask, device_id: str = "kitchen_tag", ha_device_id: str = "ha-abc") -> None:
    with app.app_context():
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id=device_id,
            kind_id="opendisplay_ha",
            name="Kitchen tag",
        )
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    entry = dict(section.get(device_id) or {})
    entry["ha_device_id"] = ha_device_id
    app.config["SETTINGS_STORE"].patch_section("devices", {device_id: entry})


def _plant_render(app: Flask, device_id: str, digest: str = "deadbeef") -> None:
    renders = app.config["RENDERS_DIR"]
    (renders / f"{digest}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = {"composition_digest": digest}


def _publisher(app: Flask, media_root: Path, calls: list) -> OpenDisplayHaPublisher:
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module

    def fake_call_service(domain, service, *, data=None, timeout=10):
        calls.append((domain, service, data))
        return []

    core.call_service = fake_call_service  # type: ignore[attr-defined]
    return OpenDisplayHaPublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=app.config["PUSH_MANAGER"].latest_render_for,
        media_root=media_root,
    )


def test_writes_media_and_calls_upload(app: Flask, tmp_path: Path) -> None:
    _make_tag(app)
    _plant_render(app, "kitchen_tag", "abc123")
    media = tmp_path / "media"
    calls: list[Any] = []
    pub = _publisher(app, media, calls)
    pub.on_push()

    # File written to <media>/tesserae/<device>.png.
    out = media / "tesserae" / "kitchen_tag.png"
    assert out.exists() and out.read_bytes().startswith(b"\x89PNG")
    # Service called with the media-source id + png type + target device.
    assert len(calls) == 1
    domain, service, data = calls[0]
    assert (domain, service) == ("opendisplay", "upload_image")
    assert data["device_id"] == "ha-abc"
    assert data["image"] == {
        "media_content_id": "media-source://media_source/local/tesserae/kitchen_tag.png",
        "media_content_type": "image/png",
    }


def test_skips_when_frame_unchanged(app: Flask, tmp_path: Path) -> None:
    _make_tag(app)
    _plant_render(app, "kitchen_tag", "abc123")
    calls: list[Any] = []
    pub = _publisher(app, tmp_path / "media", calls)
    pub.on_push()
    pub.on_push()  # same digest -> no second call
    assert len(calls) == 1


def test_pushes_again_when_frame_changes(app: Flask, tmp_path: Path) -> None:
    _make_tag(app)
    _plant_render(app, "kitchen_tag", "v1")
    calls: list[Any] = []
    pub = _publisher(app, tmp_path / "media", calls)
    pub.on_push()
    _plant_render(app, "kitchen_tag", "v2")
    pub.on_push()
    assert len(calls) == 2


def test_skips_device_without_ha_device_id(app: Flask, tmp_path: Path) -> None:
    _make_tag(app, ha_device_id="")
    _plant_render(app, "kitchen_tag")
    calls: list[Any] = []
    pub = _publisher(app, tmp_path / "media", calls)
    pub.on_push()
    assert calls == []


def test_rotation_passed_through(app: Flask, tmp_path: Path) -> None:
    _make_tag(app)
    section = app.config["SETTINGS_STORE"].get_section("devices")
    entry = dict(section["kitchen_tag"])
    entry["rotate"] = "90"
    app.config["SETTINGS_STORE"].patch_section("devices", {"kitchen_tag": entry})
    _plant_render(app, "kitchen_tag")
    calls: list[Any] = []
    pub = _publisher(app, tmp_path / "media", calls)
    pub.on_push()
    assert calls[0][2]["rotation"] == 90


def test_cleanup_and_prune(app: Flask, tmp_path: Path) -> None:
    _make_tag(app)
    _plant_render(app, "kitchen_tag")
    media = tmp_path / "media"
    pub = _publisher(app, media, [])
    pub.on_push()
    out = media / "tesserae" / "kitchen_tag.png"
    assert out.exists()

    # An orphan file for a device that isn't registered.
    (media / "tesserae" / "ghost.png").write_bytes(b"x")
    removed = pub.prune_orphans()
    assert removed == 1 and not (media / "tesserae" / "ghost.png").exists()
    assert out.exists()  # live device kept

    pub.cleanup_device("kitchen_tag")
    assert not out.exists()


def test_no_ha_core_call_service_is_soft(app: Flask, tmp_path: Path) -> None:
    _make_tag(app)
    _plant_render(app, "kitchen_tag")
    # Simulate ha_core missing its call_service (older plugin): must not raise.
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    if hasattr(core, "call_service"):
        delattr(core, "call_service")
    pub = OpenDisplayHaPublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=app.config["PUSH_MANAGER"].latest_render_for,
        media_root=tmp_path / "media",
    )
    pub.on_push()  # no crash; nothing marked sent


def test_ha_instance_is_push_transport_no_topics(app: Flask) -> None:
    _make_tag(app)
    device = app.config["DEVICE_REGISTRY"].devices["kitchen_tag"]
    # Delivered by the HA publisher, not a broker: push transport, no
    # MQTT topics derived (so the push pipeline never publishes for it).
    assert device.transport == "push"
    assert device.status_topic is None
    assert device.config_topic is None


def test_ha_instance_badge_reads_HA(app: Flask) -> None:
    from app.settings.index_routes import _transport_badge

    _make_tag(app)
    device = app.config["DEVICE_REGISTRY"].devices["kitchen_tag"]
    assert _transport_badge(device) == "HA"


def test_bridge_instance_is_rest_with_token(app: Flask) -> None:
    with app.app_context():
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id="hall_bridge",
            kind_id="opendisplay",
            name="Hall bridge tag",
        )
    device = app.config["DEVICE_REGISTRY"].devices["hall_bridge"]
    # The bridge polls over REST; a manual add inherits the kind's REST
    # transport and mints a token for the bridge to authenticate with.
    assert device.transport == "rest"
    assert device.status_topic is None
    assert isinstance(device.manifest.get("access_token"), str)
    assert device.manifest["access_token"]


def test_loader_normalizes_stale_push_instance(app: Flask, tmp_path: Path) -> None:
    """An instance file written before the kind dropped status_topic (so it
    carries a stale MQTT topic and no transport) still loads as push with
    the topic stripped."""
    from app.device_loader import load_instance_file

    registry = app.config["DEVICE_REGISTRY"]
    inst_file = tmp_path / "stale_tag.json"
    inst_file.write_text(
        json.dumps(
            {
                "id": "stale_tag",
                "kind": "opendisplay_ha",
                "name": "Stale tag",
                "status_topic": "tesserae/opendisplay_ha_stale_tag/status",
            }
        ),
        encoding="utf-8",
    )
    device = load_instance_file(registry, inst_file=inst_file, data_root=app.config["DATA_ROOT"])
    assert device is not None
    assert device.transport == "push"
    assert device.status_topic is None


def test_unwritable_media_dir_is_soft(app: Flask, tmp_path: Path) -> None:
    """A read-only / root-owned HA media folder (PermissionError on mkdir)
    must not crash the push loop or fire the upload; it warns and skips."""
    _make_tag(app)
    _plant_render(app, "kitchen_tag")
    calls: list[Any] = []
    pub = _publisher(app, tmp_path / "media", calls)

    def boom(_device_id: str, _src: Path) -> str:
        raise PermissionError("[Errno 13] Permission denied: '/media/tesserae'")

    pub._write_media = boom  # type: ignore[method-assign]
    pub.on_push()  # no crash
    assert calls == []  # upload never attempted
    assert pub._media_warned is True
