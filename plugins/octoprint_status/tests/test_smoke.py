"""Smoke test for octoprint_status — render every variant at every
supported size via ``?sample=1`` so we exercise the full cell-render
shell without needing to mock the OctoPrint HTTP API. Mirrors the
ha_todo / spotify_queue pattern."""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient


@pytest.mark.parametrize("variant", ["r1", "g2", "s3", "d4"])
@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_widget_renders_per_variant(client: FlaskClient, variant: str, size: str) -> None:
    """Each variant must render the sample's job filename without
    raising. The sample fixture (app/widget_samples.py:_octoprint_status)
    seeds a "benchy.gcode" job at 47% so the cell payload JSON carries
    the filename — a substring grep on the response proves the data
    reached the cell."""
    resp = client.get(
        f"/_test/render?plugin=octoprint_status&size={size}&variant={variant}"
        f"&theme=default&sample=1"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="octoprint_status"' in body
    # The job filename is in the cell's data-data JSON regardless of
    # variant; if hydration short-circuited or the cell errored, this
    # would be absent.
    assert "benchy.gcode" in body


def test_widget_renders_error_when_no_url(client: FlaskClient) -> None:
    """Without ``?sample=1``, server.py finds no base_url in settings and
    returns ``{"error": ...}``. We just confirm the cell still renders
    a 200 with the plugin attribute attached — the error string itself
    lives in the cell's data-data JSON and is consumed by client.js."""
    resp = client.get("/_test/render?plugin=octoprint_status&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="octoprint_status"' in resp.get_data(as_text=True)
