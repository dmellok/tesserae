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
    # transform-only — no payload export.
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
