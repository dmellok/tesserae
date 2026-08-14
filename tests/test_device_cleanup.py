"""Device cleanup + MAC-marker semantics (v0.69.2, issue #48)."""

from __future__ import annotations

import time
from pathlib import Path

from app import device_cleanup
from app.state.deleted_device_markers import DeletedDeviceMarkers
from app.state.event_log import EventLog
from app.state.page_store import Page, PageStore, Panel
from app.state.settings_store import SettingsStore


def _make_stores(tmp_path: Path):
    page_store = PageStore(tmp_path / "pages.json")
    event_log = EventLog(tmp_path / "events.db")
    settings_store = SettingsStore(tmp_path / "settings.json")
    return page_store, event_log, settings_store


def _make_page(page_id: str, *, device_ids: list[str]) -> Page:
    return Page(
        id=page_id, name=page_id, panel=Panel(w=200, h=100), cells=[], device_ids=device_ids
    )


class _FakeRegistry:
    """Minimal stand-in for DeviceRegistry: cleanup only ever asks whether an
    id resolves to a device."""

    def __init__(self, *live_ids: str) -> None:
        self.devices = {did: object() for did in live_ids}


def test_list_orphan_state_counts_bound_pages(tmp_path: Path) -> None:
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("bound1", device_ids=["esp32_lab"]))
    ps.save(_make_page("bound2", device_ids=["esp32_lab"]))
    ps.save(_make_page("shared", device_ids=["esp32_lab", "other"]))  # bound to two: excluded
    ps.save(_make_page("unrelated", device_ids=["other"]))
    summary = device_cleanup.list_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
        devices=_FakeRegistry("esp32_lab", "other"),
    )
    assert sorted(summary.page_ids) == ["bound1", "bound2"]
    # "shared" survives the wipe, but the device comes off its binding.
    assert summary.unbound_page_ids == ["shared"]


def test_page_shared_with_a_DELETED_device_is_this_devices_to_wipe(tmp_path: Path) -> None:
    """Issue #229: deleting a device leaves its id on any dashboard it shared,
    so the next device's delete saw a two-id binding and treated the dashboard
    as shared forever. Only LIVE co-owners protect a page."""
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("shared", device_ids=["deleted_a", "esp32_lab"]))
    ps.save(_make_page("really_shared", device_ids=["live_b", "esp32_lab"]))
    summary = device_cleanup.list_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
        devices=_FakeRegistry("esp32_lab", "live_b"),  # deleted_a is gone
    )
    assert summary.page_ids == ["shared"]
    assert summary.unbound_page_ids == ["really_shared"]


def test_no_registry_falls_back_to_exclusive_bindings(tmp_path: Path) -> None:
    """A caller with no registry (CLI, older call sites) keeps the conservative
    rule: only pages bound to this device alone count as its own."""
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("alone", device_ids=["esp32_lab"]))
    ps.save(_make_page("shared", device_ids=["esp32_lab", "other"]))
    summary = device_cleanup.list_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    assert summary.page_ids == ["alone"]


def test_wipe_unbinds_the_device_from_pages_it_does_not_own(tmp_path: Path) -> None:
    """A dashboard a live device still shows is kept, but stops naming the
    device that just went (#229), so the dashboards list stops counting it."""
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("shared", device_ids=["esp32_lab", "live_b"]))
    ps.save(_make_page("owned", device_ids=["esp32_lab"]))
    summary = device_cleanup.wipe_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
        devices=_FakeRegistry("live_b"),  # esp32_lab already out of the registry
    )
    assert summary.page_ids == ["owned"] and ps.get("owned") is None
    assert summary.unbound_page_ids == ["shared"]
    kept = ps.get("shared")
    assert kept is not None and kept.device_ids == ["live_b"]


def test_list_orphan_state_counts_events_for_bound_pages(tmp_path: Path) -> None:
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("home", device_ids=["esp32_lab"]))
    # Two pushes targeting the bound page → should be counted.
    el.record(type="push", source="page", target="home", status="sent")
    el.record(type="push", source="scheduler", target="home", status="sent")
    # Test-pattern event: target label ``file:test-pattern:...:<id>``
    # ends with `:esp32_lab`, so the LIKE-suffix match picks it up.
    el.record(
        type="push",
        source="file",
        target="test-pattern:palette_swatches:esp32_lab",
        status="sent",
    )
    # Unrelated push → not counted.
    el.record(type="push", source="page", target="other_page", status="sent")
    summary = device_cleanup.list_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    assert summary.event_count == 3


def test_list_orphan_state_counts_calibration_image(tmp_path: Path) -> None:
    ps, el, ss = _make_stores(tmp_path)
    calib_dir = tmp_path / "calibration_images"
    calib_dir.mkdir()
    (calib_dir / "esp32_lab.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    summary = device_cleanup.list_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    assert summary.has_calibration_image is True


def test_list_orphan_state_counts_settings(tmp_path: Path) -> None:
    ps, el, ss = _make_stores(tmp_path)
    # Simulate what the settings_store looks like for a device with
    # button_map + palette profile applied and a per-clone renderer.
    ss.patch_section("devices", {"esp32_lab": {"button_map": {"left": "refresh"}}})
    ss.patch_section("renderers", {"esp32_bin__esp32_lab": {"dither": "atkinson"}})
    ss.patch_section("renderers", {"pi_bin__other_device": {"dither": "floyd-steinberg"}})
    summary = device_cleanup.list_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    assert summary.setting_keys_devices == 1
    assert summary.setting_keys_renderers == 1


def test_wipe_orphan_state_actually_wipes(tmp_path: Path) -> None:
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("bound", device_ids=["esp32_lab"]))
    el.record(type="push", source="page", target="bound", status="sent")
    ss.patch_section("devices", {"esp32_lab": {"button_map": {"left": "refresh"}}})
    ss.patch_section("renderers", {"esp32_bin__esp32_lab": {"dither": "atkinson"}})
    calib_dir = tmp_path / "calibration_images"
    calib_dir.mkdir()
    (calib_dir / "esp32_lab.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    wiped = device_cleanup.wipe_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    # Report matches the world.
    assert sorted(wiped.page_ids) == ["bound"]
    assert wiped.event_count == 1
    assert wiped.setting_keys_devices == 1
    assert wiped.setting_keys_renderers == 1
    assert wiped.has_calibration_image is True
    # World actually cleared.
    assert ps.get("bound") is None
    assert not (tmp_path / "calibration_images" / "esp32_lab.png").exists()
    devices_ns = ss.get_section("devices") or {}
    assert "esp32_lab" not in devices_ns
    renderers_ns = ss.get_section("renderers") or {}
    assert "esp32_bin__esp32_lab" not in renderers_ns


def test_wipe_is_idempotent(tmp_path: Path) -> None:
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("bound", device_ids=["esp32_lab"]))
    first = device_cleanup.wipe_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    second = device_cleanup.wipe_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    assert first.total >= 1
    assert second.total == 0


def test_marker_records_and_reads_mac(tmp_path: Path) -> None:
    m = DeletedDeviceMarkers(tmp_path)
    m.record("esp32_lab", "AA:BB:CC:DD:EE:FF")
    entry = m.get("esp32_lab")
    assert entry is not None
    assert entry["mac"] == "aa:bb:cc:dd:ee:ff"
    assert isinstance(entry["deleted_at"], float)


def test_marker_differs_when_incoming_mac_changes(tmp_path: Path) -> None:
    m = DeletedDeviceMarkers(tmp_path)
    m.record("esp32_lab", "AA:BB:CC:DD:EE:FF")
    assert m.mac_differs("esp32_lab", "AA:BB:CC:DD:EE:FF") is False
    assert m.mac_differs("esp32_lab", "aa:bb:cc:dd:ee:ff") is False  # case-normalised
    assert m.mac_differs("esp32_lab", "11:22:33:44:55:66") is True
    assert m.mac_differs("esp32_lab", None) is True  # dropped MAC → wipe
    assert m.mac_differs("esp32_lab", "") is True


def test_marker_no_prior_deletion_never_wipes(tmp_path: Path) -> None:
    m = DeletedDeviceMarkers(tmp_path)
    # Never saw this id before → always False (no leftovers to worry about).
    assert m.mac_differs("fresh_id", None) is False
    assert m.mac_differs("fresh_id", "AA:BB:CC:DD:EE:FF") is False


def test_marker_stored_mac_missing_falls_through(tmp_path: Path) -> None:
    m = DeletedDeviceMarkers(tmp_path)
    m.record("esp32_lab", None)
    # We deleted without knowing the MAC; can't compare, so keep state.
    assert m.mac_differs("esp32_lab", "AA:BB:CC:DD:EE:FF") is False


def test_marker_clear_removes_entry(tmp_path: Path) -> None:
    m = DeletedDeviceMarkers(tmp_path)
    m.record("esp32_lab", "AA:BB:CC:DD:EE:FF")
    assert m.clear("esp32_lab") is True
    assert m.get("esp32_lab") is None
    # Second clear is a no-op.
    assert m.clear("esp32_lab") is False


def test_marker_survives_disk_round_trip(tmp_path: Path) -> None:
    m1 = DeletedDeviceMarkers(tmp_path)
    m1.record("esp32_lab", "AA:BB:CC:DD:EE:FF")
    # Second instance reads the same file.
    m2 = DeletedDeviceMarkers(tmp_path)
    assert m2.get("esp32_lab") is not None


def test_marker_deleted_at_is_close_to_now(tmp_path: Path) -> None:
    m = DeletedDeviceMarkers(tmp_path)
    before = time.time()
    m.record("esp32_lab", "AA:BB:CC:DD:EE:FF")
    after = time.time()
    entry = m.get("esp32_lab")
    assert entry is not None
    assert before <= float(entry["deleted_at"]) <= after


def test_wipe_forgets_the_devices_last_frame(tmp_path: Path) -> None:
    """Renders are content-addressed, so a surviving latest-render pointer means
    a device re-registered under the same id is handed the frame from before the
    wipe instead of a 204 (issue #199)."""
    ps, el, ss = _make_stores(tmp_path)

    class FakePush:
        def __init__(self) -> None:
            self.frames = {"esp32_lab": {"digest": "abc123"}, "other": {"digest": "def456"}}

        def forget_device(self, device_id: str) -> bool:
            return self.frames.pop(device_id, None) is not None

    push = FakePush()
    wiped = device_cleanup.wipe_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
        push_manager=push,
    )
    assert wiped.has_latest_render is True
    assert "esp32_lab" not in push.frames
    assert "other" in push.frames  # other devices untouched


def test_wipe_without_a_push_manager_still_works(tmp_path: Path) -> None:
    """The parameter is optional; a caller with nothing wired must not blow up."""
    ps, el, ss = _make_stores(tmp_path)
    ps.save(_make_page("bound", device_ids=["esp32_lab"]))
    wiped = device_cleanup.wipe_orphan_state(
        device_id="esp32_lab",
        page_store=ps,
        event_log=el,
        settings_store=ss,
        data_root=tmp_path,
    )
    assert wiped.has_latest_render is False
    assert wiped.page_ids == ["bound"]
