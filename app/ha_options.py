"""HA Add-on Configuration → Tesserae settings + log level.

When Tesserae runs as a Home Assistant Add-on, Supervisor writes the
user's Configuration-tab values to ``/data/options.json`` before
starting the container. This module reads that file and applies a
small allow-list of options:

* ``log_level`` → Python root logger level
* ``mqtt_host`` / ``mqtt_port`` / ``mqtt_username`` / ``mqtt_password``
  → ``broker.*`` in the settings store

Override-every-start: HA Configuration is the canonical source for the
fields it covers. The matching Tesserae UI fields are hidden under HA
so the user has one place to manage broker connection details.

Anything else (HA discovery toggle, mDNS, browser warmup, theme, …)
is intentionally NOT mirrored here, it'd create
two sources of truth for fields the user can already edit in
Tesserae's UI. Add to the allow-list only when there's a strong
"HA is the right place" argument.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)

# HA's log level vocabulary is broader than Python's. ``notice`` falls
# between INFO and WARNING in HA; map it to INFO so it's still visible.
# ``trace`` and ``debug`` both map to DEBUG.
_HA_LOG_LEVEL_MAP: dict[str, int] = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}

# HA Supervisor mounts the add-on's persistent volume at /data and
# writes the user's Configuration form values to options.json there.
DEFAULT_OPTIONS_PATH = Path("/data/options.json")


def load_options(path: Path = DEFAULT_OPTIONS_PATH) -> dict[str, Any] | None:
    """Read ``/data/options.json`` and return its contents.

    Returns ``None`` when the file doesn't exist (we're not under HA, or
    the user hasn't saved their Configuration tab yet) or is malformed
    (Supervisor would normally reject that upstream, so we don't crash -
    just skip the wiring)."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError) as err:
        logger.warning("HA options.json unreadable: %s", err)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def apply_log_level(options: dict[str, Any]) -> None:
    """Translate HA's ``log_level`` option onto the root logger."""
    raw = str(options.get("log_level") or "").strip().lower()
    level = _HA_LOG_LEVEL_MAP.get(raw)
    if level is None:
        return
    logging.getLogger().setLevel(level)


def _broker_patch_from_options(options: dict[str, Any]) -> dict[str, Any]:
    """Translate the four ``mqtt_*`` options into ``broker`` keys.

    Each option is only included when explicitly present, that way a
    missing key from a sparse options.json doesn't blank out an
    existing setting. Empty strings ARE forwarded, because that's how
    a user clears the field on HA's Configuration form."""
    patch: dict[str, Any] = {}
    if "mqtt_host" in options:
        patch["host"] = str(options["mqtt_host"] or "").strip()
    if "mqtt_port" in options:
        port = options.get("mqtt_port")
        if isinstance(port, int) and 1 <= port <= 65535:
            patch["port"] = port
    if "mqtt_username" in options:
        patch["username"] = str(options["mqtt_username"] or "").strip()
    if "mqtt_password" in options:
        patch["password_secret"] = str(options["mqtt_password"] or "")
    return patch


def apply_to_settings(options: dict[str, Any], settings: SettingsStore) -> None:
    """Patch ``broker`` from the supplied options."""
    patch = _broker_patch_from_options(options)
    if patch:
        settings.patch_section("broker", patch)
        logger.info("HA add-on: applied broker config (%d field(s))", len(patch))


def is_ha_addon() -> bool:
    """True iff the runtime smells like an HA Add-on container.

    ``TESSERAE_HA_INGRESS`` is set by the add-on's config.yaml; the
    Supervisor token is injected by Supervisor when ``hassio_api: true``.
    Either alone is suggestive; we require both so a stray env var on a
    bare-metal install can't trigger HA-only behaviour."""
    return bool(os.environ.get("TESSERAE_HA_INGRESS") and os.environ.get("SUPERVISOR_TOKEN"))


def apply_ha_options(
    settings: SettingsStore,
    *,
    options_path: Path | None = None,
) -> dict[str, Any] | None:
    """Top-level entry: load options.json + apply log level + settings.

    No-op (returns None) when the options file is missing / malformed.
    ``options_path`` is overridable for tests."""
    options = load_options(options_path or DEFAULT_OPTIONS_PATH)
    if options is None:
        return None
    apply_log_level(options)
    apply_to_settings(options, settings)
    return options
