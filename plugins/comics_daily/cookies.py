"""GoComics cookies pasted on the Comics page, the manual way past Bunny Shield.

When the built-in browser check (``bunny_shield.py``) can't get a session,
the operator can paste the cookies of a browser that has one. Three shapes
are read, as browser extensions export them:

* Netscape ``cookies.txt`` (Get cookies.txt Locally's "Copy All"): seven
  tab-separated columns, ``#HttpOnly_`` before the domain of some lines.
* A JSON list of ``{"name", "value", "domain", "expirationDate"}`` objects
  (Cookie-Editor, EditThisCookie), or an object holding one as ``cookies``.
* A ``Cookie`` header: ``name=value; name=value``, with or without the
  ``Cookie:`` in front, on one line or several.

Only cookies for gocomics.com are kept (a header has no domains, so all of
it is), names and values are checked so nothing can break out of a header,
and the paste must hold the shield cookie (``bunny_shield...``), without
which it can't help. Nothing here keeps or logs a value: ``server.py``
stores the header as an encrypted setting and never shows it again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.cookiejar import Cookie

SHIELD_PREFIX = "bunny_shield"
MAX_PASTE_CHARS = 20_000
MAX_COOKIES = 60
MAX_VALUE_CHARS = 4096
DOMAIN = "www.gocomics.com"

# RFC 6265: a cookie name is a token; a value is printable ASCII without
# space, double quote, comma, semicolon or backslash.
_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}$")
_VALUE_RE = re.compile(r"^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*$")


class PasteError(ValueError):
    """The paste can't be used; the message says why, for the page."""


@dataclass(frozen=True)
class Pasted:
    """The cookies read from a paste, in order, and when the shield cookie
    expires (Unix time, 0 when unknown)."""

    pairs: tuple[tuple[str, str], ...]
    expires_at: int

    def header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.pairs)


def _gocomics(domain: str) -> bool:
    host = domain.strip().lstrip(".").lower()
    return host == "gocomics.com" or host.endswith(".gocomics.com")


def _from_json(data: object) -> list[tuple[str, str, int]]:
    items = data.get("cookies") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise PasteError("That JSON isn't a list of cookies.")
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "")
        if domain and not _gocomics(domain):
            continue
        expiry = item.get("expirationDate") or item.get("expires") or 0
        try:
            expires = int(float(expiry))
        except (TypeError, ValueError):
            expires = 0
        out.append((str(item.get("name") or ""), str(item.get("value") or ""), expires))
    return out


def _from_netscape(lines: list[str]) -> list[tuple[str, str, int]] | None:
    """Rows of a cookies.txt; None when the text isn't one."""
    rows, seen = [], False
    for raw in lines:
        line = raw.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 7:
            return None if not seen else rows
        seen = True
        if not _gocomics(cols[0]):
            continue
        try:
            expires = int(cols[4].strip() or 0)
        except ValueError:
            expires = 0
        rows.append((cols[5].strip(), cols[6].strip(), expires))
    return rows if seen else None


def _from_header(text: str) -> list[tuple[str, str, int]]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("cookie:"):
            line = line[len("cookie:") :]
        for part in line.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep:
                rows.append((name.strip(), value.strip().strip('"'), 0))
            elif name.strip():
                raise PasteError(f"“{name.strip()[:30]}” isn't a name=value cookie.")
    return rows


def parse_paste(text: str) -> Pasted:
    """The cookies in ``text``, checked. Raises PasteError for anything that
    can't be used, worded for the page."""
    text = (text or "").strip()
    if not text:
        raise PasteError("Paste the cookies first.")
    if len(text) > MAX_PASTE_CHARS:
        raise PasteError("That's far more than a set of cookies; paste only gocomics.com's.")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError as err:
            raise PasteError("That looks like JSON but doesn't read as JSON.") from err
        rows = _from_json(data)
    else:
        rows = _from_netscape(text.splitlines())
        if rows is None:
            rows = _from_header(text)
    pairs: dict[str, str] = {}
    expires_at = 0
    for name, value, expires in rows:
        if not _NAME_RE.match(name):
            raise PasteError(f"“{name[:30]}” isn't a cookie name.")
        if len(value) > MAX_VALUE_CHARS or not _VALUE_RE.match(value):
            raise PasteError(f"The value of {name} has characters a cookie can't hold.")
        pairs[name] = value
        if name.startswith(SHIELD_PREFIX) and not expires_at:
            parts = value.split("#")
            if len(parts) >= 3 and parts[2].strip().isdigit():
                expires_at = int(parts[2].strip())
            elif expires > 0:
                expires_at = expires
    if not pairs:
        raise PasteError("No gocomics.com cookies in that.")
    if len(pairs) > MAX_COOKIES:
        raise PasteError(f"That's {len(pairs)} cookies; gocomics.com sets far fewer.")
    if not any(name.startswith(SHIELD_PREFIX) for name in pairs):
        raise PasteError(
            "No bunny_shield cookie in that: copy the cookies after gocomics.com has "
            "loaded a strip in your browser."
        )
    return Pasted(tuple(pairs.items()), expires_at)


def from_header(header: str) -> Pasted | None:
    """The stored header back as cookies; None when it is empty or damaged."""
    try:
        return parse_paste(header)
    except PasteError:
        return None


def jar_cookies(pasted: Pasted) -> list[Cookie]:
    """The cookies to set in the GoComics session, as host cookies of
    www.gocomics.com, sent over https only."""
    return [
        Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=DOMAIN,
            domain_specified=False,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=pasted.expires_at or None,
            discard=not pasted.expires_at,
            comment=None,
            comment_url=None,
            rest={},
        )
        for name, value in pasted.pairs
    ]
