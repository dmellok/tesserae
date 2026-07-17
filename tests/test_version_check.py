"""Background, cached update-available check for the web header."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app import online, version_check


class _App:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


def _app(online_on: bool) -> _App:
    section = {"online_features": online_on}
    settings = SimpleNamespace(get_section=lambda name, _s=section: _s if name == "app" else {})
    return _App(
        {
            "SETTINGS_STORE": settings,
            "APP_VERSION": "0.130.0",
            "INSTALL_ID": "7e57c0de-1a2b-4c3d-8e4f-0123456789ab",
        }
    )


@pytest.fixture(autouse=True)
def _reset_cache() -> Any:
    version_check.reset()
    yield
    version_check.reset()


def test_status_disabled_when_online_off() -> None:
    out = version_check.status(_app(online_on=False))  # type: ignore[arg-type]
    assert out == {"available": False, "disabled": True}


def test_status_refreshes_and_reports_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When online, status() kicks a background refresh; once it lands, a
    'behind' API response surfaces as available with the latest version."""
    calls: list[tuple[str, str | None, str | None]] = []

    def fake_latest(
        channel: str, current: str | None, install: str | None = None
    ) -> dict[str, Any]:
        calls.append((channel, current, install))
        return {
            "latest": {"version": "0.140.0", "url": "https://gh/rel"},
            "is_current": False,
            "versions_behind": 3,
        }

    monkeypatch.setattr(online, "latest_version", fake_latest)
    app = _app(online_on=True)

    version_check.status(app)  # first call: cache stale -> spawns refresh
    version_check.join_for_test()
    out = version_check.status(app)  # now the refreshed result is cached

    assert out["available"] is True
    assert out["latest"] == "0.140.0" and out["url"] == "https://gh/rel"
    assert out["channel"] == "stable" and out["behind"] == 3
    # The install id is scoped (32-hex), not the raw config UUID.
    channel, current, install = calls[0]
    assert channel == "stable" and current == "0.130.0"
    assert install and install != app.config["INSTALL_ID"] and len(install) == 32


def test_status_not_available_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        online,
        "latest_version",
        lambda *a, **k: {
            "latest": {"version": "0.130.0"},
            "is_current": True,
            "versions_behind": 0,
        },
    )
    app = _app(online_on=True)
    version_check.status(app)
    version_check.join_for_test()
    assert version_check.status(app)["available"] is False


def test_status_not_available_when_ahead_of_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An edge / source build ahead of the latest stable release: versions_behind
    is 0 but is_current is False (our version != latest release). Must NOT show a
    badge -- there's nothing newer to move to, and it would point at an OLDER
    release."""
    monkeypatch.setattr(
        online,
        "latest_version",
        lambda *a, **k: {
            "latest": {"version": "0.132.0", "url": "https://gh/rel"},
            "is_current": False,  # 0.137.0 != 0.132.0
            "versions_behind": 0,  # nothing newer than 0.137.0
        },
    )
    app = _app(online_on=True)
    app.config["APP_VERSION"] = "0.137.0"
    version_check.status(app)
    version_check.join_for_test()
    assert version_check.status(app)["available"] is False


def test_header_shows_update_badge(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The topbar renders an update badge when version_check reports available."""
    monkeypatch.setattr(
        version_check,
        "status",
        lambda app: {"available": True, "channel": "stable", "latest": "9.9.9", "url": "u"},
    )
    html = client.get("/settings/about").get_data(as_text=True)
    assert "topbar-update" in html and "v9.9.9" in html
