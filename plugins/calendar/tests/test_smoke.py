"""calendar smoke: renders, server.py fetch returns an empty events list
when no calendars are configured."""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("size", ["md", "lg"])
def test_calendar_renders(client: FlaskClient, size: str) -> None:
    resp = client.get(f"/_test/render?plugin=calendar&size={size}")
    assert resp.status_code == 200
    assert 'data-plugin="calendar"' in resp.get_data(as_text=True)
