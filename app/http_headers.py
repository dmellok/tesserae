"""Parse and validate operator-supplied HTTP request headers (#234).

Tesserae lets an operator attach headers to an outbound webpage render, so an
internal dashboard behind a bearer token or an API key can be screenshotted
without making it public. That is a credential travelling through several
layers, so the rules live here rather than at each call site: one parser, one
reject list, one redaction helper, and every surface (Send → Webpage, the
Companion route, the webpage widget) shares them.

Wire shape is a JSON object, matching ``rest_service``'s existing ``headers``
cell option rather than inventing a second convention for the same data:

    {"Authorization": "Bearer …", "X-API-Key": "…"}

``User-Agent`` is accepted but deliberately does NOT stay a header. Chromium
exposes the context user-agent to page JavaScript as ``navigator.userAgent``,
and a site that gates on the client-side value would still see the headless
default if we only rewrote the request header. :func:`split_user_agent` pulls
it out so callers can hand it to ``browser.new_context(user_agent=…)`` and
both halves agree.

What this module refuses, and why:

* **Transport and framing headers** (``Host``, ``Content-Length``,
  ``Connection``, ``Transfer-Encoding``, ``Upgrade``, ``Keep-Alive``, ``TE``,
  ``Trailer``, ``Expect``). Chromium owns these; overriding them either
  silently does nothing or corrupts the request.
* **``Cookie`` / ``Set-Cookie``.** Cookie and session handling is explicitly
  out of scope for #234, and a hand-set cookie header fights the browser's own
  jar in ways that depend on load order. Refusing is more honest than
  half-supporting it.
* **``Proxy-*`` and ``Sec-*``.** Hop-by-hop and browser-managed respectively.
* **Anything with a control character.** A newline in a value is header
  injection: it would let one configured header smuggle in others, or a
  request body. Chromium would probably reject it, but a validator that
  relies on the layer below to catch its mistakes is not a validator.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

# Bounds. Generous enough for a signed JWT (which is what most of these will
# be) and small enough that a pasted file can't become a render payload.
MAX_HEADERS = 20
MAX_NAME_LENGTH = 64
MAX_VALUE_LENGTH = 4096
MAX_RAW_LENGTH = 8192

# RFC 7230 token: what a header field-name is allowed to contain.
_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# Values may hold any visible ASCII plus space and tab. Notably NOT \r or \n.
_VALUE_RE = re.compile(r"^[\t\x20-\x7e]*$")

_FORBIDDEN_NAMES = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "upgrade",
        "keep-alive",
        "te",
        "trailer",
        "expect",
        "cookie",
        "set-cookie",
    }
)

_FORBIDDEN_PREFIXES = ("proxy-", "sec-")

USER_AGENT_NAME = "user-agent"


class HeaderError(ValueError):
    """An operator-supplied header map that can't be used.

    Carries a message written for the person who typed it, because every
    caller surfaces it straight into a form error or an API response."""


def parse_header_map(raw: str | None) -> dict[str, str]:
    """Parse a JSON-object header string into a validated map.

    Empty / absent input returns an empty dict, which every caller treats as
    "no headers configured" and must leave byte-identical to the pre-#234
    behaviour. Raises :class:`HeaderError` with a user-facing message on
    anything else that isn't usable.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    if len(text) > MAX_RAW_LENGTH:
        raise HeaderError(f"headers must be under {MAX_RAW_LENGTH} characters")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as err:
        raise HeaderError("headers must be a JSON object") from err
    if not isinstance(parsed, dict):
        raise HeaderError("headers must be a JSON object")
    if len(parsed) > MAX_HEADERS:
        raise HeaderError(f"at most {MAX_HEADERS} headers")
    return validate_header_map(parsed)


def validate_header_map(candidate: Mapping[str, object]) -> dict[str, str]:
    """Validate an already-decoded mapping. Split out from
    :func:`parse_header_map` so a caller holding a dict (a JSON API body
    rather than a form textarea) validates through the same rules."""
    out: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, raw_value in candidate.items():
        if not isinstance(raw_name, str):
            raise HeaderError("header names must be strings")
        name = raw_name.strip()
        if not name:
            raise HeaderError("header names cannot be empty")
        if len(name) > MAX_NAME_LENGTH:
            raise HeaderError(f"header name {name[:20]!r} is too long")
        if not _NAME_RE.match(name):
            raise HeaderError(f"{name!r} is not a valid header name")
        lowered = name.lower()
        if lowered in _FORBIDDEN_NAMES or lowered.startswith(_FORBIDDEN_PREFIXES):
            raise HeaderError(f"{name} is managed by the browser and cannot be set")
        if lowered in seen:
            raise HeaderError(f"{name} is set more than once")
        seen.add(lowered)
        # Numbers and booleans are a common JSON slip on a value that has to
        # go on the wire as text; coerce rather than reject, but refuse the
        # containers, where the intent is genuinely unclear.
        if isinstance(raw_value, (dict, list)):
            raise HeaderError(f"the value for {name} must be a string")
        value = str(raw_value).strip() if raw_value is not None else ""
        if len(value) > MAX_VALUE_LENGTH:
            raise HeaderError(f"the value for {name} is too long")
        if not _VALUE_RE.match(value):
            raise HeaderError(f"the value for {name} contains an unsupported character")
        out[name] = value
    return out


def split_user_agent(headers: Mapping[str, str]) -> tuple[dict[str, str], str | None]:
    """Separate ``User-Agent`` from the rest of the map.

    Returns ``(headers_without_ua, user_agent_or_None)``. The caller applies
    the second half via the browser context so ``navigator.userAgent`` agrees
    with what goes on the wire; see the module docstring.
    """
    rest: dict[str, str] = {}
    user_agent: str | None = None
    for name, value in headers.items():
        if name.lower() == USER_AGENT_NAME:
            user_agent = value or None
        else:
            rest[name] = value
    return rest, user_agent


def header_summary(headers: Mapping[str, str] | None) -> str:
    """A log-safe description: how many, and which names.

    Names are not the secret and knowing them is most of what a "why is this
    still 401" investigation needs. Values never appear here, and callers must
    not log the map itself. ``""`` when nothing is configured, so a caller can
    omit the field entirely rather than recording "0 headers"."""
    if not headers:
        return ""
    names = ", ".join(sorted(headers))
    count = len(headers)
    return f"{count} header{'s' if count != 1 else ''} ({names})"
