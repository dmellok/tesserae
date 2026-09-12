"""Locales contract for weather_hourly (docs/widgets.md#locales-strings).

weather_hourly's translation was wired in 5e131171: title, legend, empty
state, and the icon-strip condition tooltips (``ICON_COND_KEY`` in
client.js). Before that commit the tooltips leaked raw semantic icon
names ("rain-heavy") instead of the translated ``cond_*`` strings -- a
typo in that mapping (or a key present in one locale file but not the
other) would silently regress to the same thing and nothing would fail.
These tests pin the static contract (en/fr key parity, every key
client.js actually references exists in both files) plus one real
end-to-end render proving the French strings reach the DOM, mirroring
test_composer_locale.py's calendar_day pilot test.
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

PLUGIN_DIR = REPO_ROOT / "plugins" / "weather_hourly"
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
    """A key present in one locale but not the other resolves silently
    (strings_for falls back to English, or the key just goes missing) --
    this is the actual shape of the bug fixed on this branch, so pin
    it as an equality check rather than a one-way subset."""
    en_keys = set(_strings("en"))
    fr_keys = set(_strings("fr"))
    assert en_keys == fr_keys


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_no_blank_translations(locale: str) -> None:
    blanks = [k for k, v in _strings(locale).items() if not str(v).strip()]
    assert blanks == [], f"blank {locale} translation(s): {blanks}"


# -- client.js only asks for keys that actually exist --------------------

# Literal ``t("key", "fallback text")`` calls -- excludes the one dynamic
# call (``t(condKey, name)``), which is covered separately below via the
# ICON_COND_KEY mapping it reads ``condKey`` from.
_LITERAL_T_CALLS = re.compile(r't\("([a-zA-Z0-9_]+)",\s*"([^"]*)"\)')


def test_every_literal_t_call_key_exists_in_both_locales() -> None:
    calls = _LITERAL_T_CALLS.findall(CLIENT_JS)
    assert calls, "no literal t(key, fallback) calls found -- did client.js change shape?"
    en, fr = _strings("en"), _strings("fr")
    for key, _fallback in calls:
        assert key in en, f"t({key!r}, ...) has no strings/en.json entry"
        assert key in fr, f"t({key!r}, ...) has no strings/fr.json entry"


def test_literal_t_call_fallback_text_matches_shipped_english() -> None:
    """The JS fallback (used when ctx.t is absent, e.g. an untranslated
    embed) should read the same as the shipped English string, so the
    two don't drift apart over time."""
    en = _strings("en")
    for key, fallback in _LITERAL_T_CALLS.findall(CLIENT_JS):
        assert en[key] == fallback, (
            f"t({key!r}, {fallback!r}) fallback text no longer matches "
            f"strings/en.json ({en[key]!r})"
        )


def test_icon_condition_tooltip_keys_exist_in_both_locales() -> None:
    """ICON_COND_KEY maps each icon name to a ``cond_*`` strings key,
    read dynamically (``t(condKey, name)``) so the literal-call regex
    above can't see it. This is exactly the mapping the fixed bug lived
    in: a missing/mistyped cond_* key here degrades the icon-strip
    tooltip to the raw icon name instead of translated text."""
    block = re.search(r"const ICON_COND_KEY = \{(.*?)\};", CLIENT_JS, re.DOTALL)
    assert block is not None, "ICON_COND_KEY mapping not found -- did client.js change shape?"
    cond_keys = set(re.findall(r':\s*"([a-zA-Z0-9_]+)"', block.group(1)))
    assert cond_keys, "no cond_* keys parsed out of ICON_COND_KEY"
    en, fr = _strings("en"), _strings("fr")
    for key in cond_keys:
        assert key in en, f"ICON_COND_KEY value {key!r} has no strings/en.json entry"
        assert key in fr, f"ICON_COND_KEY value {key!r} has no strings/fr.json entry"


def test_every_icon_name_has_a_condition_key() -> None:
    """PH_BY_NAME enumerates every icon name the icon strip can render;
    every one of them must resolve to a tooltip key so none silently
    falls back to showing the raw semantic name (the pre-fix bug)."""
    ph_block = re.search(r"const PH_BY_NAME = \{(.*?)\};", CLIENT_JS, re.DOTALL)
    cond_block = re.search(r"const ICON_COND_KEY = \{(.*?)\};", CLIENT_JS, re.DOTALL)
    assert ph_block is not None and cond_block is not None
    icon_names = set(re.findall(r'^\s*"?([a-zA-Z0-9_-]+)"?\s*:', ph_block.group(1), re.MULTILINE))
    cond_names = set(re.findall(r'^\s*"?([a-zA-Z0-9_-]+)"?\s*:', cond_block.group(1), re.MULTILINE))
    assert icon_names == cond_names


# -- discovery: the real plugin loads clean with these strings files -----


def test_weather_hourly_discovers_without_errors_and_resolves_french() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        registry = plugin_loader.discover(
            REPO_ROOT / "plugins",
            schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
            data_root=Path(tmp),
        )
    assert not any(err.plugin_id == "weather_hourly" for err in registry.errors)
    plugin = registry.plugins["weather_hourly"]
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


def test_weather_hourly_receives_its_real_french_strings(client: FlaskClient, app: Flask) -> None:
    """No location is configured for this cell, so server.py's fetch()
    returns its friendly {"error": ...} payload without touching the
    network -- the render still carries the widget's full resolved
    strings map regardless of whether the fetch succeeded."""
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "fr"})
    resp = client.get("/_test/render?plugin=weather_hourly&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "fr"
    assert json.loads(_cell_dataset(body, "strings")) == _strings("fr")


def test_weather_hourly_falls_back_to_english_by_default(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    resp = client.get("/_test/render?plugin=weather_hourly&size=md")
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "en"
    assert json.loads(_cell_dataset(body, "strings")) == _strings("en")
