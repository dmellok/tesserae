"""weather_forecast smoke: 5 day cards render with fetched daily data,
at every supported size, with no network call."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_FAKE_PAYLOAD = json.dumps(
    {
        "daily": {
            "time": [
                "2026-06-01",
                "2026-06-02",
                "2026-06-03",
                "2026-06-04",
                "2026-06-05",
            ],
            "temperature_2m_max": [19, 21, 20, 18, 22],
            "temperature_2m_min": [10, 11, 12, 9, 13],
            "weather_code": [3, 80, 1, 2, 0],
            "precipitation_probability_max": [40, 80, 20, 10, 0],
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


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_weather_forecast_renders(client: FlaskClient, size: str) -> None:
    # ``location`` must be set in v0.1.7+ or fetch returns an empty-state
    # error (the no-coords guard added when the global-settings fallback
    # was removed).
    opts = '{"location":{"name":"Melbourne","latitude":-37.8136,"longitude":144.9631}}'
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather_forecast&size={size}&opts={opts}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather_forecast"' in body
    # First date in the daily payload threaded through into data-data.
    assert "2026-06-01" in body
    # 5 days requested -> 5 highs in the cell data.
    assert "[19, 21, 20, 18, 22]" in body or '"high": 19' in body
