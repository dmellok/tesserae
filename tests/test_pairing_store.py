"""PairingStore: issue, consume (single-use), expire, revoke."""

from __future__ import annotations

import time

from app.state.pairing_store import PairingStore


def test_issue_returns_six_digit_code() -> None:
    store = PairingStore()
    record = store.issue()
    assert record.code.isdigit()
    assert len(record.code) == 6


def test_issue_two_codes_returns_distinct_values() -> None:
    store = PairingStore()
    a = store.issue()
    b = store.issue()
    assert a.code != b.code


def test_issue_replaces_the_same_owners_pending_code() -> None:
    store = PairingStore()
    first = store.issue(owner_key="companion:one")
    second = store.issue(owner_key="companion:one")

    assert first.code != second.code
    assert store.consume(first.code) is None
    assert [record.code for record in store.list_pending()] == [second.code]


def test_issue_keeps_codes_from_different_owners() -> None:
    store = PairingStore()
    first = store.issue(owner_key="companion:one")
    second = store.issue(owner_key="companion:two")

    assert {record.code for record in store.list_pending()} == {first.code, second.code}


def test_consume_succeeds_once_then_fails() -> None:
    store = PairingStore()
    record = store.issue()
    consumed = store.consume(record.code)
    assert consumed is not None
    assert consumed.code == record.code
    # Single-use: second consume of the same code returns None.
    assert store.consume(record.code) is None


def test_consume_with_wrong_code_returns_none() -> None:
    store = PairingStore()
    store.issue()
    assert store.consume("000000") is None


def test_consume_with_malformed_code_returns_none() -> None:
    store = PairingStore()
    store.issue()
    assert store.consume("") is None
    assert store.consume("12345") is None  # too short
    assert store.consume("abc123") is None  # not digits — len matches but no match


def test_expired_code_cannot_be_consumed() -> None:
    store = PairingStore(ttl_s=1)
    record = store.issue()
    time.sleep(1.1)
    assert store.consume(record.code) is None


def test_revoke_removes_code() -> None:
    store = PairingStore()
    record = store.issue()
    assert store.revoke(record.code) is True
    assert store.consume(record.code) is None
    # Idempotent revoke.
    assert store.revoke(record.code) is False


def test_list_pending_returns_unexpired_codes_only() -> None:
    store = PairingStore(ttl_s=1)
    a = store.issue(note="device A")
    store.issue(note="device B")
    assert len(store.list_pending()) == 2
    time.sleep(1.1)
    assert store.list_pending() == []
    # Cleanup happens on access; a consume of an expired code also
    # returns None per the earlier test.
    assert store.consume(a.code) is None
