"""Locales contract for weather_forecast (docs/widgets.md#locales-strings).

weather_forecast's translation was wired in 8a922545: the title, the
two empty states, the rain-chance tooltip, and the day-strip's "Today"
/ "Tom" labels. Unlike weather_now/weather_hourly there is no
code->key mirror table to keep in sync (the widget shows no condition
text of its own, only an icon) -- the one piece of genuinely new
locale-dependent logic is ``dayLabel``'s index-2+ branch, which moved
from server.py's hardcoded English weekday abbreviation to an
Intl.DateTimeFormat call keyed on ``ctx.locale``; that's covered by
``plugins/weather_forecast/tests/clamp_check.mjs`` (picked up by
test_widget_clamp_checks.py) rather than here, since it needs a real
JS Intl call rather than string matching.

These tests pin the static en/fr contract, that every key client.js
actually references exists in both files, plus one real end-to-end
render proving the French strings reach the DOM -- mirroring
test_composer_locale.py's calendar_day pilot, test_weather_hourly_i18n.py
and test_weather_now_i18n.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import plugin_loader
from app.main import REPO_ROOT, create_app

PLUGIN_DIR = REPO_ROOT / "plugins" / "weather_forecast"
CLIENT_JS = (PLUGIN_DIR / "client.js").read_text(encoding="utf-8")


def _strings(locale: str) -> dict[str, str]:
    return json.loads((PLUGIN_DIR / "strings" / f"{locale}.json").read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))


# -- static contract: the strings/*.json files themselves ---------------


def test_locales_declared_in_manifest_match_shipped_strings_files() -> None:
    declared = set(_manifest()["locales"])
    shipped = {p.stem for p in (PLUGIN_DIR / "strings").glob("*.json")}
    assert declared == shipped


def test_en_and_fr_strings_have_identical_key_sets() -> None:
    en_keys = set(_strings("en"))
    fr_keys = set(_strings("fr"))
    assert en_keys == fr_keys


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_no_blank_translations(locale: str) -> None:
    blanks = [k for k, v in _strings(locale).items() if not str(v).strip()]
    assert blanks == [], f"blank {locale} translation(s): {blanks}"


# -- client.js only asks for keys that actually exist --------------------

# Literal ``t("key", "fallback text")`` calls. weather_forecast has no
# dynamic (variable-key) t() call at all -- unlike weather_now/
# weather_hourly's icon/condition lookups -- so this regex catches
# every single translated string in the widget.
_LITERAL_T_CALLS = re.compile(r't\("([a-zA-Z0-9_]+)",\s*"([^"]*)"\)')


def test_every_literal_t_call_key_exists_in_both_locales() -> None:
    calls = _LITERAL_T_CALLS.findall(CLIENT_JS)
    assert calls, "no literal t(key, fallback) calls found -- did client.js change shape?"
    en, fr = _strings("en"), _strings("fr")
    for key, _fallback in calls:
        assert key in en, f"t({key!r}, ...) has no strings/en.json entry"
        assert key in fr, f"t({key!r}, ...) has no strings/fr.json entry"


def test_literal_t_call_fallback_text_matches_shipped_english() -> None:
    en = _strings("en")
    for key, fallback in _LITERAL_T_CALLS.findall(CLIENT_JS):
        assert en[key] == fallback, (
            f"t({key!r}, {fallback!r}) fallback text no longer matches "
            f"strings/en.json ({en[key]!r})"
        )


def test_every_shipped_key_is_actually_referenced() -> None:
    """The flip side of the check above: a key shipped in strings/en.json
    that client.js never asks for is dead weight (or a rename that
    forgot to update the call site). day_today/day_tomorrow are read
    through dayLabel()'s literal t() calls, so this still holds for
    the whole file even though that function isn't the render() body."""
    referenced = {key for key, _fallback in _LITERAL_T_CALLS.findall(CLIENT_JS)}
    assert referenced == set(_strings("en"))


# -- discovery: the real plugin loads clean with these strings files -----


def test_weather_forecast_discovers_without_errors_and_resolves_french() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        registry = plugin_loader.discover(
            REPO_ROOT / "plugins",
            schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
            data_root=Path(tmp),
        )
    assert not any(err.plugin_id == "weather_forecast" for err in registry.errors)
    plugin = registry.plugins["weather_forecast"]
    assert plugin.strings_for("fr") == _strings("fr")
    assert plugin.strings_for("en") == _strings("en")
    # An unshipped regional tag falls back to the base language.
    assert plugin.strings_for("fr-CA") == _strings("fr")


# -- end-to-end: the real composer pipeline, mirroring calendar_day's ----
# pilot test in test_composer_locale.py.


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def _cell_dataset(html: str, attr: str) -> str:
    marker = f"data-{attr}="
    idx = html.index(marker) + len(marker)
    quote = html[idx]
    start = idx + 1
    end = html.index(quote, start)
    return html[start:end]


def test_weather_forecast_receives_its_real_french_strings(
    client: FlaskClient, app: Flask
) -> None:
    """No location is configured for this cell, so server.py's fetch()
    returns its friendly {"error": ...} payload without touching the
    network -- the render still carries the widget's full resolved
    strings map regardless of whether the fetch succeeded."""
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "fr"})
    resp = client.get("/_test/render?plugin=weather_forecast&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "fr"
    assert json.loads(_cell_dataset(body, "strings")) == _strings("fr")


def test_weather_forecast_falls_back_to_english_by_default(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    resp = client.get("/_test/render?plugin=weather_forecast&size=md")
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "en"
    assert json.loads(_cell_dataset(body, "strings")) == _strings("en")
