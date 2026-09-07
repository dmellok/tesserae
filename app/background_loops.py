"""Registry of long-running background loops, so one call can stop them all.

Every loop that owns a daemon thread and exposes ``stop()`` registers itself
here when it starts (the scheduler and its watchdog, the relay-pairing poller,
the OpenDisplay telemetry poller, HA discovery's ticker). :func:`stop_all`
signals each of them; the test suite calls it after every test.

Why: an app built with ``create_app(testing=False)`` starts the scheduler, and
every transport rebuild used to start the two pollers with their threads on.
Nothing stopped them, so a pytest worker accumulated one full set of loops
per app-building test. At around a hundred leaked threads the worker
eventually wedged inside a ``Thread.start()``, which is the intermittent
hour-long CI stall the faulthandler dump finally named (2026-09-07).

The set holds weak references: a loop object is kept alive by its own running
thread and drops out on its own once that thread has exited, so stopping is
idempotent and nothing here pins a torn-down app in memory.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Protocol

logger = logging.getLogger(__name__)


class Stoppable(Protocol):
    def stop(self) -> None: ...


_LOOPS: weakref.WeakSet[Stoppable] = weakref.WeakSet()
_LOCK = threading.Lock()

# Thread names the loops above run under; used by the test-suite guard to
# confirm they are really gone after ``stop_all``.
LOOP_THREAD_NAMES: frozenset[str] = frozenset(
    {
        "tesserae-scheduler",
        "tesserae-scheduler-watchdog",
        "relay-pairing",
        "opendisplay-ha-telemetry",
        "ha-discovery-ticker",
    }
)


def track(loop: Stoppable) -> None:
    """Remember ``loop`` so :func:`stop_all` can reach it."""
    with _LOCK:
        _LOOPS.add(loop)


def stop_all() -> int:
    """Call ``stop()`` on every tracked loop. Returns how many were signalled.
    Failures are logged and skipped so one bad loop cannot shadow the rest."""
    with _LOCK:
        loops = list(_LOOPS)
        _LOOPS.clear()
    for loop in loops:
        try:
            loop.stop()
        except Exception:
            logger.exception("stopping background loop %r", loop)
    return len(loops)
