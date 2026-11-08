"""Samsung The Frame publisher (Art Mode push).

The Art app client is replaced by a fake that records calls, so no TV is
needed. Renders are real PNGs so the JPEG conversion is exercised.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from PIL import Image

from app import device_service, samsung_frame
from app.main import REPO_ROOT, create_app
from app.samsung_frame import SamsungFramePublisher
from app.samsung_frame_art import FrameArtError, FramePairingError


@pytest.fixture(autouse=True)
def _fresh_backoff() -> None:
    # Back-off is shared by every publisher in the process; start each test clean.
    samsung_frame._BACKOFF.clear()


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


_ON = {"PowerState": "on"}


def _publisher(app: Flask, tv: FakeTV, rest: dict[str, Any] | None = _ON) -> SamsungFramePublisher:
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


def test_a_tv_in_use_gets_its_frame_once_art_mode_is_back(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    tv.art_mode = False  # someone is watching
    pub = _publisher(app, tv)
    pub.on_push()
    assert tv.uploads == []
    assert _screen(app)["screen_why"] == "in use, new frame waiting"
    assert not pub._backing_off("office_frame")
    pub.poll_once()  # still watching: nothing sent
    assert tv.uploads == []
    tv.art_mode = True  # back to Art Mode
    pub.poll_once()
    assert len(tv.uploads) == 1
    assert ("select", "MY_F0001", True) in tv.calls


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
    for absent in (
        'class="dx-grp" id="timing"',
        'id="power"',
        "Button map",
        "Client rotation",
        "Letterbox",
    ):
        assert absent not in html, absent
    assert "Frames kept on the TV" in html and "TV&#39;s own slideshow" in html


def _group_html(html: str, group_id: str) -> str:
    start = html.index(f'id="{group_id}" data-devsub')
    return html[start : html.index("data-devsub", start + 40)]


def _add_lounge(app: Flask) -> Any:
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
    return client


def test_device_page_groups_power_and_art_mode_settings(app: Flask) -> None:
    html = _add_lounge(app).get("/settings/devices/lounge_frame").get_data(as_text=True)
    power = _group_html(html, "grp-power")
    art = _group_html(html, "grp-art-mode")
    for name in ("power_control", "power_switch", "sleep_when_in_use"):
        assert f'name="{name}"' in power, name
    # The switch field follows the Power control select.
    assert power.index('name="power_control"') < power.index('name="power_switch"')
    for name in ("matte_style", "matte_colour", "take_over_slideshow", "keep_on_tv"):
        assert f'name="{name}"' in art, name
    # None of them sit in Identity or Rendering any more; the host and token do.
    identity, rendering = _group_html(html, "identity"), _group_html(html, "rendering")
    for name in ("power_control", "power_switch", "matte_style", "keep_on_tv"):
        assert f'name="{name}"' not in identity + rendering, name
    assert 'name="host"' in identity and 'name="token"' in identity


def test_the_home_assistant_switch_field_shows_only_for_a_switch(app: Flask) -> None:
    def wrap_tag(html: str) -> str:
        found = re.search(r'<div class="dx-f[^>]*data-show-if=[^>]*>', html)
        assert found is not None
        return found.group(0)

    client = _add_lounge(app)
    tv = client.get("/settings/devices/lounge_frame").get_data(as_text=True)
    assert "data-show-if-plain" in wrap_tag(tv) and " hidden" in wrap_tag(tv)
    store = app.config["SETTINGS_STORE"]
    entry = dict((store.get_section("devices") or {})["lounge_frame"], power_control="switch")
    store.patch_section("devices", {"lounge_frame": entry})
    sw = client.get("/settings/devices/lounge_frame").get_data(as_text=True)
    assert " hidden" not in wrap_tag(sw)


def test_a_save_lands_back_on_a_kind_defined_group(app: Flask) -> None:
    client = _add_lounge(app)
    resp = client.post(
        "/settings/devices/lounge_frame/save",
        data={"device_name": "Lounge", "_section": "grp-art-mode", "matte_style": "modern"},
    )
    assert resp.status_code in (302, 303), resp.status_code
    assert resp.headers["Location"].endswith("#grp-art-mode"), resp.headers["Location"]


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


class FakePower:
    """A Home Assistant switch powering the fake TV."""

    def __init__(self, tv: FakeTV, on: bool = True) -> None:
        self.tv, self.on, self.calls = tv, on, []

    def state(self, entity_id: str) -> str | None:
        return "on" if self.on else "off"

    fail_turn = False

    def available(self) -> bool:
        return True

    def turn(self, entity_id: str, on: bool) -> bool:
        self.calls.append((entity_id, on))
        if self.fail_turn:
            return False
        self.on = on
        if on:
            self.tv.art_mode = False  # a cold boot may land in TV mode
        return True


def _powered(app: Flask, tv: FakeTV, power: FakePower, quiet: list[bool]) -> SamsungFramePublisher:
    def set_art_mode(self: FakeClient, on: bool) -> None:
        self.tv.calls.append(("set_art_mode", on))
        self.tv.art_mode = on

    FakeClient.set_art_mode = set_art_mode  # type: ignore[attr-defined]
    return SamsungFramePublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=app.config["PUSH_MANAGER"].latest_render_for,
        run_async=False,
        client_factory=lambda host, **kw: FakeClient(tv, host, **kw),
        rest_info_fn=lambda host: {"PowerState": "on"} if power.on else None,
        power=power,
        quiet_fn=lambda device: quiet[0],
        sleep_fn=lambda s: None,
        power_key_fn=lambda *a, **k: None,
        wol_fn=lambda mac, host: None,
    )


def _screen(app: Flask) -> dict[str, Any]:
    return app.config["DEVICE_STATUS"]["office_frame"]["parsed"]


def test_a_push_to_a_switched_off_tv_switches_it_on_in_art_mode(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    power = FakePower(tv, on=False)
    _powered(app, tv, power, [False]).on_push()
    assert power.calls == [("switch.tv_plug", True)]
    assert ("set_art_mode", True) in tv.calls
    assert ("select", "MY_F0001", True) in tv.calls
    assert _screen(app)["screen"] == "art"


def test_quiet_hours_switch_off_a_tv_showing_art_and_hold_frames(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    pub = _powered(app, tv, power, [True])
    pub.poll_once()
    assert power.calls == [("switch.tv_plug", False)]
    assert _screen(app)["screen"] == "off" and _screen(app)["screen_why"] == "quiet hours"
    _plant_render(app, "office_frame", "aaa111")
    pub.on_push()
    assert tv.uploads == []  # held until quiet hours end


def test_quiet_hours_leave_a_tv_that_is_being_watched_on(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    tv.art_mode = False
    power = FakePower(tv, on=True)
    _powered(app, tv, power, [True]).poll_once()
    assert power.calls == []
    assert _screen(app)["screen"] == "on"


def test_end_of_quiet_hours_switches_back_on_only_what_tesserae_switched_off(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    quiet = [True]
    pub = _powered(app, tv, power, quiet)
    pub.poll_once()  # off for the night
    quiet[0] = False
    pub.poll_once()  # morning: back on
    assert power.calls == [("switch.tv_plug", False), ("switch.tv_plug", True)]
    assert _screen(app)["screen"] == "art"
    # Switched off by hand: it stays off until something is pushed.
    power.on = False
    power.calls.clear()
    pub.poll_once()
    assert power.calls == []
    assert _screen(app)["screen"] == "off" and _screen(app)["screen_why"] == "power switch off"


def test_device_card_shows_the_screen_not_a_battery(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    _powered(app, tv, power, [False]).poll_once()
    client = _admin(app)
    html = client.get("/settings/devices/office_frame").get_data(as_text=True)
    assert "Art Mode" in html and ">Screen<" in html


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("switch.tv_plug", True),
        ("input_boolean.tv", True),
        ("", True),
        ("tv plug", False),
        ("sensor.x", False),
    ],
)
def test_power_switch_must_be_an_entity_id(app: Flask, value: str, ok: bool) -> None:
    _make_frame(app)
    assert _device(app).module.validate_config({"power_switch": value})[0] is ok


def test_a_failed_morning_switch_on_is_retried(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    quiet = [True]
    pub = _powered(app, tv, power, quiet)
    pub.poll_once()  # off for the night
    quiet[0] = False
    power.fail_turn = True
    pub.poll_once()  # HA refuses: the flag stays
    assert pub.load_state(_device(app)).get("slept_by_tesserae") is True
    power.fail_turn = False
    pub.poll_once()
    assert power.on and power.calls[-1] == ("switch.tv_plug", True)
    assert "slept_by_tesserae" not in pub.load_state(_device(app))


def test_morning_switch_on_puts_a_tv_mode_boot_into_art_mode(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    quiet = [True]
    pub = _powered(app, tv, power, quiet)
    pub.poll_once()
    quiet[0] = False
    pub.poll_once()  # switched on; FakePower boots it in TV mode; Art Mode goes back on
    assert ("set_art_mode", True) in tv.calls
    assert _screen(app)["screen"] == "art" and _screen(app)["screen_why"] == ""
    assert "needs_art_mode" not in pub.load_state(_device(app))


def test_a_push_that_wakes_the_tv_clears_the_night_flag(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    quiet = [True]
    pub = _powered(app, tv, power, quiet)
    pub.poll_once()  # off for the night
    quiet[0] = False
    _plant_render(app, "office_frame", "aaa111")
    pub.on_push()  # the 08:08 catch-up push wakes it before the poller does
    assert power.on and len(tv.uploads) == 1
    power.on = False  # the owner switches it off by hand later
    power.calls.clear()
    pub.poll_once()
    assert power.calls == []
    assert _screen(app)["screen_why"] == "power switch off"


def test_quiet_hours_leave_a_tv_on_when_its_art_app_does_not_answer(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    tv.fail["connect"] = FrameArtError("art app wedged")
    power = FakePower(tv, on=True)
    _powered(app, tv, power, [True]).poll_once()
    assert power.calls == []


def test_quiet_hours_count_a_tv_that_stops_answering_as_off(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    pub = _powered(app, tv, power, [True])
    pub._rest_info = lambda host: None  # type: ignore[method-assign]
    pub.poll_once()
    pub.poll_once()
    assert power.calls == []  # nothing to switch off; not flagged for the morning
    assert _screen(app)["screen"] == "off" and _screen(app)["screen_why"] == "quiet hours"
    assert "slept_by_tesserae" not in pub.load_state(_device(app))


def test_a_missed_answer_with_the_switch_on_does_not_force_art_mode(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    tv.art_mode = False  # someone is watching
    power = FakePower(tv, on=True)
    pub = _powered(app, tv, power, [False])
    answers = iter([None])
    pub._rest_info = lambda host: next(answers, {"PowerState": "on"})  # type: ignore[method-assign]
    pub.on_push()
    assert power.calls == []
    assert ("set_art_mode", True) not in tv.calls
    assert tv.uploads == []  # being watched: the frame waits for Art Mode


def test_a_tv_that_never_starts_backs_off(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    power = FakePower(tv, on=False)
    clock = [0.0]
    monkeypatch.setattr(samsung_frame.time, "monotonic", lambda: clock[0])
    pub = _powered(app, tv, power, [False])
    pub._rest_info = lambda host: None  # type: ignore[method-assign]
    pub._sleep = lambda s: clock.__setitem__(0, clock[0] + s)  # type: ignore[method-assign]
    pub.on_push()
    assert power.calls == [("switch.tv_plug", True)]
    assert tv.uploads == []
    assert pub._backing_off("office_frame")


def test_a_held_frame_is_sent_after_quiet_hours(app: Flask) -> None:
    _make_frame(app, power_switch="switch.tv_plug", token_secret="tok-1")
    tv = FakeTV()
    power = FakePower(tv, on=True)
    quiet = [True]
    pub = _powered(app, tv, power, quiet)
    pub.poll_once()
    _plant_render(app, "office_frame", "aaa111")
    pub.on_push()
    assert tv.uploads == []
    quiet[0] = False
    pub.poll_once()  # back on, and the missed frame goes
    assert len(tv.uploads) == 1
    assert pub.pending_digest(_device(app)) is None


def test_the_poller_keeps_a_new_pairing_token(app: Flask) -> None:
    _make_frame(app, token_secret="old-token")
    tv = FakeTV()
    tv.issue_token = None
    original = FakeClient.connect

    def connect_and_rotate(self: FakeClient, pairing_timeout: float = 45) -> None:
        original(self, pairing_timeout)
        self.token = "new-token"

    FakeClient.connect = connect_and_rotate  # type: ignore[method-assign]
    try:
        _powered(app, tv, FakePower(tv), [False]).poll_once()
    finally:
        FakeClient.connect = original  # type: ignore[method-assign]
    section = app.config["SETTINGS_STORE"].get_section("devices")["office_frame"]
    assert section["token_secret"] == "new-token"


def test_power_switch_talks_to_home_assistant(app: Flask) -> None:
    calls: list[Any] = []
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    core.get_state = lambda entity_id: {"state": "off"}  # type: ignore[attr-defined]
    core.call_service = lambda *a, **k: calls.append((a, k)) or []  # type: ignore[attr-defined]
    power = samsung_frame.PowerSwitch(app)
    assert power.state("switch.tv_plug") == "off"
    assert power.turn("switch.tv_plug", True)
    assert calls == [(("homeassistant", "turn_on"), {"data": {"entity_id": "switch.tv_plug"}})]
    core.get_state = lambda entity_id: {"state": "unavailable"}  # type: ignore[attr-defined]
    assert power.state("switch.tv_plug") is None

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("HA down")

    core.call_service = boom  # type: ignore[attr-defined]
    core.get_state = boom  # type: ignore[attr-defined]
    assert power.turn("switch.tv_plug", False) is False
    assert power.state("switch.tv_plug") is None


def test_quiet_sleeps_follows_the_quiet_hours_settings(app: Flask) -> None:
    from datetime import datetime, timedelta

    _make_frame(app)
    now = datetime.now().astimezone()
    start = (now - timedelta(hours=1)).strftime("%H:%M")
    end = (now + timedelta(hours=1)).strftime("%H:%M")
    store = app.config["SETTINGS_STORE"]
    base = {
        "quiet_hours_enabled": True,
        "quiet_hours_start": start,
        "quiet_hours_end": end,
        "quiet_hours_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "timezone": "system",
    }
    pub = _publisher(app, FakeTV())
    store.patch_section("app", {**base, "quiet_hours_sleep": True})
    assert pub._quiet_sleeps(_device(app)) is True
    store.patch_section("app", {**base, "quiet_hours_sleep": False})
    assert pub._quiet_sleeps(_device(app)) is False


class OwnPowerTV:
    """A TV with network standby: the Power key puts it in standby, then off
    the network; Wake-on-LAN brings it back in Art Mode."""

    def __init__(self, tv: FakeTV) -> None:
        self.tv, self.state, self.keys, self.wols = tv, "on", 0, 0

    def rest(self, host: str) -> dict[str, Any] | None:
        if self.state == "off":
            return None
        return {
            "PowerState": self.state,
            "networkType": "wireless",
            "wifiMac": "a4:30:7a:f4:a1:f9",
            "modelName": "QN50LS03T",
        }

    def power_key(self, host: str, **kw: Any) -> None:
        self.keys += 1
        self.state = "standby"

    def wol(self, mac: str, host: str | None) -> None:
        assert mac == "a4:30:7a:f4:a1:f9"
        self.wols += 1
        self.state = "on"
        self.tv.art_mode = True


def _own_power(app: Flask, tv: FakeTV, own: OwnPowerTV, quiet: list[bool]) -> SamsungFramePublisher:
    pub = _powered(app, tv, FakePower(tv), quiet)
    pub._rest_info = own.rest  # type: ignore[method-assign]
    pub._power_key = own.power_key  # type: ignore[method-assign]
    pub._wol = own.wol  # type: ignore[method-assign]
    return pub


def test_tvs_own_power_sleeps_and_wakes_for_quiet_hours(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1")  # no switch: the TV's own power
    tv = FakeTV()
    own = OwnPowerTV(tv)
    quiet = [False]
    pub = _own_power(app, tv, own, quiet)
    pub.poll_once()  # learns the MAC while on
    assert pub.load_state(_device(app))["mac"] == "a4:30:7a:f4:a1:f9"
    quiet[0] = True
    pub.poll_once()
    assert own.keys == 1 and own.state == "standby"
    assert _screen(app)["screen_why"] == "quiet hours"
    own.state = "off"  # it leaves the network
    pub.poll_once()
    assert own.keys == 1
    quiet[0] = False
    pub.poll_once()  # morning: Wake-on-LAN
    assert own.wols == 1 and own.state == "on"
    assert _screen(app)["screen"] == "art"


def test_tvs_own_power_wakes_for_a_push(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1")
    tv = FakeTV()
    own = OwnPowerTV(tv)
    pub = _own_power(app, tv, own, [False])
    pub.poll_once()  # learns the MAC
    own.state = "standby"
    _plant_render(app, "office_frame", "aaa111")
    pub.on_push()
    assert own.wols >= 1
    assert ("select", "MY_F0001", True) in tv.calls


def test_tvs_own_power_needs_the_mac_before_it_can_wake(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1")
    tv = FakeTV()
    own = OwnPowerTV(tv)
    own.state = "off"
    pub = _own_power(app, tv, own, [False])
    _plant_render(app, "office_frame", "aaa111")
    pub.on_push()
    assert own.wols == 0 and tv.uploads == []
    assert pub._backing_off("office_frame")


def test_a_tv_off_by_hand_stays_off_with_its_own_power(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1")
    tv = FakeTV()
    own = OwnPowerTV(tv)
    pub = _own_power(app, tv, own, [False])
    pub.poll_once()
    own.state = "standby"  # the remote's Power button
    pub.poll_once()
    assert own.wols == 0
    assert _screen(app)["screen"] == "off" and _screen(app)["screen_why"] == "standby"


def test_power_control_none_leaves_the_tv_alone(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1", power_control="none")
    tv = FakeTV()
    own = OwnPowerTV(tv)
    pub = _own_power(app, tv, own, [True])
    pub.poll_once()
    pub.poll_once()
    assert own.keys == 0 and own.state == "on"


def test_switch_mode_needs_home_assistant(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1", power_control="switch", power_switch="switch.tv_plug")
    pub = _publisher(app, FakeTV())
    assert pub._mode(pub._cfg("office_frame")) == "tv"  # no Home Assistant connection


def _in_use_tv(app: Flask, setting: str | None) -> tuple[SamsungFramePublisher, FakeTV, OwnPowerTV]:
    extra = {"sleep_when_in_use": setting} if setting else {}
    _make_frame(app, token_secret="tok-1", **extra)
    tv = FakeTV()
    tv.art_mode = False  # TV mode at quiet-hours start
    own = OwnPowerTV(tv)
    return _own_power(app, tv, own, [True]), tv, own


def test_a_tv_still_in_use_sleeps_after_the_chosen_time(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub, _tv, own = _in_use_tv(app, "30")
    clock = [1_000_000.0]
    monkeypatch.setattr(samsung_frame.time, "time", lambda: clock[0])
    pub.poll_once()
    assert own.keys == 0
    assert _screen(app)["screen_why"] == "in use in quiet hours"
    clock[0] += 29 * 60
    pub.poll_once()
    assert own.keys == 0
    clock[0] += 2 * 60
    pub.poll_once()
    assert own.keys == 1 and own.state == "standby"
    state = pub.load_state(_device(app))
    assert state["slept_by_tesserae"] is True and "in_use_since" not in state


def test_the_default_is_one_hour(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    pub, _tv, own = _in_use_tv(app, None)
    clock = [1_000_000.0]
    monkeypatch.setattr(samsung_frame.time, "time", lambda: clock[0])
    pub.poll_once()
    clock[0] += 59 * 60
    pub.poll_once()
    assert own.keys == 0
    clock[0] += 2 * 60
    pub.poll_once()
    assert own.keys == 1


def test_leave_it_on_never_sleeps_a_tv_in_use(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    pub, _tv, own = _in_use_tv(app, "never")
    clock = [1_000_000.0]
    monkeypatch.setattr(samsung_frame.time, "time", lambda: clock[0])
    pub.poll_once()
    clock[0] += 10 * 3600
    pub.poll_once()
    assert own.keys == 0 and own.state == "on"


def test_art_mode_during_quiet_hours_resets_the_in_use_timer(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    pub, tv, own = _in_use_tv(app, "30")
    clock = [1_000_000.0]
    monkeypatch.setattr(samsung_frame.time, "time", lambda: clock[0])
    pub.poll_once()
    assert pub.load_state(_device(app))["in_use_since"] == 1_000_000
    tv.art_mode = True  # back to Art Mode: sleeps at once
    clock[0] += 60
    pub.poll_once()
    assert own.keys == 1
    assert "in_use_since" not in pub.load_state(_device(app))


@pytest.mark.parametrize(
    ("value", "ok"), [("never", True), ("30", True), ("60", True), ("45", False)]
)
def test_sleep_when_in_use_choices(app: Flask, value: str, ok: bool) -> None:
    _make_frame(app)
    assert _device(app).module.validate_config({"sleep_when_in_use": value})[0] is ok


def _token_of(app: Flask) -> str | None:
    cfg = (app.config["SETTINGS_STORE"].get_section("devices") or {}).get("office_frame") or {}
    return SamsungFramePublisher._token(cfg)


def test_first_pairing_keeps_the_token_while_the_tv_is_in_use(app: Flask) -> None:
    # Allow is pressed with the TV showing a programme: the send waits, but the
    # token is stored, so the next attempt doesn't prompt again.
    _make_frame(app)
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    tv.art_mode = False
    _publisher(app, tv).on_push()
    assert tv.uploads == []
    assert _token_of(app) == "tok-1"


def test_a_wired_tv_keeps_no_mac_and_isnt_put_to_sleep(app: Flask) -> None:
    # wifiMac on a wired TV is the radio's, which Wake-on-LAN can't reach:
    # sending it to standby would leave it there.
    _make_frame(app, token_secret="tok-1")
    tv = FakeTV()
    own = OwnPowerTV(tv)
    real_rest = own.rest
    own.rest = lambda host: {**(real_rest(host) or {}), "networkType": "wired"}  # type: ignore[method-assign]
    quiet = [False]
    pub = _own_power(app, tv, own, quiet)
    pub.poll_once()
    assert not pub.load_state(_device(app)).get("mac")
    quiet[0] = True
    pub.poll_once()
    assert own.keys == 0 and own.state == "on"


def test_an_unpaired_tv_isnt_sent_the_power_key(app: Flask) -> None:
    _make_frame(app)  # no token: the remote channel would ask for Allow
    tv = FakeTV()
    own = OwnPowerTV(tv)
    quiet = [False]
    pub = _own_power(app, tv, own, quiet)
    pub.poll_once()
    quiet[0] = True
    pub.update_state(_device(app), {"in_use_since": 1})  # long overdue
    pub.poll_once()
    assert own.keys == 0


def test_a_tv_that_stays_unreachable_is_recorded_once(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1")
    pub = _publisher(app, FakeTV(), rest=None)
    pub.poll_once()
    first = app.config["DEVICE_STATUS"]["office_frame"]["received_at"]
    pub.poll_once()
    pub.poll_once()
    assert app.config["DEVICE_STATUS"]["office_frame"]["received_at"] == first
    assert _screen(app)["screen_why"] == "not answering"


def test_removal_reads_the_tv_settings_before_they_are_wiped(app: Flask) -> None:
    _make_frame(app, token_secret="tok-1")
    _plant_render(app, "office_frame", "aaa111")
    tv = FakeTV()
    pub = _publisher(app, tv)
    pub.on_push()
    device = _device(app)
    cfg, state = dict(pub._cfg(device.id)), pub.load_state(device)
    app.config["SETTINGS_STORE"].patch_section("devices", {"office_frame": {}})
    pub.remove_uploads(device, cfg, state)
    assert ("delete", ["MY_F0001"]) in tv.calls


def test_ipv6_addresses_are_refused() -> None:
    from devices.samsung_frame.device import valid_host

    assert valid_host("192.0.2.10")
    assert valid_host("frame.local")
    assert not valid_host("fd00::5")
