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

    # Two publishes, one per renderer, retention follows the manifest.
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


def test_push_skips_publish_when_composition_matches_last_served(
    tmp_path: Path, composition_png: bytes
) -> None:
    """v0.71.x content-checksum skip: when the newly-rendered composition
    matches every bound device's last-served composition_digest, don't
    publish. The panel isn't asked to re-paint, which is where the
    battery win is."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    with patch("app.push.render_to_png", return_value=png):
        first = manager.push("home")
    assert first.status == "sent"
    publishes_after_first = len(mqtt_client.published)
    assert publishes_after_first == 1

    with patch("app.push.render_to_png", return_value=png):
        second = manager.push("home")

    assert second.status == "no_change"
    assert all(r.unchanged and r.error is None for r in second.renderers)
    # No additional publish for the same digest.
    assert len(mqtt_client.published) == publishes_after_first


def test_push_force_publish_bypasses_content_skip(tmp_path: Path, composition_png: bytes) -> None:
    """Issue #81: a user-initiated Send / Push (send_page / gallery)
    must repaint the panel even when the composition digest is
    bit-identical to the last render (widget data cached, weather value
    unchanged inside its refresh interval, etc.). The content-checksum
    skip is only for scheduled / automated refires.

    ``PushManager.push(force_publish=True)`` bypasses the skip and
    fires the publish so the served digest advances, letting the
    device's next /frame poll get a fresh URL instead of a 304."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    with patch("app.push.render_to_png", return_value=png):
        first = manager.push("home")
    assert first.status == "sent"

    with patch("app.push.render_to_png", return_value=png):
        second = manager.push("home", force_publish=True)

    # force_publish=True must publish again even though the composition
    # is bit-identical to the first render.
    assert second.status == "sent"
    assert all(not r.unchanged for r in second.renderers)
    assert len(mqtt_client.published) == 2


def test_push_still_publishes_when_composition_changes(
    tmp_path: Path, composition_png: bytes
) -> None:
    """A change to the rendered PNG produces a new digest, which
    doesn't match the cached one, so the publish fires again."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    with patch("app.push.render_to_png", return_value=png):
        manager.push("home")

    # A different image → different digest.
    img = Image.new("RGB", (100, 100), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    changed_png = buf.getvalue()

    with patch("app.push.render_to_png", return_value=changed_png):
        second = manager.push("home")

    assert second.status == "sent"
    assert all(not r.unchanged for r in second.renderers)
    assert len(mqtt_client.published) == 2


def test_push_not_found_when_page_missing(tmp_path: Path, composition_png: bytes) -> None:
    manager, _, _ = _wired(tmp_path, composition_png, [])
    result = manager.push("does_not_exist")
    assert result.status == "not_found"


def test_push_image_supersedes_older_pending_for_same_device(
    tmp_path: Path, composition_png: bytes
) -> None:
    """v0.69 coalescing: two rapid-fire pushes for the same device
    (with bypass_coalesce=False) mean the earlier one gets
    ``status="superseded"`` while the later one fires. The Send-file
    entry points default to ``bypass_coalesce=True`` (user intent), so
    the test flips that flag to exercise the coalescing path."""
    import threading
    import time

    renderers = [_make_renderer(tmp_path, "esp32_bin", "bin", retain=False)]
    manager, _, _ = _wired(tmp_path, composition_png, renderers)
    # Hold the processing lock so both pushes queue. Both threads will
    # bump the device generation and then block on _lock.acquire().
    manager._lock.acquire()
    results: dict[str, object] = {}

    def fire(label: str) -> None:
        results[label] = manager.push_image(
            composition_png,
            source_label=label,
            device_id="esp32",
            bypass_coalesce=False,
        )

    t1 = threading.Thread(target=fire, args=("earlier",))
    t2 = threading.Thread(target=fire, args=("later",))
    t1.start()
    time.sleep(0.1)  # let t1 bump gen to 1 and block on lock
    t2.start()
    time.sleep(0.1)  # let t2 bump gen to 2 and block on lock
    manager._lock.release()
    t1.join(timeout=10)
    t2.join(timeout=10)
    # Earlier push (gen 1) is superseded by the later push (gen 2).
    assert results["earlier"].status == "superseded"  # type: ignore[union-attr]
    assert results["later"].status == "sent"  # type: ignore[union-attr]


def test_push_image_bypass_coalesce_always_fires(tmp_path: Path, composition_png: bytes) -> None:
    """User-initiated pushes pass ``bypass_coalesce=True`` (the default)
    so they never get superseded, even when a competing push is already
    queued for the same device."""
    renderers = [_make_renderer(tmp_path, "esp32_bin", "bin", retain=False)]
    manager, _, _ = _wired(tmp_path, composition_png, renderers)
    r1 = manager.push_image(
        composition_png,
        source_label="first",
        device_id="esp32",
        bypass_coalesce=True,
    )
    r2 = manager.push_image(
        composition_png,
        source_label="second",
        device_id="esp32",
        bypass_coalesce=True,
    )
    # Sequential (both serialise on _lock), both enter the pipeline,
    # both re-publish. ``push_image`` defaults to ``force_publish=True``
    # (issue #81): user-initiated Send flows must always paint the
    # panel, even when the image bytes match the last render.
    assert r1.status == "sent"
    assert r2.status == "sent"


def test_supersede_records_event_log_row(tmp_path: Path, composition_png: bytes) -> None:
    """Superseded pushes still write an event row so History can chip
    them, mirroring the old ``busy`` semantics on the observability
    side."""
    import threading
    import time

    renderers = [_make_renderer(tmp_path, "esp32_bin", "bin", retain=False)]
    manager, _, _ = _wired(tmp_path, composition_png, renderers)
    manager._lock.acquire()

    def fire(label: str, results: list[object]) -> None:
        results.append(
            manager.push_image(
                composition_png,
                source_label=label,
                device_id="esp32",
                bypass_coalesce=False,
            )
        )

    results: list[object] = []
    t1 = threading.Thread(target=fire, args=("earlier", results))
    t2 = threading.Thread(target=fire, args=("later", results))
    t1.start()
    time.sleep(0.1)
    t2.start()
    time.sleep(0.1)
    manager._lock.release()
    t1.join(timeout=10)
    t2.join(timeout=10)
    superseded_rows = [
        row for row in manager._event_log.list(limit=20) if row.status == "superseded"
    ]
    assert len(superseded_rows) == 1
    assert superseded_rows[0].source == "file"


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


def test_prune_orphan_renders_keeps_referenced_drops_the_rest(
    tmp_path: Path, composition_png: bytes
) -> None:
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, _, _ = _wired(tmp_path, composition_png, renderers)
    renders = manager._renders_dir

    # One digest still referenced by an event; everything else is dead.
    manager._event_log.record(
        type="push", source="page", target="home", status="sent", digest="keepme123456"
    )
    (renders / "keepme123456.png").write_bytes(b"x")  # referenced -> keep
    (renders / "orphan99999999.png").write_bytes(b"x")  # no event -> drop
    (renders / "orphan99999999.bin").write_bytes(b"x")  # no event -> drop
    (renders / "thumb_home_deadbeef.png").write_bytes(b"x")  # dead thumbnail -> drop

    removed = manager.prune_orphan_renders()
    assert removed == 3
    assert (renders / "keepme123456.png").exists()
    assert not (renders / "orphan99999999.png").exists()
    assert not (renders / "orphan99999999.bin").exists()
    assert not (renders / "thumb_home_deadbeef.png").exists()


def test_multi_device_page_renders_once_per_panel_and_routes(
    tmp_path: Path, composition_png: bytes
) -> None:
    """A page bound to two instances of differing aspect renders once per
    distinct panel, and each frame fans out only to that instance's
    renderer clone (no cross-talk between the two displays)."""
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
    # Landscape (800x480) + portrait (480x800), two distinct panels.
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_land",
        kind_id="esp32_client",
    )
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_port",
        kind_id="esp32_client",
        orientation="portrait",
    )

    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(
        Page(id="multi", name="Multi", device_ids=["esp32_land", "esp32_port"], cells=[])
    )

    fakes: dict[str, _FakeMqttClient] = {}

    def factory(client_id: str) -> _FakeMqttClient:
        fakes["client"] = _FakeMqttClient(client_id)
        return fakes["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()

    manager = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.render_to_png", return_value=composition_png) as rtp:
        result = manager.push("multi")

    assert result.status == "sent"
    # Rendered once per distinct panel, not once per device.
    assert rtp.call_count == 2
    sizes = {(c.args[0].viewport_w, c.args[0].viewport_h) for c in rtp.call_args_list}
    assert sizes == {(800, 480), (480, 800)}
    # Each render fetched the composer at its own panel override.
    urls = [c.args[0].url for c in rtp.call_args_list]
    assert any("w=800&h=480" in u for u in urls)
    assert any("w=480&h=800" in u for u in urls)

    # One publish per instance clone, each frame lands only on its device.
    mqtt_client = fakes["client"]
    topics = [t for t, *_ in mqtt_client.published]
    assert sorted(topics) == [
        "tesserae/esp32_land/frame/bin",
        "tesserae/esp32_port/frame/bin",
    ]
    assert {r.renderer_id for r in result.renderers} == {
        "esp32_bin__esp32_land",
        "esp32_bin__esp32_port",
    }


def test_push_device_filter_targets_single_display(tmp_path: Path, composition_png: bytes) -> None:
    """push(page_id, device_ids={X}) renders only X's panel and fans out
    only to X's renderer, even when the page is bound to several displays.
    Filtering to a device the page doesn't target fails cleanly."""
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
    for inst, orient in (("esp32_land", "landscape"), ("esp32_port", "portrait")):
        device_service.create_instance(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id=inst,
            kind_id="esp32_client",
            orientation=orient,
        )

    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(
        Page(id="multi", name="Multi", device_ids=["esp32_land", "esp32_port"], cells=[])
    )

    fakes: dict[str, _FakeMqttClient] = {}

    def factory(client_id: str) -> _FakeMqttClient:
        fakes["client"] = _FakeMqttClient(client_id)
        return fakes["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()
    manager = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.render_to_png", return_value=composition_png) as rtp:
        result = manager.push("multi", device_ids={"esp32_land"})

    assert result.status == "sent"
    # Only the landscape panel rendered, the portrait display was excluded.
    assert rtp.call_count == 1
    topics = [t for t, *_ in fakes["client"].published]
    assert topics == ["tesserae/esp32_land/frame/bin"]
    assert {r.renderer_id for r in result.renderers} == {"esp32_bin__esp32_land"}

    # Filtering to a display the page doesn't target fails (renders nothing).
    with patch("app.push.render_to_png", return_value=composition_png) as rtp2:
        miss = manager.push("multi", device_ids={"esp32_port_not_bound"})
    assert miss.status == "failed"
    assert rtp2.call_count == 0


# -- 0.49.1 regression: HTTP-polled devices don't depend on MQTT --------


def test_http_polled_device_skips_mqtt_publish(tmp_path: Path, composition_png: bytes) -> None:
    """A TRMNL instance is HTTP-polled (``/api/display``), so the push
    pipeline must not require its MQTT topic to publish successfully.
    The frame still lands on disk + in the latest-renders map so the
    next HTTP poll serves it; the MQTT broker is bypassed entirely.

    Regression for 0.49.0, where TRMNL-only setups without a broker
    saw ``RuntimeError: transport not connected; can't publish to
    'tesserae/<id>/frame/trmnl'`` and the panel stayed on its
    placeholder image."""
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
        instance_id="trmnl_abc123",
        kind_id="trmnl_client",
    )

    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(Page(id="trmnl_only", name="TRMNL only", device_ids=["trmnl_abc123"], cells=[]))

    fakes: dict[str, _FakeMqttClient] = {}

    def factory(client_id: str) -> _FakeMqttClient:
        fakes["client"] = _FakeMqttClient(client_id)
        return fakes["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()

    manager = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.render_to_png", return_value=composition_png):
        result = manager.push("trmnl_only")

    assert result.status == "sent"
    # The MQTT transport was connected but nothing should have been
    # published; TRMNL reads frames via /api/display, not subscriptions.
    assert fakes["client"].published == []
    # The artifact was still written to disk so /api/display can find
    # it, and the latest-renders map has the digest.
    renders = list((tmp_path / "renders").iterdir())
    assert renders, "frame artifact should still land on disk"
    assert manager._latest_renders.get("trmnl_abc123") is not None


def test_http_polled_push_succeeds_without_broker_connection(
    tmp_path: Path, composition_png: bytes
) -> None:
    """The original bug shape: no Mosquitto add-on installed means the
    transport never connects, so ``publish()`` raises ``RuntimeError``.
    Pre-fix this killed every TRMNL push; post-fix the HTTP-polled
    branch never touches the transport, so the push succeeds end-to-end
    with the transport in its initial unconnected state.

    Reproduces tommerty's report on
    https://github.com/dmellok/tesserae/discussions/8."""
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
        instance_id="trmnl_def456",
        kind_id="trmnl_client",
    )

    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(Page(id="solo", name="Solo", device_ids=["trmnl_def456"], cells=[]))

    # Construct the transport but never .connect() it; this is the state
    # the app boots into when ``core-mosquitto`` resolves to nothing.
    transport = MqttTransport(
        BrokerConfig(host="x"), client_factory=lambda _id: _FakeMqttClient(_id)
    )
    # Sanity-check the precondition: the transport is unconnected, so a
    # raw publish would raise. The TRMNL render path must dodge that.
    with pytest.raises(RuntimeError):
        transport.publish("any", b"any")

    manager = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.render_to_png", return_value=composition_png):
        result = manager.push("solo")

    assert result.status == "sent"
    assert all(r.error is None for r in result.renderers)
