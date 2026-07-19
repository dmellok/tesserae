"""esp32 button-wake window (#123): schema field + validator bounds.

The stay-awake window after a button press is an optional, bounded int
config field on both ESP32 kinds. These assert the schema declares it and
the validator accepts in-range values, rejects out-of-range / non-int, and
still accepts a config that omits it (older firmware / forms).
"""

from __future__ import annotations

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture(params=["esp32_client", "esp32_bw_client"])
def esp32_kind(request, tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get(request.param)
    assert d is not None
    return d


def test_schema_declares_button_wake(esp32_kind) -> None:
    spec = esp32_kind.config_schema.get("button_wake_s")
    assert spec is not None
    assert spec["type"] == "int"
    assert spec["default"] == 0
    assert spec["min"] == 0
    assert spec["max"] == 60


def test_button_wake_in_range_is_accepted(esp32_kind) -> None:
    for wake in (0, 2, 5, 30, 60):
        ok, err = esp32_kind.validate_config({"sleep_interval_s": 300, "button_wake_s": wake})
        assert ok, err


def test_button_wake_out_of_range_is_rejected(esp32_kind) -> None:
    ok, err = esp32_kind.validate_config({"sleep_interval_s": 300, "button_wake_s": 61})
    assert not ok and err is not None and "button_wake_s" in err
    ok, err = esp32_kind.validate_config({"sleep_interval_s": 300, "button_wake_s": -1})
    assert not ok and err is not None and "button_wake_s" in err


def test_non_int_button_wake_is_rejected(esp32_kind) -> None:
    ok, err = esp32_kind.validate_config({"sleep_interval_s": 300, "button_wake_s": "soon"})
    assert not ok and err is not None and "button_wake_s" in err


def test_config_without_button_wake_still_valid(esp32_kind) -> None:
    # The field is optional so a form/firmware that predates it still passes.
    ok, err = esp32_kind.validate_config({"sleep_interval_s": 300})
    assert ok, err
