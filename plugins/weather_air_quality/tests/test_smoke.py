"""weather_air_quality smoke: composer renders cells across every
supported size with mocked Open-Meteo data — no network call."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_FAKE_PAYLOAD = json.dumps(
    {
        "current": {
            "european_aqi": 17,
            "us_aqi": 20,
            "pm2_5": 4.2,
            "pm10": 5.7,
            "ozone": 42.0,
            "nitrogen_dioxide": 11.2,
            "sulphur_dioxide": 3.2,
            "carbon_monoxide": 128.0,
        }
    }
).encode()


class _FakeResp:
    def read(self) -> bytes:
        return _FAKE_PAYLOAD

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_weather_air_quality_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather_air_quality&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather_air_quality"' in body
    assert "Melbourne" in body  # default label round-trips
    assert "17" in body  # european_aqi default
