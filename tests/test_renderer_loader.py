"""Renderer loader contract: discovery, compat rejection, signature validation,
topic resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import renderer_loader
from app.main import REPO_ROOT


@pytest.fixture
def schema_path() -> Path:
    return REPO_ROOT / "schema" / "renderer.schema.json"


def _write_renderer(root: Path, name: str, manifest_overrides: dict, body: str) -> Path:
    rdir = root / name
    rdir.mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": name,
        "version": "0.0.1",
        "orientation": "composition",
        "mime": "application/octet-stream",
        "extension": "bin",
        "topic_pattern": "tesserae/{device}/frame/bin",
        "device": "pi",
        "retain": False,
    }
    manifest.update(manifest_overrides)
    (rdir / "renderer.json").write_text(json.dumps(manifest))
    (rdir / "renderer.py").write_text(body)
    return rdir


_VALID_BODY = """
def transform(png_bytes, *, panel, settings):
    return b"OK"

def payload(digest, base_url, *, settings):
    return {"url": f"{base_url}/renders/{digest}.bin"}
"""


def test_discovers_bundled_pi_png(tmp_path: Path, schema_path: Path) -> None:
    registry = renderer_loader.discover(
        REPO_ROOT / "renderers", schema_path=schema_path, data_root=tmp_path
    )
    assert registry.errors == []
    assert "pi_png" in registry.renderers


def test_topic_substitution(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    _write_renderer(plugins_dir, "esp32_bin", {"device": "esp32"}, _VALID_BODY)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    renderer = registry.get("esp32_bin")
    assert renderer is not None
    assert renderer.topic == "tesserae/esp32/frame/bin"


def test_seed_device_settings_copies_base_to_empty_clones(
    tmp_path: Path, schema_path: Path
) -> None:
    """Picture-quality (``device_setting: true``) fields used to live on
    the renderer card. ``seed_device_settings_from_base`` carries any
    legacy renderer-wide value forward into the per-device clone if the
    clone hasn't been tuned yet, so upgrading is invisible and a newly-
    added device matches the rest of the fleet."""
    from app.state.settings_store import SettingsStore

    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    manifest = {
        "device": "pi_bin",
        "settings": [
            {
                "name": "saturation",
                "type": "slider",
                "label": "Sat",
                "default": 1.4,
                "device_setting": True,
            },
            # A renderer-wide field that should NOT be touched by the seed.
            {"name": "topic_prefix", "type": "text", "label": "Pfx", "default": "x"},
        ],
    }
    _write_renderer(plugins_dir, "pi_bin", manifest, _VALID_BODY)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )

    # Mint a clone manually (clone_for_instances needs a device registry;
    # for this unit test the synthetic clone is enough).
    base = registry.get("pi_bin")
    assert base is not None
    cloned_manifest = dict(base.manifest)
    cloned_manifest["device"] = "kitchen"
    cloned_manifest["name"] = f"{base.name} (kitchen)"
    registry.renderers["pi_bin__kitchen"] = renderer_loader.Renderer(
        id="pi_bin__kitchen",
        path=base.path,
        manifest=cloned_manifest,
        module=base.module,
        data_dir=base.data_dir,
    )

    # Seed the base with the legacy renderer-wide saturation value.
    store = SettingsStore(tmp_path / "settings.json")
    store.update_for_namespace("renderers", "pi_bin", {"saturation": 1.7}, manifest["settings"])

    renderer_loader.seed_device_settings_from_base(registry, store)

    clone_saved = store.get_for_runtime("renderers", "pi_bin__kitchen", manifest["settings"])
    assert clone_saved["saturation"] == 1.7

    # Idempotent: running again doesn't blow away clone-level changes.
    store.update_for_namespace(
        "renderers", "pi_bin__kitchen", {"saturation": 2.0}, manifest["settings"]
    )
    renderer_loader.seed_device_settings_from_base(registry, store)
    re_read = store.get_for_runtime("renderers", "pi_bin__kitchen", manifest["settings"])
    assert re_read["saturation"] == 2.0


def test_for_device_filters(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    _write_renderer(plugins_dir, "pi_a", {"device": "pi"}, _VALID_BODY)
    _write_renderer(
        plugins_dir,
        "pi_b",
        {"device": "pi", "extension": "png", "mime": "image/png"},
        _VALID_BODY,
    )
    _write_renderer(plugins_dir, "esp32_a", {"device": "esp32"}, _VALID_BODY)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    pi_ids = sorted(r.id for r in registry.for_device("pi"))
    assert pi_ids == ["pi_a", "pi_b"]


def test_compat_mismatch_is_rejected(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    _write_renderer(plugins_dir, "future", {"tesserae_compat": "9.x"}, _VALID_BODY)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "future" not in registry.renderers
    assert any("tesserae_compat" in err.message for err in registry.errors)


def test_missing_export_is_caught(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    # transform-only, no payload export.
    body = "def transform(png_bytes, *, panel, settings):\n    return b''\n"
    _write_renderer(plugins_dir, "halfbaked", {}, body)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "halfbaked" not in registry.renderers
    assert any("payload" in err.message for err in registry.errors)


def test_drifted_signature_is_caught(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    # No 'panel' kwarg on transform.
    body = """
def transform(png_bytes, *, settings):
    return b""

def payload(digest, base_url, *, settings):
    return {"url": "x"}
"""
    _write_renderer(plugins_dir, "drifted", {}, body)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    assert "drifted" not in registry.renderers
    assert any("panel" in err.message for err in registry.errors)


def test_transform_must_return_bytes(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    body = """
def transform(png_bytes, *, panel, settings):
    return "not bytes"

def payload(digest, base_url, *, settings):
    return {"url": "x"}
"""
    _write_renderer(plugins_dir, "stringy", {}, body)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    renderer = registry.get("stringy")
    assert renderer is not None
    from app.state.page_store import Panel

    with pytest.raises(TypeError, match="expected bytes"):
        renderer.transform(b"", panel=Panel(w=1, h=1), settings={})


def test_payload_must_contain_url(tmp_path: Path, schema_path: Path) -> None:
    plugins_dir = tmp_path / "renderers"
    plugins_dir.mkdir()
    body = """
def transform(png_bytes, *, panel, settings):
    return b""

def payload(digest, base_url, *, settings):
    return {"missing": "url"}
"""
    _write_renderer(plugins_dir, "no_url", {}, body)
    registry = renderer_loader.discover(
        plugins_dir, schema_path=schema_path, data_root=tmp_path / "data"
    )
    renderer = registry.get("no_url")
    assert renderer is not None
    with pytest.raises(TypeError, match="url"):
        renderer.payload("abc", "http://x", settings={})
