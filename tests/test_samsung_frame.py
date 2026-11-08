"""Samsung The Frame publisher (Art Mode push).

The Art app client is replaced by a fake that records calls, so no TV is
needed. Renders are real PNGs so the JPEG conversion is exercised.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from PIL import Image

from app import device_service, samsung_frame
from app.main import REPO_ROOT, create_app
from app.samsung_frame import SamsungFramePublisher
from app.samsung_frame_art import FrameArtError, FramePairingError


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


class FakeTV:
    """Shared TV state across connections."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.photos: list[str] = []
        self.next_id = 1
        self.art_mode = True
        self.issue_token: str | None = "tok-1"
        self.fail: dict[str, Exception] = {}
        self.uploads: list[bytes] = []


class FakeClient:
    def __init__(self, tv: FakeTV, host: str, *, token: str | None = None, name: str = "") -> None:
        self.tv, self.host, self.token, self.name = tv, host, token, name

    def _maybe_fail(self, op: str) -> None:
        if op in self.tv.fail:
            raise self.tv.fail[op]

    def connect(self, pairing_timeout: float = 45) -> None:
        self.tv.calls.append(("connect", self.host, self.token))
        self._maybe_fail("connect")
        if not self.token and self.tv.issue_token:
            self.token = self.tv.issue_token

    def close(self) -> None:
        self.tv.calls.append(("close",))

    def slideshow_off(self) -> None:
        self.tv.calls.append(("slideshow_off",))
        self._maybe_fail("slideshow_off")

    def list_content(self) -> list[dict[str, str]]:
        return [{"content_id": c, "category_id": "MY-C0002"} for c in self.tv.photos]

    def upload_jpeg(
        self,
        data: bytes,
        *,
        matte: str = "none",
        portrait_matte: str = "none",
        existing: set[str] | None = None,
    ) -> str:
        assert existing == set(self.tv.photos)
        self._maybe_fail("upload")
        cid = f"MY_F{self.tv.next_id:04d}"
        self.tv.next_id += 1
        self.tv.photos.append(cid)
        self.tv.uploads.append(data)
        self.tv.calls.append(("upload", matte, cid))
        return cid

    def art_mode_on(self) -> bool:
        return self.tv.art_mode

    def select(self, content_id: str, *, show: bool = False) -> None:
        self._maybe_fail("select")
        self.tv.calls.append(("select", content_id, show))

    def delete(self, ids: list[str]) -> None:
        self._maybe_fail("delete")
        self.tv.calls.append(("delete", list(ids)))
        self.tv.photos = [p for p in self.tv.photos if p not in ids]


def _make_frame(app: Flask, device_id: str = "office_frame", **cfg: Any) -> None:
    with app.app_context():
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id=device_id,
            kind_id="samsung_frame",
            name="Office Frame",
        )
    section = app.config["SETTINGS_STORE"].get_section("devices") or {}
    entry = dict(section.get(device_id) or {})
    entry.update({"host": "192.0.2.10", **cfg})
    app.config["SETTINGS_STORE"].patch_section("devices", {device_id: entry})


def _plant_render(
    app: Flask, device_id: str, digest: str, size: tuple[int, int] = (3840, 2160)
) -> None:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 90, 160)).save(buf, "PNG")
    (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(buf.getvalue())
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = {"composition_digest": digest}


def _publisher(app: Flask, tv: FakeTV, rest: dict[str, Any] | None = None) -> SamsungFramePublisher:
    return SamsungFramePublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=app.config["PUSH_MANAGER"].latest_render_for,
        run_async=False,
        client_factory=lambda host, **kw: FakeClient(tv, host, **kw),
        rest_info_fn=lambda host: rest,
    )


def _device(app: Flask, device_id: str = "office_frame") -> Any:
    return app.config["DEVICE_REGISTRY"].devices[device_id]


def test_kind_is_push_with_4k_full_colour_panel(app: Flask) -> None:
    _make_frame(app)
    dev = _device(app)
    assert dev.transport == "push"
    assert (dev.panel["w"], dev.panel["h"]) == (3840, 2160)
    assert dev.renderer_ids and dev.renderer_ids[0].split("__", 1)[0] == "pi_png"


def test_sends_new_frame_as_4k_baseline_jpeg_and_selects_it(app: Flask) -> None:
    _make_frame(app, matte_style="modernthin", matte_colour="warm")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    _publisher(app, tv).on_push()

    assert ("slideshow_off",) in tv.calls
    assert ("upload", "modernthin_warm", "MY_F0001") in tv.calls
    assert ("select", "MY_F0001", True) in tv.calls
    with Image.open(io.BytesIO(tv.uploads[0])) as img:
        assert img.format == "JPEG"
        assert img.size == (3840, 2160)
        assert not img.info.get("progressive")
    state = json.loads((Path(_device(app).data_dir) / "samsung_frame.json").read_text())
    assert state["last_digest"] == "aaa111"
    assert [u["content_id"] for u in state["uploads"]] == ["MY_F0001"]


def test_other_panel_sizes_are_cropped_to_4k(app: Flask) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "small", size=(1920, 1200))
    tv = FakeTV()
    _publisher(app, tv).on_push()
    with Image.open(io.BytesIO(tv.uploads[0])) as img:
        assert img.size == (3840, 2160)


def test_pairing_token_is_stored_encrypted_and_reused(app: Flask) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    pub = _publisher(app, tv)
    pub.on_push()
    raw = json.loads((app.config["DATA_ROOT"] / "core" / "settings.json").read_text())
    entry = raw["devices"]["office_frame"]
    assert "token" not in entry and entry["token_secret"] != "tok-1"
    assert (
        app.config["SETTINGS_STORE"].get_section("devices")["office_frame"]["token_secret"]
        == "tok-1"
    )

    _plant_render(app, "office_frame", "bbb222")
    pub.on_push()
    assert ("connect", "192.0.2.10", "tok-1") in tv.calls


def test_unchanged_frame_is_not_resent_even_after_restart(app: Flask) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    _publisher(app, tv).on_push()
    uploads = len(tv.uploads)
    _publisher(app, tv).on_push()
    assert len(tv.uploads) == uploads


def test_keeps_newest_uploads_and_deletes_only_its_own(app: Flask) -> None:
    _make_frame(app, keep_on_tv=2)
    tv = FakeTV()
    tv.photos = ["MY_F9000"]  # the owner's own photo
    tv.next_id = 1
    pub = _publisher(app, tv)
    for digest in ("d1", "d2", "d3"):
        _plant_render(app, "office_frame", digest)
        pub.on_push()
    assert ("delete", ["MY_F0001"]) in tv.calls
    assert tv.photos == ["MY_F9000", "MY_F0002", "MY_F0003"]


def test_failed_delete_is_retried_next_time(app: Flask) -> None:
    _make_frame(app, keep_on_tv=1)
    tv = FakeTV()
    pub = _publisher(app, tv)
    _plant_render(app, "office_frame", "d1")
    pub.on_push()
    tv.fail["delete"] = FrameArtError("busy")
    _plant_render(app, "office_frame", "d2")
    pub.on_push()
    assert "MY_F0001" in tv.photos
    del tv.fail["delete"]
    _plant_render(app, "office_frame", "d3")
    pub.on_push()
    assert ("delete", ["MY_F0001", "MY_F0002"]) in tv.calls
    assert tv.photos == ["MY_F0003"]


def test_watching_tv_is_not_interrupted(app: Flask) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    tv.art_mode = False
    _publisher(app, tv).on_push()
    assert ("select", "MY_F0001", False) in tv.calls


def test_slideshow_left_alone_when_take_over_is_off(app: Flask) -> None:
    _make_frame(app, take_over_slideshow="keep")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    _publisher(app, tv).on_push()
    assert ("slideshow_off",) not in tv.calls
    assert ("upload", "none", "MY_F0001") in tv.calls


def test_no_address_skips(app: Flask) -> None:
    _make_frame(app, host="")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    _publisher(app, tv).on_push()
    assert tv.calls == []


def test_failure_backs_off_then_poll_resends_when_tv_is_back(app: Flask) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    tv.fail["connect"] = FrameArtError("unreachable")
    pub = _publisher(app, tv, rest={"PowerState": "on", "modelName": "QN50LS03TAFXZA"})
    pub.on_push()
    assert tv.uploads == []

    del tv.fail["connect"]
    pub.poll_once()  # still backing off
    assert tv.uploads == []

    pub._backoff.clear()
    pub.poll_once()
    assert len(tv.uploads) == 1
    assert "office_frame" in app.config["DEVICE_STATUS"]


def test_pairing_refusal_is_reported_and_retried_later(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    tv.fail["connect"] = FramePairingError("the TV did not allow Tesserae")
    pub = _publisher(app, tv)
    pub.on_push()
    assert "did not allow" in caplog.text
    assert pub.pending_digest(_device(app)) == "aaa111"


def test_failed_select_keeps_frame_pending(app: Flask) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    tv.fail["select"] = FrameArtError("no")
    pub = _publisher(app, tv)
    pub.on_push()
    assert pub.pending_digest(_device(app)) == "aaa111"


def test_remove_uploads_deletes_its_frames(app: Flask) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    pub = _publisher(app, tv)
    pub.on_push()
    pub.remove_uploads(_device(app))
    assert ("delete", ["MY_F0001"]) in tv.calls
    assert not (Path(_device(app).data_dir) / "samsung_frame.json").exists()


def test_badge_reads_lan(app: Flask) -> None:
    from app.settings.index_routes import _transport_badge

    _make_frame(app)
    assert _transport_badge(_device(app)) == "LAN"


def test_matte_id() -> None:
    assert samsung_frame.matte_id({}) == "none"
    assert samsung_frame.matte_id({"matte_style": "shadowbox"}) == "shadowbox_polar"
    assert (
        samsung_frame.matte_id({"matte_style": "modern", "matte_colour": "black"}) == "modern_black"
    )


@pytest.mark.parametrize(
    ("payload", "ok"),
    [
        ({}, True),
        (
            {
                "host": "192.168.1.20",
                "matte_style": "modern",
                "matte_colour": "sage",
                "keep_on_tv": 3,
            },
            True,
        ),
        ({"host": "frame.local"}, True),
        ({"host": "not a host!"}, False),
        ({"matte_style": "gold"}, False),
        ({"keep_on_tv": 0}, False),
        ({"keep_on_tv": "x"}, False),
    ],
)
def test_validate_config(app: Flask, payload: dict[str, Any], ok: bool) -> None:
    _make_frame(app)
    module = _device(app).module
    assert module.validate_config(payload)[0] is ok


def _admin(app: Flask) -> Any:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


def test_add_device_card_has_a_samsung_frame_tab(app: Flask) -> None:
    client = _admin(app)
    html = client.get("/settings/devices").get_data(as_text=True)
    assert 'data-segmented-btn="samsung_frame"' in html
    assert 'data-transport-branch="samsung_frame"' in html
    assert 'name="kind" value="samsung_frame"' in html


def test_samsung_frame_tab_adds_a_push_device_with_its_address(app: Flask) -> None:
    client = _admin(app)
    client.post(
        "/settings/devices/add",
        data={
            "kind": "samsung_frame",
            "id": "lounge_frame",
            "name": "Lounge",
            "host": "192.0.2.20",
        },
    )
    dev = app.config["DEVICE_REGISTRY"].get("lounge_frame")
    assert dev is not None and dev.transport == "push"
    assert (
        app.config["SETTINGS_STORE"].get_section("devices")["lounge_frame"]["host"] == "192.0.2.20"
    )


def test_rest_tab_keeps_a_samsung_frame_on_push(app: Flask) -> None:
    client = _admin(app)
    client.post(
        "/settings/devices/add",
        data={"kind": "samsung_frame", "id": "den_frame", "name": "Den", "transport": "rest"},
    )
    dev = app.config["DEVICE_REGISTRY"].get("den_frame")
    assert dev is not None and dev.transport == "push"
    assert "access_token" not in dev.manifest


def test_device_page_leaves_out_what_a_tv_does_not_have(app: Flask) -> None:
    client = _admin(app)
    client.post(
        "/settings/devices/add",
        data={
            "kind": "samsung_frame",
            "id": "lounge_frame",
            "name": "Lounge",
            "host": "192.0.2.20",
        },
    )
    html = client.get("/settings/devices/lounge_frame").get_data(as_text=True)
    assert 'id="schedule"' in html and 'id="quiet-hours"' in html
    for absent in ('id="timing"', 'id="power"', "Button map", "Client rotation", "Letterbox"):
        assert absent not in html, absent
    assert "Frames kept on the TV" in html and "TV&#39;s own slideshow" in html


def test_other_devices_keep_timing_power_and_controls(app: Flask) -> None:
    client = _admin(app)
    client.post(
        "/settings/devices/add", data={"kind": "esp32_client", "id": "kitchen", "name": "K"}
    )
    html = client.get("/settings/devices/kitchen").get_data(as_text=True)
    for present in ('id="timing"', 'id="power"', "Button map"):
        assert present in html, present


def test_an_unexpected_error_backs_off(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    pub = _publisher(app, tv)

    def boom(_path: Path) -> bytes:
        raise OSError("disk full")

    monkeypatch.setattr(samsung_frame, "to_jpeg", boom)
    pub.on_push()
    assert pub._backing_off("office_frame")
    assert tv.uploads == []


def test_a_send_keeps_state_written_meanwhile(app: Flask) -> None:
    """The worker saves only its own keys, merged into the file as it is
    when it finishes, so a flag the poller set during the upload survives."""
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    pub = _publisher(app, tv)
    device = _device(app)
    original = FakeClient.select

    def select_and_meanwhile(self: FakeClient, content_id: str, *, show: bool = False) -> None:
        pub.update_state(device, {"written_by_poller": True})
        original(self, content_id, show=show)

    FakeClient.select = select_and_meanwhile  # type: ignore[method-assign]
    try:
        pub.on_push()
    finally:
        FakeClient.select = original  # type: ignore[method-assign]
    state = pub.load_state(device)
    assert state["written_by_poller"] is True
    assert state["last_digest"] == "aaa111"


def test_the_async_worker_sends_the_newest_frame_and_stops(app: Flask) -> None:
    import threading

    _make_frame(app)
    tv = FakeTV()
    pub = _publisher(app, tv)
    pub._run_async = True
    gate = threading.Event()
    original = FakeClient.upload_jpeg

    def slow_upload(self: FakeClient, data: bytes, **kw: Any) -> str:
        gate.wait(5)
        return original(self, data, **kw)

    FakeClient.upload_jpeg = slow_upload  # type: ignore[method-assign]
    try:
        _plant_render(app, "office_frame", "d1")
        pub.on_push()
        _plant_render(app, "office_frame", "d2")
        pub.on_push()  # arrives while the first upload is in flight
        gate.set()
        for _ in range(100):
            if not pub._worker_running:
                break
            threading.Event().wait(0.05)
    finally:
        FakeClient.upload_jpeg = original  # type: ignore[method-assign]
    assert not pub._worker_running
    assert pub.load_state(_device(app))["last_digest"] == "d2"
