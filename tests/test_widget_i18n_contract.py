"""Repo-wide locales contract for every bundled widget that declares
``locales`` (docs/widgets.md#locales-strings).

The per-widget ``test_*_i18n.py`` files pin the English/French pair of
the first translated widgets, plus each widget's own server.py mirrors.
This file is the generic half: whatever set of locales a widget ships,
every ``strings/<tag>.json`` must carry exactly the English key set with
no blank values, the manifest must match the files on disk, and every
literal ``t("key", "fallback")`` in client.js must resolve to an English
entry whose text is the fallback. A widget that adds a locale, or a
locale that adds a widget, is covered without touching this file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.main import REPO_ROOT

PLUGINS = REPO_ROOT / "plugins"

_LITERAL_T_CALLS = re.compile(r'\bt\("([a-zA-Z0-9_]+)",\s*"([^"]*)"\)')


def _manifest(plugin_dir: Path) -> dict:
    return json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))


def _strings(plugin_dir: Path, locale: str) -> dict[str, str]:
    return json.loads((plugin_dir / "strings" / f"{locale}.json").read_text(encoding="utf-8"))


def _localised_widgets() -> list[Path]:
    out = []
    for plugin_dir in sorted(PLUGINS.iterdir()):
        if (plugin_dir / "plugin.json").is_file() and _manifest(plugin_dir).get("locales"):
            out.append(plugin_dir)
    return out


LOCALISED = _localised_widgets()
LOCALISED_IDS = [p.name for p in LOCALISED]
PAIRS = [
    (plugin_dir, locale)
    for plugin_dir in LOCALISED
    for locale in _manifest(plugin_dir)["locales"]
    if locale != "en"
]
PAIR_IDS = [f"{p.name}-{loc}" for p, loc in PAIRS]


def test_at_least_one_widget_is_localised() -> None:
    assert LOCALISED, "no bundled widget declares locales -- did plugins/ move?"


@pytest.mark.parametrize("plugin_dir", LOCALISED, ids=LOCALISED_IDS)
def test_manifest_declares_english_first(plugin_dir: Path) -> None:
    assert _manifest(plugin_dir)["locales"][0] == "en"


@pytest.mark.parametrize("plugin_dir", LOCALISED, ids=LOCALISED_IDS)
def test_manifest_locales_match_shipped_files(plugin_dir: Path) -> None:
    declared = _manifest(plugin_dir)["locales"]
    assert len(declared) == len(set(declared)), f"duplicate locale in {plugin_dir.name}"
    shipped = {p.stem for p in (plugin_dir / "strings").glob("*.json")}
    assert set(declared) == shipped


@pytest.mark.parametrize("plugin_dir", LOCALISED, ids=LOCALISED_IDS)
def test_english_strings_are_flat_and_non_blank(plugin_dir: Path) -> None:
    en = _strings(plugin_dir, "en")
    assert en, f"{plugin_dir.name}: strings/en.json is empty"
    assert all(isinstance(v, str) and v.strip() for v in en.values())


@pytest.mark.parametrize("plugin_dir", LOCALISED, ids=LOCALISED_IDS)
def test_literal_t_calls_resolve_to_english_with_matching_fallback(plugin_dir: Path) -> None:
    js = (plugin_dir / "client.js").read_text(encoding="utf-8")
    calls = _LITERAL_T_CALLS.findall(js)
    assert calls, f"{plugin_dir.name}: no literal t(key, fallback) call in client.js"
    en = _strings(plugin_dir, "en")
    missing = sorted({k for k, _ in calls if k not in en})
    assert missing == [], f"{plugin_dir.name}: t() keys absent from strings/en.json: {missing}"
    drift = sorted({(k, f) for k, f in calls if en[k] != f})
    assert drift == [], f"{plugin_dir.name}: fallback text differs from strings/en.json: {drift}"


@pytest.mark.parametrize(("plugin_dir", "locale"), PAIRS, ids=PAIR_IDS)
def test_every_locale_carries_the_english_key_set(plugin_dir: Path, locale: str) -> None:
    en_keys = set(_strings(plugin_dir, "en"))
    loc_keys = set(_strings(plugin_dir, locale))
    assert loc_keys == en_keys, (
        f"{plugin_dir.name}/{locale}: missing {sorted(en_keys - loc_keys)}, "
        f"extra {sorted(loc_keys - en_keys)}"
    )


@pytest.mark.parametrize(("plugin_dir", "locale"), PAIRS, ids=PAIR_IDS)
def test_no_blank_translations(plugin_dir: Path, locale: str) -> None:
    blanks = [k for k, v in _strings(plugin_dir, locale).items() if not str(v).strip()]
    assert blanks == [], f"{plugin_dir.name}/{locale}: blank translation(s) {blanks}"
