"""Async job records for the Companion API write routes.

A companion dashboard push or image push takes 5-15 seconds to render and
publish, longer than an iOS Share Extension survives, so the write routes
return ``202 accepted`` with a persisted job the client polls at
``GET /api/app/v1/jobs/{id}`` until it reaches a terminal state.

Two orthogonal axes, kept deliberately separate (discussion #147):

* **Lifecycle** (``status``): ``accepted`` -> ``running`` -> terminal
  ``succeeded`` / ``failed``. This is pure infrastructure: did the render
  and publish pipeline run to completion.
* **Business outcome** (``result.status`` on a *succeeded* job):
  ``published`` (the frame reached the panel) or ``quiet`` (every target
  was inside quiet hours, so nothing was sent). "Held for quiet hours" is
  a success, never a failure, so it can never be mistaken for one.

Records persist so a poll survives an app switch or a dropped connection,
and are swept after ``retention_seconds`` (24 h default, server-advertised
via the capability probe). Persistence is best-effort across restarts: a
job left non-terminal when the process died can't resume, so it's marked
failed on load rather than left dangling.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

JobKind = Literal["dashboard_push", "image_push"]
JobStatus = Literal["accepted", "running", "succeeded", "failed"]
ResultStatus = Literal["published", "quiet"]

_TERMINAL: frozenset[str] = frozenset({"succeeded", "failed"})


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_epoch(value: str) -> float:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return 0.0


@dataclass
class Job:
    id: str
    kind: JobKind
    status: JobStatus
    target_device_ids: list[str]
    created_at: str
    updated_at: str
    label: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def public_dict(self) -> dict[str, Any]:
        """The contract ``Job`` shape (wrapped in ``{"job": ...}`` by the
        route). ``result`` / ``error`` are always present as null when
        absent, matching the ``nullable`` fields in the schema."""
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "label": self.label,
            "target_device_ids": list(self.target_device_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }

    def _persist_dict(self) -> dict[str, Any]:
        return self.public_dict()

    @classmethod
    def _from_persist(cls, raw: dict[str, Any]) -> Job | None:
        try:
            return cls(
                id=str(raw["id"]),
                kind=raw["kind"],
                status=raw["status"],
                target_device_ids=[str(d) for d in raw.get("target_device_ids", [])],
                created_at=str(raw["created_at"]),
                updated_at=str(raw["updated_at"]),
                label=(str(raw["label"]) if raw.get("label") else None),
                result=raw.get("result") if isinstance(raw.get("result"), dict) else None,
                error=raw.get("error") if isinstance(raw.get("error"), dict) else None,
            )
        except (KeyError, TypeError):
            return None


class JobStore:
    """Thread-safe, file-backed store of companion job records."""

    def __init__(self, path: Path, *, retention_seconds: int = 86_400) -> None:
        self._path = path
        self._retention_s = retention_seconds
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._load()

    def create(self, *, kind: JobKind, target_device_ids: list[str], label: str | None) -> Job:
        now = _now_iso()
        job = Job(
            id=f"job_{secrets.token_hex(10)}",
            kind=kind,
            status="accepted",
            target_device_ids=list(target_device_ids),
            created_at=now,
            updated_at=now,
            label=label,
        )
        with self._lock:
            self._gc(time.time())
            self._jobs[job.id] = job
            self._flush()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._gc(time.time())
            return self._jobs.get(job_id)

    def mark_running(self, job_id: str) -> None:
        self._transition(job_id, status="running")

    def mark_succeeded(
        self,
        job_id: str,
        *,
        result_status: ResultStatus,
        device_ids: list[str],
        reason: str | None = None,
    ) -> None:
        self._transition(
            job_id,
            status="succeeded",
            result={
                "status": result_status,
                "reason": reason,
                "device_ids": list(device_ids),
            },
        )

    def mark_failed(self, job_id: str, *, code: str, message: str) -> None:
        self._transition(
            job_id,
            status="failed",
            error={"code": code, "message": message},
        )

    # -- internals ---------------------------------------------------------

    def _transition(
        self,
        job_id: str,
        *,
        status: JobStatus,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            job.updated_at = _now_iso()
            if result is not None:
                job.result = result
            if error is not None:
                job.error = error
            self._flush()

    def _gc(self, now: float) -> None:
        """Drop terminal jobs older than the retention window. Caller holds
        the lock. Non-terminal jobs are never swept, they're still active."""
        stale = [
            jid
            for jid, job in self._jobs.items()
            if job.terminal and (now - _iso_to_epoch(job.updated_at)) > self._retention_s
        ]
        for jid in stale:
            self._jobs.pop(jid, None)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = raw.get("jobs") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            return
        dirty = False
        for item in entries:
            if not isinstance(item, dict):
                continue
            job = Job._from_persist(item)
            if job is None:
                continue
            # A job left mid-flight when the process died can't resume; a
            # panel poll should see a clear failure, not a job wedged in
            # "running" forever.
            if not job.terminal:
                job.status = "failed"
                job.error = {
                    "code": "server_restarted",
                    "message": "The server restarted before the job finished.",
                }
                job.updated_at = _now_iso()
                dirty = True
            self._jobs[job.id] = job
        if dirty:
            with self._lock:
                self._flush()

    def _flush(self) -> None:
        """Whole-file atomic rewrite. Caller holds the lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": [j._persist_dict() for j in self._jobs.values()]}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)
