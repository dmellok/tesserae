"""PushManager: end-to-end with a stub renderer and a fake transport.

The Playwright capture path (``capture_composed``) is patched out so the
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


def _distinct_png(colour: tuple[int, int, int]) -> bytes:
    """A solid-colour PNG. Different colours give different digests so a
    test can tell two rendered frames apart."""
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), colour).save(buf, format="PNG")
    return buf.getvalue()


def _make_renderer(
    tmp_path: Path, rid: str, ext: str, retain: bool, device: str | None = None
) -> Renderer:
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
            # ``device`` overrides for tests that bind a renderer clone
            # to a specific device instance (the prewarm tests).
            "device": device or ("esp32" if rid.startswith("esp32") else rid),
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


def _legacy_settings(tmp_path: Path) -> SettingsStore:
    """Settings with the legacy unbound-broadcast opt-in on, so tests that
    Send an unbound 'virtual panel' page still fan out to base renderers
    (the pre-#84 behaviour these fan-out / skip / clone tests target).
    The new default (opt-in off, unbound Send no-ops) is covered by the
    binding-gate tests below."""
    s = SettingsStore(tmp_path / "settings.json")
    s.update_section("app", {"unbound_broadcast": True})
    return s


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
        settings=_legacy_settings(tmp_path),
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

    with patch("app.push.capture_composed", return_value=(png, [])):
        result = manager.push("home")

    assert result.status == "sent"
    assert {r.renderer_id for r in result.renderers} == {"pi_png", "esp32_bin"}
    assert all(r.error is None for r in result.renderers)
    assert all(r.preview_digest is not None for r in result.renderers)
    assert all(
        (tmp_path / "renders" / f"{r.preview_digest}.png").exists() for r in result.renderers
    )

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

    with patch("app.push.capture_composed", return_value=(png, [])):
        first = manager.push("home")
    assert first.status == "sent"
    publishes_after_first = len(mqtt_client.published)
    assert publishes_after_first == 1

    with patch("app.push.capture_composed", return_value=(png, [])):
        second = manager.push("home")

    assert second.status == "no_change"
    assert all(r.unchanged and r.error is None for r in second.renderers)
    # No additional publish for the same digest.
    assert len(mqtt_client.published) == publishes_after_first


def test_no_change_backfills_missing_device_preview_without_repaint(
    tmp_path: Path, composition_png: bytes
) -> None:
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    with patch("app.push.capture_composed", return_value=(png, [])):
        first = manager.push("home")
    assert first.status == "sent"
    latest = manager.latest_render_for("pi_png")
    assert latest is not None
    old_preview = str(latest.pop("preview_digest"))
    (manager._renders_dir / f"{old_preview}.png").unlink()
    publishes = len(mqtt_client.published)

    with patch("app.push.capture_composed", return_value=(png, [])):
        second = manager.push("home")

    assert second.status == "no_change"
    latest = manager.latest_render_for("pi_png")
    assert latest is not None
    preview_digest = str(latest["preview_digest"])
    assert (manager._renders_dir / f"{preview_digest}.png").exists()
    assert len(mqtt_client.published) == publishes


def test_device_preview_failure_does_not_fail_physical_delivery(
    tmp_path: Path, composition_png: bytes
) -> None:
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    with (
        patch("app.push.capture_composed", return_value=(png, [])),
        patch("app.push.write_device_preview", side_effect=OSError("disk full")),
    ):
        result = manager.push("home")

    assert result.status == "sent"
    assert len(mqtt_client.published) == 1
    assert result.renderers[0].preview_digest is None
    latest = manager.latest_render_for("pi_png")
    assert latest is not None
    assert latest["preview_digest"] is None


def test_push_force_publish_bypasses_content_skip(tmp_path: Path, composition_png: bytes) -> None:
    """User-initiated Send / Push (send_page / gallery) repaints the
    panel even when the composition digest is bit-identical to the
    last render (widget data cached, weather value unchanged inside
    its refresh interval, etc.). The content-checksum skip is only
    for scheduled / automated refires.

    ``PushManager.push(force_publish=True)`` bypasses the skip and
    fires the publish so the served digest advances, letting the
    device's next /frame poll get a fresh URL instead of a 304."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    with patch("app.push.capture_composed", return_value=(png, [])):
        first = manager.push("home")
    assert first.status == "sent"

    with patch("app.push.capture_composed", return_value=(png, [])):
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

    with patch("app.push.capture_composed", return_value=(png, [])):
        manager.push("home")

    # A different image → different digest.
    img = Image.new("RGB", (100, 100), (0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    changed_png = buf.getvalue()

    with patch("app.push.capture_composed", return_value=(changed_png, [])):
        second = manager.push("home")

    assert second.status == "sent"
    assert all(not r.unchanged for r in second.renderers)
    assert len(mqtt_client.published) == 2


def test_push_gamut_change_repaints_despite_same_composition(
    tmp_path: Path, composition_png: bytes
) -> None:
    """Issue #81: the content-skip must key on the render inputs, not the
    composition alone. Changing a device's panel gamut (Spectra 6 ->
    ACeP) changes the packed .bin bytes even when the composition PNG is
    byte-identical, so the second push has to re-publish instead of
    skipping and leaving the device on a stale 304.

    The stub renderer emits the same bytes for both gamuts on purpose:
    the skip decision must come from the render *signature* (which folds
    in the panel gamut), not from the artifact digest."""
    renderers = [_make_renderer(tmp_path, "pi_bin", "bin", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    panel_spectra = Panel(w=100, h=100, gamut="waveshare_e6")
    panel_acep = Panel(w=100, h=100, gamut="inky_7colour")

    with (
        patch("app.push.capture_composed", return_value=(png, [])),
        patch("app.push.panel_groups_for_push", return_value=[(panel_spectra, [])]),
    ):
        first = manager.push("home")
    assert first.status == "sent"
    publishes_after_first = len(mqtt_client.published)

    with (
        patch("app.push.capture_composed", return_value=(png, [])),
        patch("app.push.panel_groups_for_push", return_value=[(panel_acep, [])]),
    ):
        second = manager.push("home")

    # Same composition, different gamut: re-publish, don't skip.
    assert second.status == "sent"
    assert all(not r.unchanged and r.error is None for r in second.renderers)
    assert len(mqtt_client.published) == publishes_after_first + 1


def test_push_same_gamut_still_skips(tmp_path: Path, composition_png: bytes) -> None:
    """Guard the optimisation the issue-#81 fix must not regress: an
    unchanged composition AND unchanged panel still skips the re-paint."""
    renderers = [_make_renderer(tmp_path, "pi_bin", "bin", retain=False)]
    manager, mqtt_client, png = _wired(tmp_path, composition_png, renderers)

    panel = Panel(w=100, h=100, gamut="waveshare_e6")
    with (
        patch("app.push.capture_composed", return_value=(png, [])),
        patch("app.push.panel_groups_for_push", return_value=[(panel, [])]),
    ):
        manager.push("home")
        publishes = len(mqtt_client.published)
        second = manager.push("home")

    assert second.status == "no_change"
    assert all(r.unchanged and r.error is None for r in second.renderers)
    assert len(mqtt_client.published) == publishes


def test_unbound_push_over_base_renderers_is_clean_on_brokerless_install(
    tmp_path: Path, composition_png: bytes
) -> None:
    """Issue #67: an unbound dashboard (bound to no device) fans out to
    every base kind renderer. On a broker-less install those renderers
    still call ``transport.publish``; before the host-default fix that
    raised ``RuntimeError('transport not connected')`` and marked the
    whole push failed in history, even though the user's actual REST
    device painted fine. With a broker-less transport (empty host, never
    connected) the publish now no-ops, so the push reports success and no
    renderer carries an error."""
    renderers = [
        _make_renderer(tmp_path, "esp32_bin", "bin", retain=True),
        _make_renderer(tmp_path, "circuitpython_png", "png", retain=False),
        _make_renderer(tmp_path, "pi_png", "png", retain=False),
    ]
    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(Page(id="home", name="Home", panel=Panel(w=100, h=100), cells=[]))
    registry = RendererRegistry(renderers={r.id: r for r in renderers})

    # Broker-less: empty host, transport constructed but never connected,
    # exactly the transport_wiring shape after the host-default fix.
    transport = MqttTransport(
        BrokerConfig(host=""), client_factory=lambda cid: _FakeMqttClient(cid)
    )

    manager = PushManager(
        registry=registry,
        page_store=page_store,
        transport=transport,
        settings=_legacy_settings(tmp_path),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
    )

    with patch("app.push.capture_composed", return_value=(composition_png, [])):
        result = manager.push("home")

    assert result.status == "sent"
    assert result.renderers, "expected the base renderers to fan out"
    assert all(r.error is None for r in result.renderers)


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
    with patch("app.push.capture_composed", return_value=(png, [])):
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
        settings=_legacy_settings(tmp_path),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.capture_composed", return_value=(composition_png, [])) as rtp:
        result = manager.push("multi")

    assert result.status == "sent"
    # Rendered once per distinct panel, not once per device.
    assert rtp.call_count == 2
    sizes = {(c.args[0].render.viewport_w, c.args[0].render.viewport_h) for c in rtp.call_args_list}
    assert sizes == {(800, 480), (480, 800)}
    # Each render fetched the composer at its own panel override.
    urls = [c.args[0].render.url for c in rtp.call_args_list]
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
    assert result.event_id is None
    assert len(result.event_ids) == 2
    assert len(set(result.event_ids)) == 2
    assert all(manager._event_log.get(event_id) is not None for event_id in result.event_ids)


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
        settings=_legacy_settings(tmp_path),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.capture_composed", return_value=(composition_png, [])) as rtp:
        result = manager.push("multi", device_ids={"esp32_land"})

    assert result.status == "sent"
    # Only the landscape panel rendered, the portrait display was excluded.
    assert rtp.call_count == 1
    topics = [t for t, *_ in fakes["client"].published]
    assert topics == ["tesserae/esp32_land/frame/bin"]
    assert {r.renderer_id for r in result.renderers} == {"esp32_bin__esp32_land"}

    # Filtering to a display the page doesn't target fails (renders nothing).
    with patch("app.push.capture_composed", return_value=(composition_png, [])) as rtp2:
        miss = manager.push("multi", device_ids={"esp32_port_not_bound"})
    assert miss.status == "failed"
    assert rtp2.call_count == 0


def test_unbound_push_does_not_overwrite_a_bound_devices_frame(
    tmp_path: Path, composition_png: bytes
) -> None:
    """Issue #83: sending an UNBOUND dashboard must not leak onto a device
    that's bound to a DIFFERENT dashboard.

    Setup: one device bound to page A. Send A (device's clone renderer
    fires, stamps ``_latest_renders[device]``). Then send page B, which is
    bound to no device. Before the fix, the unbound push fanned out to
    every renderer including the device's clone, so B's frame overwrote
    the device's ``_latest_renders`` entry and the device painted B on its
    next /frame poll. After the fix the unbound push skips per-device clone
    renderers, so the device's latest frame still points at A."""
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
        instance_id="esp32_a",
        kind_id="esp32_client",
    )

    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(Page(id="bound", name="Bound", device_ids=["esp32_a"], cells=[]))
    # Unbound page: no device_ids, renders at its own virtual panel.
    # Width a multiple of 8 so the 1-bpp base renderers can pack it.
    page_store.save(Page(id="loose", name="Loose", panel=Panel(w=800, h=480), cells=[]))

    transport = MqttTransport(
        BrokerConfig(host="x"), client_factory=lambda cid: _FakeMqttClient(cid)
    )
    transport.connect()
    manager = PushManager(
        registry=renderers,
        page_store=page_store,
        transport=transport,
        settings=_legacy_settings(tmp_path),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    # Two distinct compositions so the frames are genuinely different.
    png_a = _distinct_png((10, 20, 30))
    png_b = _distinct_png((200, 100, 50))

    with patch("app.push.capture_composed", return_value=(png_a, [])):
        manager.push("bound")
    bound_frame = manager.latest_render_for("esp32_a")
    assert bound_frame is not None
    frame_after_bound = bound_frame["digest"]

    with patch("app.push.capture_composed", return_value=(png_b, [])):
        loose = manager.push("loose")

    # The unbound push renders (it still fans out to base renderers), but
    # must NOT touch the bound device's clone.
    assert loose.status in ("sent", "no_change")
    assert not any(r.renderer_id == "esp32_bin__esp32_a" for r in loose.renderers), (
        "unbound push must not fire the bound device's clone renderer"
    )
    # The device's latest frame still points at dashboard A.
    still = manager.latest_render_for("esp32_a")
    assert still is not None
    assert still["digest"] == frame_after_bound


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
        settings=_legacy_settings(tmp_path),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.capture_composed", return_value=(composition_png, [])):
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
        settings=_legacy_settings(tmp_path),
        event_log=EventLog(tmp_path / "events.db"),
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )

    with patch("app.push.capture_composed", return_value=(composition_png, [])):
        result = manager.push("solo")

    assert result.status == "sent"
    assert all(r.error is None for r in result.renderers)


def _wired_default(tmp_path: Path, composition_png: bytes, renderers: list[Renderer]):
    """Like _wired but with default settings (unbound broadcast OFF), for
    exercising the #84 binding gate."""
    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(Page(id="home", name="Home", panel=Panel(w=100, h=100), cells=[]))
    registry = RendererRegistry(renderers={r.id: r for r in renderers})
    fakes = {}

    def factory(client_id: str):
        fakes["client"] = _FakeMqttClient(client_id)
        return fakes["client"]

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=factory)
    transport.connect()
    event_log = EventLog(tmp_path / "events.db")
    manager = PushManager(
        registry=registry,
        page_store=page_store,
        transport=transport,
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=event_log,
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
    )
    return manager, fakes["client"], event_log


def test_unbound_send_noops_by_default(tmp_path: Path, composition_png: bytes) -> None:
    """#84: an unbound dashboard Send no-ops (status 'unbound') instead of
    broadcasting to base renderers, and nothing is published."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, client, event_log = _wired_default(tmp_path, composition_png, renderers)
    with patch("app.push.capture_composed", return_value=(composition_png, [])):
        result = manager.push("home")
    assert result.status == "unbound"
    assert "isn't bound" in (result.error or "")
    assert client.published == []
    # Surfaced in the event log as a soft skip, not a failure.
    rows = event_log.list(type="push")
    assert rows[0].status == "unbound"


def test_unbound_send_broadcasts_when_opted_in(tmp_path: Path, composition_png: bytes) -> None:
    """With the legacy opt-in on, an unbound Send still fans out to base
    renderers (back-compat for single-head MQTT setups)."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, client, _ = _wired(tmp_path, composition_png, renderers)
    with patch("app.push.capture_composed", return_value=(composition_png, [])):
        result = manager.push("home")
    assert result.status == "sent"
    assert len(client.published) == 1


# -- touch ETag stability + speculative pre-compose (issue #49 linger) ----


class _FakeDevice:
    def __init__(self, id_: str, panel: dict) -> None:
        self.id = id_
        self.display_name = id_
        self.kind_of = "esp32_client"
        self.manifest = {"panel": panel}
        self.panel = panel
        # HTTP-polled REST instance (the E1003 shape): no MQTT topics,
        # frames served via /frame + latest_renders.
        self.status_topic = None
        self.transport = "rest"


class _FakeDeviceRegistry:
    def __init__(self, devices: list[_FakeDevice]) -> None:
        self.devices = {d.id: d for d in devices}

    def get(self, device_id: str):
        return self.devices.get(device_id)


def _wired_bound(tmp_path: Path, composition_png: bytes):
    """PushManager with a page bound to one device ('kitchen') whose
    renderer clone fans out only to it, the shape the touch prewarm
    path targets."""
    page_store = PageStore(tmp_path / "pages.json")
    page_store.save(
        Page(id="home", name="Home", panel=Panel(w=100, h=100), cells=[], device_ids=["kitchen"])
    )
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False, device="kitchen")]
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
        devices=_FakeDeviceRegistry([_FakeDevice("kitchen", {"w": 100, "h": 100})]),
    )
    return manager, fakes["client"], page_store


def test_touch_repush_of_unchanged_content_keeps_digest(
    tmp_path: Path, composition_png: bytes
) -> None:
    """The ETag-stability guarantee behind the E1003's touch 304 path: a
    touch action that doesn't change the canvas triggers an unforced
    re-push (source='touch'), which must leave the device's latest-render
    digest untouched so the follow-up /frame poll 304s instead of costing
    a 1.3 MB download and a ~30 s panel repaint."""
    manager, client, _ = _wired_bound(tmp_path, composition_png)

    with patch("app.push.capture_composed", return_value=(composition_png, [])):
        first = manager.push("home", device_ids={"kitchen"})
    assert first.status == "sent"
    digest_before = manager.latest_render_for("kitchen")["digest"]

    with patch("app.push.capture_composed", return_value=(composition_png, [])):
        second = manager.push("home", device_ids={"kitchen"}, source="touch")

    assert second.status == "no_change"
    assert manager.latest_render_for("kitchen")["digest"] == digest_before
    # HTTP-polled REST device: nothing rides MQTT on either push.
    assert client.published == []


def test_prewarm_page_is_consumed_by_next_push(tmp_path: Path, composition_png: bytes) -> None:
    """prewarm_page captures the composition ahead of time; the next push
    of that page consumes it and skips its own Playwright capture. The
    entry is consume-once: a second push captures fresh."""
    manager, _client, _ = _wired_bound(tmp_path, composition_png)
    calls: list[str] = []

    def fake_capture(req, pool=None):
        calls.append(req.render.url)
        return (composition_png, [])

    with patch("app.push.capture_composed", side_effect=fake_capture):
        assert manager.prewarm_page("home", device_id="kitchen") is True
        assert len(calls) == 1

        result = manager.push("home", device_ids={"kitchen"})
        assert result.status == "sent"
        # The push consumed the prewarmed composition, no second capture.
        assert len(calls) == 1

        manager.push("home", device_ids={"kitchen"})
        # Consume-once: the follow-up push captures fresh.
        assert len(calls) == 2


def test_prewarm_ignores_unbound_page_and_unknown_device(
    tmp_path: Path, composition_png: bytes
) -> None:
    manager, _client, _ = _wired_bound(tmp_path, composition_png)
    with patch("app.push.capture_composed", return_value=(composition_png, [])):
        assert manager.prewarm_page("home", device_id="not_bound") is False
        assert manager.prewarm_page("missing_page", device_id="kitchen") is False


def test_prewarm_entry_expires_after_ttl(tmp_path: Path, composition_png: bytes) -> None:
    """A stale prewarmed composition must never serve: widget data was
    captured at touch time, and a push minutes later needs fresh data."""
    manager, _client, _ = _wired_bound(tmp_path, composition_png)
    calls: list[str] = []

    def fake_capture(req, pool=None):
        calls.append(req.render.url)
        return (composition_png, [])

    with patch("app.push.capture_composed", side_effect=fake_capture):
        assert manager.prewarm_page("home", device_id="kitchen") is True
        # Backdate the cached entry past the TTL.
        with manager._precompose_lock:
            for key, (ts, png, regions, slots) in list(manager._precompose.items()):
                manager._precompose[key] = (ts - 10_000.0, png, regions, slots)
        result = manager.push("home", device_ids={"kitchen"})
        assert result.status == "sent"
        # Expired entry ignored, the push captured fresh.
        assert len(calls) == 2


def test_prewarm_misses_after_page_edit(tmp_path: Path, composition_png: bytes) -> None:
    """An edit between prewarm and push changes the page's content token,
    so the push must re-capture instead of serving the pre-edit frame."""
    manager, _client, page_store = _wired_bound(tmp_path, composition_png)
    calls: list[str] = []

    def fake_capture(req, pool=None):
        calls.append(req.render.url)
        return (composition_png, [])

    with patch("app.push.capture_composed", side_effect=fake_capture):
        assert manager.prewarm_page("home", device_id="kitchen") is True
        page = page_store.get("home")
        page_store.save(page.model_copy(update={"name": "Home v2"}))
        result = manager.push("home", device_ids={"kitchen"})
        assert result.status == "sent"
        assert len(calls) == 2


def test_prune_keeps_live_frame_artifacts_without_event_rows(
    tmp_path: Path, composition_png: bytes
) -> None:
    """Regression: the latest render for a device must survive the prune
    even when no event row references it any more (cap eviction, or the
    History page's Clear). Deleting the live frame's regions sidecar made
    every tap resolve no_target and left the touch monitor with nothing
    to overlay."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, _, _ = _wired(tmp_path, composition_png, renderers)
    renders = manager._renders_dir

    manager._latest_renders["hall_e1003"] = {
        "digest": "liveart123456",
        "ext": "bin",
        "filename": "liveart123456.bin",
        "composition_digest": "livecomp12345",
        "preview_digest": "liveprev123456",
    }
    (renders / "liveart123456.bin").write_bytes(b"x")
    (renders / "livecomp12345.png").write_bytes(b"x")
    (renders / "liveprev123456.png").write_bytes(b"x")
    (renders / "livecomp12345.regions.json").write_text('{"v":1,"regions":[]}')
    (renders / "orphan99999999.png").write_bytes(b"x")

    removed = manager.prune_orphan_renders()
    assert removed == 1
    assert (renders / "liveart123456.bin").exists()
    assert (renders / "livecomp12345.png").exists()
    assert (renders / "liveprev123456.png").exists()
    assert (renders / "livecomp12345.regions.json").exists(), (
        "the live frame's touch-region sidecar must survive the prune"
    )
    assert not (renders / "orphan99999999.png").exists()


def test_delete_history_keeps_live_frame_artifacts(tmp_path: Path, composition_png: bytes) -> None:
    """Deleting the History row for the frame a panel is currently
    showing removes the row but must leave the artifacts (thumbnail +
    regions sidecar) on disk."""
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, _, _ = _wired(tmp_path, composition_png, renderers)
    renders = manager._renders_dir

    event_id = manager._event_log.record(
        type="push", source="page", target="home", status="sent", digest="livecomp12345"
    )
    manager._latest_renders["hall_e1003"] = {
        "digest": "liveart123456",
        "ext": "bin",
        "filename": "liveart123456.bin",
        "composition_digest": "livecomp12345",
    }
    (renders / "livecomp12345.png").write_bytes(b"x")
    (renders / "livecomp12345.regions.json").write_text('{"v":1,"regions":[]}')

    assert manager.delete_history(event_id) is True
    assert (renders / "livecomp12345.png").exists()
    assert (renders / "livecomp12345.regions.json").exists()
