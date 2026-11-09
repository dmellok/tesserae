"""Minimal client for the Samsung Frame TV's local Art Mode API.

The Frame exposes its Art app on the TV's own websocket server
(``wss://<tv>:8002/api/v2/channels/com.samsung.art-app``), the same channel
the SmartThings app uses to add photos. Requests are JSON ``art_app_request``
messages; replies come back as ``d2d_service_message`` events. Image uploads
go over a second, short-lived TCP socket that the TV opens on request.

This module covers only what Tesserae needs (upload, select, list, delete,
slideshow off) and uses the standard library alone: the websocket framing is
a small RFC 6455 client. The TV presents a self-signed certificate, so TLS is
used for transport privacy but not verified, as every Samsung client does.

The first connection from a new client name shows an "Allow" prompt on the
TV; the reply then carries a token that later connections pass to skip it.

Protocol reference: the art-mode notes in xchwarze/samsung-tv-ws-api and
NickWaterton's fork (LGPL); this is an independent implementation.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import random
import socket
import ssl
import struct
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Any

ART_CHANNEL = "com.samsung.art-app"
REMOTE_CHANNEL = "samsung.remote.control"
# Holding Power this long puts the Frame in standby; a short press only
# leaves Art Mode for the TV.
POWER_HOLD_S = 3.0
MY_PHOTOS = "MY-C0002"
DEFAULT_PORT = 8002
# Pairing waits for a person to press Allow on the TV.
PAIRING_TIMEOUT_S = 45.0
REQUEST_TIMEOUT_S = 15.0
UPLOAD_CONFIRM_TIMEOUT_S = 45.0
# The TV asks clients to wait briefly before a TLS d2d connection.
SECURED_D2D_DELAY_S = 1.0
# Other clients on the channel (Home Assistant, SmartThings) are announced
# with these; they can arrive before or between our replies.
_PEER_EVENTS = {"ms.channel.clientConnect", "ms.channel.clientDisconnect"}


class FrameArtError(Exception):
    """Any failure talking to the Art app."""


class FramePairingError(FrameArtError):
    """The TV refused the client or nobody pressed Allow in time."""


class FrameArtTimeout(FrameArtError):
    """The Art app did not answer a request."""


def _insecure_tls() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _WebSocket:
    """Just enough RFC 6455 for a JSON text channel: masked client frames,
    fragment reassembly, ping/pong and close."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = b""

    @classmethod
    def connect(cls, host: str, port: int, path: str, timeout: float) -> _WebSocket:
        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            sock = _insecure_tls().wrap_socket(raw, server_hostname=host)
            key = base64.b64encode(os.urandom(16)).decode()
            request = (
                f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(request.encode())
            ws = cls(sock)
            head = ws._read_until(b"\r\n\r\n")
            status = head.split(b"\r\n", 1)[0]
            if b" 101 " not in status + b" ":
                raise FrameArtError(f"websocket upgrade refused: {status.decode(errors='replace')}")
            return ws
        except BaseException:
            raw.close()
            raise

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise FrameArtError("TV closed the connection")
            self._buf += chunk
            if len(self._buf) > 65536:
                raise FrameArtError("oversized websocket handshake")
        head, self._buf = self._buf.split(marker, 1)
        return head

    def _read_exact(self, count: int, deadline: float | None = None) -> bytes:
        while len(self._buf) < count:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("websocket read timed out")
                self._sock.settimeout(remaining)
            chunk = self._sock.recv(max(4096, count - len(self._buf)))
            if not chunk:
                raise FrameArtError("TV closed the connection")
            self._buf += chunk
        data, self._buf = self._buf[:count], self._buf[count:]
        return data

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode())

    def _send(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def recv_text(self, timeout: float) -> str:
        """Next complete text message within ``timeout`` seconds overall (the
        TV pings every few seconds, so the limit can't be per read). Pings
        are answered; binary messages (only 2017/2018 Frames) are skipped."""
        deadline = time.monotonic() + max(0.05, timeout)
        message = b""
        while True:
            b0, b1 = self._read_exact(2, deadline)
            opcode, length = b0 & 0x0F, b1 & 0x7F
            if length == 126:
                (length,) = struct.unpack(">H", self._read_exact(2, deadline))
            elif length == 127:
                (length,) = struct.unpack(">Q", self._read_exact(8, deadline))
            if length > 16 * 1024 * 1024:
                raise FrameArtError("oversized websocket message")
            mask = self._read_exact(4, deadline) if b1 & 0x80 else b""
            payload = self._read_exact(length, deadline)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode == 0x8:
                raise FrameArtError("TV closed the art channel")
            if opcode in (0x1, 0x2, 0x0):
                message += payload
                if b0 & 0x80:
                    if opcode == 0x2:
                        message = b""
                        continue
                    return message.decode("utf-8", errors="replace")

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._send(0x8, b"")
        with contextlib.suppress(OSError):
            self._sock.close()


class FrameArtClient:
    """One connection to a Frame's Art app. Not thread-safe; use one per
    worker. ``token`` is updated in place when the TV issues a new one."""

    def __init__(
        self,
        host: str,
        *,
        token: str | None = None,
        name: str = "Tesserae",
        port: int = DEFAULT_PORT,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token or None
        self.name = name
        self.timeout = timeout
        self._ws: _WebSocket | None = None

    # -- connection -------------------------------------------------------

    def __enter__(self) -> FrameArtClient:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def connect(self, pairing_timeout: float = PAIRING_TIMEOUT_S) -> None:
        query = {"name": base64.b64encode(self.name.encode()).decode()}
        if self.token:
            query["token"] = self.token
        path = f"/api/v2/channels/{ART_CHANNEL}?" + urllib.parse.urlencode(query)
        try:
            self._ws = _WebSocket.connect(self.host, self.port, path, self.timeout)
        except OSError as exc:
            raise FrameArtError(f"can't reach the TV at {self.host}: {exc}") from exc
        deadline = time.monotonic() + pairing_timeout
        while True:
            message = self._recv(deadline)
            event = message.get("event")
            if event == "ms.channel.connect":
                token = (message.get("data") or {}).get("token")
                if token:
                    self.token = str(token)
            elif event == "ms.channel.ready":
                return
            elif event in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                self.close()
                raise FramePairingError(
                    "the TV did not allow Tesserae (press Allow when the prompt shows)"
                )
            elif event == "ms.error":
                self.close()
                raise FrameArtError(f"TV refused the art channel: {message.get('data')}")

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None

    def _recv(self, deadline: float) -> dict[str, Any]:
        if self._ws is None:
            raise FrameArtError("not connected")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FrameArtTimeout(f"{self.host} did not answer in time")
        try:
            text = self._ws.recv_text(remaining)
        except TimeoutError as exc:
            raise FrameArtTimeout(f"{self.host} did not answer in time") from exc
        except OSError as exc:
            raise FrameArtError(f"connection to {self.host} failed: {exc}") from exc
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    # -- requests ---------------------------------------------------------

    def _send_request(self, data: dict[str, Any]) -> str:
        if self._ws is None:
            raise FrameArtError("not connected")
        request_id = str(data.get("id") or uuid.uuid4())
        data["id"] = data["request_id"] = request_id
        envelope = {
            "method": "ms.channel.emit",
            "params": {"event": "art_app_request", "to": "host", "data": json.dumps(data)},
        }
        self._ws.send_text(json.dumps(envelope))
        return request_id

    def _await(
        self, request_id: str, timeout: float, events: set[str] | None = None
    ) -> dict[str, Any]:
        """Wait for the Art app's reply to ``request_id``. With ``events``,
        only those sub-events count (an upload is answered twice)."""
        deadline = time.monotonic() + timeout
        while True:
            message = self._recv(deadline)
            if message.get("event") != "d2d_service_message":
                continue
            try:
                data = json.loads(message.get("data") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            sub = str(data.get("event") or "")
            reply_id = data.get("request_id") or data.get("id")
            if sub == "error":
                try:
                    original = json.loads(data.get("request_data") or "{}")
                except (TypeError, json.JSONDecodeError):
                    original = {}
                if reply_id == request_id or original.get("id") == request_id:
                    raise FrameArtError(
                        f"{original.get('request', 'art')} request failed with error {data.get('error_code')}"
                    )
                continue
            if events is not None:
                # 2022+ Frames send image_added without an id; ours is the
                # only upload in flight on this connection. Some firmware
                # answers send_image under its own event name, so a reply to
                # our id that carries the transfer details also counts.
                if sub in events and reply_id in (request_id, None, ""):
                    return data
                if "ready_to_use" in events and reply_id == request_id and "conn_info" in data:
                    return data
                continue
            if reply_id == request_id:
                return data

    def request(self, data: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        request_id = self._send_request(dict(data))
        return self._await(request_id, timeout or self.timeout)

    # -- art operations ---------------------------------------------------

    def api_version(self) -> str:
        """Art API version, e.g. "2.03" (2020/2021) or "4.3.4.0" (2022+)."""
        try:
            reply = self.request({"request": "get_api_version"})
        except FrameArtTimeout:
            reply = self.request({"request": "api_version"})
        return str(reply.get("version") or reply.get("value") or "")

    def list_content(self, category: str = MY_PHOTOS) -> list[dict[str, Any]]:
        reply = self.request({"request": "get_content_list", "category": category}, timeout=30)
        try:
            items = json.loads(reply.get("content_list") or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return [i for i in items if isinstance(i, dict) and i.get("category_id") == category]

    def upload_jpeg(
        self,
        data: bytes,
        *,
        matte: str = "none",
        portrait_matte: str = "none",
        existing: set[str] | None = None,
    ) -> str:
        """Add a JPEG to My Photos and return its content id. ``existing``
        (the ids on the TV before the upload) lets a confirmation without a
        request id be told apart from another app's upload."""
        # The TV only accepts the transfer when the request id, its "id" and
        # the conn_info id are all the same value.
        upload_id = str(uuid.uuid4())
        request_id = self._send_request(
            {
                "id": upload_id,
                "request": "send_image",
                "file_type": "jpg",
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": upload_id,
                },
                "image_date": datetime.now().strftime("%Y:%m:%d %H:%M:%S"),
                "matte_id": matte or "none",
                "portrait_matte_id": portrait_matte or "none",
                "file_size": len(data),
            }
        )
        ready = self._await(request_id, self.timeout, events={"ready_to_use"})
        try:
            conn = json.loads(ready["conn_info"])
            ip, port, key = conn["ip"], int(conn["port"]), conn["key"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise FrameArtError(f"unexpected upload reply: {ready}") from exc
        header = json.dumps(
            {
                "num": 0,
                "total": 1,
                "fileLength": len(data),
                "fileName": "tesserae",
                "fileType": "jpg",
                "secKey": key,
                "version": "0.0.1",
            }
        ).encode("ascii")
        secured = bool(conn.get("secured"))
        if secured:
            time.sleep(SECURED_D2D_DELAY_S)
        try:
            with socket.create_connection((ip, port), timeout=30) as raw:
                sock: socket.socket = (
                    _insecure_tls().wrap_socket(raw, server_hostname=ip) if secured else raw
                )
                sock.sendall(len(header).to_bytes(4, "big") + header)
                sock.sendall(data)
        except OSError as exc:
            raise FrameArtError(f"upload transfer to {ip}:{port} failed: {exc}") from exc
        deadline = time.monotonic() + UPLOAD_CONFIRM_TIMEOUT_S
        while True:
            added = self._await(
                request_id, max(0.1, deadline - time.monotonic()), events={"image_added"}
            )
            content_id = str(added.get("content_id") or "")
            own = (added.get("request_id") or added.get("id")) == request_id
            if own or existing is None or content_id not in existing:
                break
        if not content_id:
            raise FrameArtError(f"TV did not return a content id: {added}")
        return content_id

    def select(self, content_id: str, *, show: bool = False) -> None:
        """Make ``content_id`` the artwork. ``show=False`` leaves whatever
        the TV is doing alone; Art Mode shows it next time it is on."""
        self.request(
            {"request": "select_image", "category_id": None, "content_id": content_id, "show": show}
        )

    def art_mode_on(self) -> bool:
        reply = self.request({"request": "get_artmode_status"})
        return str(reply.get("value", reply.get("status", ""))) == "on"

    def set_art_mode(self, on: bool) -> None:
        """Switch Art Mode on (from TV mode) or off."""
        self.request({"request": "set_artmode_status", "value": "on" if on else "off"})

    def current(self) -> str:
        reply = self.request({"request": "get_current_artwork"})
        return str(reply.get("content_id") or "")

    def delete(self, content_ids: list[str]) -> None:
        if content_ids:
            self.request(
                {
                    "request": "delete_image_list",
                    "content_id_list": [{"content_id": c} for c in content_ids],
                }
            )

    def slideshow_off(self) -> None:
        """Stop the TV's own rotation so it doesn't replace Tesserae's frame.
        2021 and older Frames call it auto_rotation; 2022+ call it slideshow."""
        fields = {"value": "off", "category_id": MY_PHOTOS, "type": "slideshow"}
        last: Exception | None = None
        for name in ("auto_rotation", "slideshow"):
            try:
                self.request({"request": f"set_{name}_status", **fields})
                return
            except FrameArtError as exc:
                last = exc
        raise FrameArtError(f"could not turn the TV slideshow off: {last}")

    def slideshow_status(self) -> dict[str, Any]:
        last: Exception | None = None
        for name in ("auto_rotation", "slideshow"):
            try:
                return self.request({"request": f"get_{name}_status"})
            except FrameArtError as exc:
                last = exc
        raise FrameArtError(f"could not read the TV slideshow: {last}")


def hold_power_key(
    host: str,
    *,
    token: str | None,
    name: str = "Tesserae",
    port: int = DEFAULT_PORT,
    seconds: float = POWER_HOLD_S,
    timeout: float = REQUEST_TIMEOUT_S,
) -> None:
    """Hold the remote's Power key over the network: the Frame goes to
    standby (screen off, Art Mode with it). Uses the TV's remote-control
    channel with the same pairing token as the Art app."""
    query = {"name": base64.b64encode(name.encode()).decode()}
    if token:
        query["token"] = token
    path = f"/api/v2/channels/{REMOTE_CHANNEL}?" + urllib.parse.urlencode(query)
    try:
        ws = _WebSocket.connect(host, port, path, timeout)
    except OSError as exc:
        raise FrameArtError(f"can't reach the TV at {host}: {exc}") from exc
    try:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrameArtTimeout(f"{host} remote channel did not open")
            try:
                message = json.loads(ws.recv_text(remaining) or "{}")
            except TimeoutError as exc:
                raise FrameArtTimeout(f"{host} remote channel did not open") from exc
            event = message.get("event") if isinstance(message, dict) else None
            if event == "ms.channel.connect":
                break
            if event in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                raise FramePairingError("the TV did not allow Tesserae's remote")
        for cmd in ("Press", "Release"):
            ws.send_text(
                json.dumps(
                    {
                        "method": "ms.remote.control",
                        "params": {
                            "Cmd": cmd,
                            "DataOfCmd": "KEY_POWER",
                            "Option": "false",
                            "TypeOfRemote": "SendRemoteKey",
                        },
                    }
                )
            )
            if cmd == "Press":
                time.sleep(seconds)
    finally:
        ws.close()


def wake_on_lan(mac: str, host: str | None = None) -> None:
    """Send Wake-on-LAN magic packets for ``mac``: to the broadcast address,
    the /24 broadcast of ``host`` and ``host`` itself, so one gets through
    whether Tesserae runs on the host network or behind a container bridge.
    The TV needs network standby on (it is by default on a Frame)."""
    digits = "".join(c for c in mac if c.isalnum())
    if len(digits) != 12:
        raise ValueError(f"not a MAC address: {mac!r}")
    packet = b"\xff" * 6 + bytes.fromhex(digits) * 16
    targets = ["255.255.255.255"]
    if host:
        parts = host.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            targets += [".".join([*parts[:3], "255"]), host]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in targets:
            with contextlib.suppress(OSError):
                sock.sendto(packet, (target, 9))
