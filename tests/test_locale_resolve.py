"""Locale resolution: per-device override, app-level default, system
fallback, ultimate "en" fallback. Same shape as test_quiet_hours.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.locale_resolve import DEFAULT_LOCALE, resolve_locale


def _device(locale: str | None) -> SimpleNamespace:
    """Build a Device-like stand-in with a manifest dict."""
    manifest: dict = {"id": "d", "kind": "pi_png_client"}
    if locale is not None:
        manifest["locale"] = locale
    return SimpleNamespace(manifest=manifest)


def test_device_override_wins_over_app_setting() -> None:
    app = {"locale": "en"}
    assert resolve_locale(app, _device("de")) == "de"


def test_no_device_falls_back_to_app_setting() -> None:
    app = {"locale": "fr"}
    assert resolve_locale(app, None) == "fr"
    assert resolve_locale(app, _device(None)) == "fr"


def test_blank_device_locale_falls_back_to_app_setting() -> None:
    """A device manifest with an empty-string locale (e.g. a form that
    submitted a blank field) is treated as absent, not as a literal
    empty-string override."""
    app = {"locale": "fr"}
    assert resolve_locale(app, _device("")) == "fr"
    assert resolve_locale(app, _device("   ")) == "fr"


def test_app_setting_system_falls_back_to_host_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert resolve_locale({"locale": "system"}, None) == "de-DE"


def test_unset_app_setting_behaves_like_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    assert resolve_locale({}, None) == "fr-FR"


def test_lang_used_when_lc_all_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    assert resolve_locale({}, None) == "pt-BR"


def test_c_and_posix_locale_are_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "C" / "POSIX" mean "no locale configured" at the OS level, not a
    literal locale to render in."""
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "POSIX")
    assert resolve_locale({}, None) == DEFAULT_LOCALE


def test_no_env_vars_falls_back_to_en(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    assert resolve_locale({}, None) == "en"
    assert DEFAULT_LOCALE == "en"
