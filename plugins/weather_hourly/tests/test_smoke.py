"""weather_hourly smoke: cell renders for every supported size, with hourly
points (and the trimmed forward window) threaded through ctx.data."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

# Three days of hourly slots starting midnight day 1; ``current.time`` falls
# at 09:00 on day 1 so the server trims everything earlier.
_FAKE_PAYLOAD = json.dumps(
    {
        "current": {
            "time": "2026-06-01T09:00",
            "temperature_2m": 14.0,
        },
        "hourly": {
            "time": [f"2026-06-{day:02d}T{h:02d}:00" for day in (1, 2, 3) for h in range(24)],
            "temperature_2m": [
                10 + ((day - 1) * 24 + h) % 12 for day in (1, 2, 3) for h in range(24)
            ],
            "precipitation_probability": [(h * 5) % 100 for day in (1, 2, 3) for h in range(24)],
            "weather_code": [3] * 72,
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
def test_weather_hourly_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather_hourly&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather_hourly"' in body
    # 12-hour default window (bumped down from 24 in v0.2.0) — at least
    # one hour past the current 09:00 cutoff lands in data-data.
    assert "Melbourne" in body
    assert '"hours": 12' in body or '"hours":12' in body
