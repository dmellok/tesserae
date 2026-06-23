"""weather_now_scenic smoke: preset mapping table + composer render.

The preset mapping is the key piece of logic, every WMO code branches
into one of the visual themes and a regression there changes how a
panel reads at a glance. The render test confirms the manifest's
``design.palette: "extended"`` opt-in is parsed without breaking the
loader, and that fetched data threads through the composer correctly.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from plugins.weather_now_scenic.server import _preset

_FAKE_PAYLOAD = json.dumps(
    {
        "current": {
            "temperature_2m": 21.7,
            "weather_code": 0,
            "is_day": 1,
        },
        "daily": {
            "time": ["2026-06-01"],
            "sunrise": ["2026-06-01T07:01"],
            "sunset": ["2026-06-01T17:09"],
        },
    }
).encode()


class _FakeResp:
    def read(self) -> bytes:
        return _FAKE_PAYLOAD

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


@pytest.mark.parametrize(
    "icon,is_day,expected",
    [
        ("sun", True, "sunny_day"),
        ("moon", False, "clear_night"),
        ("partly", True, "partly_day"),
        ("partly-night", False, "partly_night"),
        ("cloud", True, "cloudy_day"),
        ("cloud", False, "cloudy_night"),
        ("rain", True, "rain"),
        ("rain-heavy", True, "rain"),
        ("drizzle", True, "rain"),
        ("snow", True, "snow"),
        ("storm", True, "storm"),
        ("fog", True, "cloudy_day"),
        # Unknown icon falls back to a day/night cloudy preset so the
        # client never gets handed a preset name it doesn't recognise.
        ("unknown_icon", True, "cloudy_day"),
        ("unknown_icon", False, "cloudy_night"),
    ],
)
def test_preset_mapping(icon: str, is_day: bool, expected: str) -> None:
    assert _preset(icon, is_day) == expected


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_weather_now_scenic_renders(client: FlaskClient, size: str) -> None:
    # Manifest label default flipped to ``""`` in v0.1.1 (location_search
    # migration). Pass an explicit label so the smoke test's text-match
    # assertion still has something to find.
    opts = '{"label":"Melbourne"}'
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(
            f"/_test/render?plugin=weather_now_scenic&size={size}&opts={opts}"
        )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather_now_scenic"' in body
    # The 22°C fetched temp should land in the body, the passed
    # Melbourne label too. Both prove the data + cell_options round-trip
    # through the composer.
    assert "21.7" in body or "22" in body
    assert "Melbourne" in body
