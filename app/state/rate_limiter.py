"""Per-client rate limiter for the REST register endpoint.

A 6-digit pairing code has 20 bits of entropy (~1 million values). An
unrate-limited attacker on the LAN could try thousands of codes per
second and crack a random code in minutes. Mitigation: cap *failed*
registration attempts per client IP per sliding window. Successful
registrations don't count against the cap (a successful attempt also
burns the code, so the attacker can't grind it for more credits).

The limiter is in-memory only; a restart wipes counters. Acceptable
because the rate-limit window is short (seconds) and a fresh start is
the equivalent of waiting out the window naturally. Persisting would
add disk I/O on every register call for no security gain.

Sweep-on-access: we drop expired entries when checking, so the dict
stays bounded by "number of distinct client IPs hammering us in the
last window," not by lifetime traffic.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    """Decision returned by ``RateLimiter.check_and_consume``.

    * ``allowed``: True when the caller is under the cap.
    * ``retry_after_s``: when ``allowed`` is False, how many seconds
      until the oldest counted attempt ages out of the window. The
      router uses this in the ``Retry-After`` HTTP header so the
      client knows when it's safe to try again.
    * ``remaining``: how many attempts are still available within the
      window AFTER consuming this one. Surfaced for debugging /
      logging; the route doesn't need it for the response.
    """

    allowed: bool
    retry_after_s: int
    remaining: int


class RateLimiter:
    """Sliding-window rate limiter keyed by an opaque client id
    (typically the client IP).

    ``max_attempts`` failures per ``window_s`` seconds. Successful
    attempts are recorded via ``record_success`` and drop the
    accumulated failure count for that client (they're rewarded for
    proving they have a real pairing code)."""

    def __init__(self, *, max_attempts: int = 10, window_s: int = 60) -> None:
        self._max = max_attempts
        self._window_s = window_s
        # client_id → deque of unix timestamps of recent FAILED attempts.
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_consume(self, client_id: str) -> RateLimitResult:
        """Decide whether ``client_id`` may try a new attempt.

        Pre-consumes the slot: if allowed, records the attempt's
        timestamp so the next call sees one fewer slot available. The
        caller is expected to follow up with ``record_success`` on a
        successful attempt to release the burned slot back."""
        now = time.time()
        with self._lock:
            recent = self._failures[client_id]
            self._sweep(recent, now)
            if len(recent) >= self._max:
                oldest = recent[0]
                retry_after = max(1, int((oldest + self._window_s) - now))
                return RateLimitResult(allowed=False, retry_after_s=retry_after, remaining=0)
            recent.append(now)
            return RateLimitResult(
                allowed=True,
                retry_after_s=0,
                remaining=self._max - len(recent),
            )

    def record_success(self, client_id: str) -> None:
        """A successful attempt releases this client's accumulated
        failures. Without this, a user who successfully pairs five
        devices in a row would still be rate-limited; that's bad UX
        and not a security gain (the attacker is BURNING codes, not
        guessing them)."""
        with self._lock:
            self._failures.pop(client_id, None)

    def _sweep(self, dq: deque[float], now: float) -> None:
        """Drop timestamps older than the window. Caller holds the
        lock."""
        cutoff = now - self._window_s
        while dq and dq[0] < cutoff:
            dq.popleft()
