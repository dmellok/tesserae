"""Small text helpers shared by the feed reader, the summariser and the painters."""

from __future__ import annotations

import html
import re

# gpt-oss and some feeds emit hyphens and spaces the panel fonts draw as
# boxes. En and em dashes render fine, so they stay.
_ODD_CHARACTERS = str.maketrans(
    {
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "-",  # figure dash
        "―": "—",  # horizontal bar -> em dash
        "−": "-",  # minus sign
        " ": " ",  # no-break space
        " ": " ",  # narrow no-break space
        " ": " ",  # figure space
        " ": " ",  # thin space
        " ": " ",  # hair space
        "​": None,  # zero-width space
        "⁠": None,  # word joiner
        "﻿": None,  # BOM / zero-width no-break space
    }
)
_RUNS_OF_BLANKS = re.compile(r"[ \t]{2,}")
_TAG = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"\s+")
# hnrss puts a link dump in every description; it is not story text.
_HNRSS_BOILERPLATE = re.compile(
    r"\b(?:Article|Comments) URL:\s*\S+|\bPoints:\s*\d+|#\s*Comments:\s*\d+", re.IGNORECASE
)


def normalise(text: str) -> str:
    """Replace characters the panel renders badly with plain equivalents and
    collapse runs of spaces. Newlines are kept."""
    return _RUNS_OF_BLANKS.sub(" ", text.translate(_ODD_CHARACTERS)).strip()


def squash(text: str) -> str:
    """All whitespace runs to one space."""
    return _WHITESPACE.sub(" ", text).strip()


def strip_markup(fragment: str) -> str:
    """Plain text of an HTML fragment. Tags become spaces so adjacent
    paragraphs do not glue together; entities are decoded twice because
    feeds escape markup as often as they CDATA-wrap it."""
    text = html.unescape(_TAG.sub(" ", fragment))
    return squash(html.unescape(_TAG.sub(" ", text)))


def clean_description(fragment: str) -> str:
    """An RSS description as plain story text, minus hnrss's link dump."""
    return squash(_HNRSS_BOILERPLATE.sub(" ", strip_markup(fragment)))


def truncate_bytes(text: str, limit: int) -> str:
    """At most ``limit`` UTF-8 bytes of ``text``, never splitting a character."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", errors="ignore")


def first_words(text: str, count: int) -> str:
    """The first ``count`` words of ``text``."""
    return " ".join(text.split()[: max(1, count)])


_URL_SECRET = re.compile(
    r"([?&](?:key|api_key|apikey|token|access_token)=)[^&\s'\"]+", re.IGNORECASE
)
_BEARER = re.compile(r"(Bearer\s+)[^\s'\"]+", re.IGNORECASE)


def redact(line: str, secrets: tuple[str, ...] | list[str]) -> str:
    """``line`` with every configured secret, any key in a URL query and any
    Bearer token replaced by ``***``. For everything that is logged or saved."""
    for secret in sorted((s for s in secrets if len(s) >= 4), key=len, reverse=True):
        line = line.replace(secret, "***")
    return _BEARER.sub(r"\1***", _URL_SECRET.sub(r"\1***", line))


def is_clean_key(key: str) -> bool:
    """True when ``key`` has no whitespace or control characters inside it.
    A pasted key with a newline would otherwise end up in an HTTP header,
    and http.client quotes the header back in its error message."""
    return all(c.isprintable() and not c.isspace() for c in key)
