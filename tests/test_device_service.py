"""Unit tests for the device-instance lifecycle service.

The Settings routes are thin wrappers over these; testing the service
directly locks down the behaviour that used to be duplicated (and had
drifted) across the Add-device and Discovered-register routes, most
importantly that both create paths derive topics the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import device_loader, device_service, renderer_loader
from app.main import REPO_ROOT


@pytest.fixture
def registries(tmp_path: Path):
    """Bundled kinds + renderers loaded into fresh registries, with a
    tmp instance data root."""
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
    assert devices.errors == []
    assert renderers.errors == []
    return devices, renderers, data_root


def test_derive_topic_swaps_prefix() -> None:
    assert (
        device_service.derive_topic("tesserae/esp32/status", "esp32_lab", suffix="status")
        == "tesserae/esp32_lab/status"
    )
    assert (
        device_service.derive_topic("tesserae/esp32/config", "esp32_lab", suffix="config")
        == "tesserae/esp32_lab/config"
    )


def test_derive_topic_falls_back_for_odd_shape() -> None:
    # A kind topic that doesn't fit tesserae/<prefix>/... still yields a
    # unique per-instance topic rather than copying the kind's verbatim.
    assert (
        device_service.derive_topic("weird/topic", "esp32_lab", suffix="status")
        == "tesserae/esp32_lab/status"
    )


def test_create_instance_derives_topics_and_clones(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    assert result.ok
    dev = result.device
    assert dev is not None
    assert dev.status_topic == "tesserae/esp32_lab/status"
    assert dev.config_topic == "tesserae/esp32_lab/config"  # kind has a config topic
    # Renderer clone exists for the instance.
    assert renderers.get("esp32_bin__esp32_lab") is not None
    # Persisted.
    assert (data_root / "esp32_lab.json").exists()


def test_renderer_id_for_format_matches_extension(registries) -> None:
    devices, renderers, _ = registries
    kind = devices.get("circuitpython_generic")
    assert kind is not None
    assert kind.renderer_ids == ["circuitpython_png", "circuitpython_bmp"]
    assert device_service.renderer_id_for_format(renderers, kind, "bmp") == "circuitpython_bmp"
    assert device_service.renderer_id_for_format(renderers, kind, "png") == "circuitpython_png"
    # Leading dot / casing tolerated.
    assert device_service.renderer_id_for_format(renderers, kind, ".BMP") == "circuitpython_bmp"
    # Unknown / empty leaves the kind default (None).
    assert device_service.renderer_id_for_format(renderers, kind, "webp") is None
    assert device_service.renderer_id_for_format(renderers, kind, None) is None


def test_circuitpython_generic_defaults_to_png_clone_only(registries) -> None:
    # No format declared: the multi-renderer kind must clone only its
    # first renderer so the two never fight over the device's single
    # latest-render slot.
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="cp_default",
        kind_id="circuitpython_generic",
    )
    assert result.ok and result.device is not None
    assert renderers.get("circuitpython_png__cp_default") is not None
    assert renderers.get("circuitpython_bmp__cp_default") is None
    assert result.device.renderer_ids == ["circuitpython_png__cp_default"]


def test_circuitpython_generic_bmp_pick_clones_bmp_only(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="cp_bmp",
        kind_id="circuitpython_generic",
        renderer_id="circuitpython_bmp",
    )
    assert result.ok and result.device is not None
    assert renderers.get("circuitpython_bmp__cp_bmp") is not None
    assert renderers.get("circuitpython_png__cp_bmp") is None
    assert result.device.renderer_ids == ["circuitpython_bmp__cp_bmp"]
    # Persisted on the manifest so it survives a reload.
    saved = json.loads((data_root / "cp_bmp.json").read_text())
    assert saved["renderer_id"] == "circuitpython_bmp"


def test_create_instance_ignores_unknown_renderer_id(registries) -> None:
    # A renderer_id that isn't one of the kind's renderers is dropped, so
    # the instance falls back to the kind default rather than orphaning
    # itself with no renderer clone at all.
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="cp_bad",
        kind_id="circuitpython_generic",
        renderer_id="does_not_exist",
    )
    assert result.ok and result.device is not None
    assert result.device.renderer_ids == ["circuitpython_png__cp_bad"]
    saved = json.loads((data_root / "cp_bad.json").read_text())
    assert "renderer_id" not in saved


def test_update_instance_renderer_switches_format(registries) -> None:
    # A device created as png can switch to bmp after the fact by
    # re-declaring the wire format, no delete + re-create. The renderer
    # clone flips, the manifest persists, and "changed" reports True.
    devices, renderers, data_root = registries
    created = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="cp_switch",
        kind_id="circuitpython_generic",
    )
    assert created.ok and created.device.renderer_ids == ["circuitpython_png__cp_switch"]

    result, changed = device_service.update_instance_renderer(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="cp_switch",
        wire_format="bmp",
    )
    assert changed is True and result.ok
    assert result.device.renderer_ids == ["circuitpython_bmp__cp_switch"]
    assert renderers.get("circuitpython_png__cp_switch") is None
    assert (
        json.loads((data_root / "cp_switch.json").read_text())["renderer_id"] == "circuitpython_bmp"
    )


def test_update_instance_renderer_noop_cases(registries) -> None:
    # Switching to the already-active format, or an empty / unknown one,
    # is a no-op (changed=False) rather than an error or a needless
    # rewrite + reclone.
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="cp_noop",
        kind_id="circuitpython_generic",
        renderer_id="circuitpython_bmp",
    )
    for fmt in ("bmp", "", "webp", None):
        result, changed = device_service.update_instance_renderer(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id="cp_noop",
            wire_format=fmt,
        )
        assert changed is False and result.ok
        assert result.device.renderer_ids == ["circuitpython_bmp__cp_noop"]


@pytest.fixture
def registries_with_catalog(tmp_path: Path):
    """Like ``registries`` but with the hardware catalog layered in, so
    SKU-derived kinds (``seeed_reterminal_e1004`` etc.) exist. Needed by
    the kind auto-heal tests: healing moves an instance from a generic
    protocol kind to one of its catalog siblings."""
    data_root = tmp_path / "devices"
    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=data_root,
        hardware_dir=REPO_ROOT / "hardware",
        hardware_schema_path=REPO_ROOT / "schema" / "hardware.schema.json",
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path / "rdata",
    )
    assert devices.errors == []
    assert renderers.errors == []
    return devices, renderers, data_root


def test_update_instance_kind_heals_to_catalog_sibling(registries_with_catalog) -> None:
    # A device that paired under the generic esp32_client kind moves to
    # the hardware SKU its firmware now declares. Same protocol, so the
    # renderer clone survives under the new kind and the instance file
    # records the SKU.
    devices, renderers, data_root = registries_with_catalog
    created = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="frame_office",
        kind_id="esp32_client",
        name="Office frame",
    )
    assert created.ok

    result, changed = device_service.update_instance_kind(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="frame_office",
        kind_id="seeed_reterminal_e1004",
    )
    assert changed is True and result.ok
    assert result.device is not None
    assert result.device.kind_of == "seeed_reterminal_e1004"
    assert result.device.manifest["name"] == "Office frame"
    assert renderers.get("esp32_bin__frame_office") is not None
    saved = json.loads((data_root / "frame_office.json").read_text())
    assert saved["kind"] == "seeed_reterminal_e1004"
    # The instance carried the generic kind's 800x480 landscape default,
    # copied in at create_instance. It has to follow the heal, or the
    # device renders at the wrong geometry on the SKU it just announced.
    assert saved["panel"]["w"] == 1200
    assert saved["panel"]["h"] == 1600
    assert saved["panel"]["orientation"] == "portrait"


def test_update_instance_kind_moves_inherited_orientation(registries_with_catalog) -> None:
    """A Sticky registered under the CrossInk kind and re-registering on the
    tesserae-device-firmware build must not keep the old kind's flip: the two
    describe the same glass mounted the same way but composed differently, and
    a stale ``portrait_flipped`` paints every frame 180 degrees out."""
    devices, renderers, data_root = registries_with_catalog
    assert device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="crossink_b064dc",
        kind_id="seeed_sticky_gray",
        name="Sticky",
    ).ok

    result, changed = device_service.update_instance_kind(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="crossink_b064dc",
        kind_id="seeed_reterminal_sticky",
    )
    assert changed is True and result.ok
    assert result.device is not None
    assert result.device.kind_of == "seeed_reterminal_sticky"
    assert result.device.manifest["panel"]["orientation"] == "portrait"
    assert result.device.manifest["name"] == "Sticky"
    saved = json.loads((data_root / "crossink_b064dc.json").read_text())
    assert saved["panel"]["orientation"] == "portrait"
    # Geometry was already right on both kinds; the heal must leave it alone.
    assert (saved["panel"]["w"], saved["panel"]["h"]) == (480, 800)


def test_update_instance_kind_keeps_a_deliberate_panel_override(
    registries_with_catalog,
) -> None:
    """Only fields still matching the old kind move. An orientation the user
    chose for this display is theirs, and a heal is not a licence to revert
    it."""
    devices, renderers, data_root = registries_with_catalog
    assert device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="sticky_upside_down",
        kind_id="seeed_sticky_gray",
        name="Sticky",
        orientation="landscape",
    ).ok
    saved = json.loads((data_root / "sticky_upside_down.json").read_text())
    assert saved["panel"]["orientation"] == "landscape"

    result, changed = device_service.update_instance_kind(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="sticky_upside_down",
        kind_id="seeed_reterminal_sticky",
    )
    assert changed is True and result.ok
    assert result.device is not None
    assert result.device.manifest["panel"]["orientation"] == "landscape"


def test_e1001_gray_legacy_packs_identically_to_the_plain_gray_kind(
    registries_with_catalog,
) -> None:
    """The two E1001 grayscale kinds exist to separate OTA lineages, not to
    render differently. A canvas packed for one must be byte-identical to the
    same canvas packed for the other, so an operator re-pointing a panel
    between them never changes a pixel."""
    import io

    from PIL import Image

    from app.panel import device_panel

    devices, renderers, data_root = registries_with_catalog

    def packed(instance_id: str, kind_id: str) -> bytes:
        created = device_service.create_instance(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id=instance_id,
            kind_id=kind_id,
            name=instance_id,
        )
        assert created.ok, created.error
        device = devices.get(instance_id)
        assert device is not None and device.kind_of == kind_id
        panel = device_panel(device)
        assert panel is not None
        clones = renderers.for_device(instance_id)
        assert [c.id for c in clones] == [f"esp32_gray2_bin__{instance_id}"]
        buf = io.BytesIO()
        Image.linear_gradient("L").resize((800, 480)).convert("RGB").save(buf, "PNG")
        return clones[0].transform(buf.getvalue(), panel=panel, settings={})

    plain = packed("e1001_plain", "seeed_reterminal_e1001_gray")
    legacy = packed("e1001_legacy", "seeed_reterminal_e1001_gray_legacy")

    assert len(plain) == 800 * 480 // 4  # 2bpp -> 96000
    assert legacy == plain


def test_reterminal_sticky_packs_a_96000_byte_frame(registries_with_catalog) -> None:
    """The Sticky presents 480x800 portrait but its controller scans 800x480,
    so the packed frame must still be exactly 800*480/4 bytes. A short or long
    buffer is the failure mode the firmware cannot recover from: it paints
    whatever it is handed."""
    import io

    from PIL import Image

    from app.panel import device_panel

    devices, renderers, data_root = registries_with_catalog
    created = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="sticky_pack",
        kind_id="seeed_reterminal_sticky",
        name="Sticky",
    )
    assert created.ok, created.error
    device = devices.get("sticky_pack")
    assert device is not None
    # The panel has a digitizer, so touch dispatch and the editors'
    # Interaction UI have to apply to it (and to instances of it).
    assert devices.get("seeed_reterminal_sticky").manifest.get("touch") is True
    assert device.manifest.get("touch") is True
    panel = device_panel(device)
    assert panel is not None
    clones = renderers.for_device("sticky_pack")
    assert [c.id for c in clones] == ["esp32_gray2_bin__sticky_pack"]

    buf = io.BytesIO()
    Image.linear_gradient("L").resize((480, 800)).convert("RGB").save(buf, "PNG")
    packed = clones[0].transform(buf.getvalue(), panel=panel, settings={})
    assert len(packed) == 800 * 480 // 4  # 2bpp -> 96000
    # A gradient must come back using all four levels. Asserting the values
    # merely fit in 2 bits would pass on a mono buffer dressed up as
    # grayscale, which is the mistake crosspoint_gray was added to fix.
    levels = {(byte >> shift) & 0b11 for byte in packed for shift in (0, 2, 4, 6)}
    assert levels == {0, 1, 2, 3}


def test_update_instance_kind_noop_cases(registries_with_catalog) -> None:
    # Same kind, empty, unknown, an instance id, and a cross-protocol
    # kind are all no-ops (changed=False), never an error or a move.
    devices, renderers, data_root = registries_with_catalog
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="frame_hall",
        kind_id="esp32_client",
    )
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="frame_other",
        kind_id="esp32_client",
    )
    for kind_id in ("esp32_client", "", None, "no_such_kind", "frame_other", "trmnl_client"):
        result, changed = device_service.update_instance_kind(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id="frame_hall",
            kind_id=kind_id,
        )
        assert changed is False and result.ok
        assert result.device is not None and result.device.kind_of == "esp32_client"


def test_update_instance_kind_unknown_instance(registries_with_catalog) -> None:
    devices, renderers, data_root = registries_with_catalog
    result, changed = device_service.update_instance_kind(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="ghost",
        kind_id="seeed_reterminal_e1004",
    )
    assert changed is False and not result.ok


def test_create_instance_portrait_swaps_dims(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
        panel_overrides={"w": 800, "h": 480},
        orientation="portrait",
    )
    assert result.ok and result.device is not None
    assert result.device.panel == {
        "w": 480,
        "h": 800,
        "orientation": "portrait",
        "name": "ESP32 e-paper",
    }


def test_create_instance_portrait_swaps_and_stores_orientation(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
        panel_overrides={"w": 800, "h": 480},
        orientation="portrait_flipped",
    )
    assert result.ok and result.device is not None
    panel = result.device.panel
    assert panel["orientation"] == "portrait_flipped"
    # Portrait variant → canvas is tall (w/h swapped from the landscape override).
    assert (panel["w"], panel["h"]) == (480, 800)
    # The Panel model derives flip from the orientation for the renderers.
    from app.panel import is_flipped_orientation
    from app.state.page_store import Panel

    assert is_flipped_orientation("portrait_flipped") is True
    assert is_flipped_orientation("portrait") is False
    assert Panel(w=480, h=800, flip=True).flip is True


def test_update_panel_sets_flipped_orientation(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    r1 = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        w=800,
        h=480,
        orientation="landscape_flipped",
    )
    assert r1.ok and r1.device is not None
    assert r1.device.panel["orientation"] == "landscape_flipped"


def test_update_quiet_hours_persists_and_reloads(registries) -> None:
    """An enabled quiet-hours override writes ``quiet_hours`` to the
    instance manifest and the reloaded Device exposes it."""
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        kind_id="esp32_client",
    )
    result = device_service.update_instance_quiet_hours(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        enabled=True,
        start="22:30",
        end="07:00",
    )
    assert result.ok and result.device is not None
    qh = result.device.manifest.get("quiet_hours")
    assert qh == {"enabled": True, "start": "22:30", "end": "07:00"}


def test_update_quiet_hours_clearing_drops_block(registries) -> None:
    """Saving with everything blank + disabled removes the block from
    the manifest so the device falls back to the app-level setting."""
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        kind_id="esp32_client",
    )
    # First set it.
    device_service.update_instance_quiet_hours(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        enabled=True,
        start="22:30",
        end="07:00",
    )
    # Then clear it.
    result = device_service.update_instance_quiet_hours(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        enabled=False,
        start="",
        end="",
    )
    assert result.ok and result.device is not None
    assert "quiet_hours" not in result.device.manifest


def test_create_instance_rejects_bad_id_and_unknown_kind(registries) -> None:
    devices, renderers, data_root = registries
    # Mixed case is forgivingly lowercased, but a leading digit / spaces
    # / too-short are genuinely rejected.
    for bad in ("1leading_digit", "a", "has space"):
        res = device_service.create_instance(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id=bad,
            kind_id="esp32_client",
        )
        assert not res.ok and res.device is None, bad

    bad_kind = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="ok_id",
        kind_id="nope",
    )
    assert not bad_kind.ok

    # Registering against another instance (not a kind) is refused.
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    dup = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    assert not dup.ok  # id already in use


def test_update_panel_normalises_dims_to_orientation(registries) -> None:
    """``update_instance_panel`` swaps w/h to match the chosen orientation
    when they're inconsistent, portrait must end up tall, landscape
    wide. The renderers derive rotation from ``panel.w < panel.h``, so
    a mismatch silently keeps the panel rendering at the wrong
    orientation. The settings page's client-side JS swaps the form's
    visible inputs when the orientation dropdown changes; this is the
    server-side belt + suspenders for hand-crafted POSTs or when the
    JS hasn't fired before submit."""
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    # Landscape dims (600 > 448) with a portrait orientation: server
    # swaps them so the stored canvas is tall.
    result = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        w=600,
        h=448,
        orientation="portrait",
    )
    assert result.ok and result.device is not None
    assert (result.device.panel["w"], result.device.panel["h"]) == (448, 600)
    assert result.device.panel["orientation"] == "portrait"
    saved = json.loads((data_root / "esp32_lab.json").read_text())
    assert saved["panel"]["w"] == 448
    assert saved["panel"]["h"] == 600


def test_update_panel_leaves_dims_when_already_consistent(registries) -> None:
    """When the submitted dims already match the orientation aspect
    (portrait + tall, or landscape + wide), the swap is a no-op."""
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    # Portrait dims (448 < 600) + portrait orientation, no swap.
    result = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        w=448,
        h=600,
        orientation="portrait",
    )
    assert result.ok and result.device is not None
    assert (result.device.panel["w"], result.device.panel["h"]) == (448, 600)


def test_update_panel_persists_gamut_and_clamps_unknown(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        kind_id="pi_bin_client",
    )
    ok = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=600,
        h=448,
        orientation="landscape",
        gamut="inky_7colour",
    )
    assert ok.ok and ok.device is not None
    assert ok.device.panel["gamut"] == "inky_7colour"
    # Persisted to disk.
    saved = json.loads((data_root / "bin_57.json").read_text())
    assert saved["panel"]["gamut"] == "inky_7colour"
    # An unknown gamut clamps back to the safe E6 default.
    clamped = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=600,
        h=448,
        orientation="landscape",
        gamut="bogus",
    )
    assert clamped.ok and clamped.device is not None
    assert clamped.device.panel["gamut"] == "waveshare_e6"
    # Omitting gamut leaves the stored value untouched.
    device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=600,
        h=448,
        orientation="landscape",
        gamut="inky_7colour",
    )
    kept = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=640,
        h=400,
        orientation="landscape",
    )
    assert kept.ok and kept.device is not None
    assert kept.device.panel["gamut"] == "inky_7colour"


def test_update_panel_persists_and_clamps_underscan(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        kind_id="pi_bin_client",
    )
    ok = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        w=600,
        h=448,
        orientation="landscape",
        underscan=24,
    )
    assert ok.ok and ok.device is not None
    assert ok.device.panel["underscan"] == 24
    # Oversize is clamped so the inset can't swallow the panel.
    big = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        w=600,
        h=448,
        orientation="landscape",
        underscan=99999,
    )
    assert big.ok and big.device is not None
    assert big.device.panel["underscan"] == 448 // 2 - 1  # min(w,h)//2 - 1
    # Negative clamps to 0; omitting it leaves the stored value.
    neg = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        w=600,
        h=448,
        orientation="landscape",
        underscan=-5,
    )
    assert neg.ok and neg.device is not None
    # 0 is the default, so the surfaced panel dict omits it.
    assert neg.device.panel.get("underscan", 0) == 0


def test_delete_instance_refuses_kind(registries) -> None:
    devices, renderers, _data_root = registries
    result = device_service.delete_instance(
        devices=devices, renderers=renderers, instance_id="esp32_client"
    )
    assert not result.ok
    assert devices.get("esp32_client") is not None


def test_delete_instance_removes_record_file_and_clones(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    assert renderers.get("esp32_bin__esp32_lab") is not None
    result = device_service.delete_instance(
        devices=devices, renderers=renderers, instance_id="esp32_lab"
    )
    assert result.ok
    assert devices.get("esp32_lab") is None
    assert renderers.get("esp32_bin__esp32_lab") is None
    assert not (data_root / "esp32_lab.json").exists()


def test_relocate_orphan_instance_files_moves_stray_manifests(tmp_path: Path) -> None:
    """A manifest left at the data root by a pre-fix REST /register is
    moved into data/devices/ so it loads on restart and re-pairs (#127)."""
    data_root = tmp_path
    device_data_root = tmp_path / "devices"
    device_data_root.mkdir()
    (data_root / "orphan_pico.json").write_text(
        json.dumps({"id": "orphan_pico", "kind": "pico_bin_client"}), encoding="utf-8"
    )

    moved = device_service.relocate_orphan_instance_files(
        data_root=data_root, device_data_root=device_data_root
    )

    assert moved == ["orphan_pico"]
    assert (device_data_root / "orphan_pico.json").is_file()
    assert not (data_root / "orphan_pico.json").exists()


def test_relocate_orphan_instance_files_does_not_clobber_existing(tmp_path: Path) -> None:
    """If a manifest already exists in data/devices/ (a re-pair created a
    fresh one), the stray is left in place rather than overwriting the
    newer copy."""
    data_root = tmp_path
    device_data_root = tmp_path / "devices"
    device_data_root.mkdir()
    (data_root / "dupe_pico.json").write_text(
        '{"id": "dupe_pico", "stale": true}', encoding="utf-8"
    )
    (device_data_root / "dupe_pico.json").write_text(
        '{"id": "dupe_pico", "fresh": true}', encoding="utf-8"
    )

    moved = device_service.relocate_orphan_instance_files(
        data_root=data_root, device_data_root=device_data_root
    )

    assert moved == []
    # Stray untouched; the fresh copy in data/devices/ is preserved.
    assert (data_root / "dupe_pico.json").exists()
    assert '"fresh": true' in (device_data_root / "dupe_pico.json").read_text(encoding="utf-8")


def test_usable_mac_drops_placeholders_but_keeps_spelling() -> None:
    """The claim path matches on this value, so anything that can't
    identify a device has to read as absent (issue #226). Real MACs come
    back exactly as the client spelled them, since the device card shows
    the stored string."""
    from app.device_service import usable_mac

    for placeholder in (
        None,
        123,
        "",
        "  ",
        "None",
        "NONE",
        "null",
        "nil",
        "n/a",
        "unknown",
        "undefined",
        "00:00:00:00:00:00",
        "000000000000",
        "ff:ff:ff:ff:ff:ff",
    ):
        assert usable_mac(placeholder) is None, f"{placeholder!r} should read as absent"

    assert usable_mac("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"
    assert usable_mac("  aabbccddeeff  ") == "aabbccddeeff"
    # Not every client sends a spec-shaped MAC; a stable unique id still
    # pairs, so shape is not policed beyond the placeholder check.
    assert usable_mac("aa-bb-cc-00-00-01") == "aa-bb-cc-00-00-01"
