"""Durable painting charges and recovery of failed jobs."""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from app import artwork, genai


@pytest.mark.parametrize("write", ["budget", "ledger", "trim"])
def test_payment_writes_are_synced(tmp_path, monkeypatch, write):
    synced = []
    fsync = os.fsync

    def sync(fd):
        assert os.fstat(fd).st_size > 0, "flush before syncing"
        synced.append(fd)
        fsync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    if write == "budget":
        c = artwork.Collection("test", "paintings", make=lambda job: (b"", {}), label="Test")
        c._budget_add(tmp_path, 2)
        assert c.budget_used(tmp_path) == 2
    else:
        ledger = genai.Ledger(tmp_path)
        ledger._kept_at = time.time()
        ledger.append("test", "paintings", "nano_banana", 2)
        if write == "trim":
            synced.clear()
            ledger._trim(time.time())
        assert ledger.today_cents() == 2
    assert synced, "budget and ledger writes must reach fsync"


def test_two_processes_cannot_both_charge_at_the_ceiling(tmp_path):
    script = """
import sys
import time
from pathlib import Path
from app import artwork, genai

folder, name = Path(sys.argv[1]), sys.argv[2]
ledger = genai.Ledger(folder)
ledger._kept_at = time.time()
artwork.host_configure(ledger=ledger, ceilings=lambda: (2, artwork.UNLIMITED))
original = artwork.ceiling_spent

def ceiling():
    result = original()
    (folder / ('checked-' + name)).touch()
    end = time.monotonic() + 1
    while len(list(folder.glob('checked-*'))) < 2 and time.monotonic() < end:
        time.sleep(0.01)
    return result

artwork.ceiling_spent = ceiling
(folder / ('ready-' + name)).touch()
while not (folder / 'go').exists():
    time.sleep(0.01)
try:
    artwork.charge('test', name, 2, 'nano_banana', lambda: None)
    print('paid')
except artwork.ArtworkError:
    print('capped')
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), str(n)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for n in range(2)
    ]
    try:
        end = time.monotonic() + 15
        while len(list(tmp_path.glob("ready-*"))) < 2 and time.monotonic() < end:
            time.sleep(0.01)
        assert len(list(tmp_path.glob("ready-*"))) == 2
        (tmp_path / "go").touch()
        results = [p.communicate(timeout=15) for p in processes]
        for process, (_out, err) in zip(processes, results, strict=True):
            assert process.returncode == 0, err
        assert sorted(out.strip() for out, _err in results) == ["capped", "paid"]
        assert genai.Ledger(tmp_path).today_cents() == 2
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()


def test_charge_and_refund_lock_without_fcntl(tmp_path, monkeypatch):
    calls = []
    module = SimpleNamespace(LK_LOCK=1, LK_UNLCK=2)

    def locking(fd, mode, size):
        assert size == 1
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        calls.append(mode)

    module.locking = locking
    original_import = builtins.__import__

    def windows_import(name, *args, **kwargs):
        if name == "fcntl":
            raise ModuleNotFoundError("no fcntl on Windows")
        if name == "msvcrt":
            return module
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", windows_import)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(artwork._HOST, "ledger", genai.Ledger(tmp_path))
    monkeypatch.setitem(artwork._HOST, "ceilings", lambda: (2, 2))

    def debit():
        assert calls == [module.LK_LOCK]

    artwork.charge("test", "paintings", 2, "nano_banana", debit)
    assert calls == [module.LK_LOCK, module.LK_UNLCK]
    with pytest.raises(artwork.ArtworkError):
        artwork.charge("test", "paintings", 2, "nano_banana", debit)
    assert calls == [1, 2, 1, 2], "a refused charge releases the lock"
    artwork.refund("test", "paintings", 2, "nano_banana", lambda: None)
    assert calls == [1, 2, 1, 2, 1, 2]
    assert artwork._HOST["ledger"].today_cents() == 0


def test_failed_job_keeps_its_payload_for_repaint(tmp_path):
    seen = []

    def make(job):
        seen.append(job.payload)
        raise artwork.ArtworkError("reference unavailable")

    c = artwork.Collection("test", "repaint", make=make, label="Test")
    payload = {"prompt": "a robin", "style": "watercolour"}
    job = c.job("robin", payload, "Robin", tmp_path, "k")
    c._run_one(job, 100)
    error = json.loads((c.folder(tmp_path) / "robin.err").read_text())
    assert error["payload"] == payload
    # A fresh collection has only the error card: no picture, held job or dashboard.
    c = artwork.Collection("test-restarted", "repaint", make=make, label="Test")
    assert c.repaint("robin", tmp_path, "k", {})
    end = time.monotonic() + 5
    while len(seen) < 2 and time.monotonic() < end:
        time.sleep(0.01)
    assert seen == [payload, payload]
    end = time.monotonic() + 5
    while c._q.workers and time.monotonic() < end:
        time.sleep(0.01)
    assert not c._q.workers
