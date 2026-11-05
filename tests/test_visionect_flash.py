"""Firmware flashing of a Visionect sign: the service flow against a simulated
bootloader, the listener hook, the route and the device page.

The simulated frame is a socket on localhost: a normal sign connection (status
packets), then, after the reboot command, a second connection that speaks the
bootloader protocol (hello, acks). Firmware is a short synthetic file; the
repository is a cache seeded on disk, so nothing touches the network.
"""

from __future__ import annotations

import dataclasses
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app import visionect, visionect_flash
from app import visionect_bootloader as bl
from app import visionect_firmware as fwrepo
from app.main import create_app
from tests.test_visionect import STATUS_TAGS, UUID, FakeSign, Harness, _wait
from tests.test_visionect_bootloader import REAL_HELLO, make_firmware
from tests.test_visionect_firmware_repo import LIST, entry

TARGET_HEX = "a529a106"  # 7.3.4358, older than what the frame runs: a downgrade
NEWER_HEX = "7ffffff0"
FIRMWARE = make_firmware([bytes([i]) * 64 for i in range(1, 6)])
CONNECT_REBOOT = 1


def tags(
    *,
    fw: tuple[int, int, int] = (7, 4, 4407),
    checksum: int = 0x2CB91ED6,
    battery: int = 80,
    reason: int = 3,
    hardware: tuple[int, int, int] = (1, 1, 0),
) -> list[tuple[int, int]]:
    base = [(t, v) for t, v in STATUS_TAGS if t not in (0, 10, 16, 17, 18)]
    return [
        *base,
        (0, reason),
        (3, checksum),
        (10, battery),
        (16, fw[0]),
        (17, fw[1]),
        (18, fw[2]),
        (23, hardware[0]),
        (24, hardware[1]),
        (25, hardware[2]),
    ]


def hello_bytes(**changes: Any) -> bytes:
    hello = bl.parse_hello(REAL_HELLO)
    return bl.encode_hello(bl.Hello(**{**hello.__dict__, **changes}))


def recv_exact(sock: socket.socket, n: int, timeout: float = 5.0) -> bytes:
    sock.settimeout(timeout)
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError(f"closed after {len(data)} of {n} bytes")
        data += chunk
    return data


class Bootloader:
    """The frame's bootloader dialing the listener."""

    def __init__(self, port: int, hello: bytes | None = None) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.received = b""
        if hello is not None:
            self.sock.sendall(hello)

    def offer(self) -> bytes:
        return recv_exact(self.sock, bl.OFFER_SIZE)

    def record(self) -> bytes:
        head = recv_exact(self.sock, bl.RECORD_HEADER_SIZE)
        length = int.from_bytes(head[12:14], "little")
        return head + recv_exact(self.sock, length)

    def ack(self, block: int) -> None:
        self.sock.sendall(bl.encode_ack(block))

    def closed(self, timeout: float = 3.0) -> bool:
        self.sock.settimeout(0.2)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if not self.sock.recv(65536):
                    return True
            except TimeoutError:
                continue
            except OSError:
                return True
        return False

    def close(self) -> None:
        self.sock.close()


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(visionect, "PARAM_WAIT_S", 0.2)
    # FakeSign doesn't ack pictures; only the test of the wait itself waits.
    monkeypatch.setattr(visionect, "FLASH_REBOOT_WAIT_S", 0.0)
    monkeypatch.setattr(
        visionect_flash,
        "SESSION_CONFIG",
        bl.SessionConfig(hello_timeout_s=5.0, settle_s=0.05, ack_timeout_s=1.0),
    )


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    harness = Harness(tmp_path, uuid=UUID)
    repo = fwrepo.FirmwareRepository(tmp_path / "fw", base_url="http://127.0.0.1:9", timeout=0.5)
    # Seed the cache: the list, and the one file (nothing is downloaded).
    repo._loaded = True
    repo._builds = fwrepo.parse_list(LIST)
    repo._checked_at = time.time()
    build = next(b for b in repo._builds if b.checksum_hex == TARGET_HEX)
    repo._write_atomic(repo._file_path(build), FIRMWARE)
    harness.svc.firmware_repo = repo
    assert harness.svc.start()
    try:
        yield harness
    finally:
        harness.svc.stop()


def connect(h: Harness, **kw: Any) -> FakeSign:
    sign = FakeSign(h.svc.port)
    sign.status(tags(**kw))
    assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("connected"))
    assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("identity", {}).get("hardware"))
    return sign


def job(h: Harness) -> visionect_flash.FlashJob:
    j = h.svc.flash_job("lobby")
    assert j is not None
    return j


def start_flash(h: Harness, sign: FakeSign, checksum: str = TARGET_HEX) -> None:
    assert h.svc.request_firmware_update("lobby", checksum) is None
    assert sign.wait_for(
        lambda s: any(int.from_bytes(f.payload[:4], "little") == 1 for f in s.commands())
    )
    sign.close()  # the frame reboots


def frame_log(h: Harness) -> str:
    return "\n".join(text for _t, _w, text in (h.svc.sign_view(UUID) or {})["exchanges"])


# -- what the page knows -------------------------------------------------------------


def test_identity_and_versions_come_from_the_normal_status(h: Harness) -> None:
    sign = connect(h)
    try:
        ident = h.svc.sign_view(UUID)["identity"]  # type: ignore[index]
        assert ident == {
            "firmware": (7, 4, 4407),
            "hardware": (1, 1, 0),
            "hw_name_id": 8,
            "display_id": 0xC205012C,
            "checksum": 0x2CB91ED6,
        }
        view = h.svc.firmware_view("lobby")
        assert view is not None
        assert view["installed"] == "7.4.4407" and view["latest"] == "7.4.4407"
        assert view["update_available"] is False
        assert [
            (b["version"], b["installed"], b["newest"], b["downgrade"]) for b in view["builds"]
        ] == [
            ("7.4.4407", True, True, False),
            ("7.3.4358", False, False, True),
            ("6.16.4144", False, False, True),
        ]
        assert view["blocked"] == "" and view["list_known"] is True
    finally:
        sign.close()


def test_an_update_is_noticed_when_a_newer_build_exists(h: Harness) -> None:
    repo = h.svc.firmware_repo
    assert repo is not None and repo._builds is not None
    repo._builds = [
        *repo._builds,
        *fwrepo.parse_list({"Firmwares": {"1.1.0": [entry("7ffffff0", "7.5.1")]}}),
    ]
    sign = connect(h)
    try:
        view = h.svc.firmware_view("lobby")
        assert view and view["latest"] == "7.5.1" and view["update_available"] is True
        assert view["builds"][0]["newest"] and not view["builds"][0]["downgrade"]
    finally:
        sign.close()


def test_a_frame_that_reports_no_hardware_gets_no_builds(h: Harness) -> None:
    sign = FakeSign(h.svc.port)
    sign.status([(t, v) for t, v in tags() if t not in (23, 24, 25)])
    try:
        assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("connected"))
        view = h.svc.firmware_view("lobby")
        assert view and view["builds"] == [] and view["list_known"] is False
        assert "isn't available" in (h.svc.request_firmware_update("lobby", TARGET_HEX) or "")
    finally:
        sign.close()


def test_without_a_repository_the_view_still_shows_what_is_installed(tmp_path: Path) -> None:
    harness = Harness(tmp_path, uuid=UUID)
    harness.svc.firmware_repo = fwrepo.FirmwareRepository(
        tmp_path / "fw", base_url="http://127.0.0.1:9", timeout=0.3
    )
    assert harness.svc.start()
    sign = FakeSign(harness.svc.port)
    try:
        sign.status(tags())
        assert _wait(lambda: (harness.svc.sign_view(UUID) or {}).get("connected"))
        view = harness.svc.firmware_view("lobby")
        assert view and view["installed"] == "7.4.4407"
        assert view["builds"] == [] and view["latest"] == "" and view["list_known"] is False
        assert harness.svc.request_firmware_update("lobby", TARGET_HEX)  # a plain message
        assert sign.commands() == []  # nothing was sent
    finally:
        sign.close()
        harness.svc.stop()


# -- refusals, all before anything is sent to the frame ---------------------------------


def test_refused_requests_never_reboot_the_frame(h: Harness) -> None:
    sign = connect(h, battery=29)
    try:
        assert "29%" in (h.svc.request_firmware_update("lobby", TARGET_HEX) or "")
        assert h.svc.request_firmware_update("lobby", "zz")
        sign.status(tags(battery=30))
        assert _wait(lambda: (h.svc.sign_view(UUID) or {})["status"].get("battery_pct") == 30)
        assert "Unknown firmware build" in (h.svc.request_firmware_update("lobby", "xyz") or "")
        # Another display's build, a build that doesn't exist, and the installed one.
        assert "isn't for this frame" in (h.svc.request_firmware_update("lobby", "11111111") or "")
        assert "isn't for this frame" in (h.svc.request_firmware_update("lobby", "deadbeef") or "")
        assert "isn't for this frame" in (h.svc.request_firmware_update("lobby", "6fdac4bf") or "")
        assert "already runs" in (h.svc.request_firmware_update("lobby", "2cb91ed6") or "")
        sign.wait_for(lambda s: bool(s.commands()), timeout=0.5)
        assert sign.commands() == []
        assert h.svc.flash_job("lobby") is None
    finally:
        sign.close()


def test_not_connected_and_unknown_battery_are_refused(h: Harness) -> None:
    assert "isn't connected" in (h.svc.request_firmware_update("lobby", TARGET_HEX) or "")
    sign = FakeSign(h.svc.port)
    sign.status([(t, v) for t, v in tags() if t != 10])
    try:
        assert _wait(lambda: (h.svc.sign_view(UUID) or {}).get("connected"))
        assert "battery" in (h.svc.request_firmware_update("lobby", TARGET_HEX) or "")
        view = h.svc.firmware_view("lobby")
        assert view and "battery" in view["blocked"]
    finally:
        sign.close()


def test_a_second_flash_is_refused_while_one_is_running(h: Harness) -> None:
    sign = connect(h)
    try:
        assert h.svc.request_firmware_update("lobby", TARGET_HEX) is None
        assert "already running" in (h.svc.request_firmware_update("lobby", TARGET_HEX) or "")
        assert sign.wait_for(lambda s: len(s.commands()) == 1)
        assert len(sign.commands()) == 1  # one reboot only
    finally:
        sign.close()


# -- the flash ---------------------------------------------------------------------------


def test_a_flash_runs_from_request_to_confirmed_version(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    assert job(h).phase == visionect_flash.PENDING
    assert h.svc.flash_job("lobby") is not None
    # While pending, a picture isn't sent (and nothing else is either).
    h.render("a")
    h.push()

    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        assert boot.offer() == bl.encode_offer(0xA529A106)
        records = bl.walk_firmware(FIRMWARE)
        for rec in records:
            assert boot.record() == FIRMWARE[rec.offset : rec.end]  # byte for byte
            assert job(h).phase == visionect_flash.RUNNING
            boot.ack(rec.index)
        assert boot.closed()
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.VERIFYING)
    j = job(h)
    assert (j.blocks_done, j.blocks_total) == (5, 5) and j.view()["percent"] == 100

    # No picture while the frame restarts, either.
    after = FakeSign(h.svc.port)
    try:
        after.status(tags(fw=(7, 3, 4358), checksum=0xA529A106, reason=CONNECT_REBOOT))
        assert _wait(lambda: job(h).phase == visionect_flash.DONE)
        assert "7.3.4358" in job(h).message and not job(h).active
        # Pictures resume once the version is confirmed.
        assert after.wait_for(lambda s: len(s.images()) == 1)
        view = h.svc.firmware_view("lobby")
        assert view and view["installed"] == "7.3.4358" and view["flash"]["ok"] is True  # type: ignore[index]
    finally:
        after.close()
    log = frame_log(h)
    for text in ("reboot sent", "bootloader hello", "offered firmware 7.3.4358", "block 1 of 5"):
        assert text in log
    assert "no picture" not in log


def test_no_picture_is_sent_while_a_flash_is_pending(h: Harness) -> None:
    sign = connect(h)
    try:
        assert h.svc.request_firmware_update("lobby", TARGET_HEX) is None
        assert sign.wait_for(lambda s: bool(s.commands()))
        h.render("a")
        h.push()
        sign.wait_for(lambda s: bool(s.images()), timeout=1.5)
        assert sign.images() == []
    finally:
        sign.close()


def test_a_reconnect_on_other_firmware_is_reported_as_a_mismatch(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        boot.offer()
        for rec in bl.walk_firmware(FIRMWARE):
            boot.record()
            boot.ack(rec.index)
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.VERIFYING)
    after = FakeSign(h.svc.port)
    try:
        after.status(tags(reason=CONNECT_REBOOT))  # still 7.4.4407
        assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
        assert "7.4.4407" in job(h).message and "7.3.4358" in job(h).message
    finally:
        after.close()


def _reboots(sign: FakeSign) -> int:
    return sum(int.from_bytes(f.payload[:4], "little") == 1 for f in sign.commands())


def test_a_frame_that_reconnects_without_restarting_gets_the_reboot_again(h: Harness) -> None:
    # A frame can stop reading a connection and only notice when it reconnects,
    # so the first reboot never reached it.
    first = connect(h)
    start_flash(h, first)
    tries = visionect_flash.REBOOT_TRIES
    for n in range(2, tries + 2):
        again = FakeSign(h.svc.port)
        try:
            again.status(tags())
            got = again.wait_for(lambda s: _reboots(s) == 1, timeout=1.5)
            assert job(h).phase == visionect_flash.PENDING
            if n <= tries:
                assert got, n
                assert job(h).reboots == n
            else:
                assert not got  # enough: the pending timeout decides from here
                assert job(h).reboots == tries
        finally:
            again.close()


def test_a_hello_for_other_hardware_is_refused_and_nothing_is_sent(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, hello_bytes(hardware=(2, 0, 0)))
    try:
        assert boot.closed()
        assert boot.received == b""
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
    assert "hardware" in job(h).message and "not flashed" in job(h).message
    assert not job(h).wrote_records
    # Pending state is cleared: another flash may now be requested.
    assert h.svc.flash_job("lobby") is not None and not job(h).active
    assert not h.svc._flash_wants("127.0.0.1")


def test_a_hello_for_another_display_is_refused(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, hello_bytes(display_id=7))
    try:
        assert boot.closed()
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
    assert "display" in job(h).message


def test_an_ack_timeout_stops_and_is_not_retried(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        boot.offer()
        boot.record()
        boot.ack(1)
        boot.record()  # block 2, never acknowledged
        assert boot.closed(timeout=4.0)
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
    j = job(h)
    assert "no ack for block 2" in j.message and "USB" in j.message and "power-cycle" in j.message
    assert j.wrote_records and j.blocks_done == 1
    assert not h.svc._flash_wants("127.0.0.1")  # pending is cleared: no later hello is taken


def test_a_wrong_ack_stops_the_flash(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        boot.offer()
        boot.record()
        boot.ack(3)
        assert boot.closed()
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
    assert "block 3" in job(h).message and "USB" in job(h).message


def test_a_disconnect_mid_transfer_stops_the_flash(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, REAL_HELLO)
    boot.offer()
    boot.record()
    boot.ack(1)
    boot.record()
    boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
    assert "after block 1 of 5" in job(h).message


def test_a_frame_that_never_calls_the_bootloader_times_out(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(visionect_flash, "PENDING_TIMEOUT_S", 0.3)
    sign = connect(h)
    start_flash(h, sign)
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED, timeout=3)
    assert "didn't contact its bootloader" in job(h).message and "nothing" in job(h).message


def test_a_frame_that_reboots_without_the_bootloader_is_reported(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    after = FakeSign(h.svc.port)
    try:
        after.status(tags(reason=CONNECT_REBOOT))
        assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
        assert "without calling its bootloader" in job(h).message
    finally:
        after.close()


def test_a_frame_that_does_not_return_after_flashing_is_reported(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(visionect_flash, "VERIFY_TIMEOUT_S", 0.3)
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        boot.offer()
        for rec in bl.walk_firmware(FIRMWARE):
            boot.record()
            boot.ack(rec.index)
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED, timeout=3)
    assert "hasn't reconnected" in job(h).message and "USB" in job(h).message


# -- no flash pending: behaves as before ---------------------------------------------------


def test_a_hello_with_no_flash_pending_is_dropped_as_before(h: Harness) -> None:
    sign = connect(h)
    try:
        boot = Bootloader(h.svc.port, REAL_HELLO)
        try:
            assert boot.closed()  # the library's "not the Visionect protocol"
            assert boot.received == b""
        finally:
            boot.close()
        assert h.svc.flash_job("lobby") is None
        assert (h.svc.sign_view(UUID) or {}).get("connected")  # the sign is undisturbed
        sign.status(tags())
        assert _wait(lambda: (h.svc.sign_view(UUID) or {})["last_seen"])
    finally:
        sign.close()


def test_a_hello_from_another_frame_while_one_is_pending_is_not_taken(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    other = hello_bytes(device_id=bytes.fromhex("aabbccddeeff00112233445566778899"))
    boot = Bootloader(h.svc.port, other)
    try:
        assert boot.closed()
    finally:
        boot.close()
    assert job(h).phase == visionect_flash.PENDING  # still waiting for its own frame
    real = Bootloader(h.svc.port, REAL_HELLO)
    try:
        assert real.offer() == bl.encode_offer(0xA529A106)
    finally:
        real.close()


def test_a_normal_connection_while_a_flash_is_pending_is_unaffected(h: Harness) -> None:
    sign = connect(h)
    try:
        assert h.svc.request_firmware_update("lobby", TARGET_HEX) is None
        # Another sign's normal status connection is replayed to the library.
        other = "aabbccdd-0000-1111-2222-333344445555"
        peer = FakeSign(h.svc.port, other)
        try:
            peer.status(tags())
            assert _wait(lambda: h.svc.sign_view(other) is not None)
            assert not peer.dropped(timeout=0.5)
        finally:
            peer.close()
    finally:
        sign.close()


def test_the_listener_looks_at_nothing_when_no_flash_is_pending(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    server = h.svc._server
    server.bootloader_handler = lambda *a: seen.append("called")  # type: ignore[assignment]
    sign = connect(h)  # a normal connection: no flash pending, so no sniff
    try:
        assert seen == []
    finally:
        sign.close()


def test_a_forged_hello_cannot_start_a_flash(h: Harness) -> None:
    """Nothing is pending: a hello on the port is dropped, never answered."""
    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        assert boot.closed()
    finally:
        boot.close()
    assert h.svc.flash_job("lobby") is None


# -- the route and the page ------------------------------------------------------------------


def fake_service(
    asked: list[tuple[str, str]], problem: str | None = None, **view: Any
) -> SimpleNamespace:
    firmware = {
        "installed": "7.4.4407",
        "latest": "7.5.1",
        "latest_checksum": "7ffffff0",
        "update_available": True,
        "builds": [
            {
                "checksum": "7ffffff0",
                "version": "7.5.1",
                "installed": False,
                "newest": True,
                "downgrade": False,
            },
            {
                "checksum": "2cb91ed6",
                "version": "7.4.4407",
                "installed": True,
                "newest": False,
                "downgrade": False,
            },
            {
                "checksum": "a529a106",
                "version": "7.3.4358",
                "installed": False,
                "newest": False,
                "downgrade": True,
            },
        ],
        "blocked": "",
        "flash": None,
        "list_known": True,
        "min_battery": 30,
        **view,
    }

    def request(device_id: str, checksum: str) -> str | None:
        asked.append((device_id, checksum))
        return problem

    return SimpleNamespace(
        running=True,
        port=11113,
        start_error=None,
        device_uuid=lambda _d: UUID,
        auto_bind_open=lambda _d: False,
        sign_view=lambda _u: {
            "connected": True,
            "in_sync": True,
            "connect_reason": "wakeup",
            "status": {"firmware": "7.4.4407", "battery_pct": 80},
            "exchanges": [],
        },
        request_action=lambda *_a: None,
        on_push=lambda *_a: None,
        firmware_view=lambda _d: firmware,
        firmware_token=lambda _d: "t1",
        request_firmware_update=request,
    )


@pytest.fixture
def client_app(tmp_path: Path) -> tuple[Flask, Any]:
    app = create_app(testing=True, data_root=tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    client.post("/settings/devices/add", data={"kind": "visionect", "id": "hall", "name": "Hall"})
    client.post(
        "/settings/devices/add",
        data={"id": "porch", "kind": "esp32_client", "name": "Porch", "transport": "mqtt"},
    )
    return app, client


def test_the_page_shows_versions_and_the_control_only_for_a_visionect_sign(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    monkeypatch.setattr(visionect, "service", lambda: fake_service([]))
    page = client.get("/settings/devices/hall").get_data(as_text=True)
    for text in (
        "Installed firmware",
        "Latest available",
        "Update available",
        'data-render-sync="firmware:hall"',
    ):
        assert text in page, text
    # The rail's Firmware row says an update is there.
    assert "v7.5.1 available" in page
    # The flash itself is on the Firmware page, and the tag goes there.
    assert 'name="checksum"' not in page
    assert '<a class="tg tg--warn" href="/settings/firmware"' in page
    # Not a Visionect device: none of it.
    other = client.get("/settings/devices/porch").get_data(as_text=True)
    for text in ("Installed firmware", "Latest available", "Update firmware", "visionect-firmware"):
        assert text not in other, text


def test_the_page_degrades_when_the_repository_is_unreachable(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    fake = fake_service(
        [],
        latest="",
        update_available=False,
        builds=[],
        list_known=False,
        unknown_because="Visionect's firmware list can't be reached",
    )
    monkeypatch.setattr(visionect, "service", lambda: fake)
    resp = client.get("/settings/devices/hall")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "Installed firmware" in page and "7.4.4407" in page
    assert "Update available" not in page and "Update firmware" not in page
    assert "firmware list can&#39;t be reached" in page or "firmware list can't be reached" in page
    # A service that raises never breaks the page either.
    broken = fake_service([])
    broken.firmware_view = lambda _d: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setattr(visionect, "service", lambda: broken)
    page = client.get("/settings/devices/hall").get_data(as_text=True)
    assert "Installed firmware" not in page and "Update firmware" not in page


def test_the_page_shows_progress_and_the_result(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    flash = {
        "active": True,
        "phase": "running",
        "ok": False,
        "percent": 42,
        "message": "",
        "target": "7.3.4358",
        "blocks_done": 77,
        "blocks_total": 184,
    }
    fake = fake_service([], flash=flash)
    monkeypatch.setattr(visionect, "service", lambda: fake)
    page = client.get("/settings/devices/hall").get_data(as_text=True)
    assert "Updating firmware to 7.3.4358" in page
    assert "Block 77 of 184 (42%)" in page
    assert '<progress class="dx-progress" max="100" value="42"' in page
    done = {
        **flash,
        "active": False,
        "phase": "done",
        "ok": True,
        "message": "Restarted on 7.3.4358.",
    }
    fake = fake_service([], flash=done)
    monkeypatch.setattr(visionect, "service", lambda: fake)
    page = client.get("/settings/devices/hall").get_data(as_text=True)
    assert "Firmware updated to 7.3.4358" in page and "Restarted on 7.3.4358." in page
    failed = {**flash, "active": False, "phase": "failed", "message": "No ack for block 2."}
    fake = fake_service([], flash=failed)
    monkeypatch.setattr(visionect, "service", lambda: fake)
    page = client.get("/settings/devices/hall").get_data(as_text=True)
    assert "Firmware update to 7.3.4358 failed" in page and "No ack for block 2." in page


def test_the_page_poll_signature_carries_the_firmware_state(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    monkeypatch.setattr(visionect, "service", lambda: fake_service([]))
    state = client.get("/settings/devices/render-state.json").get_json()["devices"]
    assert state["hall"].endswith("|fw:t1")
    assert "|fw:" in state["porch"]  # the fake answers for every id; the real one only for signs


def test_the_route_asks_the_service_and_says_what_happened(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(visionect, "service", lambda: fake_service(asked))
    resp = client.post(
        "/settings/devices/hall/visionect-firmware",
        data={"checksum": "a529a106"},
        follow_redirects=True,
    )
    assert asked == [("hall", "a529a106")]
    assert "Updating Hall" in resp.get_data(as_text=True)
    monkeypatch.setattr(
        visionect, "service", lambda: fake_service(asked, "The frame's battery is at 12%.")
    )
    resp = client.post(
        "/settings/devices/hall/visionect-firmware",
        data={"checksum": "a529a106"},
        follow_redirects=True,
    )
    assert "battery is at 12%" in resp.get_data(as_text=True)
    monkeypatch.setattr(visionect, "service", lambda: None)
    resp = client.post(
        "/settings/devices/hall/visionect-firmware",
        data={"checksum": "a529a106"},
        follow_redirects=True,
    )
    assert "isn&#39;t listening" in resp.get_data(as_text=True)
    # Not a Visionect device, or unknown.
    for device in ("porch", "nope"):
        resp = client.post(
            f"/settings/devices/{device}/visionect-firmware",
            data={"checksum": "a529a106"},
            follow_redirects=True,
        )
        assert "Unknown Visionect device" in resp.get_data(as_text=True)


def test_the_route_needs_a_login_and_the_same_origin(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(visionect, "service", lambda: fake_service(asked))
    # Behind the same login gate as every other settings route.
    from app.auth import _path_is_open

    assert not _path_is_open("/settings/devices/hall/visionect-firmware")
    # A cross-site form post from a signed-in browser.
    resp = client.post(
        "/settings/devices/hall/visionect-firmware",
        data={"checksum": "a529a106"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403
    assert asked == []


@pytest.mark.parametrize(
    ("hello", "word"),
    [
        ({"device_id": b"\x01" * 16}, "not this frame"),
        ({"hardware": (1, 2, 0)}, "hardware"),
        ({"hardware": (1, 1, 1)}, "hardware"),
        ({"hardware_name_id": 9}, "name id"),
        ({"bootloader": (6, 4, 0)}, "bootloader"),
        ({"display_id": 7}, "display"),
    ],
)
def test_hello_refusal_covers_each_identity_field(hello: dict[str, Any], word: str) -> None:
    real = bl.parse_hello(REAL_HELLO)
    build = fwrepo.Build(
        checksum=0xA529A106,
        checksum_text="a529a106",
        version=(7, 3, 4358),
        hardware=real.hardware,
        hardware_name_id=real.hardware_name_id,
        display_id=real.display_id,
    )
    j = visionect_flash.FlashJob(
        uuid=real.uuid,
        device_id="lobby",
        build=build,
        data=b"",
        peer_ip="",
        from_version="",
        from_checksum=None,
    )
    assert visionect_flash.hello_refusal(j, real) is None
    refused = visionect_flash.hello_refusal(j, dataclasses.replace(real, **hello))
    assert refused is not None and word in refused


def test_the_last_ack_closes_with_a_reset_as_the_suite_did(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        boot.offer()
        for rec in bl.walk_firmware(FIRMWARE):
            boot.record()
            boot.ack(rec.index)
        boot.sock.settimeout(3.0)
        with pytest.raises(ConnectionResetError):
            while boot.sock.recv(65536):
                pass
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.VERIFYING)


def test_a_record_sent_but_never_acked_counts_as_started(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    boot = Bootloader(h.svc.port, REAL_HELLO)
    try:
        boot.offer()
        boot.record()  # block 1 went out, the frame says nothing
        assert boot.closed(timeout=4.0)
    finally:
        boot.close()
    assert _wait(lambda: job(h).phase == visionect_flash.FAILED)
    assert job(h).wrote_records and job(h).blocks_done == 0
    assert "not flashed" not in job(h).message and "USB" in job(h).message


def test_a_pending_job_past_its_time_is_never_claimed_even_with_no_timer(h: Harness) -> None:
    sign = connect(h)
    start_flash(h, sign)
    j = job(h)
    assert _wait(lambda: j.timer is not None)
    assert h.svc._flash_wants("127.0.0.1")
    j.timer.cancel()
    j.timer = None
    j.reboot_at -= visionect_flash.PENDING_TIMEOUT_S + visionect_flash.PENDING_GRACE_S + 1
    assert not h.svc._flash_wants("127.0.0.1")
    assert not h.svc._flash_active(UUID)
    assert not h.svc._flash_any_active()


def test_stopping_ends_a_pending_flash(tmp_path: Path) -> None:
    harness = Harness(tmp_path, uuid=UUID)
    repo = fwrepo.FirmwareRepository(tmp_path / "fw", base_url="http://127.0.0.1:9", timeout=0.5)
    repo._loaded = True
    repo._builds = fwrepo.parse_list(LIST)
    repo._checked_at = time.time()
    build = next(b for b in repo._builds if b.checksum_hex == TARGET_HEX)
    repo._write_atomic(repo._file_path(build), FIRMWARE)
    harness.svc.firmware_repo = repo
    assert harness.svc.start()
    sign = connect(harness)
    start_flash(harness, sign)
    j = job(harness)
    harness.svc.stop()
    assert j.phase == visionect_flash.FAILED and j.finished_at
    assert "nothing was flashed" in j.message


def test_the_reboot_waits_for_an_unanswered_picture_or_settings_packet(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(visionect, "FLASH_REBOOT_WAIT_S", 30.0)
    sign = connect(h)
    try:
        s = h.svc._signs[UUID]
        assert _wait(lambda: not s.param_out and s.inflight is None)
        s.param_out = "read"
        s.params_wait_until = time.monotonic() + 30
        assert h.svc.request_firmware_update("lobby", TARGET_HEX) is None
        assert not sign.wait_for(
            lambda x: any(int.from_bytes(f.payload[:4], "little") == 1 for f in x.commands()),
            timeout=0.5,
        )
        s.param_out = ""  # the frame answered
        s.inflight = None
        assert sign.wait_for(
            lambda x: any(int.from_bytes(f.payload[:4], "little") == 1 for f in x.commands()),
            timeout=4.0,
        )
    finally:
        sign.close()


def test_a_reboot_for_a_flash_goes_out_anyway_past_the_wait(h: Harness) -> None:
    sign = connect(h)
    try:
        s = h.svc._signs[UUID]
        assert _wait(lambda: not s.param_out and s.inflight is None)
        s.param_out = "read"
        s.params_wait_until = time.monotonic() + 30
        assert h.svc.request_firmware_update("lobby", TARGET_HEX) is None
        assert sign.wait_for(
            lambda x: any(int.from_bytes(f.payload[:4], "little") == 1 for f in x.commands()),
            timeout=4.0,
        )
    finally:
        sign.close()


def test_the_firmware_page_flashes_a_visionect_frame_with_visionect_s_notes(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    fake = fake_service([])
    for build in fake.firmware_view("hall")["builds"]:
        build["notes"] = (
            [{"title": "Changes", "items": ["Fixed <the> thing"]}]
            if build["version"] == "7.5.1"
            else []
        )
    view = fake.firmware_view("hall")
    fake.firmware_view = lambda _d: view
    monkeypatch.setattr(visionect, "service", lambda: fake)
    page = client.get("/settings/firmware").get_data(as_text=True)
    for text in (
        "official Visionect firmware",
        "7.5.1 (newest)",
        "7.3.4358 (downgrade)",
        "USB Configurator",  # the risk is in the confirm dialog
        "/settings/devices/hall/visionect-firmware",
        "update available",
        "Fixed &lt;the&gt; thing",  # the docs' text is escaped
        "Visionect hasn't published notes",
    ):
        assert text in page, text
    assert "USB update only" not in page.split("Fleet rollout")[0]
    assert 'title="Other versions"' in page  # the gear, next to the available version
    assert page.index("fw-vs-gear") < page.index("update available")
    assert 'value="7ffffff0" selected' in page
    assert 'data-fw-notes="7ffffff0">' in page  # the default choice's notes show
    assert 'data-fw-notes="a529a106" hidden' in page
    # Nothing to stage for this kind in Fleet rollout.
    assert "fw-fleet-kind" not in page or "<code>visionect" not in page


def test_a_flash_from_the_firmware_page_comes_back_to_it(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(visionect, "service", lambda: fake_service(asked))
    resp = client.post("/settings/devices/hall/visionect-firmware", data={"checksum": "a529a106"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/firmware")
    assert asked == [("hall", "a529a106")]


def test_a_firmware_view_says_nothing_about_an_update_on_the_newest_build(h: Harness) -> None:
    sign = connect(h)  # reports 7.4.4407, the newest for its hardware in LIST or not
    try:
        view = h.svc.firmware_view("lobby")
        newest = view["builds"][0]["version"]
        assert view["update_available"] is (newest != "7.4.4407")
    finally:
        sign.close()


def test_an_ignored_build_is_no_longer_offered_as_an_update(h: Harness) -> None:
    sign = connect(h, fw=(7, 3, 4358), checksum=0xA529A106)
    try:
        view = h.svc.firmware_view("lobby")
        assert view["update_available"] and view["latest"] == "7.4.4407"
        h.settings.sections.setdefault("app", {})[visionect.FIRMWARE_IGNORED_KEY] = ["2cb91ed6"]
        view = h.svc.firmware_view("lobby")
        assert not view["update_available"] and view["latest"] == "7.3.4358"
        ignored = next(b for b in view["builds"] if b["version"] == "7.4.4407")
        assert ignored["ignored"] and not ignored["newest"]
    finally:
        sign.close()


def test_an_ignored_build_can_still_be_chosen_by_hand(h: Harness) -> None:
    sign = connect(h)
    try:
        h.settings.sections.setdefault("app", {})[visionect.FIRMWARE_IGNORED_KEY] = [TARGET_HEX]
        assert h.svc.request_firmware_update("lobby", TARGET_HEX) is None
    finally:
        sign.close()


def test_the_ignore_route_adds_and_removes_a_build(client_app) -> None:
    app, client = client_app
    store = app.config["SETTINGS_STORE"]

    def saved() -> Any:
        return store.get_section("app").get(visionect.FIRMWARE_IGNORED_KEY)

    url = "/settings/firmware/visionect-ignore"
    assert client.post(url, data={"ignore": "2CB91ED6"}).status_code == 302
    assert saved() == ["2cb91ed6"]
    client.post(url, data={"ignore": "2cb91ed6"})
    assert saved() == ["2cb91ed6"]  # no duplicates
    client.post(url, data={"ignore": "a529a106"})
    client.post(url, data={"restore": "2cb91ed6"})
    assert saved() == ["a529a106"]
    client.post(url, data={"ignore": "not hex; <b>"})
    assert saved() == ["a529a106"]


def test_the_firmware_page_offers_to_ignore_and_to_stop_ignoring(
    client_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = client_app
    fake = fake_service([])
    view = fake.firmware_view("hall")
    next(b for b in view["builds"] if b["version"] == "7.3.4358")["ignored"] = True
    fake.firmware_view = lambda _d: view
    monkeypatch.setattr(visionect, "service", lambda: fake)
    page = client.get("/settings/firmware").get_data(as_text=True)
    assert 'name="ignore" value="7ffffff0"' in page and "ph-eye-slash" in page
    assert 'name="restore" value="a529a106"' in page and "Stop ignoring" in page
    assert "7.3.4358 (downgrade, ignored)" in page or "7.3.4358 (ignored, downgrade)" in page
