"""Tests for ``app.secret_box``, the AES-GCM at-rest wrap/unwrap layer."""

from __future__ import annotations

import secrets

import pytest

from app.secret_box import (
    ENV_KEY,
    WIRE_PREFIX,
    SecretBox,
    SecretBoxError,
    is_wrapped,
)


def test_round_trip_strings_through_wrap_and_unwrap() -> None:
    """A wrapped value carries the ``enc:v1:`` prefix on disk and
    decrypts back to the original string. Empty strings round-trip too
    (still wrapped, just an empty payload underneath)."""
    box = SecretBox(secrets.token_bytes(32))
    for plain in ["hass-token-xyz", "", "café au lait", "🔒", "a" * 5000]:
        wrapped = box.wrap(plain)
        assert wrapped.startswith(WIRE_PREFIX)
        assert is_wrapped(wrapped) is True
        assert box.unwrap(wrapped) == plain


def test_unwrap_passes_plaintext_through_unchanged() -> None:
    """The backwards-compat path: a value without the prefix is
    returned as-is. Existing on-disk plaintext keeps working until
    the next save migrates it."""
    box = SecretBox(secrets.token_bytes(32))
    assert box.unwrap("plain-token") == "plain-token"
    assert box.unwrap("") == ""
    # Strings that *coincidentally* start with "enc" but not the full
    # prefix are still plaintext.
    assert box.unwrap("encyclopedia") == "encyclopedia"


def test_each_wrap_uses_a_fresh_nonce() -> None:
    """AES-GCM is catastrophically insecure under nonce reuse with
    the same key. The wrap path draws a fresh 12-byte nonce per call,
    so wrapping the same value twice produces different ciphertexts."""
    box = SecretBox(secrets.token_bytes(32))
    a = box.wrap("same-input")
    b = box.wrap("same-input")
    assert a != b
    assert box.unwrap(a) == box.unwrap(b) == "same-input"


def test_unwrap_with_wrong_key_raises() -> None:
    """A box keyed with B can't decrypt what box A wrapped; the GCM
    tag check fails and we surface that as ``SecretBoxError`` so the
    operator sees a real error rather than an empty token."""
    a = SecretBox(secrets.token_bytes(32))
    b = SecretBox(secrets.token_bytes(32))
    wrapped = a.wrap("hass-token")
    with pytest.raises(SecretBoxError):
        b.unwrap(wrapped)


def test_unwrap_rejects_corrupt_payload() -> None:
    """A wrapped value whose ciphertext was tampered with fails the
    tag check. Same for truncated input that's too short to even
    contain the nonce."""
    box = SecretBox(secrets.token_bytes(32))
    wrapped = box.wrap("hass-token")
    # Flip the last character to corrupt the auth tag.
    corrupted = wrapped[:-2] + ("AA" if wrapped[-2:] != "AA" else "BB")
    with pytest.raises(SecretBoxError):
        box.unwrap(corrupted)
    # Too short to contain the nonce.
    with pytest.raises(SecretBoxError):
        box.unwrap(WIRE_PREFIX + "AAAA")


def test_from_env_reads_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env-var path takes 64 hex chars and yields a usable box."""
    key = secrets.token_bytes(32)
    monkeypatch.setenv(ENV_KEY, key.hex())
    box = SecretBox.from_env()
    assert box is not None
    plain = "test-token"
    assert box.unwrap(box.wrap(plain)) == plain


def test_from_env_rejects_malformed_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-hex input or wrong-length input returns ``None``; callers
    fall back to the derived key path. We log a warning so the operator
    notices their env var didn't take effect."""
    monkeypatch.setenv(ENV_KEY, "not-hex-at-all-zzzz")
    assert SecretBox.from_env() is None
    monkeypatch.setenv(ENV_KEY, "abcd")  # too short
    assert SecretBox.from_env() is None


def test_from_env_unset_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var means no env-keyed box; resolution proceeds to the
    session-derived fallback (covered separately)."""
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert SecretBox.from_env() is None


def test_from_session_secret_derivation_is_deterministic() -> None:
    """HKDF is deterministic given the same input. Two boxes derived
    from the same session secret can interoperate (wrap on one,
    unwrap on the other), which is the property we rely on across
    process restarts."""
    session = secrets.token_bytes(32)
    a = SecretBox.from_session_secret(session)
    b = SecretBox.from_session_secret(session)
    wrapped = a.wrap("hass-token")
    assert b.unwrap(wrapped) == "hass-token"


def test_from_session_secret_distinct_inputs_distinct_keys() -> None:
    """Different session secrets derive different encryption keys, so
    a wrapped value from one host's settings.json isn't decryptable on
    another host's clone."""
    a = SecretBox.from_session_secret(secrets.token_bytes(32))
    b = SecretBox.from_session_secret(secrets.token_bytes(32))
    wrapped = a.wrap("hass-token")
    with pytest.raises(SecretBoxError):
        b.unwrap(wrapped)


def test_from_session_secret_rejects_too_short() -> None:
    """A session secret under 16 bytes is too low-entropy to derive a
    stable encryption key from. Refuse rather than silently produce
    a box with predictable output."""
    with pytest.raises(ValueError):
        SecretBox.from_session_secret(b"short")


def test_resolve_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``TESSERAE_SECRET_KEY`` is set, ``resolve`` uses it and
    ignores the session-secret fallback; verified by round-tripping a
    value through the resolved box and decrypting it with a fresh box
    constructed from the same env hex (the session secret is
    deliberately different)."""
    env_key = secrets.token_bytes(32)
    monkeypatch.setenv(ENV_KEY, env_key.hex())
    box = SecretBox.resolve(session_secret=secrets.token_bytes(32))
    wrapped = box.wrap("hass-token")
    assert SecretBox(env_key).unwrap(wrapped) == "hass-token"
