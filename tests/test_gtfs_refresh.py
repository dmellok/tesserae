"""GTFS widget: a pending timetable rebuild asks for a re-render, and one feed URL is downloaded once."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

URL = "https://example.test/gtfs-refresh.zip"
STOP = "S1"


@pytest.fixture
def gtfs(app: Flask, monkeypatch: pytest.MonkeyPatch) -> Any:
    plugin = app.config["PLUGIN_REGISTRY"].get("gtfs")
    assert plugin is not None and plugin.server_module is not None
    mod = plugin.server_module
    monkeypatch.setattr(mod, "BUILD_WAIT_S", 0.0)
    return mod


def _paths(data_dir: Path, url: str = URL) -> tuple[Path, Path]:
    key = hashlib.sha1(f"{url}|{STOP}".encode()).hexdigest()[:16]
    return data_dir / f"tt2_{key}.json", data_dir / f"err_{key}.json"


def _write_table(path: Path, age_s: float, stop_name: str = "Old Stop") -> None:
    table = {"tz": "UTC", "stop_name": stop_name, "stop_ids": [STOP], "stop_coords": {}}
    path.write_text(json.dumps(table), encoding="utf-8")
    old = time.time() - age_s
    os.utime(path, (old, old))


def _fetch(gtfs: Any, data_dir: Path, url: str = URL) -> dict[str, Any]:
    out: dict[str, Any] = gtfs.fetch(
        {"gtfs_url": url, "stop_id": STOP}, {}, ctx={"data_dir": str(data_dir)}
    )
    return out


def _blocked_build(gtfs: Any, monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    release = threading.Event()

    def build(url: str, stop_opt: str, path: Path, err_path: Path) -> None:
        del url, stop_opt, err_path
        release.wait(5)
        _write_table(path, 0, "New Stop")

    monkeypatch.setattr(gtfs, "_build", build)
    return release


def _finish(gtfs: Any, release: threading.Event) -> None:
    release.set()
    for thread in list(gtfs._BUILDS.values()):
        thread.join(5)


def test_stale_timetable_asks_for_a_refresh_while_rebuilding(
    gtfs: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tt, _err = _paths(tmp_path)
    _write_table(tt, gtfs.TIMETABLE_TTL_S + 60)
    release = _blocked_build(gtfs, monkeypatch)
    try:
        out = _fetch(gtfs, tmp_path)
        # The old timetable still paints, with the hint that a newer one is on the way.
        assert out["stop"] == "Old Stop"
        assert out["live_refresh_s"] == gtfs.LIVE_REFRESH_S
    finally:
        _finish(gtfs, release)
    after = _fetch(gtfs, tmp_path)
    assert after["stop"] == "New Stop"
    assert "live_refresh_s" not in after


def test_loading_state_asks_for_a_refresh(
    gtfs: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = _blocked_build(gtfs, monkeypatch)
    try:
        out = _fetch(gtfs, tmp_path)
        assert "Loading" in out["error"]
        assert out["live_refresh_s"] == gtfs.LIVE_REFRESH_S
    finally:
        _finish(gtfs, release)


def test_fresh_timetable_sends_no_hint(
    gtfs: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tt, _err = _paths(tmp_path)
    _write_table(tt, 60)
    release = _blocked_build(gtfs, monkeypatch)
    try:
        out = _fetch(gtfs, tmp_path)
    finally:
        _finish(gtfs, release)
    assert out["stop"] == "Old Stop"
    assert "live_refresh_s" not in out


def test_failed_rebuild_in_cooldown_sends_no_hint(
    gtfs: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tt, err = _paths(tmp_path)
    _write_table(tt, gtfs.TIMETABLE_TTL_S + 60)
    err.write_text(
        json.dumps({"error": "Couldn't load the GTFS feed right now."}), encoding="utf-8"
    )
    release = _blocked_build(gtfs, monkeypatch)
    try:
        out = _fetch(gtfs, tmp_path)
    finally:
        _finish(gtfs, release)
    # Nothing is rebuilding, so there is nothing to wait for; the old table keeps painting.
    assert out["stop"] == "Old Stop"
    assert "live_refresh_s" not in out


def test_concurrent_feed_reads_download_once(
    gtfs: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def download(url: str, timeout: float) -> bytes:
        del timeout
        calls.append(url)
        time.sleep(0.2)
        return b"zip-bytes"

    monkeypatch.setattr(gtfs, "_download", download)
    results: list[bytes] = []
    threads = [
        threading.Thread(target=lambda: results.append(gtfs._feed_bytes(URL, tmp_path)))
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert calls == [URL]
    assert results == [b"zip-bytes"] * 3


def test_different_feeds_download_in_parallel(
    gtfs: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    both_in = threading.Barrier(2, timeout=5)

    def download(url: str, timeout: float) -> bytes:
        del timeout
        both_in.wait()  # only passes when both downloads are in flight together
        return url.encode()

    monkeypatch.setattr(gtfs, "_download", download)
    out: dict[str, bytes] = {}
    urls = ["https://a.example.test/a.zip", "https://b.example.test/b.zip"]
    threads = [
        threading.Thread(target=lambda u=u: out.__setitem__(u, gtfs._feed_bytes(u, tmp_path)))
        for u in urls
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert out == {u: u.encode() for u in urls}
