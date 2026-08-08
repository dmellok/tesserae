"""Deck pre-render cache in PushManager: warm silently, promote instantly.

Mirrors test_push's device-targeted harness (real device kinds + renderer
clones, Playwright capture patched out) so warming exercises the real render
path without launching Chromium.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app.push import PushManager
from app.state.event_log import EventLog
from app.state.page_store import Page, PageStore
from app.state.settings_store import SettingsStore
from app.transport import BrokerConfig, MqttTransport


def _png(colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), colour).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def wired(tmp_path: Path):
    from app import device_loader, device_service, renderer_loader
    from app.main import REPO_ROOT

    data_root = tmp_path / "devices"
    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=data_root,
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path / "rdata",
    )
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="panel",
        kind_id="esp32_client",
        orientation="landscape",
    )
    page_store = PageStore(tmp_path / "pages.json")
    # Two pages, both bound to the device, as a deck's pages would be.
    page_store.save(Page(id="p_a", name="A", device_ids=["panel"], cells=[]))
    page_store.save(Page(id="p_b", name="B", device_ids=["panel"], cells=[]))

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=lambda cid: _FakeClient())
    transport.connect()
    settings = SettingsStore(tmp_path / "settings.json")
    manager = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=settings,
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://x:8000",
        devices=devices,
    )
    return manager


class _FakeClient:
    def __init__(self) -> None:
        self.on_connect = self.on_disconnect = self.on_message = None

    def username_pw_set(self, *a, **kw):
        pass

    def connect(self, *a, **kw):
        return 0

    def disconnect(self):
        return 0

    def loop_start(self):
        return 0

    def loop_stop(self):
        return 0

    def publish(self, *a, **kw):
        return type("R", (), {"rc": 0})()

    def subscribe(self, *a, **kw):
        return (0, 1)


def test_warm_does_not_touch_live_frame(wired) -> None:
    manager = wired
    # Device is currently showing page A.
    with patch("app.push.capture_composed", return_value=(_png((255, 0, 0)), [])):
        manager.push("p_a", device_ids={"panel"})
    live_before = dict(manager.latest_render_for("panel"))

    # Warm page B: it renders (different bytes) but the live frame stays on A.
    with patch("app.push.capture_composed", return_value=(_png((0, 0, 255)), [])):
        assert manager.warm_deck_page("p_b", "panel") is True

    assert manager.has_warm_deck_page("panel", "p_b")
    live_after = manager.latest_render_for("panel")
    assert live_after["digest"] == live_before["digest"]  # unchanged


def test_promote_swaps_in_the_warmed_frame(wired) -> None:
    manager = wired
    with patch("app.push.capture_composed", return_value=(_png((255, 0, 0)), [])):
        manager.push("p_a", device_ids={"panel"})
    a_digest = manager.latest_render_for("panel")["digest"]

    with patch("app.push.capture_composed", return_value=(_png((0, 0, 255)), [])):
        manager.warm_deck_page("p_b", "panel")
    b_digest = manager._deck_renders["panel"]["p_b"]["digest"]
    assert b_digest != a_digest

    # Promote B: the live frame is now B, with no new render.
    with patch("app.push.capture_composed", side_effect=AssertionError("must not render")):
        assert manager.promote_deck_page("panel", "p_b") is True
    assert manager.latest_render_for("panel")["digest"] == b_digest


def test_promote_miss_returns_false(wired) -> None:
    assert wired.promote_deck_page("panel", "never_warmed") is False


def test_warmed_frame_is_gc_protected(wired) -> None:
    manager = wired
    with patch("app.push.capture_composed", return_value=(_png((0, 0, 255)), [])):
        manager.warm_deck_page("p_b", "panel")
    entry = manager._deck_renders["panel"]["p_b"]
    # A warmed frame's artifact + composition digests count as live, so the
    # prune keeps them even after the event rows that referenced them age out.
    live = manager._live_digests()
    assert entry["digest"] in live
    assert entry["composition_digest"] in live
    manager.prune_orphan_renders()
    assert next(manager._renders_dir.glob(f"{entry['digest']}.*")).exists()


def test_clear_deck_cache(wired) -> None:
    manager = wired
    with patch("app.push.capture_composed", return_value=(_png((0, 0, 255)), [])):
        manager.warm_deck_page("p_a", "panel")
        manager.warm_deck_page("p_b", "panel")
    manager.clear_deck_cache("panel", keep_pages={"p_a"})
    assert manager.has_warm_deck_page("panel", "p_a")
    assert not manager.has_warm_deck_page("panel", "p_b")
    manager.clear_deck_cache("panel")
    assert not manager.has_warm_deck_page("panel", "p_a")


def test_forget_device_clears_the_live_and_warmed_frames(wired) -> None:
    """Deleting a device with the wipe option must leave no frame behind, or a
    device registered again under the same id is served the pre-wipe frame
    (issue #199). Warmed deck frames go too: the first navigation after a
    re-register would otherwise promote one into the live slot."""
    manager = wired
    with patch("app.push.capture_composed", return_value=(_png((255, 0, 0)), [])):
        manager.push("p_a", device_ids={"panel"})
    with patch("app.push.capture_composed", return_value=(_png((0, 0, 255)), [])):
        assert manager.warm_deck_page("p_b", "panel") is True
    assert manager.latest_render_for("panel") is not None

    assert manager.forget_device("panel") is True
    assert manager.latest_render_for("panel") is None
    assert manager.has_warm_deck_page("panel", "p_b") is False
    # Idempotent: nothing held the second time.
    assert manager.forget_device("panel") is False


def test_forget_device_survives_a_restart(wired, tmp_path) -> None:
    """The latest-render map is persisted, so forgetting has to write through;
    otherwise the pointer comes back with the next process."""
    manager = wired
    with patch("app.push.capture_composed", return_value=(_png((255, 0, 0)), [])):
        manager.push("p_a", device_ids={"panel"})
    manager.forget_device("panel")
    reloaded = manager._load_latest_renders()
    assert "panel" not in reloaded
