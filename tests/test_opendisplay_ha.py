"""OpenDisplay-via-HA publisher (issue: OpenDisplay support).

Renders reach OpenDisplay tags by writing the frame into HA's media folder
and calling ``opendisplay.upload_image``. These tests mock the ha_core
service call and use a temp media root, so no HA or Bluetooth is needed.
"""

from __future__ import annotations

import json
import time
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
        run_async=False,
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
        run_async=False,
    )
    pub.on_push()  # no crash; nothing marked sent


def test_async_worker_sends_off_the_push_thread(app: Flask, tmp_path: Path) -> None:
    """With run_async (the default), on_push returns immediately and a
    background worker performs the slow upload, so a push never blocks."""
    _make_tag(app)
    _plant_render(app, "kitchen_tag")
    calls: list[Any] = []
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module

    def fake_call_service(domain, service, *, data=None, timeout=10):
        calls.append((domain, service, data))
        return []

    core.call_service = fake_call_service  # type: ignore[attr-defined]
    pub = OpenDisplayHaPublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=app.config["PUSH_MANAGER"].latest_render_for,
        media_root=tmp_path / "media",
        run_async=True,
    )
    pub.on_push()  # returns immediately; worker runs in the background
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    assert len(calls) == 1


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


def _set_panel(app: Flask, device_id: str, w: int, h: int, orientation: str, **extra: Any) -> None:
    with app.app_context():
        result = device_service.update_instance_panel(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id=device_id,
            w=w,
            h=h,
            orientation=orientation,
            **extra,
        )
    assert result.ok, result.error


def _plant_real_render(app: Flask, device_id: str, w: int, h: int, digest: str = "cafe01") -> None:
    """A decodable composition with one red pixel in its top-left corner,
    so a test can see where that corner ends up."""
    import io

    from PIL import Image

    img = Image.new("RGB", (w, h), "white")
    img.putpixel((0, 0), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(buf.getvalue())
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = {"composition_digest": digest}


def _sent_frame(media: Path, device_id: str = "kitchen_tag") -> Any:
    from PIL import Image

    return Image.open(media / "tesserae" / f"{device_id}.png").convert("RGB")


def test_portrait_panel_is_turned_onto_the_tags_landscape_buffer(
    app: Flask, tmp_path: Path
) -> None:
    """Panel rotation is the one rotation control: a portrait composition is
    turned onto the tag's landscape buffer before it leaves, the mapping
    pi_png gives a polled client, and HA is asked for no turn of its own."""
    _make_tag(app)
    _set_panel(app, "kitchen_tag", 480, 800, "portrait")
    _plant_real_render(app, "kitchen_tag", 480, 800)
    calls: list[Any] = []
    media = tmp_path / "media"
    _publisher(app, media, calls).on_push()

    frame = _sent_frame(media)
    assert frame.size == (800, 480)
    # Three clockwise quarter-turns put the composition's top-left corner at
    # the buffer's bottom-left.
    assert frame.getpixel((0, 479)) == (255, 0, 0)
    assert "rotation" not in calls[0][2]


def test_flipped_mount_turns_the_frame_half_way(app: Flask, tmp_path: Path) -> None:
    """180° used to do nothing for these tags: the upright composition was
    sent whatever the mount. Now the half turn is applied."""
    _make_tag(app)
    _set_panel(app, "kitchen_tag", 800, 480, "landscape_flipped")
    _plant_real_render(app, "kitchen_tag", 800, 480)
    calls: list[Any] = []
    media = tmp_path / "media"
    _publisher(app, media, calls).on_push()

    frame = _sent_frame(media)
    assert frame.size == (800, 480)
    assert frame.getpixel((799, 479)) == (255, 0, 0)


def test_underscan_insets_the_frame(app: Flask, tmp_path: Path) -> None:
    _make_tag(app)
    _set_panel(app, "kitchen_tag", 800, 480, "landscape", underscan=20)
    _plant_real_render(app, "kitchen_tag", 800, 480)
    media = tmp_path / "media"
    _publisher(app, media, []).on_push()

    frame = _sent_frame(media)
    assert frame.size == (800, 480)
    # The corner pixel is now mat, not content.
    assert frame.getpixel((0, 0)) == (255, 255, 255)


def test_changed_mount_resends_an_unchanged_composition(app: Flask, tmp_path: Path) -> None:
    """Turning the panel does not change the composition, but it does change
    the frame the tag needs, so it must go out again."""
    _make_tag(app)
    _set_panel(app, "kitchen_tag", 800, 480, "landscape")
    _plant_real_render(app, "kitchen_tag", 800, 480)
    calls: list[Any] = []
    pub = _publisher(app, tmp_path / "media", calls)
    pub.on_push()
    pub.on_push()
    assert len(calls) == 1
    _set_panel(app, "kitchen_tag", 800, 480, "landscape_flipped")
    pub.on_push()
    assert len(calls) == 2


def test_older_ha_rotation_keeps_the_upright_composition(app: Flask, tmp_path: Path) -> None:
    """A tag set up when HA did the turning keeps working unchanged: its
    composition goes out upright and HA is told the stored rotation."""
    _make_tag(app)
    _set_panel(app, "kitchen_tag", 480, 800, "portrait")
    section = app.config["SETTINGS_STORE"].get_section("devices")
    entry = dict(section["kitchen_tag"])
    entry["rotate"] = "270"
    app.config["SETTINGS_STORE"].patch_section("devices", {"kitchen_tag": entry})
    _plant_real_render(app, "kitchen_tag", 480, 800)
    calls: list[Any] = []
    media = tmp_path / "media"
    _publisher(app, media, calls).on_push()

    frame = _sent_frame(media)
    assert frame.size == (480, 800)
    assert frame.getpixel((0, 0)) == (255, 0, 0)
    assert calls[0][2]["rotation"] == 270


def _upload_publisher(
    app: Flask, calls: list, uploads: list, *, upload_error: Exception | None = None
) -> OpenDisplayHaPublisher:
    """A publisher with no shared media folder, as on Docker / Proxmox /
    bare metal: frames go through ha_core's upload API."""
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module

    def fake_call_service(domain, service, *, data=None, timeout=10):
        calls.append((domain, service, data))
        return []

    def fake_upload_media(folder_id, filename, content, *, content_type="image/png", timeout=30):
        if upload_error is not None:
            raise upload_error
        uploads.append((folder_id, filename, content, content_type))
        return f"{folder_id}/{filename}"

    core.call_service = fake_call_service  # type: ignore[attr-defined]
    core.upload_media = fake_upload_media  # type: ignore[attr-defined]
    return OpenDisplayHaPublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=app.config["PUSH_MANAGER"].latest_render_for,
        run_async=False,
    )


def test_uploads_frame_when_there_is_no_shared_media_folder(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside the HA add-on Tesserae shares no filesystem with HA, so the
    frame goes through HA's media upload API and the action is pointed at
    the id HA stored it under. Nothing is written to a local /media."""
    monkeypatch.delenv("TESSERAE_HA_INGRESS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    _make_tag(app, "kitchen_tag", "ha-abc")
    _plant_render(app, "kitchen_tag", "deadbeef")
    calls: list[Any] = []
    uploads: list[Any] = []
    pub = _upload_publisher(app, calls, uploads)
    assert pub._shared_folder is False

    def no_local_write(_device_id: str, _src: Path) -> str:
        raise AssertionError("must not write a local media file")

    pub._write_media = no_local_write  # type: ignore[method-assign]
    pub.on_push()

    assert uploads == [
        (
            "media-source://media_source/local/tesserae",
            "kitchen_tag.png",
            b"\x89PNG\r\n\x1a\nfake",
            "image/png",
        )
    ]
    assert len(calls) == 1
    assert calls[0][2]["image"] == {
        "media_content_id": "media-source://media_source/local/tesserae/kitchen_tag.png",
        "media_content_type": "image/png",
    }
    # Unchanged frame: neither uploaded nor sent again.
    pub.on_push()
    assert len(uploads) == 1
    assert len(calls) == 1


def test_failed_upload_is_soft_and_retried(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """HA refusing the upload (a non-admin token answers 401) must not crash
    the push loop or call the action; it warns once and tries again on the
    next push."""
    import urllib.error

    monkeypatch.delenv("TESSERAE_HA_INGRESS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    _make_tag(app)
    _plant_render(app, "kitchen_tag")
    calls: list[Any] = []
    uploads: list[Any] = []
    denied = urllib.error.HTTPError("http://ha/upload", 401, "Unauthorized", None, None)  # type: ignore[arg-type]
    pub = _upload_publisher(app, calls, uploads, upload_error=denied)
    pub.on_push()  # no crash
    assert calls == []
    assert pub._media_warned is True
    assert "kitchen_tag" not in pub._last_sent

    # Token fixed: the same frame goes out on the next push.
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    core.upload_media = lambda folder_id, filename, content, **_kw: f"{folder_id}/{filename}"  # type: ignore[attr-defined]
    pub.on_push()
    assert len(calls) == 1
    assert pub._media_warned is False


def test_unwritable_shared_folder_falls_back_to_upload(app: Flask, tmp_path: Path) -> None:
    """The add-on's media folder being read-only no longer strands the tag:
    the frame is uploaded through HA's API instead."""
    _make_tag(app)
    _plant_render(app, "kitchen_tag")
    calls: list[Any] = []
    uploads: list[Any] = []
    pub = _upload_publisher(app, calls, uploads)
    pub._shared_folder = True

    def boom(_device_id: str, _src: Path) -> str:
        raise PermissionError("[Errno 13] Permission denied: '/media/tesserae'")

    pub._write_media = boom  # type: ignore[method-assign]
    pub.on_push()
    assert len(uploads) == 1
    assert len(calls) == 1
    assert pub._media_warned is False


def test_no_shared_folder_leaves_local_media_alone(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a shared folder the default /media is not Tesserae's, so the
    orphan sweep and device cleanup must not delete files under it."""
    monkeypatch.delenv("TESSERAE_HA_INGRESS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    pub = _upload_publisher(app, [], [])
    pub._media_dir = tmp_path / "media" / "tesserae"
    pub._media_dir.mkdir(parents=True)
    stray = pub._media_dir / "someone_elses.png"
    stray.write_bytes(b"x")
    assert pub.prune_orphans() == 0
    pub.cleanup_device("someone_elses")
    assert stray.exists()


def test_configured_media_root_keeps_the_shared_folder_path(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``app.opendisplay_media_root`` set to a mount of HA's media folder
    means write there, add-on or not."""
    monkeypatch.delenv("TESSERAE_HA_INGRESS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    root = tmp_path / "ha-media"
    app.config["SETTINGS_STORE"].patch_section("app", {"opendisplay_media_root": str(root)})
    _make_tag(app)
    _plant_render(app, "kitchen_tag")
    calls: list[Any] = []
    uploads: list[Any] = []
    pub = _upload_publisher(app, calls, uploads)
    assert pub._shared_folder is True
    pub.on_push()
    assert (root / "tesserae" / "kitchen_tag.png").exists()
    assert uploads == []
    assert len(calls) == 1


def test_telemetry_poll_records_heartbeat(app: Flask) -> None:
    """The telemetry poller pulls the tag's battery/signal/firmware from HA
    and feeds it through record_status_heartbeat, so the device's status
    cache shows it like any other device's heartbeat."""
    from app.opendisplay_ha import OpenDisplayHaTelemetryPoller

    _make_tag(app)
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    core.render_template = (  # type: ignore[attr-defined]
        lambda _t, timeout=10: (
            '{"fw_version": "1.4.2", "battery_pct": "88", "rssi": "-58", "temperature": "20"}'
        )
    )
    poller = OpenDisplayHaTelemetryPoller(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        run_async=False,
    )
    with app.app_context():
        assert poller.poll_once() == 1
    parsed = app.config["DEVICE_STATUS"]["kitchen_tag"]["parsed"]
    assert parsed["fw_version"] == "1.4.2"
    assert parsed["battery_pct"] == 88
    assert parsed["rssi"] == -58


def test_telemetry_poll_skips_without_ha_device_id(app: Flask) -> None:
    from app.opendisplay_ha import OpenDisplayHaTelemetryPoller

    _make_tag(app, ha_device_id="")
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    core.render_template = lambda _t, timeout=10: '{"battery_pct": "50"}'  # type: ignore[attr-defined]
    poller = OpenDisplayHaTelemetryPoller(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        run_async=False,
    )
    with app.app_context():
        assert poller.poll_once() == 0
    assert "kitchen_tag" not in app.config["DEVICE_STATUS"]


def test_telemetry_poll_works_without_an_outer_app_context(app: Flask) -> None:
    """The poller runs on its own thread, where there is no app context.
    ha_core's ``render_template`` reads config through ``current_app``, so
    every query raised "Working outside of application context", was
    swallowed at DEBUG, and the integration silently never worked
    (production, 2026-08-22)."""
    from app.opendisplay_ha import OpenDisplayHaTelemetryPoller

    _make_tag(app)
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    core.render_template = (  # type: ignore[attr-defined]
        lambda _t, timeout=10: '{"fw_version": "1.4.2", "battery_pct": "88", "rssi": "-58"}'
    )
    poller = OpenDisplayHaTelemetryPoller(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        run_async=False,
    )

    # Deliberately NO ``with app.app_context()`` here: that is the thread's
    # real situation, and it is what used to fail.
    assert poller.poll_once() == 1
    assert app.config["DEVICE_STATUS"]["kitchen_tag"]["parsed"]["battery_pct"] == 88
