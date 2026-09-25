"""koreader_client smoke: the loader picks it up, the manifest says what the
plugin relies on, and the status/config guards behave."""

from __future__ import annotations

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def koreader_client(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("koreader_client")
    assert d is not None
    return d


def test_manifest_fields(koreader_client) -> None:
    assert koreader_client.name == "KOReader e-reader"
    # 16-level packing first: the plugin decodes 4 bpp into an 8-bit blit
    # buffer, and every Kindle and Kobo width is even.
    assert koreader_client.renderer_ids[0] == "esp32_gray_bin"
    assert "esp32_bw_bin" in koreader_client.renderer_ids
    assert koreader_client.config_topic is None
    schema = koreader_client.config_schema or {}
    assert "sleep_interval_s" in schema
    # The floor matches the plugin's own floor so a typed value below a minute
    # is refused here rather than silently raised on the device.
    assert schema["sleep_interval_s"]["min"] == 60


def test_default_panel_is_a_kindle_paperwhite_2(koreader_client) -> None:
    panel = koreader_client.manifest["panel"]
    assert (panel["w"], panel["h"]) == (758, 1024)
    assert panel["gamut"] == "gray_16"


def test_parse_status_round_trips_json(koreader_client) -> None:
    payload = b'{"battery_pct": 71, "fw_version": "0.1.0", "client": "koreader"}'
    assert koreader_client.parse_status(payload) == {
        "battery_pct": 71,
        "fw_version": "0.1.0",
        "client": "koreader",
    }


def test_parse_status_handles_empty_and_non_json(koreader_client) -> None:
    assert koreader_client.parse_status(b"") == {"raw": ""}
    assert koreader_client.parse_status(b"hello") == {"raw": "hello"}


def test_validate_config_bounds(koreader_client) -> None:
    ok, err = koreader_client.validate_config({"sleep_interval_s": 900})
    assert ok and err is None
    ok, err = koreader_client.validate_config({"sleep_interval_s": 30})
    assert not ok and err is not None and ">=" in err
    ok, err = koreader_client.validate_config({"sleep_interval_s": "soon"})
    assert not ok and err is not None and "integer" in err
    ok, err = koreader_client.validate_config({})
    assert not ok
