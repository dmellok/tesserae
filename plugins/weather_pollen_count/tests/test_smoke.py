"""weather_pollen_count smoke: composer renders cells across every
supported size with mocked Open-Meteo data, no network call. The
Melbourne fallback path is left to manual verification because it
depends on the live MPC HTML structure."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

# Open-Meteo returns numeric values for European coordinates; we use
# real-looking grain counts so the band logic exercises a non-zero path.
_FAKE_PAYLOAD = json.dumps(
    {
        "current": {
            "alder_pollen": 0.5,
            "birch_pollen": 3.2,
            "grass_pollen": 42.0,
            "mugwort_pollen": 1.1,
            "olive_pollen": 0.0,
            "ragweed_pollen": 0.4,
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
def test_weather_pollen_count_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=weather_pollen_count&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="weather_pollen_count"' in body
    # The composer embeds the fetched payload as JSON on data-data, so
    # the grass count (max of grass_pollen) and source name should both
    # round-trip. Band label is computed client-side and isn't here.
    assert "42" in body
    assert "open-meteo" in body
