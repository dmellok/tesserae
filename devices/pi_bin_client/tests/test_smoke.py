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
    # No config_topic declared — UI won't render a config form for this device.
    assert pi_bin_client.config_topic is None


def test_parse_status_round_trips_json(pi_bin_client) -> None:
    payload = b'{"state": "idle", "last_paint_at": 1716700000}'
    parsed = pi_bin_client.parse_status(payload)
    assert parsed == {"state": "idle", "last_paint_at": 1716700000}


def test_parse_status_handles_empty(pi_bin_client) -> None:
    assert pi_bin_client.parse_status(b"") == {"raw": ""}


def test_parse_status_handles_non_json(pi_bin_client) -> None:
    parsed = pi_bin_client.parse_status(b"hello there")
    assert parsed == {"raw": "hello there"}
