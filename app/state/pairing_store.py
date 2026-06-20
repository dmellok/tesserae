"""Pairing-code store for first-boot REST device registration.

A pairing code is a six-digit number the user generates in the
admin UI (Settings → Devices → Pair new device), notes down, and
flashes into the firmware. The firmware POSTs ``/api/v1/device/register``
carrying that code in ``X-Pairing-Code``; the server validates the code,
hands back a per-device access token, and marks the code consumed so
it can't be replayed.

Design constraints driving the implementation:

* **Single-use.** Each code maps to one successful registration, then
  is dropped. A stolen + replayed code can't register a second device.
* **Time-limited.** Codes expire ten minutes after issue (long enough
  for the user to flash the firmware, short enough that a forgotten
  code in someone's notes can't be redeemed weeks later).
* **In-memory only.** A restart wipes pending codes. That's fine, the
  user can just generate a new one. Persisting them across restart
  would invite the "I wrote this on a sticky note last month, why
  does it still work" footgun.
* **Constant-time compare.** Six digits is 20 bits of entropy, brute-
  forceable in seconds against an unrate-limited string-equality
  check. ``secrets.compare_digest`` neutralises the timing side-
  channel; rate-limiting (cap N failed attempts per minute) is the
  next step but deferred to the routing layer.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

_CODE_TTL_S = 600  # ten minutes
_CODE_LENGTH = 6


@dataclass(frozen=True)
class PairingCode:
    code: str
    issued_at: float
    expires_at: float
    note: str  # human-readable description ("for bedroom Pico"); admin UI only


class PairingStore:
    """Thread-safe in-memory store of pending pairing codes.

    The lock guards both the dict and the GC sweep. Reads + writes are
    fast (no I/O) so coarse locking is fine."""

    def __init__(self, ttl_s: int = _CODE_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._codes: dict[str, PairingCode] = {}
        self._lock = threading.Lock()

    def issue(self, *, note: str = "") -> PairingCode:
        """Mint a fresh code. Returns the new PairingCode; the caller
        shows the ``code`` field to the admin so they can paste it
        into firmware."""
        now = time.time()
        with self._lock:
            self._gc(now)
            for _ in range(64):
                code = self._generate_code()
                if code not in self._codes:
                    record = PairingCode(
                        code=code,
                        issued_at=now,
                        expires_at=now + self._ttl_s,
                        note=note,
                    )
                    self._codes[code] = record
                    return record
        # Astronomically unlikely (would need 10**6 simultaneous live
        # codes). Falling through here means we couldn't find a free
        # code in 64 attempts, which is "something is very wrong";
        # raise rather than reuse.
        raise RuntimeError("pairing store full, cannot mint fresh code")

    def consume(self, code: str) -> PairingCode | None:
        """Redeem a code. Returns the PairingCode if it was present and
        valid, ``None`` if not. Single-use: on success the code is
        removed from the store."""
        if not code or len(code) != _CODE_LENGTH:
            return None
        now = time.time()
        with self._lock:
            self._gc(now)
            # Constant-time compare against every live code so a
            # timing leak doesn't reveal which codes are live.
            matched: PairingCode | None = None
            for stored in self._codes.values():
                if secrets.compare_digest(stored.code, code):
                    matched = stored
                    # Don't break; finish the loop for constant time.
            if matched is None:
                return None
            self._codes.pop(matched.code, None)
            return matched

    def list_pending(self) -> list[PairingCode]:
        """Snapshot of currently-valid codes. Used by the admin UI to
        show "you have a pairing code waiting" so the user doesn't
        accidentally generate a second one. Returns a copy; safe to
        iterate after the lock is released."""
        now = time.time()
        with self._lock:
            self._gc(now)
            return list(self._codes.values())

    def revoke(self, code: str) -> bool:
        """Drop a pending code (user changed their mind). Returns True
        if the code was present, False otherwise."""
        with self._lock:
            return self._codes.pop(code, None) is not None

    def _gc(self, now: float) -> None:
        """Drop expired codes. Caller holds the lock."""
        expired = [k for k, v in self._codes.items() if v.expires_at <= now]
        for k in expired:
            self._codes.pop(k, None)

    @staticmethod
    def _generate_code() -> str:
        """Random six-digit string with leading zeros preserved."""
        return f"{secrets.randbelow(10**_CODE_LENGTH):0{_CODE_LENGTH}d}"
