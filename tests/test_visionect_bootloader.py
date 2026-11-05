"""app.visionect_bootloader: the bootloader's update conversation, sans-io.

The hello, offer and ack bytes are real, from a capture of Visionect's Software
Suite flashing a 32" Place & Play. Firmware records are synthetic (a real
record's header with made-up payloads); no firmware content is used.
"""

from __future__ import annotations

import itertools
import struct

import pytest

from app import visionect_bootloader as bl

# The frame's 88-byte hello, as captured.
REAL_HELLO = bytes.fromhex(
    "020000f0 00000000 d61eb92c ffffffff 00000a00 36002000 12473830 39333036"
    "00000000 07000000 04000000 37110000 06000000 03000000 f60e0000 08000000"
    "01000000 01000000 00000000 00000000 1e000000 2c0105c2"
)
# The server's offer of a529a106, as captured.
REAL_OFFER = bytes.fromhex("020000f0 01000000 0000ffff 06a129a5")
# The first record's header (the 24th..21st bytes, ciphertext, are left out).
REAL_RECORD_HEADER = bytes.fromhex("020000f0 02000000 0100 0900 5006 e407 0b000000")
UUID = "36002000-1247-3830-3933-303600000000"
TARGET = 0xA529A106


def make_firmware(payloads: list[bytes]) -> bytes:
    out = bytearray()
    for i, payload in enumerate(payloads, start=1):
        flags = bl.FLAGS_LAST if i == len(payloads) else bl.FLAGS_MORE
        out += struct.pack("<IIHHHHI", bl.MAGIC, 2, i, flags, len(payload), 0x07E4, 0)
        out += b"\xaa\xbb\xcc\xdd" + payload
    return bytes(out)


def firmware(n: int = 3) -> bytes:
    # The first payload contains what looks like a record header: the walk must
    # use the length fields, not look for the magic.
    trap = bl.MAGIC_BYTES + b"\x02\x00\x00\x00" + b"\x09\x00" * 4
    payloads = [trap + bytes(range(16)) * 2] + [bytes([i]) * (32 + 16 * i) for i in range(1, n)]
    return make_firmware(payloads)


# -- hello -----------------------------------------------------------------------


def test_real_hello_fields() -> None:
    hello = bl.parse_hello(REAL_HELLO)
    assert hello.current_checksum == 0x2CB91ED6
    assert hello.firmware == (7, 4, 4407)
    assert hello.bootloader == (6, 3, 3830)
    assert hello.hardware_name_id == 8
    assert hello.hardware == (1, 1, 0)
    assert hello.display_id == 0xC205012C == 3255107884
    assert hello.unknown == (0xFFFFFFFF, 0x000A0000, 0, 0, 0x1E)
    assert hello.uuid == UUID
    assert "hardware 1.1.0" in hello.describe()


def test_hello_round_trips() -> None:
    assert bl.encode_hello(bl.parse_hello(REAL_HELLO)) == REAL_HELLO


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b[:-1],
        lambda b: b + b"\x00",
        lambda b: b"\x03\x00\x00\xf0" + b[4:],
        lambda b: b[:4] + b"\x01\x00\x00\x00" + b[8:],
    ],
)
def test_hello_rejects_anything_unexpected(mutate) -> None:
    with pytest.raises(bl.BootloaderError):
        bl.parse_hello(mutate(REAL_HELLO))


def test_is_bootloader_hello() -> None:
    assert bl.is_bootloader_hello(REAL_HELLO[:4])
    assert bl.is_bootloader_hello(REAL_HELLO[:8])
    assert bl.is_bootloader_hello(REAL_HELLO)
    assert not bl.is_bootloader_hello(b"")
    assert not bl.is_bootloader_hello(REAL_HELLO[:3])
    # A normal pyvisionect frame opens with protocol version 3.
    assert not bl.is_bootloader_hello(b"\x03\x00\x00\x00" + bytes(12))
    # Right magic, but not kind 0.
    assert not bl.is_bootloader_hello(REAL_OFFER[:8])
    # A TLS ClientHello.
    assert not bl.is_bootloader_hello(b"\x16\x03\x01\x02\x00\x01\x00\x01")


# -- offer and ack -----------------------------------------------------------------


def test_offer_matches_the_capture() -> None:
    assert bl.encode_offer(TARGET) == REAL_OFFER
    with pytest.raises(bl.BootloaderError):
        bl.encode_offer(1 << 32)


def test_ack_matches_the_capture_and_rejects_everything_else() -> None:
    real = bytes.fromhex("020000f0 01000000 b800 ffff 00000000")  # block 184
    assert bl.parse_ack(real) == bl.Ack(184)
    assert bl.encode_ack(184) == real
    for bad in (
        real[:-1],
        real + b"\x00",
        b"\x03\x00\x00\xf0" + real[4:],  # magic
        real[:4] + b"\x02\x00\x00\x00" + real[8:],  # kind
        real[:10] + b"\xfe\xff" + real[12:],  # marker
        real[:12] + b"\x01\x00\x00\x00",  # trailer
        bl.encode_offer(0),  # block 0
    ):
        with pytest.raises(bl.BootloaderError):
            bl.parse_ack(bad)


# -- the file -----------------------------------------------------------------------


def test_real_record_header_parses_as_a_record_header() -> None:
    magic, kind, index, flags, length, other = struct.unpack("<IIHHHH", REAL_RECORD_HEADER[:16])
    assert (magic, kind, index, flags, length, other) == (bl.MAGIC, 2, 1, 9, 1616, 2020)
    assert bl.RECORD_HEADER_SIZE + length == 1640  # the size of the first record on the wire


def test_walk_follows_the_length_fields_and_ends_at_eof() -> None:
    data = firmware(4)
    records = bl.walk_firmware(data)
    assert [r.index for r in records] == [1, 2, 3, 4]
    assert records[0].offset == 0
    assert all(a.end == b.offset for a, b in itertools.pairwise(records))
    assert records[-1].end == len(data)
    assert [r.flags for r in records] == [9, 9, 9, 13]
    # The record header look-alike inside the first payload did not split it.
    assert records[0].size == 24 + 16 + 32


def test_walk_single_record() -> None:
    assert len(bl.walk_firmware(make_firmware([bytes(16)]))) == 1


@pytest.mark.parametrize(
    ("name", "mutate", "message"),
    [
        ("empty", lambda d: b"", "empty"),
        ("short header", lambda d: d[:10], "truncated"),
        ("short payload", lambda d: d[:-1], "truncated"),
        ("trailing bytes", lambda d: d + b"\x00" * 30, "goes on"),
        ("trailing short", lambda d: d + b"\x00" * 5, "goes on"),
        ("wrong magic", lambda d: b"\x00" + d[1:], "no record header"),
        ("wrong kind", lambda d: d[:4] + b"\x01" + d[5:], "no record header"),
        ("bad index", lambda d: d[:8] + b"\x02" + d[9:], "expected 1"),
        ("bad flags", lambda d: d[:10] + b"\x0b" + d[11:], "flags"),
        ("zero length", lambda d: d[:12] + b"\x00\x00" + d[14:], "no payload"),
    ],
)
def test_walk_refuses_what_does_not_walk_cleanly(name, mutate, message) -> None:
    with pytest.raises(bl.BootloaderError, match=message):
        bl.walk_firmware(mutate(firmware(3)))


def test_walk_refuses_a_gap_and_a_missing_last_flag() -> None:
    data = make_firmware([bytes(16)] * 3)  # three records of 24 + 16 bytes
    size = len(data) // 3
    assert size == 40
    with pytest.raises(bl.BootloaderError, match="block 3"):
        bl.walk_firmware(data[:size] + data[2 * size :])
    not_last = bytearray(data)
    not_last[2 * size + 8 + 2 : 2 * size + 8 + 4] = struct.pack("<H", bl.FLAGS_MORE)
    with pytest.raises(bl.BootloaderError, match="flags"):
        bl.walk_firmware(bytes(not_last))


# -- the session -----------------------------------------------------------------------


class Frame:
    """Plays the frame against a session with a manual clock."""

    def __init__(self, data: bytes, *, accept=None, config=None) -> None:
        self.t = 0.0
        self.data = data
        self.session = bl.BootloaderSession(
            data,
            TARGET,
            accept_hello=accept,
            config=config or bl.SessionConfig(settle_s=1.0, ack_timeout_s=5.0),
            now=self.t,
        )
        self.sent = b""
        self.events: list[bl.Event] = []

    def feed(self, data: bytes) -> list[bl.Event]:
        ev = self.session.feed_at(data, self.t)
        self.events += ev
        self.sent += self.session.drain()
        return ev

    def tick(self, seconds: float = 0.0) -> list[bl.Event]:
        self.t += seconds
        ev = self.session.advance(self.t)
        self.events += ev
        self.sent += self.session.drain()
        return ev


def test_full_flash_is_byte_for_byte_the_file() -> None:
    data = firmware(5)
    f = Frame(data)
    f.feed(REAL_HELLO)
    assert f.sent == REAL_OFFER  # offer straight away, nothing else yet
    assert f.session.state is bl.State.SETTLING
    f.tick(0.5)
    assert f.sent == REAL_OFFER  # still settling
    f.tick(0.6)
    records = bl.walk_firmware(data)
    assert f.sent == REAL_OFFER + data[: records[0].end]
    for i in range(1, len(records) + 1):
        f.feed(bl.encode_ack(i))
        assert f.session.acked == i
    assert f.session.state is bl.State.DONE
    assert f.sent == REAL_OFFER + data  # exactly the file, no changes
    acked = [e for e in f.events if isinstance(e, bl.BlockAcked)]
    assert [(e.block, e.total) for e in acked] == [(i, 5) for i in range(1, 6)]
    assert isinstance(f.events[-1], bl.Finished)
    assert f.session.finished and not f.session.error


def test_one_record_at_a_time() -> None:
    data = firmware(3)
    f = Frame(data)
    f.feed(REAL_HELLO)
    f.tick(1.1)
    first = bl.walk_firmware(data)[0]
    assert f.sent == REAL_OFFER + data[: first.end]
    f.tick(3)  # nothing more until the frame acks
    assert f.sent == REAL_OFFER + data[: first.end]


def test_hello_may_arrive_in_pieces() -> None:
    f = Frame(firmware(2))
    for chunk in (REAL_HELLO[:3], REAL_HELLO[3:40], REAL_HELLO[40:87]):
        f.feed(chunk)
        assert f.sent == b""
    f.feed(REAL_HELLO[87:])
    assert f.sent == REAL_OFFER


def test_acks_may_arrive_in_pieces_and_together() -> None:
    data = firmware(3)
    f = Frame(data)
    f.feed(REAL_HELLO)
    f.tick(1.1)
    ack = bl.encode_ack(1)
    f.feed(ack[:5])
    assert f.session.acked == 0
    f.feed(ack[5:])
    assert f.session.acked == 1


def test_a_hello_for_the_wrong_hardware_is_refused_and_nothing_is_sent() -> None:
    def accept(hello: bl.Hello) -> str | None:
        return None if hello.hardware == (2, 0, 0) else "wrong hardware"

    f = Frame(firmware(2), accept=accept)
    events = f.feed(REAL_HELLO)
    assert f.sent == b""
    assert f.session.state is bl.State.FAILED
    assert f.session.error == "wrong hardware"
    failed = events[-1]
    assert isinstance(failed, bl.Failed) and failed.sent_any_record is False
    assert failed.blocks_acked == 0


@pytest.mark.parametrize(
    "junk",
    [b"\x03\x00\x00\x00" + bytes(12), b"hello there", REAL_OFFER + bytes(80)],
)
def test_anything_else_in_place_of_the_hello_fails(junk) -> None:
    f = Frame(firmware(2))
    f.feed(junk)
    assert f.session.state is bl.State.FAILED
    assert f.sent == b""


def test_a_truncated_hello_times_out() -> None:
    f = Frame(firmware(2))
    f.feed(REAL_HELLO[:50])
    f.tick(29)
    assert not f.session.finished
    f.tick(2)
    assert f.session.state is bl.State.FAILED
    assert "hello" in f.session.error


def test_a_missing_ack_times_out_and_is_not_retried() -> None:
    f = Frame(firmware(3))
    f.feed(REAL_HELLO)
    f.tick(1.1)
    sent = f.sent
    f.tick(4.9)
    assert not f.session.finished and f.sent == sent
    events = f.tick(0.2)
    assert f.session.state is bl.State.FAILED
    assert "no ack for block 1" in f.session.error
    assert isinstance(events[0], bl.Failed) and events[0].sent_any_record
    f.tick(60)
    assert f.sent == sent  # nothing resent


def test_an_ack_for_the_wrong_block_fails() -> None:
    f = Frame(firmware(4))
    f.feed(REAL_HELLO)
    f.tick(1.1)
    f.feed(bl.encode_ack(1))
    f.feed(bl.encode_ack(3))  # block 2 was sent
    assert f.session.state is bl.State.FAILED
    assert "block 3" in f.session.error and "block 2" in f.session.error


def test_a_repeated_ack_fails() -> None:
    f = Frame(firmware(4))
    f.feed(REAL_HELLO)
    f.tick(1.1)
    f.feed(bl.encode_ack(1))
    f.feed(bl.encode_ack(1))
    assert f.session.state is bl.State.FAILED


def test_a_malformed_ack_fails() -> None:
    f = Frame(firmware(3))
    f.feed(REAL_HELLO)
    f.tick(1.1)
    f.feed(b"\x00" * 16)
    assert f.session.state is bl.State.FAILED
    assert "bad ack" in f.session.error


def test_data_before_the_first_record_fails() -> None:
    f = Frame(firmware(3))
    f.feed(REAL_HELLO)
    f.feed(bl.encode_ack(1))
    assert f.session.state is bl.State.FAILED
    assert f.sent == REAL_OFFER  # no record went out


def test_an_ack_for_a_block_not_yet_sent_fails() -> None:
    f = Frame(firmware(3))
    f.feed(REAL_HELLO)
    f.tick(1.1)
    f.feed(bl.encode_ack(1) + bl.encode_ack(2))  # 2 is acked before it was sent
    assert f.session.state is bl.State.FAILED


def test_disconnect_mid_transfer_fails_with_progress() -> None:
    f = Frame(firmware(4))
    f.feed(REAL_HELLO)
    f.tick(1.1)
    f.feed(bl.encode_ack(1))
    events = f.session.feed_eof(f.t)
    assert f.session.state is bl.State.FAILED
    assert "after block 1 of 4" in f.session.error
    assert isinstance(events[0], bl.Failed)
    assert events[0].blocks_acked == 1 and events[0].sent_any_record


def test_disconnect_before_the_hello_fails() -> None:
    f = Frame(firmware(2))
    f.session.feed_eof(0)
    assert f.session.state is bl.State.FAILED
    assert "before its hello" in f.session.error


def test_data_after_the_last_ack_fails() -> None:
    f = Frame(firmware(1))
    f.feed(REAL_HELLO)
    f.tick(1.1)
    f.feed(bl.encode_ack(1))
    assert f.session.state is bl.State.DONE
    f.feed(b"\x00")
    assert f.session.state is bl.State.FAILED


def test_a_session_refuses_a_file_that_does_not_walk() -> None:
    with pytest.raises(bl.BootloaderError):
        bl.BootloaderSession(firmware(2)[:-3], TARGET)


def _with_flags(data: bytes, block: int, flags: int) -> bytes:
    """``data`` with one record's flags word replaced."""
    rec = bl.walk_firmware(data)[block - 1]
    return data[: rec.offset + 10] + struct.pack("<H", flags) + data[rec.offset + 12 :]


def test_walk_accepts_the_mid_file_flag_published_builds_use() -> None:
    # 7.4.4407's block 130 and one block of most other builds carry 8, not 9.
    data = _with_flags(firmware(3), 2, bl.FLAGS_MORE_ALT)
    assert [r.flags for r in bl.walk_firmware(data)] == [9, 8, 13]


def test_walk_still_needs_the_last_flag_on_the_last_record() -> None:
    data = firmware(3)
    last = len(bl.walk_firmware(data))
    with pytest.raises(bl.BootloaderError, match="flags"):
        bl.walk_firmware(_with_flags(data, last, bl.FLAGS_MORE_ALT))
