"""The Visionect bootloader's firmware-update conversation, sans-io.

A Visionect frame told to reboot dials the server *before* its application
runs, and speaks a different protocol from the one ``pyvisionect`` handles. Left
unanswered it gives up and boots; answered with an offer and a firmware file it
flashes the file. This module is that conversation and nothing else: bytes in,
bytes out, a clock handed in by the caller. No sockets, no asyncio, no Flask.
It follows ``pyvisionect.session.DeviceConnection`` (``feed_at`` returns events,
``drain`` returns bytes to send, ``advance`` runs the timers) so it can move
into that library.

What is known, and what is not, is in ``docs/dev/visionect-bootloader-protocol.md``.
It was measured from one packet capture of Visionect's own Software Suite
flashing a 32" Place & Play (bootloader 6.3.3830, firmware 7.4.4407 to 7.3.4358).
Everything unexpected is rejected rather than guessed at.

All integers are little-endian. Every message starts with the word
``0xF0000002`` (bytes ``02 00 00 f0``); the second word says what it is::

    0  hello   frame -> server, 88 bytes
    1  offer / ack, 16 bytes each way
    2  firmware record, server -> frame, 24-byte header + payload
"""

from __future__ import annotations

import enum
import struct
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "ACK_SIZE",
    "FLAGS_LAST",
    "FLAGS_MORE",
    "FLAGS_MORE_ALT",
    "HELLO_SIZE",
    "MAGIC",
    "MAGIC_BYTES",
    "OFFER_SIZE",
    "RECORD_HEADER_SIZE",
    "Ack",
    "BlockAcked",
    "BootloaderError",
    "BootloaderSession",
    "Event",
    "Failed",
    "Finished",
    "Hello",
    "HelloReceived",
    "OfferSent",
    "Record",
    "SessionConfig",
    "State",
    "encode_ack",
    "encode_hello",
    "encode_offer",
    "is_bootloader_hello",
    "parse_ack",
    "parse_hello",
    "walk_firmware",
]

MAGIC = 0xF0000002
MAGIC_BYTES = struct.pack("<I", MAGIC)
HELLO_SIZE = 88
OFFER_SIZE = 16
ACK_SIZE = 16
RECORD_HEADER_SIZE = 24

_KIND_HELLO = 0
_KIND_OFFER = 1  # also the ack
_KIND_RECORD = 2
_NO_BLOCK = 0xFFFF  # the constant that follows the block index in an offer or ack

FLAGS_MORE = 0x0009
"""Record flags word on nearly every record but the last."""
FLAGS_MORE_ALT = 0x0008
"""Also seen mid-file: once in most builds, never in others (7.4.4407 block 130).
Its meaning isn't known. The frame acks it like any other (measured on 7.4.4407)."""
FLAGS_LAST = 0x000D
"""Record flags word on the last record."""


class BootloaderError(ValueError):
    """A message or file that is not what the bootloader protocol allows."""


# -- hello --------------------------------------------------------------------


@dataclass(frozen=True)
class Hello:
    """The frame's first message, 22 words.

    Word layout (index: meaning). Words marked "unknown" were constant in the
    one capture; they are kept so nothing is silently dropped.

    ====  =========================================================
    0     magic
    1     0 (message kind: hello)
    2     checksum of the firmware the frame runs now
    3     0xffffffff (unknown)
    4     0x000a0000 (unknown; bytes 16..19)
    5-7   the 12-byte hardware unique id (bytes 20..31)
    8     0, the last four bytes of the 16-byte device id
    9-11  firmware major, minor, revision
    12-14 bootloader major, minor, revision
    15    hardware name id
    16-18 hardware major, minor, BOM
    19    0 (unknown)
    20    0x1e (unknown)
    21    display id
    ====  =========================================================
    """

    current_checksum: int
    device_id: bytes
    """The 16 bytes ``pyvisionect`` calls the device id: bytes 20..35."""
    firmware: tuple[int, int, int]
    bootloader: tuple[int, int, int]
    hardware_name_id: int
    hardware: tuple[int, int, int]
    display_id: int
    unknown: tuple[int, int, int, int, int]
    """The unknown words, as (word 3, word 4, word 8, word 19, word 20)."""

    @property
    def uuid(self) -> str:
        """The device id as the canonical 8-4-4-4-12 string."""
        h = self.device_id.hex()
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def describe(self) -> str:
        fw = ".".join(map(str, self.firmware))
        bl = ".".join(map(str, self.bootloader))
        hw = ".".join(map(str, self.hardware))
        return (
            f"firmware {fw} checksum {self.current_checksum:08x}, bootloader {bl}, "
            f"hardware {hw} (name id {self.hardware_name_id}), display id {self.display_id}"
        )


def is_bootloader_hello(head: bytes) -> bool:
    """Whether the first bytes of a connection open a bootloader hello.

    Cheap enough to run on every connection. A normal ``pyvisionect`` frame
    opens with its protocol version, a little-endian ``uint32`` equal to 3, so
    it cannot collide with ``02 00 00 f0``. With fewer than 8 bytes only the
    magic can be checked.
    """
    if len(head) < 4 or head[:4] != MAGIC_BYTES:
        return False
    return len(head) < 8 or head[4:8] == b"\x00\x00\x00\x00"


def parse_hello(data: bytes) -> Hello:
    """Decode an 88-byte hello. Anything else raises ``BootloaderError``."""
    if len(data) != HELLO_SIZE:
        raise BootloaderError(f"a hello is {HELLO_SIZE} bytes, got {len(data)}")
    words = struct.unpack("<22I", data)
    if words[0] != MAGIC:
        raise BootloaderError(f"bad magic {words[0]:#010x}")
    if words[1] != _KIND_HELLO:
        raise BootloaderError(f"message kind {words[1]} is not a hello")
    return Hello(
        current_checksum=words[2],
        device_id=data[20:36],
        firmware=(words[9], words[10], words[11]),
        bootloader=(words[12], words[13], words[14]),
        hardware_name_id=words[15],
        hardware=(words[16], words[17], words[18]),
        display_id=words[21],
        unknown=(words[3], words[4], words[8], words[19], words[20]),
    )


def encode_hello(hello: Hello) -> bytes:
    """Build a hello (for tests and simulators; the server never sends one)."""
    w3, w4, w8, w19, w20 = hello.unknown
    uid = hello.device_id
    words = (
        MAGIC,
        _KIND_HELLO,
        hello.current_checksum,
        w3,
        w4,
        *struct.unpack("<3I", uid[:12]),
        w8,
        *hello.firmware,
        *hello.bootloader,
        hello.hardware_name_id,
        *hello.hardware,
        w19,
        w20,
        hello.display_id,
    )
    return struct.pack("<22I", *words)


# -- offer and ack ------------------------------------------------------------


def encode_offer(checksum: int) -> bytes:
    """The server's offer: magic, 1, block 0 / ``ffff``, the target checksum."""
    if not 0 <= checksum <= 0xFFFFFFFF:
        raise BootloaderError(f"checksum {checksum} is not a uint32")
    return struct.pack("<IIHHI", MAGIC, _KIND_OFFER, 0, _NO_BLOCK, checksum)


def encode_ack(block: int) -> bytes:
    """What the frame sends after each record (for tests and simulators)."""
    if not 1 <= block <= 0xFFFF:
        raise BootloaderError(f"block {block} out of range")
    return struct.pack("<IIHHI", MAGIC, _KIND_OFFER, block, _NO_BLOCK, 0)


@dataclass(frozen=True)
class Ack:
    block: int


def parse_ack(data: bytes) -> Ack:
    """Decode a 16-byte ack. Anything that is not exactly the observed shape
    (magic, 1, block, ``ffff``, 0) raises ``BootloaderError``."""
    if len(data) != ACK_SIZE:
        raise BootloaderError(f"an ack is {ACK_SIZE} bytes, got {len(data)}")
    magic, kind, block, marker, tail = struct.unpack("<IIHHI", data)
    if magic != MAGIC:
        raise BootloaderError(f"bad magic {magic:#010x}")
    if kind != _KIND_OFFER:
        raise BootloaderError(f"message kind {kind} is not an ack")
    if marker != _NO_BLOCK or tail != 0:
        raise BootloaderError(f"unexpected ack trailer {marker:#06x} {tail:#010x}")
    if block == 0:
        raise BootloaderError("ack for block 0")
    return Ack(block)


# -- firmware file ------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """One firmware record: where it sits in the file and what its header says."""

    index: int
    """1-based block number, the one the frame acks."""
    offset: int
    size: int
    """Whole record: header plus payload."""
    flags: int

    @property
    def end(self) -> int:
        return self.offset + self.size


def walk_firmware(data: bytes) -> list[Record]:
    """Split a firmware file into its records using the header's length field.

    A record is ``magic, 2, uint16 index, uint16 flags, uint16 length, ...``
    with a 24-byte header, followed by ``length`` payload bytes; the next record
    starts right after. The payload is opaque (it looks encrypted), so the magic
    is never searched for. The walk is checked, not trusted: it must start at
    byte 0, number the blocks 1..N with no gap, carry the flags words every
    published build for this hardware uses (8 or 9, and 13 on the last), and
    land exactly on the end of the file.

    Raises ``BootloaderError`` with the offset and the reason otherwise.
    """
    if not data:
        raise BootloaderError("the firmware file is empty")
    records: list[Record] = []
    offset = 0
    total = len(data)
    while offset < total:
        if total - offset < RECORD_HEADER_SIZE:
            raise BootloaderError(
                f"truncated: {total - offset} bytes left at offset {offset}, "
                f"a record header is {RECORD_HEADER_SIZE}"
            )
        magic, kind, index, flags, length = struct.unpack_from("<IIHHH", data, offset)
        if magic != MAGIC or kind != _KIND_RECORD:
            raise BootloaderError(f"no record header at offset {offset}")
        if index != len(records) + 1:
            raise BootloaderError(f"block {index} at offset {offset}, expected {len(records) + 1}")
        if length == 0:
            raise BootloaderError(f"block {index} has no payload")
        end = offset + RECORD_HEADER_SIZE + length
        if end > total:
            raise BootloaderError(
                f"truncated: block {index} needs {end - total} more bytes than the file has"
            )
        last = end == total
        want = FLAGS_LAST if last else FLAGS_MORE
        if flags != want and (last or flags != FLAGS_MORE_ALT):
            note = (
                " (last block)"
                if last
                else " (it is flagged as the last block but the file goes on)"
                if flags == FLAGS_LAST
                else ""
            )
            raise BootloaderError(
                f"block {index} has flags {flags:#06x}, expected {want:#06x}{note}"
            )
        records.append(Record(index, offset, end - offset, flags))
        offset = end
    return records


# -- the session ---------------------------------------------------------------


class State(enum.Enum):
    WAIT_HELLO = "wait_hello"
    SETTLING = "settling"  # offer sent, first record not yet
    WAIT_ACK = "wait_ack"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class SessionConfig:
    hello_timeout_s: float = 30.0
    """Longest to wait for the complete hello (it arrived 0.45 s after connect)."""
    settle_s: float = 1.3
    """Pause between the offer and the first record. The Suite's was 1.3 s while
    it downloaded the file; whether the frame needs any is not known, so it is
    kept."""
    ack_timeout_s: float = 30.0
    """Longest to wait for one ack. The slowest observed was 1.2 s, on one build;
    waiting costs little next to stopping a flash part way."""


class Event:
    """Base class of what the session reports."""


@dataclass(frozen=True)
class HelloReceived(Event):
    hello: Hello


@dataclass(frozen=True)
class OfferSent(Event):
    checksum: int


@dataclass(frozen=True)
class BlockAcked(Event):
    block: int
    total: int


@dataclass(frozen=True)
class Finished(Event):
    blocks: int


@dataclass(frozen=True)
class Failed(Event):
    reason: str
    blocks_acked: int
    """Blocks the frame had acknowledged; 0 means nothing was written."""
    sent_any_record: bool
    """Whether any firmware record went out (the flash may have begun)."""


class BootloaderSession:
    """One bootloader conversation, from the frame's hello to the last ack.

    ``feed_at(data, now)`` takes bytes from the frame and returns events;
    ``advance(now)`` runs the timers; ``drain()`` returns the bytes to send.
    ``now`` is any monotonic clock, supplied by the caller. ``feed_eof(now)``
    reports that the frame closed the connection.

    ``accept_hello`` may refuse a hello (wrong hardware, wrong device) by
    returning a reason; the session then fails having sent nothing at all.
    """

    def __init__(
        self,
        firmware: bytes,
        target_checksum: int,
        *,
        accept_hello: Callable[[Hello], str | None] | None = None,
        config: SessionConfig | None = None,
        now: float = 0.0,
    ) -> None:
        self.records = walk_firmware(firmware)
        encode_offer(target_checksum)  # range check
        self._firmware = firmware
        self.target_checksum = target_checksum
        self._accept_hello = accept_hello
        self.config = config or SessionConfig()
        self.state = State.WAIT_HELLO
        self.hello: Hello | None = None
        self.error = ""
        self.acked = 0
        self._sent = 0  # records written so far
        self._in = bytearray()
        self._out = bytearray()
        self._deadline: float | None = now + self.config.hello_timeout_s

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def finished(self) -> bool:
        return self.state in (State.DONE, State.FAILED)

    @property
    def sent_any_record(self) -> bool:
        """Whether a firmware record has been queued to go out."""
        return self._sent > 0

    def drain(self) -> bytes:
        data = bytes(self._out)
        self._out.clear()
        return data

    # -- inputs ------------------------------------------------------------

    def feed_at(self, data: bytes, now: float) -> list[Event]:
        if self.finished:
            if data and self.state is State.DONE:
                return self._fail("the frame sent data after the last block", now)
            return []
        self._in += data
        events: list[Event] = []
        while not self.finished:
            if self.state is State.WAIT_HELLO:
                if not self._take_hello(events, now):
                    break
            elif self.state in (State.WAIT_ACK, State.SETTLING):
                if not self._take_ack(events, now):
                    break
            else:  # pragma: no cover - finished states leave the loop
                break
        return events

    def feed_eof(self, now: float) -> list[Event]:
        """The frame closed the connection."""
        if self.finished:
            return []
        if self.state is State.WAIT_HELLO:
            return self._fail("the frame closed the connection before its hello", now)
        return self._fail(
            f"the frame closed the connection after block {self.acked} of {self.total}", now
        )

    def advance(self, now: float) -> list[Event]:
        if self.finished or self._deadline is None or now < self._deadline:
            return []
        if self.state is State.SETTLING:
            self._send_next(now)
            return []
        if self.state is State.WAIT_HELLO:
            return self._fail("no complete hello from the frame", now)
        return self._fail(
            f"no ack for block {self._sent} within {self.config.ack_timeout_s:g} s "
            f"({self.acked} of {self.total} acknowledged)",
            now,
        )

    # -- internals -----------------------------------------------------------

    def _fail(self, reason: str, now: float) -> list[Event]:
        self.state = State.FAILED
        self.error = reason
        self._deadline = None
        self._in.clear()
        self._out.clear()
        return [Failed(reason, self.acked, self._sent > 0)]

    def _take_hello(self, events: list[Event], now: float) -> bool:
        head = bytes(self._in[:8])
        # Wait for more while what is here could still be a hello.
        if head[: min(4, len(head))] != MAGIC_BYTES[: min(4, len(head))] or (
            len(head) >= 8 and not is_bootloader_hello(head)
        ):
            events.extend(self._fail("not a bootloader hello", now))
            return False
        if len(self._in) < HELLO_SIZE:
            return False
        raw = bytes(self._in[:HELLO_SIZE])
        del self._in[:HELLO_SIZE]
        try:
            hello = parse_hello(raw)
        except BootloaderError as exc:
            events.extend(self._fail(str(exc), now))
            return False
        self.hello = hello
        events.append(HelloReceived(hello))
        if self._accept_hello is not None:
            refusal = self._accept_hello(hello)
            if refusal:
                events.extend(self._fail(refusal, now))
                return False
        self._out += encode_offer(self.target_checksum)
        events.append(OfferSent(self.target_checksum))
        self.state = State.SETTLING
        self._deadline = now + self.config.settle_s
        return True

    def _send_next(self, now: float) -> None:
        record = self.records[self._sent]
        self._out += self._firmware[record.offset : record.end]
        self._sent += 1
        self.state = State.WAIT_ACK
        self._deadline = now + self.config.ack_timeout_s

    def _take_ack(self, events: list[Event], now: float) -> bool:
        if not self._in:
            return False
        if self.state is State.SETTLING:
            # The frame has been told nothing yet that it could answer.
            events.extend(self._fail("the frame sent data before the first block", now))
            return False
        if len(self._in) < ACK_SIZE:
            if self._in[: min(4, len(self._in))] != MAGIC_BYTES[: min(4, len(self._in))]:
                events.extend(self._fail("bad ack", now))
            return False
        raw = bytes(self._in[:ACK_SIZE])
        del self._in[:ACK_SIZE]
        try:
            ack = parse_ack(raw)
        except BootloaderError as exc:
            events.extend(self._fail(f"bad ack: {exc}", now))
            return False
        if ack.block != self._sent:
            events.extend(
                self._fail(f"ack for block {ack.block} while waiting for block {self._sent}", now)
            )
            return False
        self.acked = ack.block
        events.append(BlockAcked(ack.block, self.total))
        if self.acked == self.total:
            if self._in:
                events.extend(self._fail("the frame sent data after the last block", now))
                return False
            self.state = State.DONE
            self._deadline = None
            events.append(Finished(self.total))
            return False
        self._send_next(now)
        if self._in:
            # Stop and wait: the frame has not seen the record just sent.
            events.extend(self._fail("the frame sent data before the next block", now))
            return False
        return True
