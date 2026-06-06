"""public_transport_times smoke: signing, composer render, missing-cred
error path, all with mocked PTV API."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "ptv_server",
        Path(__file__).resolve().parents[1] / "server.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_signature_matches_known_hmac_sha1() -> None:
    """Sanity-check the HMAC-SHA1 sign helper against a hand-calculated
    value so regressions in the signing scheme are caught immediately."""
    server = _load_server()
    url = server._sign_url("/v3/test", "1234567", "supersecret")
    # devid is appended to query, then HMAC-SHA1 of the resulting path,
    # hex uppercased, appended as signature.
    assert "devid=1234567" in url
    assert "signature=" in url
    # The path is short enough that we can predict the signature:
    import hashlib
    import hmac

    expected = (
        hmac.new(
            b"supersecret",
            b"/v3/test?devid=1234567",
            hashlib.sha1,
        )
        .hexdigest()
        .upper()
    )
    assert url.endswith(f"&signature={expected}")


def test_missing_credentials_returns_friendly_error() -> None:
    server = _load_server()
    out = server.fetch(
        {"stop_id": 1071, "route_type": 0},
        {},  # empty settings
        ctx={"data_dir": "/tmp/test_pt_nope"},
    )
    assert "error" in out
    assert "credentials" in out["error"].lower()


_FAKE_PAYLOAD = json.dumps(
    {
        "departures": [
            {
                "stop_id": 1071,
                "route_id": 4,
                "direction_id": 5,
                "scheduled_departure_utc": "2026-05-27T01:30:00Z",
                "estimated_departure_utc": "2026-05-27T01:32:00Z",
                "at_platform": False,
                "platform_number": "1",
            },
            {
                "stop_id": 1071,
                "route_id": 4,
                "direction_id": 5,
                "scheduled_departure_utc": "2026-05-27T01:45:00Z",
                "estimated_departure_utc": "2026-05-27T01:45:00Z",
                "at_platform": False,
                "platform_number": "1",
            },
        ],
        "routes": {
            "4": {"route_id": 4, "route_name": "Alamein", "route_number": ""},
        },
        "directions": {
            "5": {"direction_id": 5, "direction_name": "City (Flinders Street)"},
        },
        "stops": {
            "1071": {"stop_name": "Camberwell Railway Station (Camberwell)"},
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


def test_fetch_returns_slim_departures(tmp_path) -> None:
    """fetch() against a mocked PTV response, covers the happy path
    end-to-end including the signing helper."""
    server = _load_server()
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        out = server.fetch(
            {"stop_id": 1071, "route_type": 0, "max_results": 5},
            {"devid": "test", "key": "secret"},
            ctx={"data_dir": str(tmp_path)},
        )
    assert "error" not in out
    assert out["stop_name"] == "Camberwell Railway Station (Camberwell)"
    assert len(out["departures"]) == 2
    assert out["departures"][0]["direction_name"] == "City (Flinders Street)"
    assert out["departures"][0]["route_name"] == "Alamein"


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_ptv_renders_error_state_when_creds_missing(client: FlaskClient, size: str) -> None:
    """The composer's test render endpoint doesn't accept cell options,
    so stop_id is 0, but the upstream error path should still produce
    a rendered shell rather than 500ing."""
    resp = client.get(f"/_test/render?plugin=public_transport_times&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="public_transport_times"' in body
    # The plugin returns an error payload (no creds or no stop_id);
    # composer escapes the JSON onto data-data, so the literal "error"
    # key appears in the markup.
    assert "error" in body.lower()
