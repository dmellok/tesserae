"""PushManager: end-to-end with a stub renderer and a fake transport.

The Playwright screenshot path (``render_to_png``) is patched out so the
suite never has to launch Chromium; we feed a synthetic composition PNG and
verify the renderer + transport + disk-write hooks fire in the right order
with the right values."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from PIL import Image

from app.push import PushManager
from app.renderer_loader import Renderer, RendererRegistry
from app.state.event_log import EventLog
from app.state.page_store import Page, PageStore, Panel
from app.state.settings_store import SettingsStore
from app.transport import BrokerConfig, MqttTransport


@pytest.fixture
def composition_png() -> bytes:
    img = Image.new("RGB", (100, 100), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_renderer(tmp_path: Path, rid: str, ext: str, retain: bool) -> Renderer:
    mod = ModuleType(f"_test.{rid}")

    def transform(png_bytes, *, panel, settings):
        return png_bytes + f"::{rid}".encode()

    def payload(digest, base_url, *, settings):
        return {"url": f"{base_url}/renders/{digest}.{ext}", "rid": rid}

    mod.transform = transform  # type: ignore[attr-defined]
    mod.payload = payload  # type: ignore[attr-defined]

    return Renderer(
        id=rid,
        path=tmp_path / rid,
        manifest={
            "tesserae_compat": "1.x",
            "name": rid,
            "version": "0.0.1",
            "orientation": "composition",
            "mime": "application/octet-stream",
            "extension": ext,
            "topic_pattern": f"tesserae/{{device}}/frame/{ext}",
            # pi_bin / pi_png each have their own device id (post-split);
            # esp32_* renderers all live under the shared 'esp32' kind.
            "device": "esp32" if rid.startswith("esp32") else rid,
            "retain": retain,
        },
        module=mod,
        data_dir=tmp_path / "data" / rid,
    )


class _FakeMqttClient:
    def __init__(self, client_id: str) -> None:
        self.published: list[tuple[str, bytes, int, bool]] = []
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

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))
        return type("R", (), {"rc": 0})()

    def subscribe(self, *a, **kw):
        return (0, 1)


def _wired(tmp_path: Path, composition_png: bytes, renderers: list[Renderer]):
    page_store = PageStore(tmp_path / "pages.json")
    page = Page(
        id="home",
        name="Home",
        panel=Panel(w=100, h=100),
        cells=[],
    )
    page_store.save(page)

    registry = RendererRegistry(renderers={r.id: r for r in renderers})

    fakes = {}

    def factory(client_id: str):
        fakes["client"] = _FakeMqttClient(client_id)
        return fakes["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()

    manager = PushManager(
        registry=registry,
        page_store=page_store,
        transport=transport,
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
    )

    return manager, fakes["client"], composition_png


def test_push_fans_out_to_every_renderer(tmp_path: Path, composition_png: bytes) -> None:
    renderers = [
        _make_renderer(tmp_path, "pi_png", "png", retain=False),
        _make_renderer(tmp_path, "esp32_bin", "bin", retain=True),
    ]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    with patch("app.push.render_to_png", return_value=png):
        result = manager.push("home")

    assert result.status == "sent"
    assert {r.renderer_id for r in result.renderers} == {"pi_png", "esp32_bin"}
    assert all(r.error is None for r in result.renderers)

    # Both renderers' artifacts on disk.
    written = sorted(p.name for p in (tmp_path / "renders").iterdir())
    assert any(name.endswith(".png") for name in written)
    assert any(name.endswith(".bin") for name in written)

    # Two publishes — one per renderer, retention follows the manifest.
    topics = [t for t, *_ in mqtt_client.published]
    assert "tesserae/pi_png/frame/png" in topics
    assert "tesserae/esp32/frame/bin" in topics
    retains = {t: retain for t, _p, _q, retain in mqtt_client.published}
    assert retains["tesserae/pi_png/frame/png"] is False
    assert retains["tesserae/esp32/frame/bin"] is True

    # Payload bytes are JSON with the URL key the renderer built.
    for _topic, payload, _qos, _retain in mqtt_client.published:
        decoded = json.loads(payload.decode("utf-8"))
        assert decoded["url"].startswith("http://broker.local:8000/renders/")


def test_push_not_found_when_page_missing(tmp_path: Path, composition_png: bytes) -> None:
    manager, _, _ = _wired(tmp_path, composition_png, [])
    result = manager.push("does_not_exist")
    assert result.status == "not_found"


def test_push_busy_when_already_in_flight(tmp_path: Path, composition_png: bytes) -> None:
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, _, _ = _wired(tmp_path, composition_png, renderers)
    # Take the internal lock so the next call sees busy.
    manager._lock.acquire()
    try:
        result = manager.push("home")
    finally:
        manager._lock.release()
    assert result.status == "busy"


def test_failing_renderer_marks_push_failed_but_others_still_publish(
    tmp_path: Path, composition_png: bytes
) -> None:
    good = _make_renderer(tmp_path, "pi_png", "png", retain=False)

    bad_mod = ModuleType("_test.bad")

    def transform(png_bytes, *, panel, settings):
        raise RuntimeError("kaboom")

    def payload(digest, base_url, *, settings):
        return {"url": "x"}

    bad_mod.transform = transform  # type: ignore[attr-defined]
    bad_mod.payload = payload  # type: ignore[attr-defined]

    bad = Renderer(
        id="esp32_bin",
        path=tmp_path / "esp32_bin",
        manifest={
            "tesserae_compat": "1.x",
            "name": "esp32_bin",
            "version": "0.0.1",
            "orientation": "composition",
            "mime": "application/octet-stream",
            "extension": "bin",
            "topic_pattern": "tesserae/{device}/frame/bin",
            "device": "esp32",
            "retain": True,
        },
        module=bad_mod,
        data_dir=tmp_path / "data" / "esp32_bin",
    )

    manager, mqtt_client, png = _wired(tmp_path, composition_png, [good, bad])
    with patch("app.push.render_to_png", return_value=png):
        result = manager.push("home")

    assert result.status == "failed"
    per = {r.renderer_id: r for r in result.renderers}
    assert per["pi_png"].error is None
    assert per["esp32_bin"].error is not None
    assert "kaboom" in per["esp32_bin"].error

    # The good renderer's publish still happened.
    topics = [t for t, *_ in mqtt_client.published]
    assert topics == ["tesserae/pi_png/frame/png"]
