"""app.visionect_firmware: the repository client, against a local fake server.

Nothing here touches the real repository; firmware files are short synthetic
records.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from app import visionect_firmware as fwrepo
from tests.test_visionect_bootloader import make_firmware


@pytest.fixture(autouse=True)
def _no_pinned_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test files are made up, so the real builds' known hashes are off."""
    monkeypatch.setattr(fwrepo, "KNOWN_SHA256", {})


def entry(checksum: str, fw: str, hw: str = "1.1.0", display: str = "3255107884", **meta) -> dict:
    fw_major, fw_minor, fw_rev = fw.split(".")
    hw_major, hw_minor, bom = hw.split(".")
    return {
        "ID": f"1_{checksum.zfill(8)}_{fw}_8_{hw}_{display}_universal.firmware",
        "Revision": fw,
        "Hardware": hw,
        "Meta": {
            "AppName": "UniFw",
            "Checksum": checksum,
            "Display": "DISPLAY_X",
            "DisplayID": display,
            "FwMajor": fw_major,
            "FwMinor": fw_minor,
            "FwRevision": fw_rev,
            "HwBOM": bom,
            "HwMajor": hw_major,
            "HwMinor": hw_minor,
            "HwNameID": "8",
            "type": "firmware",
            **meta,
        },
    }


LIST = {
    "Version": "1.0",
    "Firmwares": {
        "1.1.0": [
            entry("2cb91ed6", "7.4.4407"),
            entry("a529a106", "7.3.4358"),
            entry("1e4a1ca", "6.16.4144"),  # leading zero dropped, as the repository writes it
            entry("11111111", "7.4.4407", display="7"),  # another display
            entry("22222222", "7.4.4407", AppName="UniFw_AC"),
            entry("33333333", "7.9.1", type="bootloader"),
        ],
        "2.0.0": [entry("6fdac4bf", "7.4.4407", hw="2.0.0")],
        "0.0.0": [],
    },
}
FIRMWARE = make_firmware([bytes(16), bytes(32)])


class Repo:
    def __init__(self) -> None:
        self.list_body = json.dumps(LIST).encode()
        self.etag = '"v1"'
        self.files: dict[str, bytes] = {"a529a106": FIRMWARE}
        self.requests: list[tuple[str, str | None]] = []
        self.list_status = 200
        self.file_status: dict[str, int] = {}


@pytest.fixture
def server() -> Iterator[tuple[Repo, str]]:
    state = Repo()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            state.requests.append((self.path, self.headers.get("If-None-Match")))
            if self.path == "/api/v2/list":
                if state.list_status != 200:
                    self.send_response(state.list_status)
                    self.end_headers()
                    return
                if self.headers.get("If-None-Match") == state.etag:
                    self.send_response(304)
                    self.send_header("ETag", state.etag)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("ETag", state.etag)
                self.send_header("Content-Length", str(len(state.list_body)))
                self.end_headers()
                self.wfile.write(state.list_body)
            elif self.path.startswith("/api/v1/get/"):
                name = self.path.rsplit("/", 1)[1]
                if name in state.file_status:
                    self.send_response(state.file_status[name])
                    self.end_headers()
                elif name in state.files:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(state.files[name])))
                    self.end_headers()
                    self.wfile.write(state.files[name])
                else:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def make(tmp_path: Path, url: str) -> fwrepo.FirmwareRepository:
    return fwrepo.FirmwareRepository(tmp_path / "cache", base_url=url, timeout=3)


def test_parse_keeps_only_unifw_firmware() -> None:
    builds = fwrepo.parse_list(LIST)
    assert {b.checksum_hex for b in builds} == {
        "2cb91ed6",
        "a529a106",
        "01e4a1ca",
        "11111111",
        "6fdac4bf",
    }
    short = next(b for b in builds if b.checksum_hex == "01e4a1ca")
    assert short.checksum_text == "1e4a1ca" and short.checksum == 0x01E4A1CA


@pytest.mark.parametrize("payload", [[], {}, {"Firmwares": []}, {"Firmwares": {"a": 1}}])
def test_a_list_of_the_wrong_shape(payload) -> None:
    if payload == {"Firmwares": {"a": 1}}:
        assert fwrepo.parse_list(payload) == []
    else:
        with pytest.raises(fwrepo.RepositoryError):
            fwrepo.parse_list(payload)


def test_compatible_needs_the_same_hardware_name_and_display_newest_first() -> None:
    builds = fwrepo.parse_list(LIST)
    got = fwrepo.compatible(builds, hardware=(1, 1, 0), hardware_name_id=8, display_id=3255107884)
    assert [b.version_text for b in got] == ["7.4.4407", "7.3.4358", "6.16.4144"]
    assert (
        fwrepo.compatible(builds, hardware=(1, 1, 1), hardware_name_id=8, display_id=3255107884)
        == []
    )
    assert (
        fwrepo.compatible(builds, hardware=(1, 1, 0), hardware_name_id=5, display_id=3255107884)
        == []
    )
    assert fwrepo.compatible(builds, hardware=(1, 1, 0), hardware_name_id=8, display_id=99) == []
    assert [
        b.checksum_hex
        for b in fwrepo.compatible(
            builds, hardware=(2, 0, 0), hardware_name_id=8, display_id=3255107884
        )
    ] == ["6fdac4bf"]


def test_list_is_cached_on_disk_and_asked_again_with_the_etag(server, tmp_path: Path) -> None:
    state, url = server
    repo = make(tmp_path, url)
    assert repo.builds() is None and repo.stale()
    assert repo.refresh()
    assert len(repo.builds() or []) == 5
    assert state.requests == [("/api/v2/list", None)]
    assert (tmp_path / "cache" / "list.json").exists()
    # A fresh process reads the cache and does not ask again until it is old.
    again = make(tmp_path, url)
    assert len(again.builds() or []) == 5 and not again.stale()
    # Old: asks with If-None-Match and takes the 304.
    again._checked_at = 0.0
    assert again.stale()
    assert again.refresh()
    assert state.requests[-1] == ("/api/v2/list", '"v1"')
    assert not again.stale()
    # A changed list replaces the cache.
    state.etag = '"v2"'
    state.list_body = json.dumps({"Firmwares": {"1.1.0": [entry("a529a106", "7.3.4358")]}}).encode()
    assert again.refresh()
    assert [b.checksum_hex for b in again.builds() or []] == ["a529a106"]


def test_unreachable_repository_keeps_the_cache_and_does_not_raise(server, tmp_path: Path) -> None:
    state, url = server
    repo = make(tmp_path, url)
    assert repo.refresh()
    state.list_status = 500
    repo._checked_at = 0.0
    assert repo.refresh() is False
    assert "500" in repo.last_error
    assert len(repo.builds() or []) == 5
    assert not repo.stale()  # not hammered: retried after a pause
    # Nothing listening at all.
    dead = make(tmp_path / "other", "http://127.0.0.1:9")
    assert dead.refresh() is False and dead.builds() is None and dead.last_error


def test_garbage_list_is_refused(server, tmp_path: Path) -> None:
    state, url = server
    state.list_body = b"<html>not json</html>"
    repo = make(tmp_path, url)
    assert repo.refresh() is False
    assert repo.builds() is None
    state.list_body = b"[1, 2]"
    assert repo.refresh() is False


def test_fetch_downloads_verifies_and_caches(server, tmp_path: Path) -> None:
    state, url = server
    repo = make(tmp_path, url)
    repo.refresh()
    build = next(b for b in repo.builds() or [] if b.checksum_hex == "a529a106")
    assert repo.cached_file(build) is None
    assert repo.fetch(build) == FIRMWARE
    assert (tmp_path / "cache" / "a529a106.bin").read_bytes() == FIRMWARE
    n = len(state.requests)
    assert repo.fetch(build) == FIRMWARE  # from the cache
    assert len(state.requests) == n


def test_fetch_refuses_a_file_that_does_not_walk(server, tmp_path: Path) -> None:
    state, url = server
    state.files["a529a106"] = FIRMWARE[:-1]
    repo = make(tmp_path, url)
    repo.refresh()
    build = next(b for b in repo.builds() or [] if b.checksum_hex == "a529a106")
    with pytest.raises(fwrepo.RepositoryError, match="not a file the frame accepts"):
        repo.fetch(build)
    assert not (tmp_path / "cache" / "a529a106.bin").exists()


def test_a_corrupt_cached_file_is_not_used(server, tmp_path: Path) -> None:
    _state, url = server
    repo = make(tmp_path, url)
    repo.refresh()
    build = next(b for b in repo.builds() or [] if b.checksum_hex == "a529a106")
    repo.fetch(build)
    (tmp_path / "cache" / "a529a106.bin").write_bytes(FIRMWARE[:-5])
    assert repo.cached_file(build) is None
    assert repo.fetch(build) == FIRMWARE  # downloaded again


def test_fetch_tries_the_zero_padded_checksum_after_a_404(server, tmp_path: Path) -> None:
    state, url = server
    state.files = {"01e4a1ca": FIRMWARE}  # only the padded name exists
    repo = make(tmp_path, url)
    repo.refresh()
    build = next(b for b in repo.builds() or [] if b.checksum_hex == "01e4a1ca")
    assert repo.fetch(build) == FIRMWARE
    assert [p for p, _ in state.requests if "get" in p] == [
        "/api/v1/get/1e4a1ca",
        "/api/v1/get/01e4a1ca",
    ]


def test_fetch_failures_are_plain_errors(server, tmp_path: Path) -> None:
    state, url = server
    state.files = {}
    repo = make(tmp_path, url)
    repo.refresh()
    build = next(b for b in repo.builds() or [] if b.checksum_hex == "a529a106")
    with pytest.raises(fwrepo.RepositoryError, match="couldn't download"):
        repo.fetch(build)
    state.file_status["a529a106"] = 503
    with pytest.raises(fwrepo.RepositoryError):
        repo.fetch(build)
    dead = make(tmp_path / "x", "http://127.0.0.1:9")
    with pytest.raises(fwrepo.RepositoryError):
        dead.fetch(build)


def test_oversized_answers_are_refused(server, tmp_path: Path, monkeypatch) -> None:
    state, url = server
    monkeypatch.setattr(fwrepo, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(fwrepo, "MAX_LIST_BYTES", 10)
    repo = make(tmp_path, url)
    assert repo.refresh() is False
    state.files["a529a106"] = FIRMWARE
    full = fwrepo.parse_list(LIST)
    build = next(b for b in full if b.checksum_hex == "a529a106")
    with pytest.raises(fwrepo.RepositoryError):
        repo.fetch(build)


def _serve_raw(answer: bytes) -> tuple[str, Any]:
    """A server that sends ``answer`` as is, to every connection, then hangs up."""
    listener = socket.create_server(("127.0.0.1", 0))

    def run() -> None:
        with contextlib.suppress(OSError):
            while True:
                conn, _ = listener.accept()
                with conn:
                    conn.recv(65536)
                    conn.sendall(answer)

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}", listener


@pytest.mark.parametrize(
    "answer",
    [
        # A chunked body cut short: http.client raises IncompleteRead.
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n40\r\nshort",
        # A gzip body cut short: EOFError, not OSError. mtime=0 keeps the
        # bytes, and so the test id, the same on every xdist worker.
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 12\r\n\r\n"
        + gzip.compress(b'{"Firmwares": {}}', mtime=0)[:12],
        # Not gzip's deflate stream at all: zlib.error.
        b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 14\r\n\r\n"
        + b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"
        + b"\xff\xff\xff\xff",
        # Not HTTP at all.
        b"nonsense\r\n\r\n",
    ],
)
def test_a_broken_answer_is_a_plain_failure_with_backoff(tmp_path: Path, answer: bytes) -> None:
    url, listener = _serve_raw(answer)
    try:
        repo = fwrepo.FirmwareRepository(tmp_path, base_url=url, timeout=2.0)
        assert repo.refresh() is False
        assert repo.last_error
        assert repo.stale() is False  # waits before asking again
    finally:
        listener.close()


def test_a_download_cut_short_is_a_plain_failure(tmp_path: Path) -> None:
    url, listener = _serve_raw(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n40\r\nshort")
    try:
        repo = fwrepo.FirmwareRepository(tmp_path, base_url=url, timeout=2.0)
        build = fwrepo.parse_list({"Firmwares": {"x": [entry("a529a106", "7.3.4358")]}})[0]
        with pytest.raises(fwrepo.RepositoryError):
            repo.fetch(build)
    finally:
        listener.close()


NOTES = """==================
Visionect Firmware
==================

Intro paragraph.

Firmware version 7
------------------

Release 7.3.4358
~~~~~~~~~~~~~~~~~

**Improvements:**
 * First thing.
 * Second thing
   wrapped.

Release 7.1.4276
~~~~~~~~~~~~~~~~~

**Changes:**
 * ``CODE`` change.

**Fixes:**
 * A fix.

Release 6.0.1
~~~~~~~~~~~~~

Nothing listed.
"""


def test_release_notes_are_split_by_version() -> None:
    notes = fwrepo.parse_release_notes(NOTES)
    assert notes["7.3.4358"] == [
        {"title": "Improvements", "items": ["First thing.", "Second thing wrapped."]}
    ]
    assert [s["title"] for s in notes["7.1.4276"]] == ["Changes", "Fixes"]
    assert notes["7.1.4276"][0]["items"] == ["CODE change."]
    assert "6.0.1" not in notes and "7.4.4407" not in notes


def test_a_pinned_build_refuses_a_file_that_isnt_the_known_one(
    server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    _state, url = server
    repo = make(tmp_path, url)
    repo.refresh()
    build = next(b for b in repo.builds() or [] if b.checksum_hex == "a529a106")
    monkeypatch.setattr(fwrepo, "KNOWN_SHA256", {build.checksum: "0" * 64})
    with pytest.raises(fwrepo.RepositoryError, match="doesn't match the known file"):
        repo.fetch(build)
    assert repo.cached_file(build) is None
    good = hashlib.sha256(FIRMWARE).hexdigest()
    monkeypatch.setattr(fwrepo, "KNOWN_SHA256", {build.checksum: good})
    assert repo.fetch(build) == FIRMWARE
    # A cached file that has changed since is not used.
    monkeypatch.setattr(fwrepo, "KNOWN_SHA256", {build.checksum: "0" * 64})
    assert repo.cached_file(build) is None


def test_a_checksum_that_isnt_plain_hex_is_skipped() -> None:
    payload = {"Firmwares": {"x": [entry("+a529a106", "7.3.4358"), entry("٣", "7.3.4358")]}}
    assert fwrepo.parse_list(payload) == []
