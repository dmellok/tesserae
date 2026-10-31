"""Locale resolution: per-device override + a single app-level default.

Two layers, same shape as ``app.quiet_hours``:

* **App-level** (Settings -> Server -> App): the default locale for the
  whole install, ``settings.app.locale``.
* **Per-device override** (an instance or kind's ``locale`` field): a
  panel in a different room -- or a different household member's desk
  -- can render in its own language off one server. This is the "German
  kitchen panel, English office panel" case the plugin locales contract
  exists for.

``resolve_locale`` is a pure function like ``resolve_quiet_hours``: it
takes a plain settings dict and a duck-typed device (anything with a
``.manifest`` dict, so tests can pass a ``SimpleNamespace`` instead of
a real ``Device``), never touches ``current_app`` itself. Callers reach
it the same way they reach quiet hours: ``resolve_locale(settings_store
.get_section("app") or {}, device)``.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_LOCALE = "en"

# POSIX locale env vars, most to least specific, matching the order a
# libc locale lookup itself would use. "C" / "POSIX" mean "no locale
# configured", not "render in a language called C", so they're skipped
# rather than returned.
_LOCALE_ENV_VARS: tuple[str, ...] = ("LC_ALL", "LANG")


def _device_locale(device: Any | None) -> str | None:
    """Pull a ``locale`` string off a Device-like object, or ``None``.

    Reads ``.manifest`` rather than a ``Device.locale`` property so
    this module stays decoupled from ``app.device_loader`` (same
    reasoning as ``app.quiet_hours._device_override``) and so tests
    can pass a bare ``SimpleNamespace(manifest={...})``."""
    if device is None:
        return None
    manifest = getattr(device, "manifest", None)
    if isinstance(manifest, dict):
        value = manifest.get("locale")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _system_locale() -> str | None:
    """Best-effort host locale from the standard POSIX env vars,
    normalised to a BCP-47-ish tag (``fr_FR.UTF-8`` -> ``fr-FR``).

    Deliberately not ``locale.getdefaultlocale()`` (deprecated since
    3.11, removed in 3.13) -- reading the env vars directly is the
    same technique ``app.tz_resolve`` already uses for ``TZ``, and
    keeps this module free of a stdlib API on its way out."""
    for var in _LOCALE_ENV_VARS:
        raw = os.environ.get(var, "").strip()
        if not raw:
            continue
        tag = raw.split(".", 1)[0].replace("_", "-")  # drop ".UTF-8", "_" -> "-"
        # Compare after stripping the codeset so "C.UTF-8" (the usual
        # Docker/Ubuntu default) is skipped like bare "C", not returned
        # as a language tag Intl will reject.
        if not tag or tag.upper() in ("C", "POSIX"):
            continue
        return tag
    return None


def resolve_locale(app_settings: dict[str, Any], device: Any | None = None) -> str:
    """Effective locale for rendering ``device``'s pages.

    Resolution order:

    1. Per-device override (``device.manifest["locale"]``), when set.
    2. App-level ``settings.app.locale``, when set and not ``"system"``.
    3. ``"system"`` (or app-level unset) -> the host's own locale via
       ``LC_ALL`` / ``LANG``.
    4. ``"en"``, the ultimate fallback so callers always get a
       non-empty, renderable tag.
    """
    override = _device_locale(device)
    if override:
        return override
    raw = str(app_settings.get("locale") or "system").strip()
    if raw and raw.lower() != "system":
        return raw
    return _system_locale() or DEFAULT_LOCALE


# -- clock style ---------------------------------------------------------------
#
# Whether a locale reads times on a 12-hour clock ("6:30 PM") or a 24-hour
# one ("18:30"), matching what a browser's
# ``Intl.DateTimeFormat(locale, {hour: "numeric"}).resolvedOptions().hourCycle``
# picks (taken from ICU 74's CLDR data). The server decides once and the page
# receives the answer, so the server's markup and the client's redraw never
# disagree. Each language has a default and the regions that flip it; a
# language not listed reads 24-hour.
_H12_LANGUAGES: frozenset[str] = frozenset(
    {
        "am",
        "ar",
        "bn",
        "el",
        "en",
        "fil",
        "gu",
        "hi",
        "km",
        "kn",
        "ko",
        "ml",
        "mr",
        "ms",
        "pa",
        "so",
        "sq",
        "ta",
        "te",
        "tl",
        "ur",
    }
)
_FLIP_REGIONS: dict[str, frozenset[str]] = {
    lang: frozenset(regions.split())
    for lang, regions in {
        "af": "NA",
        "ar": "IL KM MA",
        "en": "150 AI AT BE BI BW BZ CC CH CK CM CX CZ DD DE DG DK EE ES FI FK FR FX GB "
        "GE GG GI GS HU ID IE IL IM IO IT JE JP KE LT LV MG MS MT MU MV NF NG NL NO NR NU "
        "PL PN PT RH RO RW SC SE SH SI SK SX TK TV TZ UA UG UK ZA ZW",
        "es": "419 AR BO CL CO CR CU DO EC GT HN MX NI PA PE PH PR PY SV US UY VE",
        "fr": "DJ DZ MR NH SY TD TN VU",
        "ha": "GH",
        "ko": "CN",
        "ms": "ID",
        "ne": "IN",
        "pt": "MO",
        "so": "KE",
        "sq": "MK",
        "ta": "LK",
        "tr": "CY",
        "zh": "HK MO MY SG TW",
    }.items()
}


def time_cycle(locale: str | None) -> str:
    """``"h12"`` or ``"h23"`` for a BCP-47-ish tag (``en-US`` -> h12,
    ``en-GB`` / ``de`` -> h23). A ``-u-hc-`` extension wins; ``zh-Hant``
    without a region reads as Taiwan, as Intl does."""
    parts = str(locale or "").replace("_", "-").split("-")
    lowered = [p.lower() for p in parts]
    if "u" in lowered:
        ext = lowered[lowered.index("u") + 1 :]
        if "hc" in ext[:-1]:
            hc = ext[ext.index("hc") + 1]
            if hc in ("h11", "h12"):
                return "h12"
            if hc in ("h23", "h24"):
                return "h23"
        parts = parts[: lowered.index("u")]
    lang = parts[0].lower() if parts else ""
    region = next(
        (
            p.upper()
            for p in parts[1:]
            if (len(p) == 2 and p.isalpha()) or (len(p) == 3 and p.isdigit())
        ),
        "",
    )
    if not region and lang == "zh" and any(p.lower() == "hant" for p in parts[1:]):
        region = "TW"
    h12 = lang in _H12_LANGUAGES
    if region in _FLIP_REGIONS.get(lang, ()):
        h12 = not h12
    return "h12" if h12 else "h23"
