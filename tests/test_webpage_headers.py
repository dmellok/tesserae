"""Where a configured header is allowed to go, and where it must not (#234).

Three properties, each of which is a security bug if it regresses:

* a credential reaches only its own origin, never another widget's upstream,
  a third-party subresource, or a redirect target;
* the block decision wins over header injection, so a host the SSRF guard
  refuses is never handed credentials on the way out;
* a secret cell option never reaches the composed DOM.

The interceptor is exercised through a fake Playwright ``page`` / ``route``
pair rather than a real browser: what's under test is the routing decision,
and a headless Chromium would only make it slower and flakier.
"""

from __future__ import annotations

from typing import Any

from app.renderer import RenderRequest, _install_request_policy, origin_of
from app.state.page_store import Cell, Page
from app.webpage_headers import headers_by_origin_for_page

INTERNAL = "https://dash.internal.test"
OTHER = "https://cdn.other.test"
TOKEN = {"Authorization": "Bearer super-secret"}


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url
        self.headers = {"accept": "*/*"}


class _FakeRoute:
    """Records what the interceptor decided for one request."""

    def __init__(self, url: str) -> None:
        self.request = _FakeRequest(url)
        self.aborted = False
        self.continued = False
        self.sent_headers: dict[str, str] | None = None

    def abort(self) -> None:
        self.aborted = True

    def continue_(self, **kwargs: Any) -> None:
        self.continued = True
        headers = kwargs.get("headers")
        self.sent_headers = dict(headers) if headers is not None else None


class _FakePage:
    def __init__(self) -> None:
        self.handler: Any = None

    def route(self, _pattern: str, handler: Any) -> None:
        self.handler = handler

    def send(self, url: str) -> _FakeRoute:
        assert self.handler is not None, "no interceptor installed"
        route = _FakeRoute(url)
        self.handler(route)
        return route


def _page_with(headers_by_origin: dict[str, dict[str, str]] | None, **kw: Any) -> _FakePage:
    page = _FakePage()
    request = RenderRequest(url=INTERNAL, headers_by_origin=headers_by_origin, **kw)
    _install_request_policy(page, request)
    return page


# -- installation --------------------------------------------------------


def test_no_headers_and_no_strictness_installs_nothing() -> None:
    """The common render path must not pay for interception it doesn't use,
    and must stay byte-identical to the pre-#234 behaviour."""
    page = _page_with(None)
    assert page.handler is None


def test_an_empty_header_map_still_installs_nothing() -> None:
    """A cell configured with an empty map shouldn't quietly turn on
    interception for the whole render."""
    page = _page_with({INTERNAL: {}})
    assert page.handler is None


def test_headers_alone_install_the_interceptor_on_a_permissive_render() -> None:
    """Composer renders are permissive (allow_local=True). Before #234 that
    meant no interception at all, so this is the case where headers had to
    bring their own hook rather than ride the SSRF guard's."""
    page = _page_with({INTERNAL: dict(TOKEN)})
    assert page.handler is not None


# -- origin scoping ------------------------------------------------------


def test_the_matching_origin_receives_the_header() -> None:
    route = _page_with({INTERNAL: dict(TOKEN)}).send(f"{INTERNAL}/status")
    assert route.continued and not route.aborted
    assert route.sent_headers is not None
    assert route.sent_headers["Authorization"] == "Bearer super-secret"
    # Merged onto what Chromium built, not substituted for it.
    assert route.sent_headers["accept"] == "*/*"


def test_a_different_origin_receives_nothing() -> None:
    """The requirement from the issue: never forward auth headers to a
    different origin. A font CDN on the same page is the everyday case."""
    route = _page_with({INTERNAL: dict(TOKEN)}).send(f"{OTHER}/font.woff2")
    assert route.continued
    assert route.sent_headers is None


def test_the_same_host_on_a_different_scheme_receives_nothing() -> None:
    route = _page_with({INTERNAL: dict(TOKEN)}).send("http://dash.internal.test/status")
    assert route.sent_headers is None


def test_a_different_port_is_a_different_origin() -> None:
    route = _page_with({INTERNAL: dict(TOKEN)}).send("https://dash.internal.test:8443/status")
    assert route.sent_headers is None


def test_a_redirect_hop_off_origin_drops_the_credential() -> None:
    """Chromium follows redirects internally, each hop arriving as its own
    request. Scoping by origin is what makes the token stop at the boundary
    without anything having to track redirect chains."""
    page = _page_with({INTERNAL: dict(TOKEN)})
    assert page.send(f"{INTERNAL}/login").sent_headers is not None
    assert page.send("https://attacker.test/collect").sent_headers is None


def test_two_cells_on_one_page_do_not_see_each_other_s_credentials() -> None:
    page = _page_with(
        {
            INTERNAL: {"Authorization": "Bearer one"},
            OTHER: {"Authorization": "Bearer two"},
        }
    )
    first = page.send(f"{INTERNAL}/a")
    second = page.send(f"{OTHER}/b")
    assert first.sent_headers is not None
    assert second.sent_headers is not None
    assert first.sent_headers["Authorization"] == "Bearer one"
    assert second.sent_headers["Authorization"] == "Bearer two"


# -- blocking wins -------------------------------------------------------


def test_a_blocked_host_is_aborted_and_never_given_headers(monkeypatch: Any) -> None:
    """Ordering is load-bearing. A header configured for a private host must
    not become a credential handed to something the guard was refusing."""
    import app.net_guard as net_guard

    monkeypatch.setattr(net_guard, "host_is_blocked", lambda host, allow_local=True: True)
    route = _page_with({INTERNAL: dict(TOKEN)}, allow_local=False).send(f"{INTERNAL}/status")
    assert route.aborted
    assert not route.continued
    assert route.sent_headers is None


def test_strict_but_allowed_host_still_gets_its_headers(monkeypatch: Any) -> None:
    import app.net_guard as net_guard

    monkeypatch.setattr(net_guard, "host_is_blocked", lambda host, allow_local=True: False)
    route = _page_with({INTERNAL: dict(TOKEN)}, allow_local=False).send(f"{INTERNAL}/status")
    assert route.continued
    assert route.sent_headers is not None


# -- origin_of -----------------------------------------------------------


def test_origin_of_normalises_case_and_keeps_explicit_ports() -> None:
    assert origin_of("HTTPS://Dash.Internal.TEST/a?b=1") == INTERNAL
    assert origin_of("http://10.0.0.5:8080/x") == "http://10.0.0.5:8080"
    assert origin_of("not-a-url") == ""
    assert origin_of("") == ""


# -- collecting from a page ---------------------------------------------


def _cell(cell_id: str, plugin: str | None, options: dict[str, Any]) -> Cell:
    return Cell(id=cell_id, plugin=plugin, x=0, y=0, w=100, h=100, options=options)


def _page(*cells: Cell) -> Page:
    return Page(id="p1", name="P", cells=list(cells))


def test_a_configured_webpage_cell_yields_its_origin() -> None:
    page = _page(
        _cell(
            "c1",
            "webpage",
            {"url": f"{INTERNAL}/status", "headers": '{"Authorization": "Bearer t"}'},
        )
    )
    assert headers_by_origin_for_page(page) == {INTERNAL: {"Authorization": "Bearer t"}}


def test_a_page_with_nothing_configured_returns_none() -> None:
    """None rather than {} so the renderer installs no interception at all."""
    assert headers_by_origin_for_page(_page(_cell("c1", "webpage", {"url": INTERNAL}))) is None
    assert headers_by_origin_for_page(_page(_cell("c1", "clock", {}))) is None
    assert headers_by_origin_for_page(_page()) is None


def test_other_widgets_are_ignored_even_with_a_headers_option() -> None:
    """``rest_service`` also has a secret ``headers`` cell option, but it does
    its own fetching in server.py and must not get browser injection too."""
    page = _page(_cell("c1", "rest_service", {"url": INTERNAL, "headers": '{"X-K": "v"}'}))
    assert headers_by_origin_for_page(page) is None


def test_a_malformed_header_map_skips_that_cell_without_raising() -> None:
    """One broken cell shouldn't take the other five widgets down with it."""
    page = _page(
        _cell("bad", "webpage", {"url": INTERNAL, "headers": "not json"}),
        _cell("good", "webpage", {"url": f"{OTHER}/x", "headers": '{"X-K": "v"}'}),
    )
    assert headers_by_origin_for_page(page) == {OTHER: {"X-K": "v"}}


def test_headers_without_a_usable_url_are_dropped() -> None:
    page = _page(_cell("c1", "webpage", {"url": "", "headers": '{"X-K": "v"}'}))
    assert headers_by_origin_for_page(page) is None


def test_two_cells_sharing_an_origin_merge_in_page_order() -> None:
    page = _page(
        _cell("c1", "webpage", {"url": f"{INTERNAL}/a", "headers": '{"X-K": "first"}'}),
        _cell("c2", "webpage", {"url": f"{INTERNAL}/b", "headers": '{"X-Other": "second"}'}),
    )
    assert headers_by_origin_for_page(page) == {INTERNAL: {"X-K": "first", "X-Other": "second"}}
