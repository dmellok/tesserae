"""Backfill firmware-native panel dims on pre-v0.20 esp32 device manifests.

Devices created before the PanelPreset refactor have no native_w /
native_h on disk. The dims-only fallback in ``device_panel`` is
ambiguous for the 1200×1600 / 1600×1200 case (Inky 13.3" vs Waveshare
13.3" Spectra 6) — the latter is portrait-native and the former is
landscape-native. Wrong stride → distorted panel.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.device_service import backfill_native_panel_dims


def _write(path: Path, raw: dict) -> None:
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_backfill_adds_native_dims_to_waveshare_13_3(tmp_path: Path) -> None:
    """The 1200×1600 esp32 case is the load-bearing one. Without backfill,
    the dims-only matcher in device_panel picks Inky 13.3" (1600×1200,
    landscape-native), packs at the wrong row stride, and the panel
    paints a tiled-looking distortion."""
    path = tmp_path / "esp32_office.json"
    _write(
        path,
        {
            "id": "esp32_office",
            "kind": "esp32_client",
            "name": "Office",
            "panel": {"w": 1200, "h": 1600, "orientation": "portrait"},
        },
    )
    assert backfill_native_panel_dims(tmp_path) == ["esp32_office"]
    panel = _read(path)["panel"]
    assert (panel["native_w"], panel["native_h"]) == (1200, 1600)


def test_backfill_handles_landscape_mounted_waveshare(tmp_path: Path) -> None:
    """Same panel, mounted sideways (1600×1200). The native stride is
    still the portrait 1200×1600 — that's intrinsic to the hardware,
    not the mount."""
    path = tmp_path / "esp32_office.json"
    _write(
        path,
        {
            "id": "esp32_office",
            "kind": "esp32_client",
            "name": "Office",
            "panel": {"w": 1600, "h": 1200, "orientation": "landscape"},
        },
    )
    backfill_native_panel_dims(tmp_path)
    panel = _read(path)["panel"]
    assert (panel["native_w"], panel["native_h"]) == (1200, 1600)


def test_backfill_handles_800x480_landscape_native(tmp_path: Path) -> None:
    """PhotoPainter / Waveshare 7.5" / Inky 7.3" via ESP32 — all
    landscape-native 800×480. Either mount orientation backfills to
    the same native stride."""
    landscape = tmp_path / "esp32_a.json"
    _write(
        landscape,
        {
            "id": "esp32_a",
            "kind": "esp32_client",
            "name": "A",
            "panel": {"w": 800, "h": 480, "orientation": "landscape"},
        },
    )
    portrait = tmp_path / "esp32_b.json"
    _write(
        portrait,
        {
            "id": "esp32_b",
            "kind": "esp32_client",
            "name": "B",
            "panel": {"w": 480, "h": 800, "orientation": "portrait"},
        },
    )
    backfill_native_panel_dims(tmp_path)
    assert (_read(landscape)["panel"]["native_w"], _read(landscape)["panel"]["native_h"]) == (
        800,
        480,
    )
    assert (_read(portrait)["panel"]["native_w"], _read(portrait)["panel"]["native_h"]) == (
        800,
        480,
    )


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    """Already-migrated manifests are left alone — no re-write, not in
    the patched list. Safe to run at every startup."""
    path = tmp_path / "esp32_done.json"
    _write(
        path,
        {
            "id": "esp32_done",
            "kind": "esp32_client",
            "name": "Done",
            "panel": {
                "w": 1200,
                "h": 1600,
                "orientation": "portrait",
                "native_w": 1200,
                "native_h": 1600,
            },
        },
    )
    before = path.read_text(encoding="utf-8")
    assert backfill_native_panel_dims(tmp_path) == []
    assert path.read_text(encoding="utf-8") == before


def test_backfill_skips_non_esp32_kinds(tmp_path: Path) -> None:
    """pi_bin / pi_png / trmnl_png don't read native_w/native_h — the
    migration leaves their manifests alone. Same dims, different kind,
    no patch."""
    path = tmp_path / "lounge.json"
    _write(
        path,
        {
            "id": "lounge",
            "kind": "pi_bin_client",
            "name": "Lounge",
            "panel": {"w": 1200, "h": 1600, "orientation": "portrait"},
        },
    )
    assert backfill_native_panel_dims(tmp_path) == []
    panel = _read(path)["panel"]
    assert "native_w" not in panel
    assert "native_h" not in panel


def test_backfill_skips_unknown_dims(tmp_path: Path) -> None:
    """Custom-dim esp32 devices can't be inferred from the hardcoded
    table. Leave them alone so the user / device_panel fall back to the
    composition-dim path instead of getting a wrong guess written to
    disk."""
    path = tmp_path / "esp32_weird.json"
    _write(
        path,
        {
            "id": "esp32_weird",
            "kind": "esp32_client",
            "name": "Weird",
            "panel": {"w": 1024, "h": 768, "orientation": "landscape"},
        },
    )
    assert backfill_native_panel_dims(tmp_path) == []
    panel = _read(path)["panel"]
    assert "native_w" not in panel


def test_backfill_returns_empty_on_missing_dir(tmp_path: Path) -> None:
    """A fresh install with no devices/ dir yet — no-op, no exception."""
    assert backfill_native_panel_dims(tmp_path / "does-not-exist") == []
