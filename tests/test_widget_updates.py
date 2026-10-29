"""The sidebar's count of catalog widgets with an update waiting."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app import widget_updates
from app.marketplace import CatalogEntry, InstalledRecord, Marketplace


def _entry(
    catalog_id: str, version: str = "2.0.0", tesserae_min: str | None = None
) -> CatalogEntry:
    return CatalogEntry(
        id=catalog_id,
        name=catalog_id.title(),
        description="",
        icon=None,
        author_name="t",
        author_github=None,
        tags=[],
        kind="widget",
        tesserae_compat="0.x",
        official=False,
        screenshot_sizes=[],
        extra_screenshot_count=0,
        folders=None,
        release_version=version,
        release_tarball_url="https://example.invalid/x.tar.gz",
        release_sha256="0" * 64,
        source=None,
        tesserae_min=tesserae_min,
    )


def _record(catalog_id: str, version: str) -> InstalledRecord:
    return InstalledRecord(
        catalog_id=catalog_id,
        version=version,
        installed_at="2026-01-01T00:00:00+00:00",
        folders=[catalog_id],
        sha256="0" * 64,
        source=None,
    )


def _app(*, online_on: bool = True, badge: Any = None, mkt: Any = None) -> Any:
    section: dict[str, Any] = {"online_features": online_on}
    if badge is not None:
        section["widget_update_badge"] = badge
    settings = SimpleNamespace(get_section=lambda name, _s=section: _s if name == "app" else {})
    return SimpleNamespace(
        config={"SETTINGS_STORE": settings, "MARKETPLACE": mkt, "APP_VERSION": "0.490.0"}
    )


def _mkt(entries: list[CatalogEntry] | None, installed: dict[str, InstalledRecord]) -> MagicMock:
    mkt = MagicMock(spec=Marketplace)
    mkt.index_url.return_value = "https://catalog.invalid/widgets.json"
    mkt.cached_index.return_value = entries
    mkt.fetch_index.return_value = entries or []
    mkt.installed.return_value = installed
    return mkt


@pytest.fixture(autouse=True)
def _reset() -> Any:
    widget_updates.reset()
    yield
    widget_updates.reset()


def test_applicable_updates_follow_the_browse_rows() -> None:
    """Behind the index and runnable here: counted. Current, not installed,
    or needing a newer Tesserae: not."""
    entries = [
        _entry("behind"),
        _entry("current"),
        _entry("absent"),
        _entry("too_new", tesserae_min="99.0.0"),
    ]
    installed = {
        "behind": _record("behind", "1.0.0"),
        "current": _record("current", "2.0.0"),
        "too_new": _record("too_new", "1.0.0"),
    }
    due = widget_updates.applicable_updates(entries, installed, "0.490.0")
    assert [e.id for e in due] == ["behind"]


def test_status_is_none_when_online_is_off() -> None:
    mkt = _mkt([_entry("behind")], {"behind": _record("behind", "1.0.0")})
    assert widget_updates.status(_app(online_on=False, mkt=mkt)) is None
    mkt.fetch_index.assert_not_called()


@pytest.mark.parametrize("off", [False, "0", "false", "off"])
def test_status_is_none_when_the_badge_is_switched_off(off: Any) -> None:
    mkt = _mkt([_entry("behind")], {"behind": _record("behind", "1.0.0")})
    assert widget_updates.status(_app(badge=off, mkt=mkt)) is None
    mkt.fetch_index.assert_not_called()


def test_status_counts_from_the_cached_index_without_blocking() -> None:
    entries = [_entry("a"), _entry("b"), _entry("c")]
    installed = {"a": _record("a", "1.0.0"), "b": _record("b", "1.0.0"), "c": _record("c", "2.0.0")}
    mkt = _mkt(entries, installed)
    got = widget_updates.status(_app(mkt=mkt))
    widget_updates.join_for_test()
    assert got == {"count": 2, "ids": ["a", "b"]}


def test_status_kicks_a_background_fetch_when_nothing_is_cached() -> None:
    """First render after boot: nothing cached, so None now and a fetch off
    the request thread; the next render answers from what it pulled."""
    entries = [_entry("a")]
    mkt = _mkt(None, {"a": _record("a", "1.0.0")})
    assert widget_updates.status(_app(mkt=mkt)) is None
    widget_updates.join_for_test()
    mkt.fetch_index.assert_called_once()
    mkt.cached_index.return_value = entries
    assert widget_updates.status(_app(mkt=mkt)) == {"count": 1, "ids": ["a"]}
    # Fresh: no second fetch within the TTL.
    mkt.fetch_index.assert_called_once()


def test_status_is_none_when_everything_is_current() -> None:
    mkt = _mkt([_entry("a")], {"a": _record("a", "2.0.0")})
    assert widget_updates.status(_app(mkt=mkt)) is None


def test_a_failed_fetch_backs_off_instead_of_retrying_every_render() -> None:
    mkt = _mkt(None, {})
    mkt.fetch_index.side_effect = RuntimeError("offline")
    widget_updates.status(_app(mkt=mkt))
    widget_updates.join_for_test()
    widget_updates.status(_app(mkt=mkt))
    widget_updates.join_for_test()
    assert mkt.fetch_index.call_count == 1


def test_status_is_none_without_a_marketplace_or_index_url() -> None:
    assert widget_updates.status(_app(mkt=None)) is None
    mkt = _mkt([_entry("a")], {"a": _record("a", "1.0.0")})
    mkt.index_url.return_value = ""
    assert widget_updates.status(_app(mkt=mkt)) is None


def test_entries_are_plain_dataclasses() -> None:
    """Guard for the helper above: the fields it fills are the real ones."""
    assert {f.name for f in dataclasses.fields(CatalogEntry)} >= {
        "id",
        "release_version",
        "tesserae_min",
    }
