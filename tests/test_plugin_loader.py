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
    """Smoke test against the real plugins dir: bundled fonts_core parses
    cleanly with at least one font."""
    registry = plugin_loader.discover(
        REPO_ROOT / "plugins",
        schema_path=schema_path,
        data_root=tmp_path,
    )
    assert "fonts_core" in registry.plugins
    assert registry.errors == []
    assert len(registry.fonts) > 0


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


def test_palette_defaults_to_strict() -> None:
    """Widgets without a ``design`` block read as strict, the default
    behaviour predating the opt-in. Matches the loader's contract that
    absent fields don't change existing widgets."""
    plugin = plugin_loader.Plugin(
        id="toy",
        path=Path("/tmp/toy"),
        manifest={"kind": "widget", "name": "Toy", "supports": {"sizes": ["md"]}},
        data_dir=Path("/tmp/toy_data"),
    )
    assert plugin.palette == "strict"


def test_palette_extended_opt_in() -> None:
    """``design.palette: "extended"`` flips the property, signalling
    to reviewers + future device-side logic that this widget uses the
    dither pass to approximate arbitrary CSS colours on the panel."""
    plugin = plugin_loader.Plugin(
        id="fancy",
        path=Path("/tmp/fancy"),
        manifest={
            "kind": "widget",
            "name": "Fancy",
            "supports": {"sizes": ["md"]},
            "design": {"palette": "extended"},
        },
        data_dir=Path("/tmp/fancy_data"),
    )
    assert plugin.palette == "extended"


def test_palette_garbage_value_falls_back_to_strict() -> None:
    """A malformed palette string (e.g. typo'd by a contributor)
    falls back to strict so a bad manifest doesn't quietly grant the
    extended opt-in. Schema validation upstream catches the literal
    bad value; this guards the runtime accessor too."""
    plugin = plugin_loader.Plugin(
        id="typo",
        path=Path("/tmp/typo"),
        manifest={
            "kind": "widget",
            "name": "Typo",
            "supports": {"sizes": ["md"]},
            "design": {"palette": "fancy"},
        },
        data_dir=Path("/tmp/typo_data"),
    )
    assert plugin.palette == "strict"


def test_extended_palette_manifest_passes_schema(tmp_path: Path, schema_path: Path) -> None:
    """End-to-end: a manifest declaring the extended palette opt-in
    discovers without schema errors. Locks in the schema's enum +
    object shape so a future schema bump can't quietly break the
    contract."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_minimal_plugin(
        plugins_dir,
        "scenic_toy",
        {"design": {"palette": "extended"}},
    )
    registry = plugin_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "scenic_toy" in registry.plugins
    assert registry.plugins["scenic_toy"].palette == "extended"


def test_discover_merges_additional_plugins_dirs(tmp_path: Path, schema_path: Path) -> None:
    """0.42.2: marketplace-installed widgets live under a separate
    user dir (data/marketplace/) so they survive Docker image
    upgrades. The loader walks both the bundled dir and the
    additional dirs, merging the resulting registries."""
    bundled = tmp_path / "plugins"
    bundled.mkdir()
    user = tmp_path / "marketplace"
    user.mkdir()
    _write_minimal_plugin(bundled, "bundled_widget", {"name": "Bundled"})
    _write_minimal_plugin(user, "user_widget", {"name": "User"})

    registry = plugin_loader.discover(
        bundled,
        schema_path=schema_path,
        data_root=tmp_path / "data",
        additional_plugins_dirs=[user],
    )
    assert "bundled_widget" in registry.plugins
    assert "user_widget" in registry.plugins


def test_discover_bundled_wins_on_duplicate_id(tmp_path: Path, schema_path: Path) -> None:
    """If a user has a marketplace widget that shares an id with a
    bundled widget (rare; happens if a marketplace widget gets
    adopted into the bundle later), the bundled copy wins and the
    user copy is rejected with a 'duplicate plugin id' error so the
    admin notices and resolves manually."""
    bundled = tmp_path / "plugins"
    bundled.mkdir()
    user = tmp_path / "marketplace"
    user.mkdir()
    _write_minimal_plugin(bundled, "shared", {"name": "Bundled copy"})
    _write_minimal_plugin(user, "shared", {"name": "User copy"})

    registry = plugin_loader.discover(
        bundled,
        schema_path=schema_path,
        data_root=tmp_path / "data",
        additional_plugins_dirs=[user],
    )
    assert registry.plugins["shared"].name == "Bundled copy"
    assert any(err.plugin_id == "shared" and "duplicate" in err.message for err in registry.errors)
