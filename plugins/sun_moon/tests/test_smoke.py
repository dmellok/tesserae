"""sun_moon smoke: composer hands the cell to the client; server.py fetch
caches its first call and skips the network on the second."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_sun_moon_renders(client: FlaskClient, size: str) -> None:
    # The composer calls server.py.fetch synchronously while rendering;
    # patch urlopen so we don't actually hit open-meteo during the test.
    fake_payload = json.dumps(
        {
            "daily": {"sunrise": ["2026-06-01T06:00"], "sunset": ["2026-06-01T17:30"]},
            "timezone": "Australia/Melbourne",
        }
    ).encode()

    class _FakeResp:
        def read(self):
            return fake_payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=sun_moon&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="sun_moon"' in body
    # Composer embedded the fetched data so the client can paint without
    # a second roundtrip.
    assert "2026-06-01T06:00" in body


def test_sun_moon_cache_round_trip(tmp_path: Path) -> None:
    import importlib.util

    from app.main import REPO_ROOT

    spec = importlib.util.spec_from_file_location(
        "_plugin_sun_moon_server", REPO_ROOT / "plugins" / "sun_moon" / "server.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fake_payload = json.dumps(
        {
            "daily": {"sunrise": ["2026-06-01T06:00"], "sunset": ["2026-06-01T17:30"]},
            "timezone": "UTC",
        }
    ).encode()

    class _FakeResp:
        def __init__(self):
            self._read = False

        def read(self):
            self._read = True
            return fake_payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    ctx = {"data_dir": str(tmp_path), "preview": False}
    with patch("urllib.request.urlopen", return_value=_FakeResp()) as mock_url:
        first = mod.fetch({"latitude": 1.0, "longitude": 2.0}, {}, ctx=ctx)
        second = mod.fetch({"latitude": 1.0, "longitude": 2.0}, {}, ctx=ctx)
    assert first == second
    # The second call must NOT touch urlopen — it was served from the
    # on-disk cache.
    assert mock_url.call_count == 1
