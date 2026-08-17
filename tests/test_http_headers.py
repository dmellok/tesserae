"""Operator-supplied request headers: parsing, validation, redaction (#234).

The parser is the only thing standing between a textarea and a credential on
the wire, so the cases below are the ones where a permissive reading would be
a security bug rather than a usability annoyance: header injection via a
newline, transport headers Chromium owns, and values that quietly stringify
into something unintended.
"""

from __future__ import annotations

import json

import pytest

from app.http_headers import (
    MAX_HEADERS,
    MAX_RAW_LENGTH,
    MAX_VALUE_LENGTH,
    HeaderError,
    header_summary,
    parse_header_map,
    split_user_agent,
    validate_header_map,
)

# -- the empty case ------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "\n"])
def test_absent_input_is_no_headers(raw: str | None) -> None:
    """Every caller treats {} as "not configured" and must then render down
    the pre-#234 path with no request interception at all."""
    assert parse_header_map(raw) == {}


# -- accepted shapes ----------------------------------------------------


def test_a_json_object_parses() -> None:
    assert parse_header_map('{"Authorization": "Bearer abc", "X-API-Key": "k"}') == {
        "Authorization": "Bearer abc",
        "X-API-Key": "k",
    }


def test_names_and_values_are_trimmed() -> None:
    assert parse_header_map('{"  X-Token  ": "  v  "}') == {"X-Token": "v"}


def test_numbers_and_booleans_coerce_to_text() -> None:
    """A JSON slip on a value that has to go on the wire as text. Coercing is
    friendlier than refusing and can't produce anything unsafe."""
    assert parse_header_map('{"X-Count": 3, "X-On": true}') == {
        "X-Count": "3",
        "X-On": "True",
    }


def test_case_is_preserved_as_typed() -> None:
    """HTTP header names are case-insensitive, but echoing back what the
    operator typed makes the editor and the log line recognisable."""
    assert "X-Api-Key" in parse_header_map('{"X-Api-Key": "k"}')


# -- rejected shapes ----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2]",
        '"a string"',
        "42",
        "null",
    ],
)
def test_non_objects_are_refused(raw: str) -> None:
    with pytest.raises(HeaderError, match="JSON object"):
        parse_header_map(raw)


@pytest.mark.parametrize(
    "name",
    [
        "Host",
        "Content-Length",
        "Connection",
        "Transfer-Encoding",
        "Upgrade",
        "Keep-Alive",
        "TE",
        "Trailer",
        "Expect",
        "Cookie",
        "Set-Cookie",
        "Proxy-Authorization",
        "Sec-Fetch-Mode",
        # Case must not be a way around the list.
        "hOsT",
        "content-length",
    ],
)
def test_browser_managed_headers_are_refused(name: str) -> None:
    with pytest.raises(HeaderError, match="managed by the browser"):
        parse_header_map(json.dumps({name: "x"}))


@pytest.mark.parametrize("value", ["a\r\nX-Evil: yes", "a\nb", "a\rb", "a\x00b", "a\x7fb"])
def test_control_characters_in_a_value_are_refused(value: str) -> None:
    """Header injection. A newline in a value would let one configured header
    smuggle in others, or a body. Chromium would probably refuse it; a
    validator that leans on the layer below is not a validator."""
    with pytest.raises(HeaderError, match="unsupported character"):
        parse_header_map(json.dumps({"X-Test": value}))


@pytest.mark.parametrize("name", ["X Token", "X:Token", "X\nToken", "", "   ", "Xé"])
def test_invalid_header_names_are_refused(name: str) -> None:
    with pytest.raises(HeaderError):
        parse_header_map(json.dumps({name: "v"}))


def test_container_values_are_refused() -> None:
    with pytest.raises(HeaderError, match="must be a string"):
        parse_header_map('{"X-Test": {"nested": 1}}')


def test_duplicate_names_differing_only_in_case_are_refused() -> None:
    with pytest.raises(HeaderError, match="more than once"):
        parse_header_map('{"X-Token": "a", "x-token": "b"}')


def test_bounds_are_enforced() -> None:
    too_many = {f"X-H{i}": "v" for i in range(MAX_HEADERS + 1)}
    with pytest.raises(HeaderError, match=f"at most {MAX_HEADERS}"):
        parse_header_map(json.dumps(too_many))

    with pytest.raises(HeaderError, match="too long"):
        parse_header_map(json.dumps({"X-Token": "v" * (MAX_VALUE_LENGTH + 1)}))

    with pytest.raises(HeaderError, match=f"under {MAX_RAW_LENGTH}"):
        parse_header_map('{"X-Token": "' + "v" * MAX_RAW_LENGTH + '"}')


def test_validate_header_map_shares_the_rules_with_the_parser() -> None:
    """The Companion route holds a decoded dict rather than a textarea string;
    it must not get a laxer path."""
    with pytest.raises(HeaderError, match="managed by the browser"):
        validate_header_map({"Host": "evil.test"})


# -- user agent ---------------------------------------------------------


def test_user_agent_is_split_out_case_insensitively() -> None:
    """It goes to the browser context, not the header set, so page JavaScript
    reading navigator.userAgent agrees with the wire."""
    rest, ua = split_user_agent({"user-AGENT": "Mozilla/5.0", "X-Token": "k"})
    assert ua == "Mozilla/5.0"
    assert rest == {"X-Token": "k"}


def test_no_user_agent_leaves_the_map_alone() -> None:
    rest, ua = split_user_agent({"X-Token": "k"})
    assert ua is None
    assert rest == {"X-Token": "k"}


def test_an_empty_user_agent_is_treated_as_unset() -> None:
    _rest, ua = split_user_agent({"User-Agent": ""})
    assert ua is None


# -- redaction ----------------------------------------------------------


def test_summary_names_headers_but_never_values() -> None:
    summary = header_summary({"Authorization": "Bearer super-secret", "X-API-Key": "k"})
    assert summary == "2 headers (Authorization, X-API-Key)"
    assert "super-secret" not in summary
    assert "Bearer" not in summary


def test_summary_is_empty_when_nothing_is_configured() -> None:
    """Callers omit the field entirely rather than logging "0 headers"."""
    assert header_summary(None) == ""
    assert header_summary({}) == ""


def test_summary_singular_reads_correctly() -> None:
    assert header_summary({"Authorization": "x"}) == "1 header (Authorization)"
