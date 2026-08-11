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


def test_clear_password_wipes_section(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    auth.set_password(store, "secretpass")
    assert auth.password_is_set(store)
    auth.clear_password(store)
    assert not auth.password_is_set(store)
    # Section is wiped, not just blanked, verify on disk.
    assert store.get_section("auth") == {}


def test_password_required_defaults_true(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    assert auth.password_required(store) is True


def test_set_password_disabled_toggles_required(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "s.json")
    auth.set_password(store, "secretpass")
    auth.set_password_disabled(store, True)
    assert auth.password_required(store) is False
    # The stored hash survives the toggle, re-enabling restores the same
    # password without forcing the user to pick a new one.
    assert auth.verify_password(store, "secretpass")
    auth.set_password_disabled(store, False)
    assert auth.password_required(store) is True
    assert auth.verify_password(store, "secretpass")


def test_cli_reset_clears_password(tmp_path: Path, monkeypatch) -> None:
    """``tesserae --reset-password`` wipes the auth section on disk so the
    next request drops back to /setup. The CLI resolves the data root via
    TESSERAE_DATA_ROOT for headless invocations."""
    from app.main import _reset_password

    settings_path = tmp_path / "core" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    store = SettingsStore(settings_path)
    auth.set_password(store, "secretpass")
    assert auth.password_is_set(store)

    monkeypatch.setenv("TESSERAE_DATA_ROOT", str(tmp_path))
    _reset_password()

    fresh = SettingsStore(settings_path)
    assert not auth.password_is_set(fresh)


def test_plugin_assets_bypass_the_gate_but_admin_pages_do_not() -> None:
    """The loopback exemption exists so the in-process renderer can pull a
    widget's ``client.js`` and static assets while composing. It is not meant
    to cover a plugin's admin page: those list loader errors and plugin
    contents, and some (gtfs's stop finder) turn a query argument into an
    outbound HTTP request. A trailing slash used to satisfy the old
    "has a second segment" test, so every one of them was reachable without
    a session from loopback."""
    assert auth._path_is_loopback_only("/plugins/gtfs/client.js") is True
    assert auth._path_is_loopback_only("/plugins/weather_now/client.css") is True
    assert auth._path_is_loopback_only("/plugins/f1_next_race/static/circuits/monza.svg") is True

    assert auth._path_is_loopback_only("/plugins/") is False
    assert auth._path_is_loopback_only("/plugins/gtfs") is False
    assert auth._path_is_loopback_only("/plugins/gtfs/") is False
    assert auth._path_is_loopback_only("/plugins/calendar_core/") is False
