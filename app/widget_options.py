"""Editor metadata from widget strings, with per-key English fallbacks."""

from __future__ import annotations

from typing import Any

from flask import current_app, has_app_context

from app.locale_resolve import resolve_locale


def localized_option_specs(plugin: Any, group: str) -> list[dict[str, Any]]:
    locale = "en"
    if has_app_context():
        locale = resolve_locale(current_app.config["SETTINGS_STORE"].get_section("app") or {})
    strings = getattr(plugin, "strings", {})
    translated = strings.get(locale, strings.get(locale.split("-", 1)[0], {}))
    english = strings.get("en", {})
    result = []
    for option in plugin.manifest.get(group, []):
        spec = dict(option)
        for field in ("label", "help", "placeholder"):
            key = f"{group}.{spec['name']}.{field}"
            text = translated.get(key) or english.get(key)
            if text:
                spec[field] = text
        result.append(spec)
    return result
