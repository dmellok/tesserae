"""The language list every locale picker offers, derived from what the
installed widgets actually ship rather than hand-maintained.

Three surfaces show a "panel text language" picker: the app-wide
default (Settings -> Server), the per-device override (Settings ->
Devices) and the dev preview pages (/_test/widgets, /_test/preview).
They used to each carry their own hardcoded English / French list, so
a widget shipping ``strings/sk.json`` was unreachable from the UI until
someone edited all three. Now they all ask :func:`available_locales`,
which unions the ``locales`` every loaded widget declares (see
``Plugin.strings`` in ``app.plugin_loader``) and always includes
English, the language every widget's own fallback text is written in.

Labels are native-language names from a small static table; a tag the
table doesn't know renders as the tag itself, so an unexpected
``strings/xx.json`` still shows up in the picker rather than being
silently dropped.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.locale_resolve import DEFAULT_LOCALE


class _HasStrings(Protocol):
    strings: dict[str, dict[str, str]]


class _Registry(Protocol):
    def widgets(self) -> list[Any]: ...


# Native-language display names for the tags a widget is likely to ship.
# Regional variants fall back to the base language's label plus the
# region code (``pt-BR`` -> "Português (BR)") when only the base is known.
LOCALE_LABELS: dict[str, str] = {
    "en": "English",
    "fr": "Français",
    "de": "Deutsch",
    "es": "Español",
    "it": "Italiano",
    "nl": "Nederlands",
    "pt": "Português",
    "pt-BR": "Português (Brasil)",
    "sk": "Slovenčina",
    "cs": "Čeština",
    "pl": "Polski",
    "sv": "Svenska",
    "da": "Dansk",
    "nb": "Norsk bokmål",
    "fi": "Suomi",
    "hu": "Magyar",
    "ro": "Română",
    "el": "Ελληνικά",
    "tr": "Türkçe",
    "ru": "Русский",
    "uk": "Українська",
    "ja": "日本語",
    "ko": "한국어",
    "zh": "中文",
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
}


def label_for(tag: str) -> str:
    """Display label for a locale tag; the tag itself when unknown."""
    if tag in LOCALE_LABELS:
        return LOCALE_LABELS[tag]
    base, _, region = tag.partition("-")
    if region and base in LOCALE_LABELS:
        return f"{LOCALE_LABELS[base]} ({region})"
    return tag


def available_locales(registry: _Registry | None) -> list[dict[str, str]]:
    """``[{"value": tag, "label": name}, ...]`` for every locale at least
    one loaded widget ships strings for, plus English. English leads;
    the rest sort by label so the picker reads the same regardless of
    which widget happened to load first. ``registry`` may be ``None``
    (very early startup, some unit tests): the list is then just
    English."""
    tags: set[str] = {DEFAULT_LOCALE}
    if registry is not None:
        for plugin in registry.widgets():
            strings = getattr(plugin, "strings", None) or {}
            tags.update(str(tag) for tag in strings)
    rest = sorted(
        (t for t in tags if t != DEFAULT_LOCALE), key=lambda t: (label_for(t).casefold(), t)
    )
    return [{"value": t, "label": label_for(t)} for t in (DEFAULT_LOCALE, *rest)]
