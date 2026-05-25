"""year_progress smoke: renders at every declared size."""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_year_progress_renders(client: FlaskClient, size: str) -> None:
    resp = client.get(f"/_test/render?plugin=year_progress&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="year_progress"' in body
