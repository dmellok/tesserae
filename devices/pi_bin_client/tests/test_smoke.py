"""pi_bin_client smoke: loader picks it up, parse_status round-trips JSON,
non-JSON payloads degrade to a 'raw' key."""

from __future__ import annotations

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def pi_bin_client(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("pi_bin_client")
    assert d is not None
    return d


def test_manifest_fields(pi_bin_client) -> None:
    assert pi_bin_client.name == "Pi BIN client"
    assert pi_bin_client.renderer_ids == ["pi_bin"]
    assert pi_bin_client.status_topic == "tesserae/pi_bin/status"
    # No config_topic declared — REST clients pick up the cadence from the
    # /api/v1/device/<id>/status response, MQTT clients ignore it.
    assert pi_bin_client.config_topic is None
    # sleep_interval_s lives in the config_schema so the device form
    # renders the field + the REST API has a value to echo as next_poll_s.
    assert "sleep_interval_s" in (pi_bin_client.config_schema or {})


def test_parse_status_round_trips_json(pi_bin_client) -> None:
    payload = b'{"state": "idle", "last_paint_at": 1716700000}'
    parsed = pi_bin_client.parse_status(payload)
    assert parsed == {"state": "idle", "last_paint_at": 1716700000}


def test_parse_status_handles_empty(pi_bin_client) -> None:
    assert pi_bin_client.parse_status(b"") == {"raw": ""}


def test_parse_status_handles_non_json(pi_bin_client) -> None:
    parsed = pi_bin_client.parse_status(b"hello there")
    assert parsed == {"raw": "hello there"}


def test_validate_config_accepts_in_range(pi_bin_client) -> None:
    ok, err = pi_bin_client.validate_config({"sleep_interval_s": 900})
    assert ok and err is None


def test_validate_config_rejects_below_min(pi_bin_client) -> None:
    ok, err = pi_bin_client.validate_config({"sleep_interval_s": 10})
    assert not ok
    assert err is not None and ">=" in err


def test_validate_config_rejects_above_max(pi_bin_client) -> None:
    ok, err = pi_bin_client.validate_config({"sleep_interval_s": 7 * 86400 + 1})
    assert not ok
    assert err is not None and "<=" in err


def test_validate_config_rejects_non_integer(pi_bin_client) -> None:
    ok, err = pi_bin_client.validate_config({"sleep_interval_s": "fifteen"})
    assert not ok
    assert err is not None


def test_validate_config_requires_field(pi_bin_client) -> None:
    ok, err = pi_bin_client.validate_config({})
    assert not ok
    assert err is not None and "missing" in err
