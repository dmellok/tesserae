"""PushManager extensions: push_image / push_url_image / push_webpage /
republish / delete_history + event log integration + composition
thumbnail write."""

from __future__ import annotations

import io
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
def panel_png() -> bytes:
    img = Image.new("RGB", (100, 80), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _stub_renderer(
    tmp_path: Path, rid: str, ext: str, retain: bool, device: str = "pi"
) -> Renderer:
    mod = ModuleType(f"_test.{rid}")

    def transform(png_bytes, *, panel, settings):
        return png_bytes + f"::{rid}".encode()

    def payload(digest, base_url, *, settings):
        return {"url": f"{base_url}/renders/{digest}.{ext}"}

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
            "topic_pattern": f"tesserae/{device}/frame/{ext}",
            "device": device,
            "retain": retain,
        },
        module=mod,
        data_dir=tmp_path / "data" / rid,
    )


class _FakeMqttClient:
    on_connect = on_disconnect = on_message = None

    def __init__(self, *a, **kw):
        self.published: list[tuple[str, bytes, int, bool]] = []

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


def _wire(tmp_path: Path, panel_png: bytes, renderers: list[Renderer] | None = None):
    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(Page(id="home", name="Home", panel=Panel(w=100, h=80), cells=[]))
    settings = SettingsStore(tmp_path / "settings.json")
    settings.update_section("app", {"panel_w": 100, "panel_h": 80})
    event_log = EventLog(tmp_path / "events.db")

    factories = {"client": None}

    def factory(client_id: str):
        factories["client"] = _FakeMqttClient(client_id)
        return factories["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()

    if renderers is None:
        renderers = [_stub_renderer(tmp_path, "pi_png", "png", False)]
    registry = RendererRegistry(renderers={r.id: r for r in renderers})

    manager = PushManager(
        registry=registry,
        page_store=page_store,
        transport=transport,
        settings=settings,
        event_log=event_log,
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
    )
    return manager, event_log, factories["client"], tmp_path / "renders"


def test_push_image_writes_composition_thumbnail(tmp_path: Path, panel_png: bytes) -> None:
    manager, event_log, _client, renders_dir = _wire(tmp_path, panel_png)
    result = manager.push_image(panel_png, source_label="hello.png")
    assert result.status == "sent"
    assert result.composition_digest is not None
    # Composition PNG is the thumbnail, must land on disk.
    assert (renders_dir / f"{result.composition_digest}.png").exists()
    # Event log row recorded with source='file'.
    rows = event_log.list(type="push")
    assert len(rows) == 1
    assert rows[0].source == "file"
    assert rows[0].target == "hello.png"
    assert rows[0].digest == result.composition_digest


def test_push_webpage_logs_failure_when_renderer_breaks(tmp_path: Path, panel_png: bytes) -> None:
    manager, event_log, _client, _ = _wire(tmp_path, panel_png)
    # Patch render_to_png at the import site PushManager uses.
    with patch("app.push.render_to_png", side_effect=RuntimeError("playwright kaboom")):
        result = manager.push_webpage("https://example.com")
    assert result.status == "failed"
    assert "playwright kaboom" in (result.error or "")
    # Failed render still records an event row.
    rows = event_log.list(type="push")
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].source == "webpage"


def test_republish_reuses_stored_composition(tmp_path: Path, panel_png: bytes) -> None:
    manager, event_log, client, _ = _wire(tmp_path, panel_png)
    first = manager.push_image(panel_png, source_label="src.png")
    assert first.status == "sent"
    # Clear published log so we can see the resend land.
    client.published.clear()
    resend = manager.republish(first.event_id)  # type: ignore[arg-type]
    assert resend.status == "sent"
    # Resend writes its own event row tagged source="resend".
    rows = event_log.list(type="push")
    assert rows[0].source == "resend"
    assert rows[0].target == "src.png"
    # Companion 0.4 job correlation can use this exact returned ID; it
    # never needs to infer the new row from timestamps or composition data.
    assert resend.event_id == rows[0].id
    # The original target is preserved as the resend's target.
    assert len(client.published) == 1


def test_republish_replays_original_device_targets(tmp_path: Path, panel_png: bytes) -> None:
    """#119: a resend must fan out to the devices the original push hit.

    An unbound fan-out skips per-device clone renderers (#83), so before
    the fix a resend to a device served by a clone updated nothing: the
    device's latest-render entry kept pointing at the newer frame and its
    /frame poll answered 304 against the resent frame's differing ETag."""
    clone = _stub_renderer(tmp_path, "pi_png__dev1", "png", False, device="dev1")
    manager, event_log, _client, _ = _wire(tmp_path, panel_png, renderers=[clone])

    first = manager.push_image(panel_png, source_label="gallery.png", device_id="dev1")
    assert first.status == "sent"
    other = Image.new("RGB", (100, 80), (10, 20, 30))
    buf = io.BytesIO()
    other.save(buf, format="PNG")
    second = manager.push_image(buf.getvalue(), source_label="calendar.png", device_id="dev1")
    assert second.status == "sent"
    assert manager.latest_render_for("dev1")["composition_digest"] == second.composition_digest

    resend = manager.republish(first.event_id)  # type: ignore[arg-type]
    assert resend.status == "sent"
    latest = manager.latest_render_for("dev1")
    assert latest["composition_digest"] == first.composition_digest
    # REST clients get one forced 200 so the panel repaints (v0.149.5).
    assert latest["force_refetch"] is True
    rows = event_log.list(type="push")
    assert rows[0].source == "resend"
    assert rows[0].extra["device_ids"] == ["dev1"]


def test_republish_preserves_image_fit_and_can_narrow_original_targets(
    tmp_path: Path, panel_png: bytes
) -> None:
    renderers = [
        _stub_renderer(tmp_path, "pi_png__dev1", "png", False, device="dev1"),
        _stub_renderer(tmp_path, "pi_png__dev2", "png", False, device="dev2"),
    ]
    manager, event_log, _client, _ = _wire(tmp_path, panel_png, renderers=renderers)

    first = manager._fan_out(
        panel_png,
        {"w": 100, "h": 80},
        source="file",
        target="shared.png",
        started=0.0,
        device_filters={"dev1", "dev2"},
        image_fit="blur",
    )
    assert first.status == "sent"
    original = event_log.get(first.event_id)  # type: ignore[arg-type]
    assert original is not None
    assert original.extra["fit"] == "blur"

    resend = manager.republish(first.event_id, device_ids={"dev2"})  # type: ignore[arg-type]
    assert resend.status == "sent"
    replay = event_log.get(resend.event_id)  # type: ignore[arg-type]
    assert replay is not None
    assert replay.source == "resend"
    assert replay.extra["device_ids"] == ["dev2"]
    assert replay.extra["fit"] == "blur"


def test_republish_fails_when_thumbnail_evicted(tmp_path: Path, panel_png: bytes) -> None:
    manager, _, _, renders_dir = _wire(tmp_path, panel_png)
    first = manager.push_image(panel_png, source_label="src.png")
    (renders_dir / f"{first.composition_digest}.png").unlink()
    resend = manager.republish(first.event_id)  # type: ignore[arg-type]
    assert resend.status == "failed"
    assert "evicted" in (resend.error or "")


def test_delete_history_drops_artifact_when_unreferenced(tmp_path: Path, panel_png: bytes) -> None:
    manager, _, _, renders_dir = _wire(tmp_path, panel_png)
    result = manager.push_image(panel_png, source_label="x.png")
    digest = result.composition_digest
    assert (renders_dir / f"{digest}.png").exists()
    # While the digest is a device's current frame it survives the row
    # delete (the panel is still showing it; its regions sidecar and
    # preview thumbnail must not vanish underneath it).
    assert manager.delete_history(result.event_id) is True  # type: ignore[arg-type]
    assert (renders_dir / f"{digest}.png").exists()
    # Once superseded (no longer any device's latest render) the same
    # delete semantics drop the unreferenced artifact.
    row_id = manager._event_log.record(
        type="push", source="file", target="x.png", status="sent", digest=digest
    )
    manager._latest_renders.clear()
    assert manager.delete_history(row_id) is True
    assert not (renders_dir / f"{digest}.png").exists()


def test_delete_history_keeps_artifact_when_still_referenced(
    tmp_path: Path, panel_png: bytes
) -> None:
    manager, _, _, renders_dir = _wire(tmp_path, panel_png)
    a = manager.push_image(panel_png, source_label="x.png")
    b = manager.push_image(panel_png, source_label="x.png")
    # Identical bytes -> same composition digest -> shared thumbnail.
    assert a.composition_digest == b.composition_digest
    manager.delete_history(a.event_id)  # type: ignore[arg-type]
    # The second push still references it; PNG must stay.
    assert (renders_dir / f"{a.composition_digest}.png").exists()


def test_push_url_image_rejects_non_http_schemes(tmp_path: Path, panel_png: bytes) -> None:
    manager, event_log, _, _ = _wire(tmp_path, panel_png)
    result = manager.push_url_image("file:///etc/passwd")
    assert result.status == "failed"
    assert event_log.list(type="push")[0].error.startswith("fetch:")
