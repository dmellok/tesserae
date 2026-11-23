"""HA Add-on Configuration → Tesserae settings + log level.

When Tesserae runs as a Home Assistant Add-on, Supervisor writes the
user's Configuration-tab values to ``/data/options.json`` before
starting the container. This module reads that file and applies a
small allow-list of options:

* ``log_level`` → Python root logger level
* ``mqtt_host`` / ``mqtt_port`` / ``mqtt_username`` / ``mqtt_password``
  → ``broker.*`` in the settings store

When ``mqtt_username`` is blank, the broker credentials come from
Supervisor's MQTT service instead (``GET http://supervisor/services/mqtt``),
the way other apps such as zigbee2mqtt get them (#600). Values entered in
the options still win: Supervisor's answer is used only for a blank
username, and only when the options name no other host.

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
import urllib.error
import urllib.request
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


def resolve_log_level(name: str | None) -> int | None:
    """Map a level name to a ``logging`` level, or None if empty / unknown.

    Accepts HA's ``log_level`` vocabulary (trace / notice / fatal …) plus
    the standard Python names, so the same helper serves the add-on option,
    the ``TESSERAE_LOG_LEVEL`` env var, and the ``--log-level`` flag."""
    raw = str(name or "").strip().lower()
    if not raw:
        return None
    extra = {"warn": logging.WARNING, "critical": logging.CRITICAL}
    return _HA_LOG_LEVEL_MAP.get(raw) or extra.get(raw)


def apply_log_level(options: dict[str, Any]) -> None:
    """Translate HA's ``log_level`` option onto the root logger."""
    level = resolve_log_level(options.get("log_level"))
    if level is not None:
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


SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"


def fetch_supervisor_mqtt() -> dict[str, Any] | None:
    """Ask Supervisor for the MQTT service, or None when it has none.

    Supervisor answers ``{"result": "ok", "data": {host, port, ssl, username,
    password, ...}}`` for an app that declares ``services: [mqtt:want]``, and
    an error when no broker app provides the service."""
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        return None
    req = urllib.request.Request(SUPERVISOR_MQTT_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as err:
        logger.info("HA add-on: no MQTT service from Supervisor (%s)", err)
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) and data.get("host") else None


def _broker_patch_from_supervisor(
    options: dict[str, Any], service: dict[str, Any]
) -> dict[str, Any]:
    """Broker keys from Supervisor's MQTT service, or {} when the options decide.

    The options win: a username entered there, or a host other than the
    service's (an anonymous broker elsewhere), keeps the service out. A TLS
    service is not applied, because the broker settings have no TLS option and
    a plain connection to a TLS port would only fail."""
    if str(options.get("mqtt_username") or "").strip():
        return {}
    entered_host = str(options.get("mqtt_host") or "").strip()
    host = str(service.get("host") or "").strip()
    if entered_host and entered_host != host:
        return {}
    if service.get("ssl"):
        logger.warning(
            "HA add-on: Supervisor's MQTT service uses TLS, which Tesserae does not support; not applied"
        )
        return {}
    patch: dict[str, Any] = {"host": host}
    port = service.get("port")
    if isinstance(port, int) and 1 <= port <= 65535:
        patch["port"] = port
    patch["username"] = str(service.get("username") or "").strip()
    patch["password_secret"] = str(service.get("password") or "")
    return patch


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
    if is_ha_addon() and not str(options.get("mqtt_username") or "").strip():
        service = fetch_supervisor_mqtt()
        patch = _broker_patch_from_supervisor(options, service) if service else {}
        if patch:
            settings.patch_section("broker", patch)
            logger.info(
                "HA add-on: applied the MQTT service from Supervisor (host %s)", patch["host"]
            )
    return options
