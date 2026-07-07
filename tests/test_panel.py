"""Panel resolution + multi-head grouping.

``panel_groups_for_push`` (render-once-per-distinct-panel) and
``preview_groups_for_page`` (one editor preview per distinct aspect)
are the routing brains behind binding a page to several displays. The
two group differently on purpose: push keys off exact dims + flip (a
flipped panel needs its own 180°-turned frame), preview keys off aspect
ratio (same shape = same layout, so a 800x480 and a 1600x960 share one
card).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import device_loader, device_service, renderer_loader
from app.main import REPO_ROOT
from app.panel import (
    fit_cells_to_panel,
    panel_groups_for_push,
    preview_groups_for_page,
    resolve_panel_for_page,
)
from app.state.page_store import Page
from app.state.settings_store import SettingsStore


@pytest.fixture
def env(tmp_path: Path):
    """Bundled kinds + renderers in fresh registries, plus a settings
    store whose virtual panel is a known size for the unbound fallback."""
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
    settings = SettingsStore(tmp_path / "settings.json")
    settings.update_section("app", {"panel_preset": "custom", "panel_w": 1024, "panel_h": 768})

    def make(
        instance_id: str,
        orientation: str | None = None,
        gamut: str | None = None,
        underscan: int | None = None,
        kind_id: str = "esp32_client",
    ) -> None:
        device_service.create_instance(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id=instance_id,
            kind_id=kind_id,
            orientation=orientation,
        )
        if gamut is not None or underscan is not None:
            dev = devices.get(instance_id)
            assert dev is not None and dev.panel is not None
            device_service.update_instance_panel(
                devices=devices,
                renderers=renderers,
                data_root=data_root,
                instance_id=instance_id,
                w=int(dev.panel["w"]),
                h=int(dev.panel["h"]),
                orientation=str(dev.panel.get("orientation", "landscape")),
                gamut=gamut,
                underscan=underscan,
            )

    return devices, settings, make


def test_unbound_page_falls_back_to_virtual_panel(env) -> None:
    devices, settings, _make = env
    page = Page(id="p", name="P", cells=[])

    assert resolve_panel_for_page(page, devices, settings) == _virtual(1024, 768)

    groups = panel_groups_for_push(page, devices, settings)
    assert len(groups) == 1
    panel, device_ids = groups[0]
    assert (panel.w, panel.h) == (1024, 768)
    assert device_ids == []  # empty => fan out to every renderer

    previews = preview_groups_for_page(page, devices, settings)
    assert len(previews) == 1
    assert previews[0]["label"] == "Virtual panel"
    assert previews[0]["devices"] == []


def test_push_groups_by_dims_then_flip(env) -> None:
    """Two same-size landscape panels share one push group; a portrait
    panel is its own; a flipped landscape is its own (needs a 180° frame)
    even though it shares dims with the unflipped pair."""
    devices, settings, make = env
    make("esp32_a")  # landscape 800x480
    make("esp32_b")  # landscape 800x480 (identical -> groups with a)
    make("esp32_tall", orientation="portrait")  # 480x800
    make("esp32_flip", orientation="landscape_flipped")  # 800x480, flip

    page = Page(
        id="p",
        name="P",
        device_ids=["esp32_a", "esp32_b", "esp32_tall", "esp32_flip"],
        cells=[],
    )
    groups = panel_groups_for_push(page, devices, settings)

    by_key = {(p.w, p.h, p.flip): ids for p, ids in groups}
    assert sorted(by_key[(800, 480, False)]) == ["esp32_a", "esp32_b"]
    assert by_key[(480, 800, False)] == ["esp32_tall"]
    assert by_key[(800, 480, True)] == ["esp32_flip"]
    assert len(groups) == 3


def test_preview_groups_by_aspect_merges_flip_and_resolution(env) -> None:
    """Preview keys off aspect ratio: the unflipped + flipped landscape
    panels (same dims) collapse to one card, the portrait gets its own.
    Labels describe the shape + reduced ratio."""
    devices, settings, make = env
    make("esp32_a")  # 800x480 -> 5:3 landscape
    make("esp32_flip", orientation="landscape_flipped")  # 800x480 -> same aspect
    make("esp32_tall", orientation="portrait")  # 480x800 -> 3:5 portrait

    page = Page(id="p", name="P", device_ids=["esp32_a", "esp32_flip", "esp32_tall"], cells=[])
    previews = preview_groups_for_page(page, devices, settings)

    by_label = {g["label"]: g for g in previews}
    assert set(by_label) == {"Landscape 5:3", "Portrait 3:5"}
    assert sorted(by_label["Landscape 5:3"]["devices"]) == ["esp32_a", "esp32_flip"]
    assert by_label["Portrait 3:5"]["devices"] == ["esp32_tall"]
    assert (by_label["Landscape 5:3"]["w"], by_label["Landscape 5:3"]["h"]) == (800, 480)


def test_single_bound_device_drives_single_panel_contexts(env) -> None:
    """``resolve_panel_for_page`` (editor layout grid, default compose)
    returns the bound device's panel when there's only one bound,
    ignoring the virtual one."""
    devices, settings, make = env
    make("esp32_tall", orientation="portrait")  # 480x800
    page = Page(id="p", name="P", device_ids=["esp32_tall"], cells=[])
    panel = resolve_panel_for_page(page, devices, settings)
    assert (panel.w, panel.h) == (480, 800)


def test_largest_bound_panel_wins_when_multiple_devices(env) -> None:
    """When multiple devices are bound, ``resolve_panel_for_page``
    returns the LARGEST panel by area — deterministic regardless of
    bind order, so a layout doesn't silently re-anchor to a new
    panel just because the user added a second device. Also gives
    the layout editor the most pixels to work with."""
    devices, settings, make = env
    make("small", kind_id="esp32_client")  # 800×480 = 384k
    make("big", kind_id="pi_bin_client")  # 1424×1200 = 1.7M
    # Bind small first so previous "first targeted" semantics would
    # have returned 800×480; new "largest" semantics must return the
    # 1424×1200 regardless of order.
    page = Page(id="p", name="P", device_ids=["small", "big"], cells=[])
    panel = resolve_panel_for_page(page, devices, settings)
    assert (panel.w, panel.h) == (1424, 1200)
    # Reorder bind list, same answer.
    page_reordered = Page(id="p", name="P", device_ids=["big", "small"], cells=[])
    assert resolve_panel_for_page(page_reordered, devices, settings) == panel


def test_push_groups_split_by_gamut(env) -> None:
    """Two devices with identical dims but different colour gamuts must
    render as separate push groups, each frame packs to its own palette."""
    devices, settings, make = env
    make("esp32_e6")  # default waveshare_e6
    make("esp32_7c", gamut="inky_7colour")  # same 800x480, 7-colour

    page = Page(id="p", name="P", device_ids=["esp32_e6", "esp32_7c"], cells=[])
    groups = panel_groups_for_push(page, devices, settings)

    by_gamut = {p.gamut: ids for p, ids in groups}
    assert by_gamut["waveshare_e6"] == ["esp32_e6"]
    assert by_gamut["inky_7colour"] == ["esp32_7c"]
    assert len(groups) == 2


def test_panel_gamut_defaults_and_surfaces(env) -> None:
    """A device with no gamut set resolves to waveshare_e6; an explicit
    gamut surfaces onto the resolved Panel."""
    devices, settings, make = env
    make("esp32_plain")
    make("esp32_inky", gamut="inky_7colour")

    plain = resolve_panel_for_page(
        Page(id="a", name="A", device_ids=["esp32_plain"], cells=[]), devices, settings
    )
    inky = resolve_panel_for_page(
        Page(id="b", name="B", device_ids=["esp32_inky"], cells=[]), devices, settings
    )
    assert plain.gamut == "waveshare_e6"
    assert inky.gamut == "inky_7colour"


def test_push_groups_split_by_underscan(env) -> None:
    """Same-size panels with different mat insets render separately so each
    group's Panel carries the right underscan for its renderers."""
    devices, settings, make = env
    make("esp32_flush")  # underscan 0
    make("esp32_matted", underscan=20)
    page = Page(id="p", name="P", device_ids=["esp32_flush", "esp32_matted"], cells=[])
    groups = panel_groups_for_push(page, devices, settings)
    by_us = {p.underscan: ids for p, ids in groups}
    assert by_us[0] == ["esp32_flush"]
    assert by_us[20] == ["esp32_matted"]
    assert len(groups) == 2


def test_panel_underscan_surfaces(env) -> None:
    devices, settings, make = env
    make("esp32_mat", underscan=15)
    panel = resolve_panel_for_page(
        Page(id="a", name="A", device_ids=["esp32_mat"], cells=[]), devices, settings
    )
    assert panel.underscan == 15


def test_panel_vflip_surfaces_end_to_end(env) -> None:
    """v0.69.18 (issue #65, regression from v0.69.16): the ``vflip`` opt-in
    declared on the PicPak kind's manifest must reach the resolved Panel
    the renderer sees, so the row-reverse step in ``esp32_bin`` actually
    fires. Broken end to end pre-v0.69.18 because three separate panel-key
    allow-lists (``device_loader``, ``panel``, ``push``) each dropped the
    flag silently.

    Locks the flow: create a picpak instance, resolve the page's Panel,
    assert ``vflip`` is True. Any of the three allow-lists dropping the
    flag again would flip this test red."""
    devices, settings, make = env
    make("picpak_a", kind_id="picpak_client")
    picpak = resolve_panel_for_page(
        Page(id="a", name="A", device_ids=["picpak_a"], cells=[]), devices, settings
    )
    assert picpak.vflip is True

    # Non-vflip panels stay False so unrelated devices aren't dragged
    # into a row-reverse they don't need.
    make("esp32_plain")
    plain = resolve_panel_for_page(
        Page(id="b", name="B", device_ids=["esp32_plain"], cells=[]), devices, settings
    )
    assert plain.vflip is False


def test_unknown_device_ids_are_skipped(env) -> None:
    """A page bound to an id that no longer exists falls back to the
    virtual panel rather than erroring."""
    devices, settings, _make = env
    page = Page(id="p", name="P", device_ids=["ghost"], cells=[])
    groups = panel_groups_for_push(page, devices, settings)
    assert len(groups) == 1 and groups[0][1] == []


def test_landscape_native_preset_populates_native_dims(tmp_path: Path) -> None:
    """``resolve_settings_panel`` carries the preset's firmware-native
    row stride through to ``Panel.native_w / native_h``. For a
    landscape-native preset (Inky 7.3") the native dims equal the
    landscape composition; portrait calibration just swaps the
    composition w/h."""
    from app.panel import resolve_settings_panel

    settings = SettingsStore(tmp_path / "s.json")
    settings.update_section("app", {"panel_preset": "inky_7_3", "panel_orientation": "landscape"})
    panel = resolve_settings_panel(settings)
    assert (panel.w, panel.h) == (800, 480)
    assert (panel.native_w, panel.native_h) == (800, 480)

    settings.update_section("app", {"panel_preset": "inky_7_3", "panel_orientation": "portrait"})
    panel = resolve_settings_panel(settings)
    assert (panel.w, panel.h) == (480, 800)  # portrait composition
    assert (panel.native_w, panel.native_h) == (800, 480)  # firmware stride unchanged


def test_portrait_native_preset_populates_native_dims(tmp_path: Path) -> None:
    """Waveshare 13.3" Spectra 6 (used with ESP32) is portrait-native
    (1200×1600). The preset's landscape composition view is the
    rotated (1600, 1200), but native_w/native_h always carry the
    portrait stride regardless of which way the user mounts it."""
    from app.panel import resolve_settings_panel

    settings = SettingsStore(tmp_path / "s.json")
    settings.update_section(
        "app", {"panel_preset": "waveshare_e6_13_3", "panel_orientation": "portrait"}
    )
    panel = resolve_settings_panel(settings)
    assert (panel.w, panel.h) == (1200, 1600)
    assert (panel.native_w, panel.native_h) == (1200, 1600)

    settings.update_section(
        "app", {"panel_preset": "waveshare_e6_13_3", "panel_orientation": "landscape"}
    )
    panel = resolve_settings_panel(settings)
    assert (panel.w, panel.h) == (1600, 1200)  # landscape composition
    assert (panel.native_w, panel.native_h) == (1200, 1600)  # firmware stride unchanged


def test_custom_panel_has_no_native_dims(tmp_path: Path) -> None:
    """Custom dims can't tell us the firmware orientation without an
    extra UI knob, so the renderer falls back to packing at (w, h).
    ``native_w / native_h`` stay None for the custom path."""
    from app.panel import resolve_settings_panel

    settings = SettingsStore(tmp_path / "s.json")
    settings.update_section(
        "app",
        {
            "panel_preset": "custom",
            "panel_w": 1024,
            "panel_h": 768,
            "panel_orientation": "landscape",
        },
    )
    panel = resolve_settings_panel(settings)
    assert (panel.w, panel.h) == (1024, 768)
    assert panel.native_w is None
    assert panel.native_h is None


def _virtual(w: int, h: int):
    from app.state.page_store import Panel

    return Panel(w=w, h=h)


# -- fit_cells_to_panel top-strip anchoring (v0.71.1) ------------------


def test_fit_cells_to_panel_rotates_layout_to_match_target_orientation():
    """Baseline: without the top_strip hint, a landscape layout auto-
    rotates 90° clockwise to fit a portrait target. Locks in the pre-
    v0.71.1 behaviour."""
    # Bar at (0, 0, 800, 48) followed by one big body cell.
    cells = [(0, 0, 800, 48), (0, 48, 800, 432)]
    fitted = fit_cells_to_panel(cells, 480, 800)
    # Landscape -> portrait rotates; bar's top-edge (y=0) maps to the
    # right edge. This is exactly the misplacement bug the top_strip
    # hint fixes.
    bar = fitted[0]
    assert bar[0] > 200, f"expected bar's x on the right edge after rotation, got {bar}"


def test_fit_cells_to_panel_top_strip_anchors_to_top_of_portrait_target():
    """With top_strip_index set, the status-bar-style cell stays at
    (0, 0, target_w, ~strip_h) regardless of rotation. Others are
    fitted into the below-strip band."""
    cells = [(0, 0, 800, 48), (0, 48, 800, 432)]
    fitted = fit_cells_to_panel(cells, 480, 800, top_strip_index=0)
    bar = fitted[0]
    assert bar[0] == 0
    assert bar[1] == 0
    assert bar[2] == 480  # full width of target
    # Strip height scaled proportionally (design short = 480,
    # target short = 480, so 48 stays about 48).
    assert 40 <= bar[3] <= 60, f"strip height off, got {bar}"
    # Body cell sits below the strip.
    body = fitted[1]
    assert body[1] >= bar[3]
    assert body[1] + body[3] <= 800


def test_fit_cells_to_panel_top_strip_keeps_top_when_orientation_matches():
    """Same orientation (landscape design -> landscape target) still
    anchors the strip at the top; the code path is orientation-
    independent."""
    cells = [(0, 0, 800, 48), (0, 48, 800, 432)]
    fitted = fit_cells_to_panel(cells, 1200, 720, top_strip_index=0)
    bar = fitted[0]
    assert (bar[0], bar[1]) == (0, 0)
    assert bar[2] == 1200
    # Design short axis 480; target short axis 720. Strip scales by
    # 720/480 = 1.5, so 48 -> 72.
    assert bar[3] == 72


def test_fit_cells_to_panel_top_strip_only_cell():
    """Degenerate: page with only the status bar cell still renders
    it at the top of the target."""
    fitted = fit_cells_to_panel([(0, 0, 800, 48)], 480, 800, top_strip_index=0)
    assert len(fitted) == 1
    x, y, w, h = fitted[0]
    assert (x, y, w) == (0, 0, 480)
    assert h > 0
