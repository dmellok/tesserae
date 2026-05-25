"""Auth helpers: hash + verify, set/replace, secret_key persistence."""

from __future__ import annotations

from pathlib import Path

from app import auth
from app.state.settings_store import SettingsStore


def test_password_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    assert not auth.password_is_set(store)
    auth.set_password(store, "correct horse battery staple")
    assert auth.password_is_set(store)
    assert auth.verify_password(store, "correct horse battery staple")
    assert not auth.verify_password(store, "wrong")


def test_password_hash_is_stored_under_secret_key(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    auth.set_password(store, "pw")
    raw = store.get_section("auth")
    # On-disk key must be the secret-suffixed name so it's grep-visible.
    assert "password_hash_secret" in raw
    assert "password_hash" not in raw
    assert "password_salt" in raw
    assert raw["password_iterations"] == auth.PBKDF2_ITERATIONS


def test_secret_key_persists(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    store_a = SettingsStore(p)
    key_a = auth.secret_key(store_a)
    # A fresh store reading the same file must produce the same key,
    # otherwise sessions would invalidate on every restart.
    store_b = SettingsStore(p)
    key_b = auth.secret_key(store_b)
    assert key_a == key_b
    assert len(key_a) == 32


def test_verify_returns_false_when_no_password_set(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    assert not auth.verify_password(store, "anything")
