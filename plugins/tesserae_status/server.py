"""tesserae_status: dashboard status strip.

Server-side prep:

* Reads the running Tesserae version so the widget's update chip can
  compare to api.tesserae.ink/version/latest client-side.
* Resolves panel-side signals from the bound devices: battery (min
  across bound devices), Wi-Fi label (from RSSI), broker label
  (present when MQTT is configured).
* Counts device kinds with a firmware update available, using the
  in-memory firmware_check cache (:mod:`app.firmware_check`). Passive
  read; the widget never triggers a fresh fetch on its own.
* Emits the widget-scoped install id (set by the composer when the
  manifest declares ``needs_scoped_id``) so the client's update-check
  chip can pass it to api.tesserae.ink for aggregate install counts
  without any cross-widget correlation.

Layout, chip mode, background colour and auto-contrast happen entirely
client-side; the payload just carries the values the chips render.
"""

from __future__ import annotations

from typing import Any

from flask import current_app


def _server_version() -> str | None:
    """Return the running server version, read from ``pyproject.toml``.

    Tesserae is deployed by git clone + editable install (never as a
    pinned wheel), so ``importlib.metadata`` reports the version at
    install time not what's actually running. ``pyproject.toml`` is
    the truer signal after a ``git pull``.
    """
    try:
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not pyproject.exists():
            return None
        text = pyproject.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                _, _, rhs = stripped.partition("=")
                return rhs.strip().strip('"').strip("'") or None
    except Exception:
        return None
    return None


def _panel_battery() -> int | None:
    """Return the battery percentage of the first bound battery device
    with a fresh reading, or ``None`` when no data is available."""
    status_cache = current_app.config.get("DEVICE_STATUS") or {}
    battery_pcts: list[int] = []
    for entry in status_cache.values():
        parsed = (entry or {}).get("parsed") or {}
        pct = parsed.get("battery_pct")
        if isinstance(pct, (int, float)) and 0 < float(pct) <= 100:
            battery_pcts.append(int(pct))
    return min(battery_pcts) if battery_pcts else None


def _wifi_label() -> str | None:
    """Return a coarse Wi-Fi strength label ("Excellent" / "Good" /
    "Fair" / "Weak") derived from the worst RSSI across bound devices.

    Mirrors the buckets Settings uses (`_humanize_signal`) but returns
    just the short label so it fits in a chip. ``None`` when no device
    reports RSSI.
    """
    status_cache = current_app.config.get("DEVICE_STATUS") or {}
    worst: int | None = None
    for entry in status_cache.values():
        parsed = (entry or {}).get("parsed") or {}
        rssi = parsed.get("rssi")
        try:
            value = int(rssi) if rssi is not None else None
        except (TypeError, ValueError):
            value = None
        if value is None:
            continue
        if worst is None or value < worst:
            worst = value
    if worst is None:
        return None
    if worst >= -55:
        return "Excellent"
    if worst >= -65:
        return "Good"
    if worst >= -75:
        return "Fair"
    return "Weak"


def _broker_configured() -> bool:
    """True when an MQTT broker URL is set in the app settings."""
    settings = current_app.config.get("SETTINGS_STORE")
    if settings is None:
        return False
    try:
        section = settings.get_section("broker") or {}
    except Exception:
        return False
    url = section.get("mqtt_url") or section.get("url")
    return bool(url)


def _firmware_updates_available() -> int:
    """Count device kinds where at least one registered instance is on an
    older firmware than the aggregator reports.

    Skips the lookup entirely when
    ``settings.app.check_firmware_updates`` is disabled (the app-level
    opt-in for outbound api.tesserae.ink firmware calls, off by
    default). Otherwise reads the in-memory firmware_check cache
    without triggering fresh fetches.
    """
    if not _firmware_check_enabled():
        return 0
    from app import firmware_check as firmware_check_module

    devices = current_app.config.get("DEVICE_REGISTRY")
    if devices is None:
        return 0
    status_cache = current_app.config.get("DEVICE_STATUS") or {}
    outdated_kinds: set[str] = set()
    for device in devices.all():
        if device.kind_of is None:
            continue
        parsed = (status_cache.get(device.id) or {}).get("parsed") or {}
        current_fw = parsed.get("fw_version")
        current_str = str(current_fw) if isinstance(current_fw, (str, int, float)) else None
        latest = firmware_check_module.latest_for_kind(device.kind_of)
        state = firmware_check_module.compare_versions(current_str, latest)
        if state == "outdated":
            outdated_kinds.add(device.kind_of)
    return len(outdated_kinds)


def _firmware_check_enabled() -> bool:
    """Mirror of the same helper in :mod:`app.settings.index_routes`.
    Kept local so the widget doesn't reach into Settings internals."""
    settings = current_app.config.get("SETTINGS_STORE")
    if settings is None:
        return False
    try:
        section = settings.get_section("app") or {}
    except Exception:
        return False
    raw = section.get("check_firmware_updates")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return False


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    """Return the payload the client.js renders."""
    del settings

    def _bool(name: str, default: bool) -> bool:
        raw = options.get(name)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return default

    version = _server_version()

    return {
        "mode": str(options.get("mode") or "bar"),
        "chipMode": str(options.get("chipMode") or "icon-text"),
        "dashboardName": str(options.get("dashboardName") or "").strip(),
        "page_name": str(ctx.get("page_name") or ""),
        "leadingIcon": _bool("leadingIcon", True),
        "panelBg": str(options.get("panelBg") or "#1B1A16"),
        "time_format": str(options.get("time_format") or "24h"),
        # Chip visibility flags
        "show_time": _bool("show_time", True),
        "show_battery": _bool("show_battery", True),
        "show_wifi": _bool("show_wifi", True),
        "show_broker": _bool("show_broker", True),
        "broker_label": str(options.get("broker_label") or "HA"),
        "check_for_updates": _bool("check_for_updates", False),
        "show_firmware_updates": _bool("show_firmware_updates", True),
        # Values populated server-side
        "battery_pct": _panel_battery() if _bool("show_battery", True) else None,
        "wifi_label": _wifi_label() if _bool("show_wifi", True) else None,
        "broker_available": _broker_configured() if _bool("show_broker", True) else False,
        "version": version,
        "firmware_updates": (
            _firmware_updates_available() if _bool("show_firmware_updates", True) else 0
        ),
        # Aggregate-only install identifier for the update-check fetch.
        "install_scoped_id": ctx.get("widget_scoped_id") or "",
    }
