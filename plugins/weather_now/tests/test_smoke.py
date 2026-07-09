"""weather_now smoke: composer renders cells with the fetched data threaded
through ctx.data, across every supported size, with no network call."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from plugins.weather_now import server as weather_now_server

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
            "uv_index": 2.1,
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
    # Pass a label via ``?opts=`` since the manifest default for
    # ``label`` flipped to ``""`` in v0.1.9 (the location_search
    # migration). Without an explicit label or location, the rendered
    # widget has no title bar, which is intentional but breaks the
    # "Melbourne" string-search the test used to do.
    opts = '{"location":{"name":"Melbourne","latitude":-37.8136,"longitude":144.9631},"label":"Melbourne"}'
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather_now&size={size}&opts={opts}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather_now"' in body
    # The fetched payload is threaded through the composer into data-data.
    assert "18.4" in body
    assert "Melbourne" in body


@pytest.mark.parametrize("size", ["md", "lg"])
def test_weather_now_location_search_promotes_to_label(client: FlaskClient, size: str) -> None:
    """When the cell has a ``location`` dict (the new ``location_search``
    shape) and no explicit ``label`` override, the composer's
    ``_resolved_options`` promotes ``location.name`` into the ``label``
    slot, so the widget renders with the city as its place name."""
    opts = (
        '{"location":{"name":"Berlin","country":"DE","admin1":"Berlin",'
        '"latitude":52.52,"longitude":13.405}}'
    )
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather_now&size={size}&opts={opts}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Berlin" in body
    assert "Melbourne" not in body


def test_weather_now_uses_current_uv_and_labels_daily_rain(tmp_path) -> None:
    """UV is a current condition; rain probability remains today's chance."""
    payload = {
        "current": {
            "temperature_2m": 26.1,
            "weather_code": 2,
            "apparent_temperature": 31.2,
            "wind_speed_10m": 8.8,
            "wind_direction_10m": 90,
            "relative_humidity_2m": 91,
            "is_day": 0,
            "uv_index": 0.0,
        },
        "daily": {
            "time": ["2026-07-09"],
            "temperature_2m_max": [31],
            "temperature_2m_min": [25],
            "uv_index_max": [9.05],
            "sunrise": ["2026-07-09T05:45"],
            "sunset": ["2026-07-09T19:12"],
            "precipitation_probability_max": [99],
        },
    }
    urls: list[str] = []

    def fake_fetch_json(url: str, **kwargs) -> dict:
        del kwargs
        urls.append(url)
        return payload

    opts = {
        "latitude": 22.5455,
        "longitude": 114.0683,
        "units": "metric",
        "label": "Home",
    }
    with patch.object(weather_now_server, "fetch_json", side_effect=fake_fetch_json):
        result = weather_now_server.fetch(opts, {}, ctx={"data_dir": tmp_path})

    assert urls and "uv_index" in urls[0].split("&daily=", 1)[0]
    assert result["uv"] == 0.0
    metrics = {metric["icon"]: metric for metric in result["metrics"]}
    assert metrics["uv"]["value"] == 0.0
    assert metrics["rainprob"]["label"] == "Rain (today)"
    assert metrics["rainprob"]["value"] == 99
