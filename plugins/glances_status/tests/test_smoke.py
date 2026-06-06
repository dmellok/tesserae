"""Smoke test for glances_status — render the widget at every size
via ``?sample=1`` (so the canned payload skips the real Glances
HTTP fetch) plus a couple of unit tests against the server module's
tone heuristic and uptime parser.
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from plugins.glances_status.server import _split_auth_from_url, _tone, _uptime_seconds


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_widget_renders_at_every_size(client: FlaskClient, size: str) -> None:
    """Each size must render the sample's hostname without raising.
    The sample fixture (``app/widget_samples.py:_glances_status``)
    seeds the host as ``nas``; a substring grep on the response body
    proves the data reached the cell."""
    resp = client.get(f"/_test/render?plugin=glances_status&size={size}&sample=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="glances_status"' in body
    # Hostname appears in the title bar via ``data.label``; if cell
    # hydration silently swallowed an exception, this would be absent.
    assert "nas" in body


def test_widget_renders_error_when_no_url(client: FlaskClient) -> None:
    """Without ``?sample=1``, server.py finds no base_url in settings
    and returns ``{"error": ...}``. The cell shell still 200s; the
    string lives in the cell's data payload for client.js to surface."""
    resp = client.get("/_test/render?plugin=glances_status&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="glances_status"' in resp.get_data(as_text=True)


# -- tone heuristic ----------------------------------------------------


@pytest.mark.parametrize(
    "cpu, mem, disk, expected",
    [
        # Headroom on every dimension → ok.
        (10.0, 30.0, 40.0, "ok"),
        # Single dimension nudges past its danger threshold → danger wins.
        (95.0, 10.0, 10.0, "danger"),
        (10.0, 91.0, 10.0, "danger"),
        (10.0, 10.0, 96.0, "danger"),
        # Single dimension in the warn band, none in danger → warn.
        (72.0, 10.0, 10.0, "warn"),
        (10.0, 80.0, 10.0, "warn"),
        (10.0, 10.0, 88.0, "warn"),
        # Danger trumps warn on different dimensions.
        (75.0, 95.0, 10.0, "danger"),
        # None reported (offline-ish, but still a numeric path) → ok.
        (None, None, None, "ok"),
    ],
)
def test_tone_thresholds(
    cpu: float | None, mem: float | None, disk: float | None, expected: str
) -> None:
    assert _tone(cpu, mem, disk) == expected


# -- URL-embedded auth -------------------------------------------------


def test_split_auth_from_url_no_creds_passes_through() -> None:
    """A bare URL must come back unchanged with no Authorization
    header — the bulk-case for unauthed Glances installs shouldn't
    pay any cost."""
    cleaned, headers = _split_auth_from_url("http://nas.local:61208")
    assert cleaned == "http://nas.local:61208"
    assert headers == {}


def test_split_auth_from_url_extracts_basic_auth() -> None:
    """``user:pass@`` in the URL becomes a Basic ``Authorization``
    header and the credentials are scrubbed from the cleaned URL so
    nothing leaks into log lines or error messages."""
    cleaned, headers = _split_auth_from_url("http://admin:hunter2@nas.local:61208/api")
    assert cleaned == "http://nas.local:61208/api"
    # Decode the token back to confirm the expected creds round-trip.
    import base64

    token = headers["Authorization"].removeprefix("Basic ")
    assert base64.b64decode(token).decode() == "admin:hunter2"


def test_split_auth_from_url_handles_empty_password() -> None:
    """Some users set a username with no password — the encoder must
    still produce a valid ``user:`` Basic token instead of crashing."""
    cleaned, headers = _split_auth_from_url("http://watcher@nas:61208")
    assert cleaned == "http://nas:61208"
    assert "Authorization" in headers


# -- uptime parser -----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # The "1 day, H:M:S" shape Glances v3 emits.
        ("1 day, 4:23:11", 86400 + 4 * 3600 + 23 * 60 + 11),
        # Multi-day formatting.
        ("3 days, 0:05:00", 3 * 86400 + 5 * 60),
        # Sub-day shape (H:M:S, no "day" prefix).
        ("4:23:11", 4 * 3600 + 23 * 60 + 11),
        # M:S shape (less than an hour).
        ("23:11", 23 * 60 + 11),
        # Numeric input (Glances v4 sometimes returns float seconds).
        (3600.0, 3600),
        (86_400, 86400),
        # Bad / missing inputs degrade to None rather than raising.
        (None, None),
        ("", None),
        ("not an uptime", None),
        (0, None),
        (-5, None),
    ],
)
def test_uptime_seconds_parser(raw: object, expected: int | None) -> None:
    assert _uptime_seconds(raw) == expected
