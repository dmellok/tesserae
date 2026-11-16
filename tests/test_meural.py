"""Stock Meural LAN delivery, renewal and sleep ownership."""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from PIL import Image

from app import device_service
from app.main import REPO_ROOT, create_app
from app.meural import MeuralClient, MeuralError, MeuralPublisher, register


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )


class Frame:
    """The external frame boundary; the publisher and image conversion stay real."""

    def __init__(self):
        self.sleeping = False
        self.uploads = []
        self.power = []
        self.fail_upload = False
        self.orientations = []

    def is_sleeping(self):
        return self.sleeping

    def status(self):
        return {"fw_version": "2.0.9", "orientation": "portrait", "rssi": -64}

    def set_sleep(self, sleeping):
        self.power.append(sleeping)
        self.sleeping = sleeping

    def set_orientation(self, orientation):
        self.orientations.append(orientation)

    def upload(self, data):
        if self.fail_upload:
            raise MeuralError("upload rejected")
        self.uploads.append(data)


@pytest.fixture
def setup(app):
    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DATA_ROOT"],
        instance_id="test_frame",
        kind_id="meural",
        name="Example frame",
    )
    assert result.ok, result.error
    settings = app.config["SETTINGS_STORE"]
    cfg = dict((settings.get_section("devices") or {}).get("test_frame") or {})
    cfg["host"] = "192.0.2.10"
    settings.patch_section("devices", {"test_frame": cfg})
    frame = Frame()
    clock = [100.0]
    quiet = [False]
    latest = {"composition_digest": "first"}
    image = Image.new("RGB", (108, 192), "red")
    image.save(app.config["RENDERS_DIR"] / "first.png")

    def publisher(run_async=False):
        return MeuralPublisher(
            app=app,
            devices=app.config["DEVICE_REGISTRY"],
            settings=settings,
            renders_dir=app.config["RENDERS_DIR"],
            latest_render_fn=lambda _: latest,
            client_factory=lambda _: frame,
            clock=lambda: clock[0],
            quiet_fn=lambda _: quiet[0],
            run_async=run_async,
        )

    return SimpleNamespace(
        app=app,
        frame=frame,
        clock=clock,
        quiet=quiet,
        latest=latest,
        publisher=publisher,
        settings=settings,
        device=app.config["DEVICE_REGISTRY"].devices["test_frame"],
    )


def test_device_is_native_lan_and_preserves_portrait(setup):
    setup.publisher().on_push()
    assert setup.device.transport == "push"
    assert setup.device.status_topic is None
    assert len(setup.frame.uploads) == 1
    with Image.open(io.BytesIO(setup.frame.uploads[0])) as image:
        assert image.format == "JPEG"
        assert image.size == (108, 192)
    from app.settings.index_routes import _transport_badge

    assert _transport_badge(setup.device) == "LAN"


def test_renews_unchanged_preview_before_expiry(setup):
    pub = setup.publisher()
    pub.poll_once()
    setup.clock[0] = 110
    pub.poll_once()
    assert len(setup.frame.uploads) == 1
    setup.clock[0] = 115
    pub.poll_once()
    assert len(setup.frame.uploads) == 2


def test_new_composition_is_sent_without_waiting(setup):
    pub = setup.publisher()
    pub.poll_once()
    setup.latest["composition_digest"] = "second"
    Image.new("RGB", (108, 192), "blue").save(setup.app.config["RENDERS_DIR"] / "second.png")
    pub.on_push()
    assert len(setup.frame.uploads) == 2
    assert setup.frame.uploads[0] != setup.frame.uploads[1]


def test_failed_upload_can_retry_and_is_not_marked_delivered(setup):
    pub = setup.publisher()
    setup.frame.fail_upload = True
    pub.poll_once()
    assert setup.frame.uploads == []
    setup.frame.fail_upload = False
    setup.clock[0] += 60
    pub.poll_once()
    assert len(setup.frame.uploads) == 1


def test_unexpected_device_failure_does_not_kill_renewal(setup, monkeypatch):
    pub = setup.publisher()
    with monkeypatch.context() as context:
        context.setattr(
            setup.frame, "upload", lambda _: (_ for _ in ()).throw(RuntimeError("broken"))
        )
        pub.poll_once()
    setup.clock[0] += 60
    pub.poll_once()
    assert len(setup.frame.uploads) == 1


def test_unreachable_frame_does_not_report_a_fresh_checkin(setup, monkeypatch):
    pub = setup.publisher()
    pub.poll_once()
    before = setup.app.config["DEVICE_STATUS"]["test_frame"]["received_at"]

    def unavailable():
        raise MeuralError("frame unavailable")

    monkeypatch.setattr(setup.frame, "is_sleeping", unavailable)
    setup.clock[0] += 60
    pub.poll_once()
    assert setup.app.config["DEVICE_STATUS"]["test_frame"]["received_at"] == before


def test_status_storage_failure_does_not_prevent_upload(setup, monkeypatch):
    def unavailable(**kwargs):
        raise OSError("disk busy")

    monkeypatch.setattr("app.transport_wiring.record_status_heartbeat", unavailable)
    setup.publisher().poll_once()
    assert len(setup.frame.uploads) == 1


def test_frame_at_new_address_does_not_inherit_quiet_sleep(setup):
    setup.quiet[0] = True
    pub = setup.publisher()
    pub.poll_once()
    cfg = dict(setup.settings.get_section("devices")["test_frame"])
    cfg["host"] = "192.0.2.11"
    setup.settings.patch_section("devices", {"test_frame": cfg})
    setup.quiet[0] = False
    setup.publisher().poll_once()
    assert setup.frame.power == [True]


def test_manually_sleeping_frame_is_left_asleep(setup):
    setup.frame.sleeping = True
    pub = setup.publisher()
    pub.poll_once()
    setup.quiet[0] = True
    pub.poll_once()
    setup.quiet[0] = False
    pub.poll_once()
    assert setup.frame.power == []
    assert setup.frame.uploads == []


def test_own_quiet_sleep_is_resumed_after_restart(setup):
    setup.quiet[0] = True
    setup.publisher().poll_once()
    assert setup.frame.power == [True]
    assert setup.frame.uploads == []
    setup.quiet[0] = False
    setup.publisher().poll_once()
    assert setup.frame.power == [True, False]
    assert len(setup.frame.uploads) == 1


def test_address_change_invalidates_delivery_and_sleep_ownership(setup):
    pub = setup.publisher()
    pub.poll_once()
    cfg = dict(setup.settings.get_section("devices")["test_frame"])
    cfg["host"] = "192.0.2.11"
    setup.settings.patch_section("devices", {"test_frame": cfg})
    pub.poll_once()
    assert len(setup.frame.uploads) == 2


def test_missing_address_or_artifact_does_not_send(setup):
    cfg = dict(setup.settings.get_section("devices")["test_frame"])
    cfg["host"] = ""
    setup.settings.patch_section("devices", {"test_frame": cfg})
    setup.publisher().poll_once()
    assert setup.frame.uploads == []
    cfg["host"] = "192.0.2.10"
    setup.settings.patch_section("devices", {"test_frame": cfg})
    setup.latest["composition_digest"] = "missing"
    setup.publisher().poll_once()
    assert setup.frame.uploads == []


def test_stop_prevents_stale_publisher_uploads(setup):
    pub = setup.publisher()
    pub.stop()
    pub.on_push()
    pub.poll_once()
    assert setup.frame.uploads == []


def test_removed_device_is_not_changed_after_an_inflight_read(setup, monkeypatch):
    def removed():
        setup.app.config["DEVICE_REGISTRY"].devices.pop("test_frame")
        return False

    setup.quiet[0] = True
    monkeypatch.setattr(setup.frame, "is_sleeping", removed)
    setup.publisher().poll_once()
    assert setup.frame.power == []
    assert setup.frame.orientations == []
    assert setup.frame.uploads == []


def test_rebuild_stops_old_publisher(setup):
    old = setup.app.config["MEURAL_PUBLISHER"]
    with patch.object(old, "stop", wraps=old.stop) as stop:
        register(setup.app)
    stop.assert_called_once()
    assert setup.app.config["MEURAL_PUBLISHER"] is not old


def test_background_push_wakes_worker_and_stop_prevents_more_sends(setup, monkeypatch):
    uploaded = threading.Event()
    original_upload = setup.frame.upload

    def upload(data):
        original_upload(data)
        uploaded.set()

    monkeypatch.setattr(setup.frame, "upload", upload)
    pub = setup.publisher(run_async=True)
    try:
        pub.start()
        assert uploaded.wait(3)
        uploaded.clear()
        setup.latest["composition_digest"] = "second"
        Image.new("RGB", (108, 192), "blue").save(setup.app.config["RENDERS_DIR"] / "second.png")
        pub.on_push()
        assert uploaded.wait(3)
    finally:
        pub.stop()
    pub.on_push()
    pub.poll_once()
    assert len(setup.frame.uploads) == 2


def test_stopping_does_not_wait_for_an_inflight_upload(setup, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    replacement_entered = threading.Event()

    def upload(data):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        else:
            replacement_entered.set()
        setup.frame.uploads.append(data)

    monkeypatch.setattr(setup.frame, "upload", upload)
    pub = setup.publisher(run_async=True)
    pub.start()
    assert entered.wait(3)

    def stop():
        pub.stop()
        stopped.set()

    stopper = threading.Thread(target=stop)
    stopper.start()
    replacement = None
    try:
        assert stopped.wait(0.5), "saving settings must not wait for a slow frame"
        setup.latest["composition_digest"] = "second"
        Image.new("RGB", (108, 192), "blue").save(setup.app.config["RENDERS_DIR"] / "second.png")
        replacement = setup.publisher(run_async=True)
        replacement.start()
        assert not replacement_entered.wait(0.2), "old and new uploads must stay ordered"
    finally:
        release.set()
        stopper.join(3)
        pub.stop()
        if replacement is not None:
            assert replacement_entered.wait(3)
            replacement.stop()
    assert len(setup.frame.uploads) == 2
    assert setup.frame.uploads[0] != setup.frame.uploads[1]


@pytest.mark.parametrize("host", ["http://frame", "frame/path", "frame:80", "bad host"])
def test_rejects_invalid_address(setup, host):
    valid, _ = setup.device.module.validate_config({"host": host})
    assert not valid


@pytest.mark.parametrize("interval", [0, -5, True, 1.5, "oops"])
def test_rejects_invalid_preview_interval(setup, interval):
    valid, _ = setup.device.module.validate_config({"refresh_interval_s": interval})
    assert not valid


def test_status_parser_does_not_copy_credentials(setup):
    parsed = setup.device.module.parse_status(
        json.dumps(
            {
                "fw_version": "2.0.9",
                "rssi": -64,
                "screen": "on",
                "wifi_status": {"softap": {"password": "private"}},
                "token": "private",
            }
        ).encode()
    )
    assert parsed["fw_version"] == "2.0.9"
    assert parsed["rssi"] == -64
    assert "private" not in json.dumps(parsed)


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def test_client_sends_multipart_photo_with_jpeg_type(monkeypatch):
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return response({"status": "pass", "response": "Postcard is ready."})

    monkeypatch.setattr("app.meural.open_request", urlopen)
    MeuralClient("192.0.2.10").upload(b"jpeg-data")
    request = requests[0]
    assert request.full_url == "http://192.0.2.10/remote/postcard/"
    assert request.get_method() == "POST"
    assert b'name="photo"' in request.data
    assert b"Content-Type: image/jpeg" in request.data
    assert b"jpeg-data" in request.data


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "fail", "response": "rejected"},
        {"response": "missing status"},
        [],
    ],
)
def test_client_rejects_unsuccessful_protocol_reply(monkeypatch, payload):
    monkeypatch.setattr("app.meural.open_request", lambda *a, **kw: response(payload))
    with pytest.raises(MeuralError):
        MeuralClient("192.0.2.10").upload(b"jpeg-data")


def test_client_filters_system_status(monkeypatch):
    monkeypatch.setattr(
        "app.meural.open_request",
        lambda *a, **kw: response(
            {
                "status": "pass",
                "response": {
                    "version": "2.0.9",
                    "orientation": "portrait",
                    "lux": "4",
                    "backlight": "9",
                    "free_space": 3177,
                    "wifi_status": {"signal": "-64", "softap": {"password": "private"}},
                },
            }
        ),
    )
    status = MeuralClient("192.0.2.10").status()
    assert status["fw_version"] == "2.0.9"
    assert status["rssi"] == -64
    assert "private" not in json.dumps(status)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Meural tests must replace the HTTP boundary")

    monkeypatch.setattr("app.meural.open_request", blocked)


def test_client_closes_http_error_response_without_exposing_payload(monkeypatch):
    import urllib.error

    handle = io.BytesIO(b'{"password":"example-private-value"}')

    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "failed", {}, handle)

    monkeypatch.setattr("app.meural.open_request", fail)
    with pytest.raises(MeuralError) as caught:
        MeuralClient("192.0.2.10").upload(b"jpeg")
    assert "example-private-value" not in str(caught.value)
    assert handle.closed


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.1",
        "169.254.1.2",
        "0.0.0.0",
        "localhost",
        "LOCALHOST",
        "frame.localhost",
        "::1",
    ],
)
def test_rejects_non_lan_addresses(host):
    from app.meural import valid_host

    assert not valid_host(host)


@pytest.mark.parametrize("host", ["192.0.2.10", "10.0.0.2", "192.168.1.2", "meural.local"])
def test_accepts_lan_addresses(host):
    from app.meural import valid_host

    assert valid_host(host)


def test_lan_reply_has_a_size_limit(monkeypatch):
    monkeypatch.setattr(
        "app.meural.open_request",
        lambda *a, **kw: io.BytesIO(
            b'{"status":"pass","response":true,"padding":"' + b" " * (1024 * 1024) + b'"}'
        ),
    )
    with pytest.raises(MeuralError):
        MeuralClient("192.0.2.10").is_sleeping()


def test_redirects_do_not_forward_authorization():
    from urllib.request import Request

    from app.meural_http import NoRedirect

    request = Request("https://example.com", headers={"Authorization": "Token example"})
    assert (
        NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://other.example")
        is None
    )
