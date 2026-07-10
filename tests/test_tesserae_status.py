"""Per-device telemetry shaping for the tesserae_status widget."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask

from plugins.tesserae_status import server


def _app(status: dict[str, dict[str, Any]]) -> Flask:
    app = Flask(__name__)
    app.config["DEVICE_STATUS"] = status
    return app


def test_manifest_places_temperature_units_below_temperature() -> None:
    manifest_path = Path(__file__).parents[1] / "plugins" / "tesserae_status" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    option_names = [option["name"] for option in manifest["cell_options"]]

    temperature_index = option_names.index("show_temperature")
    assert option_names[temperature_index : temperature_index + 3] == [
        "show_temperature",
        "units",
        "show_humidity",
    ]


def test_panel_environment_uses_target_device_without_aggregation() -> None:
    app = _app(
        {
            "kitchen": {
                "received_at": 10.0,
                "parsed": {"temperature_c": "24.25", "humidity_pct": 150},
            },
            "office": {
                "received_at": 20.0,
                "parsed": {"temperature_c": 29.0, "humidity_pct": 42.0},
            },
        }
    )

    with app.app_context():
        assert server._panel_environment("kitchen") == (24.25, 100.0)
        assert server._panel_environment("missing") == (None, None)


def test_panel_environment_preview_uses_one_latest_sensor_heartbeat() -> None:
    app = _app(
        {
            "older-complete": {
                "received_at": 10.0,
                "parsed": {"temperature_c": 21.0, "humidity_pct": 44.0},
            },
            "newer-temperature-only": {
                "received_at": 20.0,
                "parsed": {"temperature_c": 27.5},
            },
        }
    )

    with app.app_context():
        assert server._panel_environment() == (27.5, None)


def test_panel_environment_rejects_non_finite_and_boolean_values() -> None:
    app = _app(
        {
            "panel": {
                "received_at": 10.0,
                "parsed": {"temperature_c": True, "humidity_pct": "nan"},
            }
        }
    )

    with app.app_context():
        assert server._panel_environment("panel") == (None, None)


def test_fetch_exposes_environment_options_and_respects_visibility() -> None:
    app = _app(
        {
            "panel": {
                "received_at": 10.0,
                "parsed": {"temperature_c": 25.4, "humidity_pct": 58.2},
            }
        }
    )
    options = {
        "units": "imperial",
        "show_temperature": False,
        "show_humidity": True,
        "show_battery": False,
        "show_wifi": False,
        "show_broker": False,
        "show_firmware_updates": False,
    }

    with app.app_context():
        payload = server.fetch(options, {}, ctx={"target_device_id": "panel"})

    assert payload["units"] == "imperial"
    assert payload["show_temperature"] is False
    assert payload["temperature_c"] is None
    assert payload["show_humidity"] is True
    assert payload["humidity_pct"] == 58.2


def test_fetch_defaults_environment_visibility_to_on() -> None:
    app = _app(
        {
            "panel": {
                "received_at": 10.0,
                "parsed": {"temperature_c": 25.4, "humidity_pct": 58.2},
            }
        }
    )
    options = {
        "show_battery": False,
        "show_wifi": False,
        "show_broker": False,
        "show_firmware_updates": False,
    }

    with app.app_context():
        payload = server.fetch(options, {}, ctx={"target_device_id": "panel"})

    assert payload["units"] == "metric"
    assert payload["temperature_c"] == 25.4
    assert payload["humidity_pct"] == 58.2
