"""trmnl_client device contract.

HTTP-polled e-paper client implementing the TRMNL BYOS protocol. The
device polls ``GET /api/display`` on its configured cadence; Tesserae
hands back the URL of the latest render artifact. The device identifies
itself by an ``access-token`` header, the user generates that in
Tesserae and pastes it into the client config (no MAC, no pairing flow).

Status reaches Tesserae via headers on every ``/api/display`` request
rather than a separate MQTT heartbeat. ``app.trmnl_api`` (the HTTP
blueprint that backs ``/api/display``) collects the headers into a
JSON object and hands it to ``parse_status`` below. The resulting
dict feeds the same device-status cache + Settings → Devices freshness
display as the MQTT-based clients.

There is no ``config_topic``, config (refresh rate, etc.) is pushed
to the device by embedding it in the ``/api/display`` JSON response,
not by publishing to a separate broker topic. ``validate_config`` is
still exported so the admin UI's config form has its server-side
guard before the values get embedded in the response payload.
"""

from __future__ import annotations

import json
from typing import Any

# Bounds match config_schema in device.json. Duplicated here on
# purpose: the manifest lives next to the form (UI affordance),
# the constants live next to validate_config (server-side guard) -
# neither one trusts the other.
REFRESH_RATE_MIN_S = 5
REFRESH_RATE_MAX_S = 24 * 60 * 60


# Header names the TRMNL BYOS protocol uses for status fields. Different
# implementations of the client use slightly different spellings -
# notably the KOReader plugin sends ``Percent-Charged`` and
# ``Png-Width``/``Png-Height``, while native TRMNL firmware sends
# ``Battery-Voltage`` and ``Width``/``Height``. Lookup is case-insensitive
# (see parse_status), so these are stored already case-folded.
_BATTERY_PCT_HEADERS = ("percent-charged", "battery-percent")
# Native TRMNL firmware historically sent millivolts as an integer
# (e.g. ``"3860"``), but some builds send volts as a decimal string
# (e.g. ``"3.86"``). Both are accepted, see ``_parse_battery_voltage``.
_BATTERY_MV_HEADERS = ("battery-voltage",)
_RSSI_HEADERS = ("rssi",)
_FW_HEADERS = ("fw-version", "user-agent")  # User-Agent is the KOReader fallback
_WIDTH_HEADERS = ("png-width", "width")
_HEIGHT_HEADERS = ("png-height", "height")
# Official TRMNL DIY-kit firmware (XIAO-based ESP32-C3) puts the
# device's MAC in ``Id`` and a board identifier (e.g.
# ``xiao_epaper_display``) in ``Model``. Surface both so the device
# card distinguishes "the XIAO kit" from "a Kindle running KOReader"
# at a glance, instead of relying on the synthetic ``trmnl_<token>``
# id.
_MAC_HEADERS = ("id",)
_MODEL_HEADERS = ("model",)


def parse_status(payload: bytes) -> dict[str, Any]:
    """Normalise an /api/display request's headers into the same status
    shape the MQTT-based devices produce.

    ``payload`` is a JSON object of headers preserved as-sent by the
    client, ``app.trmnl_api`` builds it from Flask ``request.headers``.
    Different BYOS clients use different cases (``percent-charged``,
    ``Percent-Charged``, ``PERCENT_CHARGED``), so header lookup is
    case-insensitive. Always returns a dict with the well-known keys
    (``None`` when missing) plus the original headers under
    ``_raw_headers`` so the Settings card can show anything we didn't
    anticipate."""
    out: dict[str, Any] = {
        "battery_pct": None,
        "battery_mv": None,
        "rssi": None,
        "fw_version": None,
        "panel_w": None,
        "panel_h": None,
        "mac": None,
        "model": None,
    }
    if not payload:
        return out
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        out["error"] = "header payload was not JSON"
        return out
    if not isinstance(decoded, dict):
        out["error"] = f"expected JSON object, got {type(decoded).__name__}"
        return out

    # Build a case-folded lookup so a header sent as ``Percent-Charged``,
    # ``percent-charged`` or ``PERCENT-CHARGED`` all resolve the same way.
    folded = {k.casefold(): v for k, v in decoded.items() if isinstance(k, str)}
    out["battery_pct"] = _first_int(folded, _BATTERY_PCT_HEADERS)
    out["battery_mv"] = _parse_battery_voltage(folded)
    out["rssi"] = _first_int(folded, _RSSI_HEADERS)
    out["fw_version"] = _first_str(folded, _FW_HEADERS)
    out["panel_w"] = _first_int(folded, _WIDTH_HEADERS)
    out["panel_h"] = _first_int(folded, _HEIGHT_HEADERS)
    out["mac"] = _first_str(folded, _MAC_HEADERS)
    out["model"] = _first_str(folded, _MODEL_HEADERS)
    # Drop the originals into a debug bucket so the Settings card can
    # surface unexpected headers without us having to anticipate every
    # client implementation up front.
    out["_raw_headers"] = decoded
    return out


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Check the payload makes sense before it's embedded in the next
    ``/api/display`` response."""
    if "refresh_rate_s" not in payload:
        return False, "missing 'refresh_rate_s'"
    try:
        rate = int(payload["refresh_rate_s"])
    except (TypeError, ValueError):
        return False, "refresh_rate_s must be an integer"
    if rate < REFRESH_RATE_MIN_S:
        return False, f"refresh_rate_s must be >= {REFRESH_RATE_MIN_S} (got {rate})"
    if rate > REFRESH_RATE_MAX_S:
        return False, f"refresh_rate_s must be <= {REFRESH_RATE_MAX_S} (got {rate})"
    return True, None


# -- internals ----------------------------------------------------------


def _parse_battery_voltage(headers: dict[str, Any]) -> int | None:
    """Normalise the TRMNL ``Battery-Voltage`` header to millivolts.

    Two formats observed in the wild:

    * Integer millivolts: ``"3860"`` (older / DIY-kit firmware).
    * Decimal volts: ``"3.86"`` (newer native firmware, also some
      community builds).

    Heuristic: parse as float, then if the result is below 100,
    interpret as volts and multiply by 1000. A real LiPo never reads
    below 100 mV (it'd be far past brownout), and a real volts reading
    never exceeds ~5 V, so the threshold is unambiguous.
    """
    for k in _BATTERY_MV_HEADERS:
        if k not in headers:
            continue
        raw = headers[k]
        if raw is None:
            continue
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        if value < 100:
            # Decimal volts -> millivolts.
            return round(value * 1000)
        return round(value)
    return None


def _first_int(headers: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    """Return the first header in ``keys`` that coerces to int, else None."""
    for k in keys:
        if k in headers:
            try:
                return int(headers[k])
            except (TypeError, ValueError):
                continue
    return None


def _first_str(headers: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first header in ``keys`` that is a non-empty string, else None."""
    for k in keys:
        if k in headers:
            value = headers[k]
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
