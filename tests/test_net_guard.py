"""SSRF guard classification: strict widget mode vs. permissive operator mode.

Uses IP literals and ``localhost`` only so ``socket.getaddrinfo`` stays local
and the suite makes no DNS calls.
"""

from __future__ import annotations

import pytest

from app.net_guard import BlockedURLError, assert_operator_url, host_is_blocked

# --- strict mode (default): the generic widget fetch path ---


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback
        "::1",
        "localhost",
        "10.0.0.5",  # RFC1918
        "172.16.5.5",
        "192.168.1.5",
        "169.254.169.254",  # link-local / cloud metadata
        "",  # no host
    ],
)
def test_strict_blocks_local_and_metadata(host: str) -> None:
    assert host_is_blocked(host) is True


@pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1"])
def test_strict_allows_public(host: str) -> None:
    assert host_is_blocked(host) is False


# --- operator mode (allow_local=True): webpage screenshot + remote image ---


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "localhost", "10.0.0.5", "192.168.1.5", "8.8.8.8"],
)
def test_operator_allows_loopback_lan_public(host: str) -> None:
    # Same-host capture is a supported use case, so loopback + LAN pass here.
    assert host_is_blocked(host, allow_local=True) is False


@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",  # cloud metadata is never legitimate
        "169.254.1.1",  # link-local generally
        "224.0.0.1",  # multicast
    ],
)
def test_operator_still_blocks_metadata_and_reserved(host: str) -> None:
    assert host_is_blocked(host, allow_local=True) is True


# --- assert_operator_url end to end ---


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:3000", "http://192.168.1.10/dashboard", "https://8.8.8.8/"],
)
def test_assert_operator_url_allows(url: str) -> None:
    assert_operator_url(url)  # does not raise


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # metadata
        "ftp://example.com/x",  # bad scheme
        "file:///etc/passwd",  # bad scheme
        "http://",  # no host
    ],
)
def test_assert_operator_url_refuses(url: str) -> None:
    with pytest.raises(BlockedURLError):
        assert_operator_url(url)


# --- canonical hosts, resolution and pinning (no real DNS or sockets) ---

import socket  # noqa: E402
from typing import Any  # noqa: E402

from app import net_guard  # noqa: E402


@pytest.mark.parametrize(
    "url",
    [
        "http://127%2e0%2e0%2e1:8123/x.png",  # urllib decodes this to loopback
        "http://%31%32%37.0.0.1/",
        "http://2130706433/",  # decimal
        "http://0177.0.0.1/",  # octal (or decimal 177, depending on who asks)
        "http://0x7f.1/",  # hex
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped IPv6
        "http://[::ffff:7f00:1]/",
        "http://[64:ff9b::7f00:1]/",  # NAT64 well-known prefix
        "http://[2002:7f00:1::]/",  # 6to4 around loopback
        "http://[::ffff:169.254.169.254]/",
        "https://[/x",  # malformed
        "http://exa mple.com/",
        "http://localhost./",
    ],
)
def test_strict_guard_sees_through_spellings(url: str) -> None:
    with pytest.raises(BlockedURLError):
        net_guard.assert_public_url(url)


def test_a_host_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing(*a: Any, **k: Any) -> Any:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", nothing)
    with pytest.raises(BlockedURLError, match="doesn't resolve"):
        net_guard.assert_public_url("https://nowhere.example/")
    assert host_is_blocked("nowhere.example") is True


def test_idna_hosts_are_encoded_before_resolving(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def resolve(host: str, *a: Any, **k: Any) -> Any:
        asked.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", resolve)
    net_guard.assert_public_url("https://BÜCHER.example./x")
    assert asked == ["xn--bcher-kva.example"]


def test_the_connection_goes_to_the_address_that_was_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS rebinding: the first answer is public, every later one loopback.
    Whatever happens, no connection is made to the loopback address."""
    answers = iter(["93.184.216.34"])

    def rebinding(host: str, *a: Any, **k: Any) -> Any:
        ip = next(answers, "127.0.0.1")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    connected: list[tuple[str, int]] = []

    def connect(address: tuple[str, int], *a: Any, **k: Any) -> Any:
        connected.append(address)
        raise OSError("test: no sockets")

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", rebinding)
    monkeypatch.setattr(net_guard.socket, "create_connection", connect)
    with pytest.raises((BlockedURLError, OSError)):
        net_guard.fetch_bytes("http://rebind.example/x.png")
    assert ("127.0.0.1", 80) not in connected


def test_a_pinned_request_keeps_the_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    sent: list[bytes] = []

    class _Sock:
        def sendall(self, data: bytes) -> None:
            sent.append(data)

        def makefile(self, *a: Any, **k: Any) -> Any:
            import io

            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

        def close(self) -> None:
            pass

        def setsockopt(self, *a: Any) -> None:
            pass

    connected: list[tuple[str, int]] = []

    def connect(address: tuple[str, int], *a: Any, **k: Any) -> Any:
        connected.append(address)
        return _Sock()

    monkeypatch.setattr(net_guard.socket, "create_connection", connect)
    body, _ = net_guard.fetch_bytes("http://pictures.example:8080/x.png")
    assert body == b"ok"
    assert connected == [("93.184.216.34", 8080)]
    assert b"Host: pictures.example:8080" in b"".join(sent)


class _FakeSock:
    def __init__(self, sent: list[bytes]) -> None:
        self._sent = sent

    def sendall(self, data: bytes) -> None:
        self._sent.append(data)

    def makefile(self, *a: Any, **k: Any) -> Any:
        import io

        return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    def close(self) -> None:
        pass

    def setsockopt(self, *a: Any) -> None:
        pass


def test_an_unreachable_ipv6_address_falls_back_to_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1::1", 0, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
    ]
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda *a, **k: answers)
    tried: list[str] = []
    sent: list[bytes] = []

    def connect(address: tuple[str, int], *a: Any, **k: Any) -> Any:
        tried.append(address[0])
        if ":" in address[0]:
            raise OSError("network unreachable")
        return _FakeSock(sent)

    monkeypatch.setattr(net_guard.socket, "create_connection", connect)
    body, _ = net_guard.fetch_bytes("http://dual.example/x")
    assert body == b"ok"
    assert tried == ["2606:2800:220:1::1", "93.184.216.34"]


def test_the_host_header_carries_the_idna_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )
    sent: list[bytes] = []
    monkeypatch.setattr(net_guard.socket, "create_connection", lambda *a, **k: _FakeSock(sent))
    net_guard.fetch_bytes("http://BÜCHER.example:8080/x")
    wire = b"".join(sent)
    assert b"Host: xn--bcher-kva.example:8080" in wire
    assert net_guard.canonical_url("https://user:pw@BÜCHER.example./a?b=1") == (
        "https://xn--bcher-kva.example/a?b=1"
    )


def test_the_guarded_path_ignores_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documented: a proxy would resolve the host itself, undoing the check."""
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    opener = net_guard.guarded_opener()
    proxies = [h for h in opener.handlers if isinstance(h, net_guard.urllib.request.ProxyHandler)]
    assert all(not h.proxies for h in proxies)  # none at all, or one with no proxies


@pytest.mark.parametrize("tcp_elapsed", [9.0, 10.0])
def test_tls_stays_within_the_connect_deadline(
    monkeypatch: pytest.MonkeyPatch, tcp_elapsed: float
) -> None:
    from types import SimpleNamespace

    now = 0.0

    class Sock:
        timeout = 0.0
        closed = False

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def close(self) -> None:
            self.closed = True

    sock = Sock()

    def connect(address: tuple[str, int], timeout: float, source: Any) -> Sock:
        nonlocal now
        if ":" in address[0]:
            now += 7.0
            raise OSError("IPv6 unreachable")
        now += tcp_elapsed - 7.0
        sock.settimeout(timeout)
        return sock

    def handshake(raw: Sock, *, server_hostname: str) -> Sock:
        nonlocal now
        assert server_hostname == "pictures.example"
        now += min(2.5, raw.timeout)
        if raw.timeout < 2.5:
            raise TimeoutError("TLS timed out")
        return raw

    tls = net_guard.ssl.create_default_context()
    monkeypatch.setattr(tls, "wrap_socket", handshake)
    monkeypatch.setattr(net_guard, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(net_guard.socket, "create_connection", connect)
    connection = net_guard._PinnedHTTPSConnection(
        "pictures.example", pinned=["2606:2800:220:1::1", "93.184.216.34"], context=tls, timeout=10
    )
    with pytest.raises(TimeoutError):
        connection.connect()
    assert now <= 10.0
    assert sock.closed
    assert connection.sock is None
