"""The TCP side of the Visionect listener, bounded for an open LAN port.

``pyvisionect.io.VisionectServer`` accepts every connection and decodes
whatever arrives, and the protocol has no authentication, so anything on the
LAN can talk to port 11113. pyvisionect 0.1.1 leaves a few things unbounded
that this module bounds before the library sees a byte:

* **Frame size.** The decoder buffers a frame up to ``MAX_FRAME_LENGTH``
  (50 MiB) per connection. A sign's own frames are tiny (a status packet is
  a few hundred bytes, an ACK 8), so frames over ``MAX_INBOUND_FRAME`` are
  refused here.
* **Decompression.** The block decoder checks only the first block's count
  against its 50 MiB cap and never bounds a block's ``UncompressedLength``,
  and the cramjam codec returns that many bytes even from a one-byte block:
  a 45-byte frame inflates to as much as the header claims (4 GiB). A sign
  never compresses what it sends; a compressed frame is passed on only when
  its blocks declare at most ``MAX_INBOUND_PLAINTEXT`` bytes in all.
* **Slow identification.** The library's identify timeout applies per read,
  so a peer dribbling a byte every few seconds never trips it. Here a
  connection has ``IDENTIFY_TIMEOUT_S`` in all to identify, after which it
  is dropped (and a sign re-dials).
* **Connection count.** At most ``MAX_CONNECTIONS`` sockets at once (a bound
  sign's own address is exempt), and ``MAX_CONNECTIONS_PER_PEER`` from one
  address.

It also works around a library bug: ``_handle_client`` awaits its timer task
in a ``finally`` that suppresses only ``CancelledError``, so a write error in
that task skipped the removal of the connection and the socket's close.

Both hooks override private methods (``_handle_client``, the asyncio accept
callback, and ``_run_timers``); ``tests/test_visionect.py`` pins their
signatures so a pyvisionect upgrade that moves them fails loudly. All of this
belongs upstream in pyvisionect; it lives here until it is there.

Plaintext only: Tesserae never gives the server an ``ssl_context``. A TLS
ClientHello is handed to the library untouched, which closes it and logs that
the sign has TLS switched on.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from typing import Any

from pyvisionect.io import VisionectServer
from pyvisionect.io.tcp import looks_like_client_hello
from pyvisionect.session import DeviceStateStore
from pyvisionect.wire import (
    BLOCK_HEADER_SIZE,
    PROTOCOL_HEADER_SIZE,
    PROTOCOL_VERSION,
    BlockHeader,
    Compression,
    ProtocolHeader,
)

logger = logging.getLogger("app.visionect")

READ_CHUNK = 65536
# The largest frame a sign is allowed to send, and what a compressed one may
# declare it inflates to. Both are far above anything a sign sends.
MAX_INBOUND_FRAME = 64 * 1024
MAX_INBOUND_PLAINTEXT = 256 * 1024
# A real sign sends its first status within a second of connecting. Also the
# library's own per-read identify timeout, which was 120 s: after a Tesserae
# restart a sign that re-dialed and said nothing held that long.
IDENTIFY_TIMEOUT_S = 20.0
MAX_CONNECTIONS = 32
MAX_CONNECTIONS_PER_PEER = 8
# Addresses already warned about, so a flood logs once per address.
_MAX_WARNED_PEERS = 64


def peer_ip(writer: Any) -> str:
    peer = writer.get_extra_info("peername") if writer is not None else None
    return str(peer[0]) if isinstance(peer, tuple) and peer else ""


def abort(writer: Any) -> None:
    """Drop a socket now, without draining what is queued on it."""
    transport = getattr(writer, "transport", None)
    if transport is not None:
        try:
            transport.abort()
        except Exception:
            logger.debug("visionect: aborting a socket failed", exc_info=True)


def is_closing(writer: Any) -> bool:
    transport = getattr(writer, "transport", None)
    return transport is None or bool(transport.is_closing())


def declared_plaintext(body: bytes) -> int:
    """What an LZ4 block chain says it inflates to: the sum of each block's
    ``UncompressedLength`` (its payload length when stored). A chain that
    runs out mid-header is left for the library to reject."""
    total = 0
    offset = 0
    while len(body) - offset >= BLOCK_HEADER_SIZE:
        header = BlockHeader.unpack_from(body, offset)
        total += header.payload_length if header.is_stored else header.uncompressed_length
        offset += BLOCK_HEADER_SIZE + header.payload_length
    return total


class InboundGuard:
    """Stands in for the connection's ``StreamReader`` and hands the library
    only whole frames that passed the size checks. A frame that fails them
    drops the socket, and the library sees end of stream."""

    def __init__(self, reader: Any, writer: Any, server: GuardedServer) -> None:
        self._reader = reader
        self._writer = writer
        self._server = server
        self._buf = bytearray()  # received, not yet checked
        self._out = bytearray()  # checked, not yet handed over
        self._passthrough = False
        self._eof = False
        self.identified = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._reader, name)

    async def read(self, n: int = -1) -> bytes:
        while not self._out and not self._eof:
            chunk = await self._reader.read(READ_CHUNK)
            if not chunk:
                # Hand over a truncated tail as is: the library drops it.
                self._eof = True
                self._out += self._buf
                self._buf.clear()
                break
            self._buf += chunk
            problem = self._check()
            if problem:
                self._reject(problem)
                return b""
        size = len(self._out) if n is None or n < 0 else n
        data = bytes(self._out[:size])
        del self._out[:size]
        return data

    def _check(self) -> str:
        """Move every complete, acceptable frame from ``_buf`` to ``_out``.
        Returns why the stream is refused, or ""."""
        while True:
            if self._passthrough:
                self._out += self._buf
                self._buf.clear()
                return ""
            if len(self._buf) < PROTOCOL_HEADER_SIZE:
                return ""
            header = ProtocolHeader.unpack_from(bytes(self._buf[:PROTOCOL_HEADER_SIZE]))
            if header.version != PROTOCOL_VERSION:
                if looks_like_client_hello(bytes(self._buf[:PROTOCOL_HEADER_SIZE])):
                    # The library closes it and says the sign has TLS on.
                    self._passthrough = True
                    continue
                return "not the Visionect protocol"
            if header.length > MAX_INBOUND_FRAME:
                return f"a {header.length}-byte frame"
            total = PROTOCOL_HEADER_SIZE + header.length
            if len(self._buf) < total:
                return ""
            if header.compression != Compression.NONE:
                inflates = declared_plaintext(bytes(self._buf[PROTOCOL_HEADER_SIZE:total]))
                if inflates > MAX_INBOUND_PLAINTEXT:
                    return f"a frame that inflates to {inflates} bytes"
            self._out += self._buf[:total]
            del self._buf[:total]

    def _reject(self, problem: str) -> None:
        self._eof = True
        self._buf.clear()
        self._out.clear()
        self._server.rejected_frames += 1
        self._server.note_peer(self._writer, f"dropped the connection: it sent {problem}")
        abort(self._writer)


class GuardedServer(VisionectServer):
    """``VisionectServer`` with the bounds described in the module docstring."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("identify_timeout", IDENTIFY_TIMEOUT_S)
        super().__init__(**kwargs)
        self._open = 0
        self._per_peer: Counter[str] = Counter()
        self._guards: dict[Any, InboundGuard] = {}
        self._warned_peers: dict[str, None] = {}
        self.refused_connections = 0
        self.rejected_frames = 0
        self.identify_timeouts = 0
        # Whether Tesserae keeps state for a UUID (a sign it tracks). The
        # library allocates store and stats entries for every UUID a frame
        # names, admitted or not; the rest are pruned as connections close.
        self.keep_identity: Callable[[bytes], bool] = lambda _device_id: False
        # A bound sign's own address, exempt from MAX_CONNECTIONS (never from
        # the per-address cap), so a few busy addresses can't lock it out.
        self.trusted_peer: Callable[[str], bool] = lambda _ip: False

    def mark_identified(self, writer: Any) -> None:
        """The socket behind ``writer`` identified as a sign Tesserae keeps."""
        guard = self._guards.get(writer)
        if guard is not None:
            guard.identified = True

    def note_peer(self, writer: Any, what: str) -> None:
        ip = peer_ip(writer) or "?"
        if ip in self._warned_peers:
            logger.debug("visionect: %s %s", ip, what)
            return
        self._warned_peers[ip] = None
        while len(self._warned_peers) > _MAX_WARNED_PEERS:
            self._warned_peers.pop(next(iter(self._warned_peers)))
        logger.warning("visionect: %s %s", ip, what)

    async def _handle_client(self, reader: Any, writer: Any) -> None:
        ip = peer_ip(writer)
        full = self._open >= MAX_CONNECTIONS and not self.trusted_peer(ip)
        if full or self._per_peer[ip] >= MAX_CONNECTIONS_PER_PEER:
            self.refused_connections += 1
            self.note_peer(writer, "refused: too many open connections")
            abort(writer)
            return
        self._open += 1
        self._per_peer[ip] += 1
        guard = InboundGuard(reader, writer, self)
        self._guards[writer] = guard
        deadline = asyncio.get_running_loop().call_later(
            IDENTIFY_TIMEOUT_S, self._identify_deadline, writer
        )
        try:
            # Duck-typed: the library only ever calls read() on its reader.
            await super()._handle_client(guard, writer)  # type: ignore[arg-type]
        finally:
            deadline.cancel()
            self._guards.pop(writer, None)
            self._open -= 1
            self._per_peer[ip] -= 1
            if self._per_peer[ip] <= 0:
                del self._per_peer[ip]
            self.prune_identities()

    def prune_identities(self) -> None:
        """Forget the per-UUID state the library keeps (its device store,
        ``stats.last_seen``) for every UUID with no open connection that
        Tesserae doesn't keep: a malformed first status, a sign refused for
        being one unknown too many, a made-up UUID."""
        live = set(getattr(self, "_connections", None) or ())

        def gone(device_id: bytes) -> bool:
            return device_id not in live and not self.keep_identity(device_id)

        prune = getattr(self.store, "prune", None)
        if callable(prune):
            prune(gone)
        last_seen = self.stats.last_seen
        for device_id in [d for d in last_seen if gone(d)]:
            del last_seen[device_id]

    def _identify_deadline(self, writer: Any) -> None:
        guard = self._guards.get(writer)
        if guard is None or guard.identified:
            return
        self.identify_timeouts += 1
        logger.info(
            "visionect: closing %s: not identified within %.0fs (a sign re-dials)",
            peer_ip(writer) or "?",
            IDENTIFY_TIMEOUT_S,
        )
        abort(writer)

    async def _run_timers(self, conn: Any, writer: Any) -> None:
        try:
            await super()._run_timers(conn, writer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Raised out of this task, the error would skip the library's
            # cleanup (see the module docstring). End quietly, and drop the
            # socket so the read loop ends and does that cleanup itself.
            logger.debug("visionect: timer task for %s ended: %s", peer_ip(writer), exc)
            abort(writer)


class PrunableStore(DeviceStateStore):
    """``DeviceStateStore`` that can forget a UUID. 0.1.1 has no way to, so
    a stream of made-up UUIDs would grow it without bound."""

    def discard(self, device_id: bytes) -> None:
        states = getattr(self, "_states", None)
        if isinstance(states, dict):
            states.pop(device_id, None)

    def prune(self, drop: Callable[[bytes], bool]) -> None:
        """Forget every UUID ``drop`` says to."""
        states = getattr(self, "_states", None)
        if isinstance(states, dict):
            for device_id in [d for d in states if drop(d)]:
                del states[device_id]
