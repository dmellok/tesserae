"""The Samsung Frame Art Mode client: websocket framing, reply matching and
the upload transfer, against in-process fakes (no TV)."""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any

import pytest

from app import samsung_frame_art as art
from app.samsung_frame_art import FrameArtClient, FrameArtError, FrameArtTimeout, FramePairingError


def _server_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """An unmasked server-to-client frame."""
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def _read_client_frame(sock: socket.socket) -> tuple[int, bytes]:
    def exact(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            buf += sock.recv(n - len(buf))
        return buf

    b0, b1 = exact(2)
    assert b1 & 0x80, "client frames must be masked"
    length = b1 & 0x7F
    if length == 126:
        (length,) = struct.unpack(">H", exact(2))
    elif length == 127:
        (length,) = struct.unpack(">Q", exact(8))
    mask = exact(4)
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(exact(length)))
    return b0 & 0x0F, data


def test_websocket_answers_pings_and_reassembles_fragments() -> None:
    a, b = socket.socketpair()
    ws = art._WebSocket(a)
    long_text = json.dumps({"event": "x", "data": "y" * 300}).encode()
    b.sendall(
        _server_frame(0x9, b"\x00")
        + _server_frame(0x1, long_text[:100], fin=False)
        + _server_frame(0x0, long_text[100:])
    )
    assert ws.recv_text(2) == long_text.decode()
    assert _read_client_frame(b) == (0xA, b"\x00")
    ws.send_text("hello")
    assert _read_client_frame(b) == (0x1, b"hello")
    a.close()
    b.close()


def test_websocket_timeout_is_overall_not_per_read() -> None:
    """The TV pings every few seconds; pings must not extend the deadline."""
    a, b = socket.socketpair()
    ws = art._WebSocket(a)
    stop = threading.Event()

    def pinger() -> None:
        while not stop.wait(0.05):
            try:
                b.sendall(_server_frame(0x9, b"\x00"))
            except OSError:
                return

    t = threading.Thread(target=pinger, daemon=True)
    t.start()
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        ws.recv_text(0.4)
    assert time.monotonic() - start < 1.5
    stop.set()
    a.close()
    b.close()


class FakeWS:
    """Scripted channel: ``replies`` maps a request name to the messages the
    TV sends once that request arrives."""

    def __init__(self, replies: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.replies = replies or {}
        self.sent: list[dict[str, Any]] = []
        self.queue: list[str] = []

    def push(self, message: dict[str, Any]) -> None:
        self.queue.append(json.dumps(message))

    def send_text(self, text: str) -> None:
        envelope = json.loads(text)
        data = json.loads(envelope["params"]["data"])
        self.sent.append(data)
        for reply in self.replies.get(data["request"], []):
            reply = json.loads(json.dumps(reply).replace("$ID", data["id"]))
            self.push(reply)

    on_idle: Any = None

    def recv_text(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while not self.queue:
            if self.on_idle is not None:
                self.on_idle()
            if self.queue:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError
            time.sleep(0.02)
        return self.queue.pop(0)

    def close(self) -> None:
        pass


def _d2d(payload: dict[str, Any]) -> dict[str, Any]:
    return {"event": "d2d_service_message", "data": json.dumps(payload)}


def _client(ws: FakeWS) -> FrameArtClient:
    client = FrameArtClient("192.0.2.10", timeout=0.5)
    client._ws = ws  # type: ignore[assignment]
    return client


def test_connect_skips_other_clients_and_keeps_new_token(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWS()
    ws.push({"event": "ms.channel.connect", "data": {"token": "12345", "clients": []}})
    ws.push({"event": "ms.channel.clientConnect", "data": {"deviceName": "W0hBXSBUVg=="}})
    ws.push({"event": "ms.channel.ready", "data": {}})
    monkeypatch.setattr(art._WebSocket, "connect", classmethod(lambda cls, *a, **k: ws))
    client = FrameArtClient("192.0.2.10")
    client.connect(pairing_timeout=1)
    assert client.token == "12345"


def test_connect_refused_raises_pairing_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWS()
    ws.push({"event": "ms.channel.unauthorized"})
    monkeypatch.setattr(art._WebSocket, "connect", classmethod(lambda cls, *a, **k: ws))
    with pytest.raises(FramePairingError):
        FrameArtClient("192.0.2.10").connect(pairing_timeout=1)


def test_connect_without_ready_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWS()
    ws.push({"event": "ms.channel.connect", "data": {}})
    monkeypatch.setattr(art._WebSocket, "connect", classmethod(lambda cls, *a, **k: ws))
    with pytest.raises(FrameArtTimeout):
        FrameArtClient("192.0.2.10").connect(pairing_timeout=0.2)


def test_request_matches_its_own_reply_and_ignores_others() -> None:
    ws = FakeWS(
        {
            "get_content_list": [
                _d2d({"event": "artmode_status", "value": "on"}),
                _d2d({"event": "content_list", "id": "someone-else", "content_list": "[]"}),
                _d2d(
                    {
                        "event": "content_list",
                        "id": "$ID",
                        "content_list": json.dumps(
                            [
                                {"content_id": "MY_F0001", "category_id": "MY-C0002"},
                                {"content_id": "SAM-1", "category_id": "MY-C0008"},
                            ]
                        ),
                    }
                ),
            ]
        }
    )
    assert [i["content_id"] for i in _client(ws).list_content()] == ["MY_F0001"]
    assert ws.sent[0]["request_id"] == ws.sent[0]["id"]


def test_error_reply_raises() -> None:
    ws = FakeWS(
        {
            "select_image": [
                _d2d(
                    {
                        "event": "error",
                        "error_code": "-1",
                        "request_data": json.dumps({"id": "$ID", "request": "select_image"}),
                    }
                ),
            ]
        }
    )
    with pytest.raises(FrameArtError, match="error -1"):
        _client(ws).select("MY_F0001")


def test_slideshow_off_falls_back_to_the_2022_name() -> None:
    ws = FakeWS(
        {
            "set_auto_rotation_status": [
                _d2d(
                    {
                        "event": "error",
                        "error_code": "-9",
                        "request_data": json.dumps({"id": "$ID"}),
                    }
                ),
            ],
            "set_slideshow_status": [
                _d2d({"event": "slideshow_status", "id": "$ID", "value": "off"})
            ],
        }
    )
    _client(ws).slideshow_off()
    assert [s["request"] for s in ws.sent] == ["set_auto_rotation_status", "set_slideshow_status"]
    assert ws.sent[1]["value"] == "off"


def test_upload_sends_header_and_bytes_over_d2d_socket() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    received: dict[str, Any] = {}

    def receive() -> None:
        conn, _ = listener.accept()
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
        n = int.from_bytes(buf[:4], "big")
        received["header"] = json.loads(buf[4 : 4 + n])
        received["body"] = buf[4 + n :]

    t = threading.Thread(target=receive, daemon=True)
    t.start()
    conn_info = json.dumps({"ip": "127.0.0.1", "port": port, "key": "sec-key", "secured": False})
    ws = FakeWS(
        {"send_image": [_d2d({"event": "ready_to_use", "id": "$ID", "conn_info": conn_info})]}
    )
    client = _client(ws)
    client.timeout = 2
    confirmed: list[bool] = []

    def confirm_after_transfer() -> None:
        # The TV confirms only after the bytes have arrived.
        if "body" in received and not confirmed:
            confirmed.append(True)
            ws.push(
                _d2d(
                    {
                        "event": "image_added",
                        "content_id": "MY_F0042",
                        "request_id": ws.sent[0]["id"],
                    }
                )
            )

    ws.on_idle = confirm_after_transfer
    payload = b"\xff\xd8" + b"x" * 200_000 + b"\xff\xd9"
    content_id = client.upload_jpeg(payload, matte="modern_polar")
    t.join(2)
    listener.close()
    assert content_id == "MY_F0042"
    assert received["header"]["secKey"] == "sec-key"
    assert received["header"]["fileLength"] == len(payload)
    assert received["body"] == payload
    assert ws.sent[0]["matte_id"] == "modern_polar"
    assert ws.sent[0]["file_size"] == len(payload)
    assert ws.sent[0]["conn_info"]["id"] == ws.sent[0]["id"] == ws.sent[0]["request_id"]


def test_upload_ignores_another_apps_image_added_without_an_id() -> None:
    conn_info = json.dumps({"ip": "127.0.0.1", "port": 1, "key": "k"})
    ws = FakeWS(
        {"send_image": [_d2d({"event": "ready_to_use", "id": "$ID", "conn_info": conn_info})]}
    )
    client = _client(ws)
    client.timeout = 2
    sent: list[bool] = []

    class FakeSock:
        def __enter__(self) -> FakeSock:
            return self

        def __exit__(self, *a: object) -> None:
            pass

        def sendall(self, data: bytes) -> None:
            if not sent:
                sent.append(True)
                # Someone else's photo lands first, without an id; then ours.
                ws.push(_d2d({"event": "image_added", "content_id": "MY_F0001"}))
                ws.push(_d2d({"event": "image_added", "content_id": "MY_F0099"}))

    import app.samsung_frame_art as mod

    real = mod.socket.create_connection
    mod.socket.create_connection = lambda *a, **k: FakeSock()  # type: ignore[assignment]
    try:
        content_id = client.upload_jpeg(b"jpeg", existing={"MY_F0001"})
    finally:
        mod.socket.create_connection = real  # type: ignore[assignment]
    assert content_id == "MY_F0099"
