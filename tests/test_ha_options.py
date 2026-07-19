"""HA Add-on Configuration → Tesserae settings."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app.ha_options import (
    apply_ha_options,
    apply_log_level,
    apply_to_settings,
    load_options,
    resolve_log_level,
)
from app.state.settings_store import SettingsStore


def _write_options(tmp_path: Path, payload: dict[str, Any]) -> Path:
    p = tmp_path / "options.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_load_options_missing_returns_none(tmp_path: Path) -> None:
    assert load_options(tmp_path / "nope.json") is None


def test_load_options_malformed_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text("{not valid", encoding="utf-8")
    assert load_options(p) is None


def test_load_options_non_object_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "options.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_options(p) is None


def test_apply_log_level_sets_root_logger() -> None:
    root = logging.getLogger()
    original = root.level
    try:
        apply_log_level({"log_level": "debug"})
        assert root.level == logging.DEBUG
        apply_log_level({"log_level": "warning"})
        assert root.level == logging.WARNING
    finally:
        root.setLevel(original)


def test_apply_log_level_unknown_is_noop() -> None:
    root = logging.getLogger()
    original = root.level
    try:
        root.setLevel(logging.INFO)
        apply_log_level({"log_level": "bogus"})
        assert root.level == logging.INFO
    finally:
        root.setLevel(original)


def test_apply_to_settings_patches_broker(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    apply_to_settings(
        {
            "mqtt_host": "core-mosquitto",
            "mqtt_port": 1883,
            "mqtt_username": "ha-user",
            "mqtt_password": "hunter2",
        },
        settings,
    )
    broker = settings.get_section("broker")
    assert broker["host"] == "core-mosquitto"
    assert broker["port"] == 1883
    assert broker["username"] == "ha-user"
    assert broker["password_secret"] == "hunter2"


def test_apply_to_settings_missing_keys_dont_clear(tmp_path: Path) -> None:
    """Sparse options.json shouldn't blank a previously-set username."""
    settings = SettingsStore(tmp_path / "settings.json")
    settings.patch_section("broker", {"username": "preset", "host": "preset.local"})
    apply_to_settings({"mqtt_host": "new.local"}, settings)
    broker = settings.get_section("broker")
    assert broker["host"] == "new.local"
    assert broker["username"] == "preset"


def test_apply_to_settings_empty_string_clears(tmp_path: Path) -> None:
    """Explicit empty string IS how a user clears the field on HA's form."""
    settings = SettingsStore(tmp_path / "settings.json")
    settings.patch_section("broker", {"username": "preset"})
    apply_to_settings({"mqtt_username": ""}, settings)
    assert settings.get_section("broker")["username"] == ""


def test_apply_to_settings_rejects_invalid_port(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    settings.patch_section("broker", {"port": 1883})
    apply_to_settings({"mqtt_port": 99999}, settings)
    assert settings.get_section("broker")["port"] == 1883


def test_apply_ha_options_end_to_end(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    options_path = _write_options(
        tmp_path,
        {
            "log_level": "debug",
            "mqtt_host": "core-mosquitto",
            "mqtt_port": 1883,
        },
    )
    root = logging.getLogger()
    original = root.level
    try:
        result = apply_ha_options(settings, options_path=options_path)
    finally:
        root.setLevel(original)
    assert result is not None
    broker = settings.get_section("broker")
    assert broker["host"] == "core-mosquitto"
    assert broker["port"] == 1883


def test_apply_ha_options_no_file_is_noop(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    assert apply_ha_options(settings, options_path=tmp_path / "missing.json") is None
    assert settings.get_section("broker") == {}


@pytest.mark.parametrize(
    "level,expected",
    [
        ("trace", logging.DEBUG),
        ("debug", logging.DEBUG),
        ("info", logging.INFO),
        ("notice", logging.INFO),
        ("warning", logging.WARNING),
        ("error", logging.ERROR),
        ("fatal", logging.CRITICAL),
    ],
)
def test_ha_log_levels_round_trip(level: str, expected: int) -> None:
    root = logging.getLogger()
    original = root.level
    try:
        root.setLevel(logging.WARNING)
        apply_log_level({"log_level": level})
        assert root.level == expected
    finally:
        root.setLevel(original)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("debug", logging.DEBUG),
        ("trace", logging.DEBUG),
        ("INFO", logging.INFO),
        ("warn", logging.WARNING),
        ("warning", logging.WARNING),
        ("critical", logging.CRITICAL),
        ("fatal", logging.CRITICAL),
        ("", None),
        (None, None),
        ("bogus", None),
    ],
)
def test_resolve_log_level(name: str | None, expected: int | None) -> None:
    assert resolve_log_level(name) == expected
