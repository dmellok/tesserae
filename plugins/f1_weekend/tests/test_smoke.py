"""f1_weekend smoke: composer renders cells across every supported size
with mocked Jolpica data — no network call."""

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
                        "round": "6",
                        "raceName": "Monaco Grand Prix",
                        "date": "2026-06-07",
                        "time": "13:00:00Z",
                        "Circuit": {
                            "circuitId": "monaco",
                            "circuitName": "Circuit de Monaco",
                            "Location": {"locality": "Monte Carlo", "country": "Monaco"},
                        },
                        "FirstPractice":  {"date": "2026-06-05", "time": "11:30:00Z"},
                        "SecondPractice": {"date": "2026-06-05", "time": "15:00:00Z"},
                        "ThirdPractice":  {"date": "2026-06-06", "time": "10:30:00Z"},
                        "Qualifying":     {"date": "2026-06-06", "time": "14:00:00Z"},
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


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_f1_weekend_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=f1_weekend&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="f1_weekend"' in body
    assert "Monaco Grand Prix" in body
    # Each session label round-trips through the data payload.
    for label in ("FP1", "FP2", "FP3", "QUAL", "RACE"):
        assert label in body
