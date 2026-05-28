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
    panel_groups_for_push,
    preview_groups_for_page,
    resolve_panel_for_page,
)
from app.state.page_store import Page
from app.state.settings_store import SettingsStore


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — test fixture
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

    def make(instance_id: str, orientation: str | None = None) -> None:
        device_service.create_instance(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id=instance_id,
            kind_id="esp32_client",
            orientation=orientation,
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

    page = Page(
        id="p", name="P", device_ids=["esp32_a", "esp32_flip", "esp32_tall"], cells=[]
    )
    previews = preview_groups_for_page(page, devices, settings)

    by_label = {g["label"]: g for g in previews}
    assert set(by_label) == {"Landscape 5:3", "Portrait 3:5"}
    assert sorted(by_label["Landscape 5:3"]["devices"]) == ["esp32_a", "esp32_flip"]
    assert by_label["Portrait 3:5"]["devices"] == ["esp32_tall"]
    assert (by_label["Landscape 5:3"]["w"], by_label["Landscape 5:3"]["h"]) == (800, 480)


def test_first_bound_device_drives_single_panel_contexts(env) -> None:
    """``resolve_panel_for_page`` (editor layout grid, default compose)
    uses the first targeted device's panel, ignoring the virtual one."""
    devices, settings, make = env
    make("esp32_tall", orientation="portrait")  # 480x800
    page = Page(id="p", name="P", device_ids=["esp32_tall"], cells=[])
    panel = resolve_panel_for_page(page, devices, settings)
    assert (panel.w, panel.h) == (480, 800)


def test_unknown_device_ids_are_skipped(env) -> None:
    """A page bound to an id that no longer exists falls back to the
    virtual panel rather than erroring."""
    devices, settings, _make = env
    page = Page(id="p", name="P", device_ids=["ghost"], cells=[])
    groups = panel_groups_for_push(page, devices, settings)
    assert len(groups) == 1 and groups[0][1] == []


def _virtual(w: int, h: int):  # noqa: ANN202
    from app.state.page_store import Panel

    return Panel(w=w, h=h)
