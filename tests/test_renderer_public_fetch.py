"""A pool fetch of a user-chosen URL checks every redirect hop.

``FetchRequest`` goes out through Playwright's APIRequestContext, which page
interception never sees, so a public URL that redirects to a loopback, LAN or
metadata address would be followed unseen. ``allow_local=False`` makes the
fetch follow redirects itself and validate each target first.
"""

from __future__ import annotations

from typing import Any

import pytest

from app import renderer
from app.net_guard import BlockedURLError
from app.renderer import FetchRequest


class _Resp:
    def __init__(self, status: int, location: str = "", body: str = "") -> None:
        self.status = status
        self.status_text = "x"
        self.ok = 200 <= status < 300
        self.headers = {"location": location} if location else {}
        self._body = body

    def text(self) -> str:
        return self._body


class _Api:
    def __init__(self, replies: dict[str, _Resp]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> _Resp:
        self.calls.append((url, kwargs))
        return self.replies[url]


class _Context:
    def __init__(self, api: _Api) -> None:
        self.request = api

    def close(self) -> None:
        pass


class _Browser:
    def __init__(self, api: _Api) -> None:
        self.api = api

    def new_context(self, **kwargs: Any) -> _Context:
        return _Context(self.api)


@pytest.fixture(autouse=True)
def _public_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """``*.example`` resolves to a public address; literals need no lookup."""
    import socket

    from app import net_guard

    real = socket.getaddrinfo
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    def resolve(host: str, *a: Any, **k: Any) -> Any:
        return public if host.endswith(".example") else real(host, *a, **k)

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", resolve)


def _fetch(api: _Api, url: str, *, allow_local: bool = False) -> str:
    return renderer._fetch_one(_Browser(api), FetchRequest(url=url, allow_local=allow_local))  # type: ignore[arg-type]


def test_a_public_fetch_follows_a_public_redirect() -> None:
    api = _Api(
        {
            "https://feeds.example/rss": _Resp(302, "/v2/rss"),
            "https://feeds.example/v2/rss": _Resp(200, body="<rss/>"),
        }
    )
    assert _fetch(api, "https://feeds.example/rss") == "<rss/>"
    assert [url for url, _ in api.calls] == [
        "https://feeds.example/rss",
        "https://feeds.example/v2/rss",
    ]
    # Chromium is never left to follow a hop on its own.
    assert all(kwargs["max_redirects"] == 0 for _, kwargs in api.calls)


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:8765/api/pages",
        "http://192.168.1.10/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
    ],
)
def test_a_redirect_to_a_private_target_is_refused_unrequested(target: str) -> None:
    api = _Api({"https://feeds.example/rss": _Resp(302, target)})
    with pytest.raises(BlockedURLError):
        _fetch(api, "https://feeds.example/rss")
    assert [url for url, _ in api.calls] == ["https://feeds.example/rss"]


def test_a_private_url_is_refused_before_any_request() -> None:
    api = _Api({})
    with pytest.raises(BlockedURLError):
        _fetch(api, "http://127.0.0.1:8765/api/pages")
    assert api.calls == []


def test_a_redirect_loop_gives_up() -> None:
    api = _Api({"https://feeds.example/rss": _Resp(302, "https://feeds.example/rss")})
    with pytest.raises(BlockedURLError, match="too many redirects"):
        _fetch(api, "https://feeds.example/rss")
    assert len(api.calls) == renderer.FETCH_MAX_REDIRECTS + 1


def test_a_fixed_host_fetch_is_left_to_chromium() -> None:
    """Reddit's fetch names its own host, so it keeps Chromium's redirects."""
    api = _Api({"https://www.reddit.com/r/x/.rss": _Resp(200, body="<feed/>")})
    assert _fetch(api, "https://www.reddit.com/r/x/.rss", allow_local=True) == "<feed/>"
    ((_, kwargs),) = api.calls
    assert "max_redirects" not in kwargs
