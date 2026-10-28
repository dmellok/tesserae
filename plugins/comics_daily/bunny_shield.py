"""Bunny Shield, the BunnyCDN anti-bot check in front of gocomics.com.

No network here: this module only recognises the challenge page, solves its
proof of work, and judges whether a saved session still looks usable. The
GoComics client in ``server.py`` does the requests around it.

How the challenge works (kept here so nobody has to relearn it):

* The challenge page arrives as HTTP 403, or as 200 with a ``cdn-challenge``
  header, and its body says "Establishing a secure connection" and carries
  ``data-pow="userkey#challenge#timestamp#signature"``. A 200 status alone
  proves nothing. The bare string "bunny-shield" proves nothing either:
  normal pages load ``/.bunny-shield/bd/bunnyprint.js``.
* The answer is the smallest ``i`` for which
  ``Argon2id(secret=challenge + str(i), salt=userkey, t=2, m=512 KiB, p=1,
  32 bytes)`` has ``DIFFICULTY`` leading zero bits, posted back to
  ``/.bunny-shield/verify-pow`` as ``data_pow#i``. Expect about 256 tries.
* The session cookie is named ``bunny_shield_id_<n>`` (once just
  ``bunny_shield``), so it is matched by prefix. Never rewrite its path or
  re-add it; the server sets it correctly.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Iterable, Mapping
from http.cookiejar import Cookie

# The difficulty and Argon2id cost the challenge script uses. Fixed by
# BunnyCDN, not sent in the page.
DIFFICULTY = 13
ARGON2_ITERATIONS = 2
ARGON2_MEMORY_KIB = 512
ARGON2_LANES = 1
ARGON2_LENGTH = 32
# About 256 tries are expected at difficulty 13 (a millisecond or two each);
# a cap far above that keeps a changed challenge from spinning for hours.
MAX_ITERATIONS = 20_000

COOKIE_PREFIX = "bunny_shield"
VERIFY_PATH = "/.bunny-shield/verify-pow"
RESPONSE_HEADER = "BunnyShield-Challenge-Response"


class SolverUnavailable(RuntimeError):
    """This Python's ``cryptography`` has no Argon2id (it arrived in 44).

    Imported lazily so an older install still loads the widget and fetches
    every other source; only a GoComics challenge needs the solver.
    """


def _argon2id() -> type:
    try:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    except ImportError as err:
        raise SolverUnavailable("cryptography 44 or newer is needed for Argon2id") from err
    return Argon2id


_DATA_POW_RE = re.compile(r"""data-pow\s*=\s*["']([^"']+)["']""")
_CHALLENGE_TEXT = "Establishing a secure connection"


def is_challenge(headers: Mapping[str, str] | None, body: str) -> bool:
    """True only for the real challenge page, whatever the status code."""
    if headers is not None and headers.get("cdn-challenge"):
        return True
    return _CHALLENGE_TEXT in body or bool(_DATA_POW_RE.search(body))


def find_data_pow(body: str) -> str | None:
    """The ``data-pow`` value of a challenge page, or None when it has none."""
    match = _DATA_POW_RE.search(body)
    if match is None:
        return None
    value = html.unescape(match.group(1)).strip()
    return value if value.count("#") >= 1 else None


def _meets_difficulty(digest: bytes, difficulty: int) -> bool:
    """Whether ``digest`` starts with ``difficulty`` zero bits.

    The challenge script tests the hex string: ``difficulty // 8`` leading
    "0" characters, then the next hex digit masked with
    ``0xff >> ((whole + 1) * 8 - difficulty)``. Kept in that form so it
    matches the browser bit for bit, odd as the mask is.
    """
    hexed = digest.hex()
    whole = difficulty // 8
    if not hexed.startswith("0" * whole):
        return False
    mask = 0xFF >> (((whole + 1) * 8) - difficulty)
    return (int(hexed[whole], 16) & mask) == 0


def solve(
    data_pow: str, *, difficulty: int = DIFFICULTY, max_iterations: int = MAX_ITERATIONS
) -> str | None:
    """Find the proof-of-work answer for ``data_pow``, or None within the cap.

    Raises ValueError for a value that cannot be a challenge (Argon2 wants a
    salt of at least 8 bytes), so a garbled page fails loudly, not slowly,
    and :class:`SolverUnavailable` when this Python can't do Argon2id.
    """
    parts = data_pow.split("#")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError("malformed data-pow")
    salt = parts[0].encode()
    challenge = parts[1]
    if len(salt) < 8:
        raise ValueError("data-pow user key is too short")
    argon2id = _argon2id()
    for i in range(max_iterations):
        kdf = argon2id(
            salt=salt,
            length=ARGON2_LENGTH,
            iterations=ARGON2_ITERATIONS,
            lanes=ARGON2_LANES,
            memory_cost=ARGON2_MEMORY_KIB,
        )
        if _meets_difficulty(kdf.derive(f"{challenge}{i}".encode()), difficulty):
            return str(i)
    return None


def challenge_response(data_pow: str, answer: str) -> str:
    """The value of the verify request's ``BunnyShield-Challenge-Response``."""
    return f"{data_pow}#{answer}"


def cookie_expiry(cookie: Cookie) -> int:
    """Unix expiry of a shield cookie, 0 when it has none (a session cookie).

    Older cookies carried the expiry inside the value (``key#sig#<unix>``);
    newer ones only have the ordinary Set-Cookie expiry, if any.
    """
    parts = (cookie.value or "").split("#")
    if len(parts) >= 3 and parts[2].strip().isdigit():
        return int(parts[2].strip())
    return int(cookie.expires) if cookie.expires else 0


def has_session(cookies: Iterable[Cookie], now: float | None = None) -> bool:
    """Whether a saved shield cookie is present and not known to have expired.

    A cookie without an expiry is trusted until a page answers with the
    challenge again; that is the only reliable signal for session cookies.
    """
    now = time.time() if now is None else now
    for cookie in cookies:
        if not cookie.name.startswith(COOKIE_PREFIX):
            continue
        expiry = cookie_expiry(cookie)
        if expiry == 0 or expiry > now:
            return True
    return False
