"""Bridge-version awareness: which tesserae-mcp called, and is it out of date.

The bridge is installed separately from Tesserae, so an operator can sit on an
old one indefinitely without noticing. It already names itself in every request's
``User-Agent``, so the server records that and Settings shows a card once
something has actually connected.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import mcp_bridge
from app.main import REPO_ROOT, create_app

_REMOTE = {"REMOTE_ADDR": "203.0.113.9"}  # a non-loopback client


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    a.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    return a


def _store(app: Flask) -> Any:
    return app.config["SETTINGS_STORE"]


def _admin(app: Flask) -> Any:
    """A client past onboarding, so /settings renders instead of redirecting."""
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


# -- the constant tracks the shipped bridge -----------------------------


def test_expected_version_matches_the_bridge_in_this_repo() -> None:
    """The server's idea of "current" is a constant, so it can answer without a
    network call. This is the check that stops it going stale: bump the bridge
    and this fails until EXPECTED_VERSION follows."""
    source = (REPO_ROOT / "packages" / "tesserae-mcp" / "tesserae_mcp" / "__init__.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^__version__ = "([^"]+)"', source, re.MULTILINE)
    assert match, "couldn't find __version__ in the bridge package"
    assert match.group(1) == mcp_bridge.EXPECTED_VERSION, (
        "app/mcp_bridge.py EXPECTED_VERSION is out of step with "
        "packages/tesserae-mcp; bump it alongside the bridge."
    )


# -- recording ----------------------------------------------------------


def test_a_bridge_call_records_its_version(app: Flask) -> None:
    app.test_client().get("/api/mcp/devices", headers={"User-Agent": "tesserae-mcp/0.9.0"})

    status = mcp_bridge.status(_store(app))
    assert status["seen"] is True
    assert status["version"] == "0.9.0"
    assert status["update_available"] is True


def test_nothing_is_recorded_before_a_client_connects(app: Flask) -> None:
    # The card hangs off "seen": an install that enabled MCP but never pointed
    # an agent at it should see no card at all, not an empty one.
    assert mcp_bridge.status(_store(app))["seen"] is False


def test_a_current_bridge_reports_no_update(app: Flask) -> None:
    app.test_client().get(
        "/api/mcp/devices",
        headers={"User-Agent": f"tesserae-mcp/{mcp_bridge.EXPECTED_VERSION}"},
    )

    status = mcp_bridge.status(_store(app))
    assert status["version"] == mcp_bridge.EXPECTED_VERSION
    assert status["update_available"] is False


def test_a_bridge_ahead_of_us_is_not_nagged(app: Flask) -> None:
    # Someone running the bridge from a clone can be ahead of the server's
    # constant. Telling them to "upgrade" to an older version would be wrong.
    app.test_client().get("/api/mcp/devices", headers={"User-Agent": "tesserae-mcp/99.0.0"})

    assert mcp_bridge.status(_store(app))["update_available"] is False


def test_an_unrecognised_client_is_activity_without_a_version(app: Flask) -> None:
    app.test_client().get("/api/mcp/devices", headers={"User-Agent": "curl/8.4.0"})

    status = mcp_bridge.status(_store(app))
    assert status["seen"] is True
    assert status["unknown_client"] is True
    assert status["version"] == ""
    assert status["update_available"] is False


def test_an_unauthorised_call_records_nothing(app: Flask) -> None:
    # Otherwise an unauthenticated stranger could write to settings, and the
    # card would report a client that never got in.
    resp = app.test_client().get(
        "/api/mcp/devices",
        headers={"User-Agent": "tesserae-mcp/0.9.0"},
        environ_overrides=_REMOTE,
    )

    assert resp.status_code == 401
    assert mcp_bridge.status(_store(app))["seen"] is False


def test_repeat_calls_dont_rewrite_settings(app: Flask) -> None:
    """A compose session fires dozens of calls a minute; the timestamp only
    drives a "last seen" line, so it's refreshed on a timer, not per request."""
    client = app.test_client()
    headers = {"User-Agent": "tesserae-mcp/0.9.0"}
    client.get("/api/mcp/devices", headers=headers)
    first = mcp_bridge.status(_store(app))["at"]

    client.get("/api/mcp/devices", headers=headers)

    assert mcp_bridge.status(_store(app))["at"] == first


def test_a_changed_client_is_written_through_immediately(app: Flask) -> None:
    client = app.test_client()
    client.get("/api/mcp/devices", headers={"User-Agent": "tesserae-mcp/0.9.0"})

    client.get("/api/mcp/devices", headers={"User-Agent": "tesserae-mcp/0.12.0"})

    assert mcp_bridge.status(_store(app))["version"] == "0.12.0"


def test_a_stale_timestamp_is_refreshed(app: Flask) -> None:
    store = _store(app)
    store.patch_section(
        "app",
        {
            "mcp_bridge_seen": {
                "version": "0.9.0",
                "client": "tesserae-mcp/0.9.0",
                "at": time.time() - (mcp_bridge._REFRESH_SECONDS + 60),
            }
        },
    )

    app.test_client().get("/api/mcp/devices", headers={"User-Agent": "tesserae-mcp/0.9.0"})

    assert mcp_bridge.status(store)["at"] > time.time() - 30


# -- surfaces -----------------------------------------------------------


def test_instructions_names_the_shipped_bridge(app: Flask) -> None:
    # Additive key: an older bridge reads instructions/doc_shape and ignores it,
    # so this needed no schema bump.
    payload = app.test_client().get("/api/mcp/instructions").get_json()

    assert payload["bridge"]["latest"] == mcp_bridge.EXPECTED_VERSION
    assert payload["bridge"]["upgrade"] == mcp_bridge.UPGRADE_COMMAND
    assert payload["schema"] == 1


def test_settings_shows_the_card_only_after_a_connection(app: Flask) -> None:
    client = _admin(app)

    before = client.get("/settings/system").get_data(as_text=True)
    assert "Connected bridge" not in before

    client.get("/api/mcp/devices", headers={"User-Agent": "tesserae-mcp/0.9.0"})
    after = client.get("/settings/system").get_data(as_text=True)

    assert "Connected bridge" in after
    assert mcp_bridge.UPGRADE_COMMAND in after


def test_settings_card_stays_shut_for_a_current_bridge(app: Flask) -> None:
    """Open on its own only when there's something to act on; otherwise it's a
    one-line status the operator can ignore."""
    client = _admin(app)
    client.get(
        "/api/mcp/devices",
        headers={"User-Agent": f"tesserae-mcp/{mcp_bridge.EXPECTED_VERSION}"},
    )

    html = client.get("/settings/system").get_data(as_text=True)

    assert "Connected bridge" in html
    assert "<details open>" not in html
