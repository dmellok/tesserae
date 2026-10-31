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


def test_c_with_codeset_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "C.UTF-8" (the usual Docker/Ubuntu default) must be skipped like
    bare "C"; leaking the tag "C" makes Intl throw in calendar widgets."""
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("LANG", "POSIX.UTF-8")
    assert resolve_locale({}, None) == DEFAULT_LOCALE


def test_c_codeset_lc_all_still_yields_to_real_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert resolve_locale({}, None) == "de-DE"


def test_no_env_vars_falls_back_to_en(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    assert resolve_locale({}, None) == "en"
    assert DEFAULT_LOCALE == "en"


@pytest.mark.parametrize(
    ("locale", "cycle"),
    [
        ("en-US", "h12"),
        ("en", "h12"),
        ("en_US.UTF-8", "h12"),
        ("en-CA", "h12"),
        ("en-AU", "h12"),
        ("en-IN", "h12"),
        ("es-MX", "h12"),
        ("es-419", "h12"),
        ("zh-Hant", "h12"),
        ("zh-TW", "h12"),
        ("ko", "h12"),
        ("en-GB", "h23"),
        ("en-IE", "h23"),
        ("en-150", "h23"),
        ("de", "h23"),
        ("de-DE", "h23"),
        ("fr", "h23"),
        ("fr-CA", "h23"),
        ("es", "h23"),
        ("pt-BR", "h23"),
        ("ja", "h23"),
        ("zh", "h23"),
        ("sr-Latn-RS", "h23"),
        ("en-US-u-hc-h23", "h23"),
        ("de-DE-u-ca-gregory-hc-h12", "h12"),
        ("", "h23"),
        (None, "h23"),
    ],
)
def test_time_cycle_matches_intl(locale: str | None, cycle: str) -> None:
    """The browser's ``Intl.DateTimeFormat(locale, {hour: "numeric"})``
    hourCycle for the same tag (ICU 74)."""
    from app.locale_resolve import time_cycle

    assert time_cycle(locale) == cycle
