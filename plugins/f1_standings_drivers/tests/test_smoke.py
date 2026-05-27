"""f1_standings_drivers smoke: composer renders cells across every
supported size with mocked Jolpica data — no network call."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_FAKE_PAYLOAD = json.dumps(
    {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [
                    {
                        "season": "2026",
                        "round": "5",
                        "DriverStandings": [
                            {
                                "position": "1",
                                "points": "131",
                                "wins": "4",
                                "Driver": {"code": "ANT", "givenName": "Andrea Kimi", "familyName": "Antonelli"},
                                "Constructors": [{"constructorId": "mercedes", "name": "Mercedes"}],
                            },
                            {
                                "position": "2",
                                "points": "88",
                                "wins": "1",
                                "Driver": {"code": "RUS", "givenName": "George", "familyName": "Russell"},
                                "Constructors": [{"constructorId": "mercedes", "name": "Mercedes"}],
                            },
                            {
                                "position": "3",
                                "points": "75",
                                "wins": "0",
                                "Driver": {"code": "LEC", "givenName": "Charles", "familyName": "Leclerc"},
                                "Constructors": [{"constructorId": "ferrari", "name": "Ferrari"}],
                            },
                        ],
                    }
                ]
            }
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
def test_f1_standings_drivers_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=f1_standings_drivers&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="f1_standings_drivers"' in body
    assert "Antonelli" in body
    assert "131" in body
