"""Per-page hostname caching for strict external webpage renders.

Companion webpage sends install this route interceptor for every Chromium
request. Pages with many same-host assets must not repeat a synchronous DNS
classification until the navigation deadline expires, while distinct hosts
and failures must still be blocked independently and fail closed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from app.renderer import RenderRequest, _install_request_policy


def _route(url: str) -> MagicMock:
    route = MagicMock()
    route.request.url = url
    return route


def test_strict_guard_classifies_each_hostname_once_per_page() -> None:
    page = MagicMock()
    request = RenderRequest(url="https://www.example/", allow_local=False)
    routes = [
        _route("https://www.example/"),
        _route("https://www.example/assets/site.css"),
        _route("https://private.example/metrics.gif"),
        _route("https://private.example/another.gif"),
    ]

    with patch(
        "app.net_guard.host_is_blocked",
        side_effect=lambda host, *, allow_local: host == "private.example",
    ) as classify:
        _install_request_policy(page, request)
        handler = page.route.call_args.args[1]
        for route in routes:
            handler(route)

    assert classify.call_args_list == [
        call("www.example", allow_local=False),
        call("private.example", allow_local=False),
    ]
    routes[0].continue_.assert_called_once_with()
    routes[1].continue_.assert_called_once_with()
    routes[2].abort.assert_called_once_with()
    routes[3].abort.assert_called_once_with()


def test_strict_guard_caches_resolution_failures_as_blocked() -> None:
    page = MagicMock()
    request = RenderRequest(url="https://unresolved.example/", allow_local=False)
    routes = [
        _route("https://unresolved.example/"),
        _route("https://unresolved.example/site.js"),
    ]

    with patch("app.net_guard.host_is_blocked", side_effect=OSError("DNS failed")) as classify:
        _install_request_policy(page, request)
        handler = page.route.call_args.args[1]
        for route in routes:
            handler(route)

    classify.assert_called_once_with("unresolved.example", allow_local=False)
    routes[0].abort.assert_called_once_with()
    routes[1].abort.assert_called_once_with()


def test_new_page_attempt_gets_a_fresh_hostname_classification() -> None:
    request = RenderRequest(url="https://www.example/", allow_local=False)
    pages = [MagicMock(), MagicMock()]

    with patch("app.net_guard.host_is_blocked", return_value=False) as classify:
        for page in pages:
            _install_request_policy(page, request)
            handler = page.route.call_args.args[1]
            handler(_route("https://www.example/asset.js"))

    assert classify.call_args_list == [
        call("www.example", allow_local=False),
        call("www.example", allow_local=False),
    ]


def test_operator_render_does_not_install_the_strict_guard() -> None:
    page = MagicMock()
    request = RenderRequest(url="http://display.lan/", allow_local=True)

    _install_request_policy(page, request)

    page.route.assert_not_called()
