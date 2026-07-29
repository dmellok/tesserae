"""Background executor for Companion API write jobs.

The write routes enqueue a unit of work here and return ``202`` immediately
with a persisted :class:`~app.state.job_store.Job` the client polls. This
runner advances that job through ``running`` to a terminal state on a
worker thread, inside a Flask app context so the work can reach
``PushManager`` and the stores via ``current_app``.

The work callable returns a :class:`JobOutcome` describing the business
result (published / quiet) or a failure; the runner maps it onto the job
record. Any unexpected exception becomes an ``internal_error`` failure so a
poll never hangs on a wedged job.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.state.job_store import JobStore, ResultStatus

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobOutcome:
    """Result of a job's work callable. Either a success (``ok=True`` with a
    business ``result_status``) or a failure (``ok=False`` with a code +
    message)."""

    ok: bool
    result_status: ResultStatus = "published"
    device_ids: tuple[str, ...] = ()
    history_event_ids: tuple[str, ...] | None = None
    reason: str | None = None
    code: str = ""
    message: str = ""

    @classmethod
    def published(
        cls,
        device_ids: list[str],
        *,
        history_event_ids: list[str] | None = None,
    ) -> JobOutcome:
        return cls(
            ok=True,
            result_status="published",
            device_ids=tuple(device_ids),
            history_event_ids=(
                tuple(dict.fromkeys(history_event_ids)) if history_event_ids is not None else None
            ),
        )

    @classmethod
    def quiet(cls, device_ids: list[str], reason: str) -> JobOutcome:
        return cls(ok=True, result_status="quiet", device_ids=tuple(device_ids), reason=reason)

    @classmethod
    def failed(cls, code: str, message: str) -> JobOutcome:
        return cls(ok=False, code=code, message=message)


class CompanionJobs:
    """Owns a small thread pool that runs companion job work callables."""

    def __init__(self, app: Flask, job_store: JobStore, *, max_workers: int = 2) -> None:
        self._app = app
        self._jobs = job_store
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="companion-job"
        )

    def enqueue(self, job_id: str, work: Callable[[], JobOutcome]) -> None:
        """Schedule ``work`` for a persisted job. Returns immediately; the
        job advances to a terminal state on a worker thread."""
        self._executor.submit(self._run, job_id, work)

    def _run(self, job_id: str, work: Callable[[], JobOutcome]) -> None:
        self._jobs.mark_running(job_id)
        try:
            with self._app.app_context():
                outcome = work()
        except Exception as err:
            logger.exception("companion job %s crashed", job_id)
            self._jobs.mark_failed(job_id, code="internal_error", message=str(err))
            return
        if outcome.ok:
            self._jobs.mark_succeeded(
                job_id,
                result_status=outcome.result_status,
                device_ids=list(outcome.device_ids),
                reason=outcome.reason,
                history_event_ids=(
                    list(outcome.history_event_ids)
                    if outcome.history_event_ids is not None
                    else None
                ),
            )
        else:
            self._jobs.mark_failed(job_id, code=outcome.code, message=outcome.message)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
