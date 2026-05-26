"""Plugin loader contract: discovery, compat rejection, asset routing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import plugin_loader
from app.main import REPO_ROOT


@pytest.fixture
def schema_path() -> Path:
    return REPO_ROOT / "schema" / "plugin.schema.json"


def _write_minimal_plugin(root: Path, name: str, manifest_overrides: dict) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": "Toy",
        "version": "0.0.1",
        "kind": "widget",
        "supports": {"sizes": ["md"]},
    }
    manifest.update(manifest_overrides)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest))
    (plugin_dir / "client.js").write_text("export default function () {}\n")
    return plugin_dir


def test_discovers_bundled_plugins(tmp_path: Path, schema_path: Path) -> None:
    """Smoke test against the real plugins dir: themes_core is the only
    bundled plugin and it parses cleanly, with at least one theme."""
    registry = plugin_loader.discover(
        REPO_ROOT / "plugins",
        schema_path=schema_path,
        data_root=tmp_path,
    )
    assert "themes_core" in registry.plugins
    assert registry.errors == []
    assert registry.get_theme("default") is not None


def test_plugin_id_is_folder_name(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_minimal_plugin(plugins_dir, "renamed_widget", {"name": "Renamed"})
    registry = plugin_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "renamed_widget" in registry.plugins
    assert registry.plugins["renamed_widget"].id == "renamed_widget"


def test_compat_mismatch_is_rejected(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_minimal_plugin(plugins_dir, "from_future", {"tesserae_compat": "9.x"})
    registry = plugin_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "from_future" not in registry.plugins
    assert any("tesserae_compat" in err.message for err in registry.errors)


def test_compat_same_major_minor_pinned(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_minimal_plugin(plugins_dir, "pinned", {"tesserae_compat": "1.0"})
    registry = plugin_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "pinned" in registry.plugins


def test_invalid_manifest_is_rejected(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_minimal_plugin(plugins_dir, "broken", {"kind": "not-a-kind"})
    registry = plugin_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "broken" not in registry.plugins
    assert any("manifest schema" in err.message for err in registry.errors)


def test_cell_option_defaults_merged() -> None:
    manifest = {
        "tesserae_compat": "1.x",
        "name": "Toy",
        "version": "0.0.1",
        "kind": "widget",
        "supports": {"sizes": ["md"]},
        "cell_options": [
            {"name": "a", "type": "string", "label": "A", "default": "x"},
            {"name": "b", "type": "boolean", "label": "B", "default": True},
            {"name": "c", "type": "string", "label": "C"},
        ],
    }
    plugin = plugin_loader.Plugin(
        id="toy",
        path=Path("/tmp/toy"),
        manifest=manifest,
        data_dir=Path("/tmp/toy_data"),
    )
    defaults = plugin.cell_option_defaults()
    assert defaults == {"a": "x", "b": True}
