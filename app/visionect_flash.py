"""Running one firmware flash: the job's state and the socket driver.

``app.visionect_bootloader`` is the sans-io conversation; this is the part that
owns a TCP connection for it. ``app.visionect`` creates a ``FlashJob`` when an
update is asked for and hands the bootloader's connection to ``run_session``.

A flash is never retried. Whatever goes wrong, the job records a plain message
and ends; an interrupted flash has never been observed, so nothing here assumes
the frame recovers by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.visionect_bootloader import (
    HELLO_SIZE,
    BlockAcked,
    BootloaderSession,
    Failed,
    Finished,
    Hello,
    HelloReceived,
    OfferSent,
    SessionConfig,
    State,
)
from app.visionect_firmware import Build

logger = logging.getLogger(__name__)

# From the reboot command to the bootloader's call (observed: about 12 s).
PENDING_TIMEOUT_S = 180.0
# A frame can stop reading its connection and only find out when it reconnects,
# so the reboot goes out again on each new connection, as the Suite does, this
# many times at most.
REBOOT_TRIES = 4
# How far past that a pending job counts as expired even if its timer never ran.
PENDING_GRACE_S = 60.0
# From the last ack to the frame's reconnect on the new firmware (about 10 s).
VERIFY_TIMEOUT_S = 300.0
# The Suite closed the socket 4.6 ms after the last ack; a little longer here.
CLOSE_GAP_S = 0.05
TICK_S = 0.05
# Bootloader versions (major, minor) a flash has been measured on.
MEASURED_BOOTLOADERS = {(6, 3)}
USB_HINT = (
    "If the frame doesn't come back after a power cycle, reconnect it with the USB cable "
    "and use Visionect's Configurator to flash it."
)

# The session's timeouts; tests shorten them.
SESSION_CONFIG = SessionConfig()

PENDING = "pending"
RUNNING = "running"
VERIFYING = "verifying"
DONE = "done"
FAILED = "failed"
ACTIVE = (PENDING, RUNNING, VERIFYING)


@dataclass
class FlashJob:
    """One requested flash of one frame."""

    uuid: str
    device_id: str
    build: Build
    data: bytes = field(repr=False)
    peer_ip: str
    from_version: str
    from_checksum: int | None
    requested_at: float = field(default_factory=time.time)
    reboot_at: float = 0.0  # when the reboot last went out
    reboots: int = 0
    phase: str = PENDING
    blocks_done: int = 0
    blocks_total: int = 0
    message: str = ""
    finished_at: float = 0.0
    wrote_records: bool = False  # an offer or a record went out: the frame may be mid-flash
    timer: Any = field(default=None, repr=False)  # the loop's TimerHandle for the phase's timeout
    version: int = 0  # bumped on every change; the page polls it

    @property
    def active(self) -> bool:
        return self.phase in ACTIVE

    def touch(self) -> None:
        self.version += 1

    def view(self) -> dict[str, Any]:
        pct = int(self.blocks_done * 100 / self.blocks_total) if self.blocks_total else 0
        return {
            "uuid": self.uuid,
            "phase": self.phase,
            "active": self.active,
            "target": self.build.version_text,
            "target_checksum": self.build.checksum_hex,
            "from": self.from_version,
            "blocks_done": self.blocks_done,
            "blocks_total": self.blocks_total,
            "percent": pct,
            "message": self.message,
            "ok": self.phase == DONE,
            "requested_at": self.requested_at,
            "finished_at": self.finished_at or None,
            "version": self.version,
        }


def hello_refusal(job: FlashJob, hello: Hello) -> str | None:
    """Why this hello must not be offered ``job``'s build, or None. The frame
    must be the one the job is for, on exactly the hardware revision, hardware
    name id and display the build is for."""
    b = job.build
    if hello.device_id.hex() != job.uuid.replace("-", ""):
        return f"the bootloader that called is not this frame ({hello.uuid})"
    if hello.bootloader[:2] not in MEASURED_BOOTLOADERS:
        return (
            f"the frame's bootloader is {'.'.join(map(str, hello.bootloader))}, and a flash has "
            "only been measured on 6.3"
        )
    if hello.hardware != b.hardware:
        return (
            f"the frame's hardware is {'.'.join(map(str, hello.hardware))} but the chosen "
            f"firmware is for {'.'.join(map(str, b.hardware))}"
        )
    if hello.hardware_name_id != b.hardware_name_id:
        return (
            f"the frame's hardware name id is {hello.hardware_name_id} but the chosen "
            f"firmware is for {b.hardware_name_id}"
        )
    if hello.display_id != b.display_id:
        return (
            f"the frame's display id is {hello.display_id} but the chosen firmware is for "
            f"{b.display_id}"
        )
    return None


async def read_hello(
    reader: Any, head: bytes, *, timeout: float, now: Callable[[], float] = time.monotonic
) -> bytes:
    """Read until ``HELLO_SIZE`` bytes have arrived or the stream ends / times
    out; returns what was read (``head`` included)."""
    data = bytearray(head)
    deadline = now() + timeout
    while len(data) < HELLO_SIZE:
        left = deadline - now()
        if left <= 0:
            break
        try:
            chunk = await asyncio.wait_for(reader.read(HELLO_SIZE - len(data)), left)
        except (TimeoutError, ConnectionError, OSError):
            break
        if not chunk:
            break
        data += chunk
    return bytes(data)


async def run_session(
    reader: Any,
    writer: Any,
    first: bytes,
    job: FlashJob,
    *,
    on_change: Callable[[str], None],
    abort: Callable[[Any], None],
    config: SessionConfig | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Drive the bootloader conversation on a connection already known to open
    with a hello for ``job`` (``first`` is everything read so far).

    Updates ``job`` in place, calls ``on_change(log_line)`` for every step
    worth logging, and ends the connection: closed on success, aborted
    otherwise. Never raises for a protocol or network problem.
    """
    session = BootloaderSession(
        job.data,
        job.build.checksum,
        accept_hello=lambda hello: hello_refusal(job, hello),
        config=config or SESSION_CONFIG,
        now=clock(),
    )
    job.blocks_total = session.total

    def handle(events: list[Any]) -> None:
        for event in events:
            if isinstance(event, HelloReceived):
                on_change(f"bootloader hello: {event.hello.describe()}")
            elif isinstance(event, OfferSent):
                digest = hashlib.sha256(job.data).hexdigest()
                on_change(
                    f"offered firmware {job.build.version_text} ({event.checksum:08x}), "
                    f"file sha256 {digest}"
                )
                # What a bootloader does after an offer has only been seen when
                # the flash went on, so from here it may need a power cycle.
                job.wrote_records = True
                job.phase = RUNNING
                job.touch()
            elif isinstance(event, BlockAcked):
                job.blocks_done = event.block
                job.wrote_records = True
                job.touch()
                if event.block == 1 or event.block % 20 == 0 or event.block == event.total:
                    on_change(f"block {event.block} of {event.total} acknowledged")
            elif isinstance(event, Finished):
                on_change(f"all {event.blocks} blocks acknowledged")
            elif isinstance(event, Failed):
                job.wrote_records = job.wrote_records or event.sent_any_record

    async def flush() -> bool:
        data = session.drain()
        if not data:
            return True
        job.wrote_records = job.wrote_records or session.sent_any_record
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionError, OSError):
            return False
        return True

    try:
        handle(session.feed_at(first, clock()))
        while not session.finished:
            if not await flush():
                handle(session.feed_eof(clock()))
                break
            if session.finished:
                break
            try:
                data: bytes | None = await asyncio.wait_for(reader.read(65536), TICK_S)
            except TimeoutError:
                data = None
            except (ConnectionError, OSError):
                data = b""
            now = clock()
            if data == b"":
                handle(session.feed_eof(now))
                break
            if data:
                handle(session.feed_at(data, now))
            handle(session.advance(now))
        await flush()
    except asyncio.CancelledError:
        # Tesserae is shutting down mid-flash.
        job.phase = FAILED
        job.finished_at = time.time()
        job.message = failure_message(
            "Tesserae stopped while the flash was running", job.wrote_records
        )
        job.touch()
        abort(writer)
        raise

    if session.state is State.DONE:
        job.phase = VERIFYING
        job.touch()
        await asyncio.sleep(CLOSE_GAP_S)
        # The Suite ended the connection with a reset a few ms after the last
        # ack, and so do we. abort() alone sends a FIN; a zero linger makes the
        # close a reset.
        send_reset_on_close(writer)
        abort(writer)
        return
    job.phase = FAILED
    job.finished_at = time.time()
    job.touch()
    job.message = failure_message(session.error, job.wrote_records)
    with contextlib.suppress(Exception):
        abort(writer)


def send_reset_on_close(writer: Any) -> None:
    """Make closing this connection send a TCP reset rather than a FIN."""
    sock = writer.get_extra_info("socket")
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))


def failure_message(reason: str, wrote: bool) -> str:
    if not wrote:
        return f"The frame was not flashed: {reason}."
    return (
        f"The flash stopped part way: {reason}. The frame may be stuck in its bootloader: "
        f"power-cycle it first. {USB_HINT}"
    )
