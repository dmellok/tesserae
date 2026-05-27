"""f1_last_race smoke: composer renders cells across every supported
size with mocked Jolpica data — no network call."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_FAKE_PAYLOAD = json.dumps(
    {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2026",
                        "round": "5",
                        "raceName": "Canadian Grand Prix",
                        "date": "2026-05-24",
                        "Circuit": {
                            "circuitId": "villeneuve",
                            "circuitName": "Circuit Gilles Villeneuve",
                            "Location": {"locality": "Montreal", "country": "Canada"},
                        },
                        "Results": [
                            {
                                "position": "1",
                                "points": "25",
                                "Driver": {
                                    "code": "ANT",
                                    "givenName": "Andrea Kimi",
                                    "familyName": "Antonelli",
                                },
                                "Constructor": {"constructorId": "mercedes", "name": "Mercedes"},
                                "Time": {"millis": "5295758", "time": "1:28:15.758"},
                                "FastestLap": {"rank": "1"},
                                "status": "Finished",
                            },
                            {
                                "position": "2",
                                "points": "18",
                                "Driver": {
                                    "code": "HAM",
                                    "givenName": "Lewis",
                                    "familyName": "Hamilton",
                                },
                                "Constructor": {"constructorId": "ferrari", "name": "Ferrari"},
                                "Time": {"millis": "5306526", "time": "+10.768"},
                                "status": "Finished",
                            },
                            {
                                "position": "3",
                                "points": "15",
                                "Driver": {
                                    "code": "VER",
                                    "givenName": "Max",
                                    "familyName": "Verstappen",
                                },
                                "Constructor": {"constructorId": "red_bull", "name": "Red Bull"},
                                "Time": {"millis": "5307034", "time": "+11.276"},
                                "status": "Finished",
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
def test_f1_last_race_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=f1_last_race&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="f1_last_race"' in body
    assert "Antonelli" in body
    assert "villeneuve" in body
