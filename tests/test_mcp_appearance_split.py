"""Appearance is its own read, not part of every catalog read (#257).

`list_widgets` is the most expensive thing an MCP agent does, and the theme /
style / font lists were 18% of it while being needed only when something is
actually restyled -- which most builds never do. They are counted in the
catalog and served in full by their own endpoint, the same shape the icon set
already uses here for the same reason.

The saving is only real if the agent can still find them, so these tests pin
the pointer as hard as they pin the omission.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def _enable(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})


def test_the_catalog_no_longer_carries_the_lists(app: Flask) -> None:
    """The whole point: 838 tokens of themes/styles/fonts stop riding along."""
    _enable(app)
    appearance = app.test_client().get("/api/mcp/catalog").get_json()["appearance"]
    assert not isinstance(appearance.get("themes"), list)
    assert not isinstance(appearance.get("styles"), list)
    assert not isinstance(appearance.get("fonts"), list)


def test_the_catalog_says_how_many_of_each_there_are(app: Flask) -> None:
    """A count is what tells an agent the endpoint is worth calling."""
    _enable(app)
    client = app.test_client()
    summary = client.get("/api/mcp/catalog").get_json()["appearance"]
    full = client.get("/api/mcp/appearance").get_json()
    for key in ("themes", "styles", "fonts"):
        assert summary[key] == len(full[key]), key


def test_the_catalog_points_at_the_endpoint(app: Flask) -> None:
    """Omitting the lists without saying where they went would be a regression."""
    _enable(app)
    summary = app.test_client().get("/api/mcp/catalog").get_json()["appearance"]
    assert summary["endpoint"] == "/api/mcp/appearance"
    assert summary.get("usage")


def test_the_endpoint_serves_what_the_catalog_used_to(app: Flask) -> None:
    """Nothing is lost, only moved."""
    _enable(app)
    full = app.test_client().get("/api/mcp/appearance").get_json()
    assert isinstance(full["themes"], list)
    assert isinstance(full["styles"], list)
    assert isinstance(full["fonts"], list)
    assert full["themes"], "no themes served"
    assert full["fonts"], "no fonts served"


def test_the_split_actually_shrinks_the_catalog(app: Flask) -> None:
    """The issue is a token-cost one, so the size is the assertion.

    The appearance block must be a small fraction of what the endpoint
    serves; anything else means the summary grew back into a payload.
    """
    _enable(app)
    client = app.test_client()
    summary = json.dumps(client.get("/api/mcp/catalog").get_json()["appearance"])
    full = json.dumps(client.get("/api/mcp/appearance").get_json())
    assert len(summary) * 5 < len(full), (
        f"the catalog's appearance block is {len(summary)} B against "
        f"{len(full)} B served on demand; it is no longer a pointer"
    )


def test_the_endpoint_is_behind_the_same_gate(app: Flask) -> None:
    """A new route must not become a way around the experiment gate."""
    assert app.test_client().get("/api/mcp/appearance").status_code == 404
