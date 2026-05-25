"""weather smoke: composer + fetched data lands in the cell markup."""

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
            "relative_humidity_2m": 58,
        },
        "daily": {
            "time": ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"],
            "temperature_2m_max": [19, 21, 20, 18],
            "temperature_2m_min": [10, 11, 12, 9],
            "weather_code": [3, 80, 1, 2],
            "precipitation_probability_max": [60, 80, 20, 10],
            "uv_index_max": [3.0, 4.0, 4.5, 3.2],
            "sunrise": ["2026-06-01T07:01"],
            "sunset": ["2026-06-01T17:09"],
        },
        "hourly": {
            "time": [f"2026-06-01T{h:02d}:00" for h in range(6)],
            "temperature_2m": [10, 11, 13, 15, 16, 17],
            "weather_code": [3, 3, 3, 2, 1, 1],
        },
    }
).encode()


class _FakeResp:
    def read(self):
        return _FAKE_PAYLOAD

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_weather_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather"' in body
    # Fetched data threaded through the composer into the cell's data-data.
    assert "18.4" in body  # current temp
    assert "South Morang" in body  # default place label
