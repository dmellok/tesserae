"""Clock widget smoke test: mounts at each declared size and asserts the
markup the composer hands to the bootstrap is well-formed.

Lives next to the plugin (under ``plugins/clock/tests/``) so the smoke is
co-located with the code it covers — same pattern every other plugin will
follow. The ``client`` fixture comes from the top-level ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_clock_renders_at_size(client: FlaskClient, size: str) -> None:
    resp = client.get(f"/_test/render?plugin=clock&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="clock"' in body
    assert 'data-cell-id="test-cell"' in body
    # The clock plugin declares three cell_options with defaults — the
    # composer should serialise their merged defaults onto the cell.
    assert "format" in body
    assert "show_seconds" in body
