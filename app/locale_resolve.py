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
