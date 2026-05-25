"""SettingsStore: secret rename on disk, masking for admin reads, real
values for runtime reads, defaults from manifest."""

from __future__ import annotations

import json
from pathlib import Path

from app.state.settings_store import SECRET_MASK, SettingsStore

WIDGET_FIELDS = [
    {"name": "base_url", "type": "string", "label": "URL", "default": "http://x"},
    {"name": "api_key", "type": "string", "label": "Key", "secret": True},
    {"name": "show_debug", "type": "boolean", "label": "Debug", "default": False},
]


def test_secret_renamed_on_disk(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"base_url": "http://api.example", "api_key": "xyzzy", "show_debug": True},
        WIDGET_FIELDS,
    )
    on_disk = json.loads((tmp_path / "s.json").read_text())
    assert on_disk["plugins"]["weather"] == {
        "base_url": "http://api.example",
        "api_key_secret": "xyzzy",
        "show_debug": True,
    }


def test_get_for_runtime_strips_secret_suffix(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"api_key": "xyzzy"},
        WIDGET_FIELDS,
    )
    runtime = store.get_for_runtime("plugins", "weather", WIDGET_FIELDS)
    assert runtime["api_key"] == "xyzzy"
    # Defaults from the manifest fill in the gaps.
    assert runtime["base_url"] == "http://x"
    assert runtime["show_debug"] is False


def test_get_for_admin_masks_secrets(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"api_key": "xyzzy", "show_debug": True},
        WIDGET_FIELDS,
    )
    admin = store.get_for_admin("plugins", "weather", WIDGET_FIELDS)
    assert admin["api_key"] == SECRET_MASK
    assert admin["show_debug"] is True


def test_resubmit_masked_secret_keeps_existing_value(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace("plugins", "weather", {"api_key": "original"}, WIDGET_FIELDS)
    # The UI displays the mask; if the user re-submits without editing it,
    # the on-disk secret must NOT be replaced with the mask.
    store.update_for_namespace(
        "plugins", "weather", {"api_key": SECRET_MASK, "show_debug": True}, WIDGET_FIELDS
    )
    runtime = store.get_for_runtime("plugins", "weather", WIDGET_FIELDS)
    assert runtime["api_key"] == "original"
    assert runtime["show_debug"] is True


def test_update_ignores_keys_not_in_manifest(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_for_namespace(
        "plugins",
        "weather",
        {"api_key": "k", "rogue_field": "junk"},
        WIDGET_FIELDS,
    )
    on_disk = json.loads((tmp_path / "s.json").read_text())
    assert "rogue_field" not in on_disk["plugins"]["weather"]


def test_section_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    store.update_section("broker", {"host": "h", "port": 1883})
    store.patch_section("broker", {"port": 8883, "username": "u"})
    assert store.get_section("broker") == {"host": "h", "port": 8883, "username": "u"}


def test_persistence_across_instances(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    a = SettingsStore(p)
    a.update_section("app", {"base_url": "http://lan/"})
    b = SettingsStore(p)
    assert b.get_section("app") == {"base_url": "http://lan/"}
