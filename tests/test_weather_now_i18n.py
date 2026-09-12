"""Locales contract for weather_now (docs/widgets.md#locales-strings).

weather_now's translation was wired in 161ecd74: title, "feels", the
empty states, the 8-item metric-label strip (``METRIC_LABEL_KEY``), and
the WMO condition text (``COND_KEY_BY_CODE``). The latter two are each
a hand-maintained mirror of a table server.py owns (the metrics list's
``icon`` field, the ``_WMO`` code table) -- client.js's own comments say
so ("mirrors server.py's _WMO table 1:1 ... keep in sync"), and nothing
enforced it. A code added to ``_WMO`` (or a metric icon renamed) without
a matching client.js entry would silently fall back to the untranslated
English text server.py sends, in every non-English locale, with no
error anywhere. These tests pin both mirrors against server.py's real
source (via ``ast``, not string matching, so nested calls like
``_round_div(current.get(...), 1000)`` don't trip up a naive regex),
plus the static en/fr contract and one real end-to-end render, mirroring
test_composer_locale.py's calendar_day pilot and test_weather_hourly_i18n.py.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app import plugin_loader
from app.main import REPO_ROOT, create_app

PLUGIN_DIR = REPO_ROOT / "plugins" / "weather_now"
CLIENT_JS = (PLUGIN_DIR / "client.js").read_text(encoding="utf-8")
SERVER_PY = (PLUGIN_DIR / "server.py").read_text(encoding="utf-8")
SERVER_AST = ast.parse(SERVER_PY)


def _strings(locale: str) -> dict[str, str]:
    return json.loads((PLUGIN_DIR / "strings" / f"{locale}.json").read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))


def _js_object_block(name: str) -> str:
    m = re.search(rf"const {name} = \{{(.*?)\}};", CLIENT_JS, re.DOTALL)
    assert m is not None, f"{name} mapping not found -- did client.js change shape?"
    return m.group(1)


def _cond_key_by_code() -> dict[int, str]:
    block = _js_object_block("COND_KEY_BY_CODE")
    pairs = re.findall(r"(\d+):\s*\"([a-zA-Z0-9_]+)\"", block)
    assert pairs, "no entries parsed out of COND_KEY_BY_CODE"
    return {int(code): key for code, key in pairs}


def _metric_label_key() -> dict[str, str]:
    block = _js_object_block("METRIC_LABEL_KEY")
    pairs = re.findall(r'([a-zA-Z0-9_-]+):\s*"([a-zA-Z0-9_]+)"', block)
    assert pairs, "no entries parsed out of METRIC_LABEL_KEY"
    return dict(pairs)


def _server_wmo_codes() -> set[int]:
    """The real ``_WMO`` dict's keys, read via ast so this can't drift
    from a regex's idea of the table's shape. ``_WMO`` carries a type
    annotation (``dict[int, tuple[str, str, str]]``), so it's an
    ``AnnAssign`` node, not a plain ``Assign``."""
    for node in ast.walk(SERVER_AST):
        is_wmo_target = (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_WMO" for t in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_WMO"
        )
        if is_wmo_target and node.value is not None:
            assert isinstance(node.value, ast.Dict)
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("_WMO dict not found in server.py -- did it get renamed/moved?")


def _server_metric_icon_names() -> set[str]:
    """Every ``icon`` literal passed to ``_metric(...)`` in the real
    ``metrics = [...]`` list, read via ast (a regex split on commas
    would misparse ``_metric(..., _round_div(current.get(...), 1000), ...)``,
    whose nested call itself contains a comma)."""
    names: set[str] = set()
    for node in ast.walk(SERVER_AST):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_metric"
            and len(node.args) >= 4
            and isinstance(node.args[3], ast.Constant)
        ):
            names.add(node.args[3].value)
    assert names, "no _metric(...) calls found in server.py -- did the metrics list change shape?"
    return names


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

# Literal ``t("key", "fallback text")`` calls -- excludes the two dynamic
# calls (``t(condKey, ...)``, ``t(labelKey, ...)``), covered separately
# below via the mappings those variables are read from.
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


def test_metric_label_key_values_exist_in_both_locales() -> None:
    en, fr = _strings("en"), _strings("fr")
    for icon, key in _metric_label_key().items():
        assert key in en, f"METRIC_LABEL_KEY[{icon!r}] = {key!r} has no strings/en.json entry"
        assert key in fr, f"METRIC_LABEL_KEY[{icon!r}] = {key!r} has no strings/fr.json entry"


def test_cond_key_by_code_values_exist_in_both_locales() -> None:
    en, fr = _strings("en"), _strings("fr")
    for code, key in _cond_key_by_code().items():
        assert key in en, f"COND_KEY_BY_CODE[{code}] = {key!r} has no strings/en.json entry"
        assert key in fr, f"COND_KEY_BY_CODE[{code}] = {key!r} has no strings/fr.json entry"


# -- the two mirrors client.js's own comments say must stay in sync -----


def test_cond_key_by_code_covers_exactly_the_server_wmo_codes() -> None:
    """A WMO code added to (or removed from) server.py's ``_WMO`` table
    without a matching client.js update would silently render
    untranslated English condition text in every non-English locale --
    this is the "keep in sync" comment above ``COND_KEY_BY_CODE``,
    turned into an assertion."""
    assert set(_cond_key_by_code()) == _server_wmo_codes()


def test_metric_label_key_covers_exactly_the_server_metric_icons() -> None:
    """Same drift risk as above, for the metrics grid: a metric icon
    renamed or added in server.py's ``metrics = [...]`` list without a
    matching ``METRIC_LABEL_KEY`` entry silently shows the untranslated
    English label."""
    assert set(_metric_label_key()) == _server_metric_icon_names()


# -- discovery: the real plugin loads clean with these strings files -----


def test_weather_now_discovers_without_errors_and_resolves_french() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        registry = plugin_loader.discover(
            REPO_ROOT / "plugins",
            schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
            data_root=Path(tmp),
        )
    assert not any(err.plugin_id == "weather_now" for err in registry.errors)
    plugin = registry.plugins["weather_now"]
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


def test_weather_now_receives_its_real_french_strings(client: FlaskClient, app: Flask) -> None:
    """No location is configured for this cell, so server.py's fetch()
    returns its friendly {"error": ...} payload without touching the
    network -- the render still carries the widget's full resolved
    strings map regardless of whether the fetch succeeded."""
    app.config["SETTINGS_STORE"].patch_section("app", {"locale": "fr"})
    resp = client.get("/_test/render?plugin=weather_now&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "fr"
    assert json.loads(_cell_dataset(body, "strings")) == _strings("fr")


def test_weather_now_falls_back_to_english_by_default(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    resp = client.get("/_test/render?plugin=weather_now&size=md")
    body = resp.get_data(as_text=True)
    assert _cell_dataset(body, "locale") == "en"
    assert json.loads(_cell_dataset(body, "strings")) == _strings("en")
