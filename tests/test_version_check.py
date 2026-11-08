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
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> Any:
    # Pin the channel: a source checkout off a tag would otherwise report
    # ``main`` and send these stable tests down the commit path.
    monkeypatch.setattr(version_check, "_channel", lambda: "stable")
    monkeypatch.delenv("TESSERAE_BUILD_SHA", raising=False)
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
    assert out["label"] == "v0.140.0"
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
        lambda app: {
            "available": True,
            "channel": "stable",
            "latest": "9.9.9",
            "url": "u",
            "label": "v9.9.9",
        },
    )
    html = client.get("/settings/about").get_data(as_text=True)
    assert "topbar-update" in html and "v9.9.9" in html


_SHA = "31f23cb3b5d53a9d113a9b53ca5ebe8d780212a6"


def _main_response(behind: int | None) -> dict[str, Any]:
    return {
        "channel": "main",
        "latest": {"sha": "4e8ed82", "url": "https://gh/commit"},
        "is_current": None if behind is None else behind == 0,
        "commits_behind": behind,
    }


def test_edge_build_behind_main_reports_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """An edge image sends its stamped build commit on the main channel and
    lights the badge with the number of commits it is behind."""
    calls: list[tuple[str, str | None]] = []

    def fake_latest(
        channel: str, current: str | None, install: str | None = None
    ) -> dict[str, Any]:
        calls.append((channel, current))
        return _main_response(3)

    monkeypatch.setattr(online, "latest_version", fake_latest)
    monkeypatch.setattr(version_check, "_channel", lambda: "edge")
    monkeypatch.setenv("TESSERAE_BUILD_SHA", _SHA)
    app = _app(online_on=True)
    version_check.status(app)
    version_check.join_for_test()
    out = version_check.status(app)

    assert calls == [("main", _SHA)]
    assert out["available"] is True and out["channel"] == "edge"
    assert out["behind"] == 3 and out["latest"] == "4e8ed82"
    assert out["label"] == "3 new commits"


def test_edge_build_on_head_of_main_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(online, "latest_version", lambda *a, **k: _main_response(0))
    monkeypatch.setattr(version_check, "_channel", lambda: "edge")
    monkeypatch.setenv("TESSERAE_BUILD_SHA", _SHA)
    app = _app(online_on=True)
    version_check.status(app)
    version_check.join_for_test()
    assert version_check.status(app)["available"] is False


def test_edge_build_with_unknown_commit_shows_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit the API can't place (a fork, an unpushed build) comes back as
    commits_behind None: no badge rather than a guess."""
    monkeypatch.setattr(online, "latest_version", lambda *a, **k: _main_response(None))
    monkeypatch.setattr(version_check, "_channel", lambda: "edge")
    monkeypatch.setenv("TESSERAE_BUILD_SHA", _SHA)
    app = _app(online_on=True)
    version_check.status(app)
    version_check.join_for_test()
    assert version_check.status(app)["available"] is False


def test_edge_build_without_commit_stamp_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """An edge image from before the build commit was stamped has nothing to
    compare, so it neither asks the API nor shows a badge."""
    called: list[Any] = []
    monkeypatch.setattr(online, "latest_version", lambda *a, **k: called.append(a))
    monkeypatch.setattr(version_check, "_channel", lambda: "edge")
    monkeypatch.setattr(version_check, "_build_sha", lambda: "")
    app = _app(online_on=True)
    version_check.status(app)
    version_check.join_for_test()
    assert version_check.status(app)["available"] is False
    assert called == []


def test_single_commit_label_is_singular(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(online, "latest_version", lambda *a, **k: _main_response(1))
    monkeypatch.setattr(version_check, "_channel", lambda: "main")
    monkeypatch.setattr(version_check, "_build_sha", lambda: _SHA)
    app = _app(online_on=True)
    version_check.status(app)
    version_check.join_for_test()
    assert version_check.status(app)["label"] == "1 new commit"


def test_header_shows_edge_update_badge(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        version_check,
        "status",
        lambda app: {
            "available": True,
            "channel": "edge",
            "latest": "4e8ed82",
            "url": "u",
            "behind": 3,
            "label": "3 new commits",
        },
    )
    html = client.get("/settings/about").get_data(as_text=True)
    assert "topbar-update" in html and "Update · 3 new commits" in html
    assert "3 new commits on main" in html
    assert "v4e8ed82" not in html
