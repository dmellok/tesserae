"""Phase 1c: transport_loader discovers transports/<id>/transport.json
manifests at startup.

The loader is metadata-only: it surfaces the LIST of available
transports + their capabilities so the Settings UI can list them and so
a future third transport can be added by dropping a folder. The actual
transport implementations live elsewhere (app/transport.py for MQTT,
app/rest_api.py for REST); these tests cover discovery + validation +
registry semantics, not the implementations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.main import REPO_ROOT
from app.transport_loader import TransportRegistry, discover


@pytest.fixture
def schema_path() -> Path:
    return REPO_ROOT / "schema" / "transport.schema.json"


def test_discovers_bundled_mqtt_and_rest(schema_path: Path) -> None:
    """The two transports shipped in tree (transports/mqtt + transports/
    rest) load without errors and expose their declared metadata."""
    registry = discover(REPO_ROOT / "transports", schema_path=schema_path)
    assert registry.errors == [], registry.errors

    mqtt = registry.get("mqtt")
    assert mqtt is not None
    assert mqtt.kind == "push"
    assert mqtt.broker_required is True
    assert mqtt.capabilities.get("retain_semantics") is True
    assert mqtt.capabilities.get("push_to_device") is True

    rest = registry.get("rest")
    assert rest is not None
    assert rest.kind == "pull"
    assert rest.broker_required is False
    assert rest.capabilities.get("retain_semantics") is True
    assert rest.capabilities.get("push_to_device") is False


def test_all_returns_alphabetically_sorted(schema_path: Path) -> None:
    """``all()`` returns transports in deterministic order across
    renders (alphabetical by id), so the Settings UI doesn't
    re-shuffle the list on each request."""
    registry = discover(REPO_ROOT / "transports", schema_path=schema_path)
    ids = [t.id for t in registry.all()]
    assert ids == sorted(ids)


def test_default_returns_rest_per_manifest_flag(schema_path: Path) -> None:
    """REST declares ``default_for_new_devices: true`` so it's what the
    onboarding wizard + Pair card pick for new devices."""
    registry = discover(REPO_ROOT / "transports", schema_path=schema_path)
    default = registry.default()
    assert default is not None
    assert default.id == "rest"


def test_missing_transport_json_logged_as_error(tmp_path: Path, schema_path: Path) -> None:
    """A folder under transports/ without a transport.json shows as a
    LoaderError rather than killing the boot path."""
    (tmp_path / "broken").mkdir()
    registry = discover(tmp_path, schema_path=schema_path)
    assert registry.transports == {}
    assert len(registry.errors) == 1
    assert "transport.json missing" in registry.errors[0].message


def test_invalid_json_caught(tmp_path: Path, schema_path: Path) -> None:
    """Malformed JSON manifests don't blow up discovery."""
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "transport.json").write_text("{not valid json", encoding="utf-8")
    registry = discover(tmp_path, schema_path=schema_path)
    assert registry.transports == {}
    assert len(registry.errors) == 1
    assert "invalid JSON" in registry.errors[0].message


def test_schema_validation_catches_missing_required_fields(
    tmp_path: Path, schema_path: Path
) -> None:
    """Manifests missing required fields are caught by jsonschema."""
    good = tmp_path / "fishy"
    good.mkdir()
    (good / "transport.json").write_text(
        json.dumps(
            {
                "tesserae_compat": "1.x",
                "id": "fishy",
                # missing name, version, kind
            }
        ),
        encoding="utf-8",
    )
    registry = discover(tmp_path, schema_path=schema_path)
    assert registry.transports == {}
    assert len(registry.errors) == 1
    assert "manifest schema" in registry.errors[0].message


def test_compat_mismatch_rejected(tmp_path: Path, schema_path: Path) -> None:
    """A transport declaring tesserae_compat=9.x (a future major)
    won't load against this host (major version 1)."""
    weird = tmp_path / "weird"
    weird.mkdir()
    (weird / "transport.json").write_text(
        json.dumps(
            {
                "tesserae_compat": "9.x",
                "id": "weird",
                "name": "Time-travelling transport",
                "version": "1.0.0",
                "kind": "push",
            }
        ),
        encoding="utf-8",
    )
    registry = discover(tmp_path, schema_path=schema_path)
    assert registry.transports == {}
    assert len(registry.errors) == 1
    assert "tesserae_compat" in registry.errors[0].message


def test_id_must_match_folder_name(tmp_path: Path, schema_path: Path) -> None:
    """A typo where the manifest's ``id`` doesn't match the folder
    name is a hard error, otherwise Device.transport would lookup a
    transport id that resolves to a different directory than its
    implementation lives in."""
    typo_dir = tmp_path / "websocket"
    typo_dir.mkdir()
    (typo_dir / "transport.json").write_text(
        json.dumps(
            {
                "tesserae_compat": "1.x",
                "id": "ws",  # mismatch with folder name
                "name": "WebSocket",
                "version": "1.0.0",
                "kind": "push",
            }
        ),
        encoding="utf-8",
    )
    registry = discover(tmp_path, schema_path=schema_path)
    assert registry.transports == {}
    assert len(registry.errors) == 1
    assert "does not match folder name" in registry.errors[0].message


def test_registry_installed_in_app_config() -> None:
    """End-to-end: the factory installs TRANSPORT_REGISTRY in
    app.config so the Settings UI + push pipeline can read it."""
    import tempfile

    from app.main import create_app

    with tempfile.TemporaryDirectory() as td:
        app = create_app(
            testing=True,
            data_root=Path(td),
            plugins_dir=REPO_ROOT / "plugins",
            renderers_dir=REPO_ROOT / "renderers",
            devices_dir=REPO_ROOT / "devices",
        )
        registry = app.config.get("TRANSPORT_REGISTRY")
        assert isinstance(registry, TransportRegistry)
        assert registry.get("mqtt") is not None
        assert registry.get("rest") is not None
