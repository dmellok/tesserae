"""weather_now smoke: composer renders cells with the fetched data threaded
through ctx.data, across every supported size, with no network call."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_FAKE_PAYLOAD = json.dumps(
    {
        "current": {
            "temperature_2m": 18.4,
            "weather_code": 3,
            "apparent_temperature": 17.1,
            "wind_speed_10m": 12.5,
            "wind_direction_10m": 180,
            "relative_humidity_2m": 58,
            "is_day": 1,
        },
        "daily": {
            "time": ["2026-06-01"],
            "temperature_2m_max": [19],
            "temperature_2m_min": [10],
            "uv_index_max": [3.0],
            "sunrise": ["2026-06-01T07:01"],
            "sunset": ["2026-06-01T17:09"],
            "precipitation_probability_max": [60],
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


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_weather_now_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather_now&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather_now"' in body
    # The fetched payload is threaded through the composer into data-data.
    assert "18.4" in body
    assert "Melbourne" in body
