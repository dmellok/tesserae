"""Idempotency-Key handling for the Companion API write routes.

Both write routes (dashboard push, image push) require an
``Idempotency-Key`` header. A Share Extension or a Shortcut that resubmits
after an uncertain response (dropped connection, app suspended mid-flight)
must resolve to the *same* job, not fire a second render.

The contract: reusing a key with the same credential, method, path, and
payload returns the original job; changing the payload under the same key
is a conflict (``409 idempotency_conflict``). The key is scoped to the
credential, so two paired phones can pick the same key without colliding.

Each record stores a ``fingerprint`` (a hash over method + path + payload)
so a replay can be told apart from a conflict, plus the ``job_id`` the key
first minted. Records are swept after ``retention_seconds`` (24 h default,
server-advertised), matching what most payment APIs settle on.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def fingerprint(method: str, path: str, payload: bytes) -> str:
    """Stable hash over the request identity. Same method + path + payload
    -> same fingerprint -> a replay; a different payload under the same key
    -> a conflict."""
    h = hashlib.sha256()
    h.update(method.encode("utf-8"))
    h.update(b"\x00")
    h.update(path.encode("utf-8"))
    h.update(b"\x00")
    h.update(payload)
    return h.hexdigest()


@dataclass
class IdempotencyRecord:
    fingerprint: str
    job_id: str
    created_at: float


class IdempotencyStore:
    """Thread-safe, file-backed idempotency ledger keyed by
    ``(token_id, key)``."""

    def __init__(self, path: Path, *, retention_seconds: int = 86_400) -> None:
        self._path = path
        self._retention_s = retention_seconds
        self._lock = threading.Lock()
        # (token_id, key) -> record
        self._records: dict[tuple[str, str], IdempotencyRecord] = {}
        self._load()

    def reserve(
        self,
        *,
        token_id: str,
        key: str,
        fingerprint: str,
        make_job: Callable[[], str],
    ) -> tuple[str, bool, bool]:
        """Atomically resolve an idempotency key. Returns
        ``(job_id, created, conflict)``:

        * Fresh key: ``make_job()`` mints a new job; returns
          ``(job_id, True, False)``.
        * Same key + same fingerprint: replay; returns the original
          ``(job_id, False, False)`` without minting anything.
        * Same key + different fingerprint: ``("", False, True)`` and the
          caller returns ``409 idempotency_conflict``.
        """
        now = time.time()
        with self._lock:
            self._gc(now)
            existing = self._records.get((token_id, key))
            if existing is not None:
                if existing.fingerprint == fingerprint:
                    return existing.job_id, False, False
                return "", False, True
            job_id = make_job()
            self._records[(token_id, key)] = IdempotencyRecord(
                fingerprint=fingerprint, job_id=job_id, created_at=now
            )
            self._flush()
            return job_id, True, False

    # -- internals ---------------------------------------------------------

    def _gc(self, now: float) -> None:
        stale = [
            k for k, rec in self._records.items() if (now - rec.created_at) > self._retention_s
        ]
        for k in stale:
            self._records.pop(k, None)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = raw.get("records") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                token_id = str(item["token_id"])
                key = str(item["key"])
                self._records[(token_id, key)] = IdempotencyRecord(
                    fingerprint=str(item["fingerprint"]),
                    job_id=str(item["job_id"]),
                    created_at=float(item["created_at"]),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "records": [
                {
                    "token_id": token_id,
                    "key": key,
                    "fingerprint": rec.fingerprint,
                    "job_id": rec.job_id,
                    "created_at": rec.created_at,
                }
                for (token_id, key), rec in self._records.items()
            ]
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)
