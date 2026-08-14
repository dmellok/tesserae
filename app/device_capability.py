"""Server-computed capability support for a device, keyed by protocol
capability rather than by model name.

Several surfaces need the same answer to "can this display do X?": the
Offline Album form in the Picture Gallery plugin, the Companion API's
device list, and anything later that binds a capability-gated feature to
a target. Each was answering it differently, or not at all: the album
form offered every registered device whose kind resolved, so an
unsupported panel could be bound and would then silently never receive a
collection.

The rule here is deliberately narrow. Support is read from what the
device most recently *advertised*, never from its kind, its model, or an
assumption about what storage it has. A future client with internal
flash advertising ``frame_cache`` is supported; a listed SD-card model
that has not advertised it is not.

Three states, because "we don't know" is a different answer from "no":

``supported``
    The latest usable report advertises the capability.
``unsupported``
    A usable report exists and explicitly lacks it.
``unknown``
    There is no usable current report: the device has never checked in,
    or its last beat is old enough that a current-state capability read
    from it would be a guess.

The last distinction matters because the capabilities this module
reports are current-state: firmware advertises ``frame_cache`` only
while storage is present and mounted, and a card can be pulled between
wakes (see ``app/transport_wiring.py``, which deliberately does not
carry it forward). A beat from four sleep cycles ago is not evidence
either way.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from flask import current_app

from app.device_loader import Device

# Capability names, matching the keys firmware advertises in its
# register / heartbeat body.
FRAME_CACHE = "frame_cache"

# Every capability the support map reports. One entry today; the map
# shape exists so a second capability is a constant here rather than a
# new response field the clients have to learn.
CAPABILITIES: tuple[str, ...] = (FRAME_CACHE,)

# Machine-readable explanations. Clients localise the ones they know and
# fall back to a generic string for anything newer, so adding a code
# here is additive.
REASON_NOT_ADVERTISED = "not_advertised"
REASON_NO_HEARTBEAT = "no_usable_heartbeat"
REASON_STALE_HEARTBEAT = "stale_heartbeat"

# Floor under the freshness window. A device that polls every few
# seconds shouldn't read as stale because one beat was skipped.
_FRESHNESS_FLOOR_S = 300
# How many of the device's own wake cycles of silence before its last
# beat stops counting as current.
_FRESHNESS_CYCLES = 3


def freshness_threshold_s(device: Device) -> float:
    """How long since the last heartbeat before a device's report stops
    counting as current.

    E-ink clients sleep for long stretches by design, so the threshold
    tracks the device's own poll cadence rather than a fixed wall-clock
    window: a few wake cycles of silence, with a floor so a chatty
    device isn't written off on a single skipped beat."""
    settings = current_app.config.get("SETTINGS_STORE")
    section = (settings.get_section("devices") or {}) if settings is not None else {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    interval = 60
    if isinstance(stored, dict) and isinstance(stored.get("sleep_interval_s"), int):
        interval = int(stored["sleep_interval_s"])
    else:
        schema = device.config_schema or {}
        spec = schema.get("sleep_interval_s") if isinstance(schema, dict) else None
        if isinstance(spec, dict) and isinstance(spec.get("default"), int):
            interval = int(spec["default"])
    return max(interval * _FRESHNESS_CYCLES, _FRESHNESS_FLOOR_S)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def heartbeat_freshness(device: Device, status: dict[str, Any] | None) -> tuple[str, str | None]:
    """``(freshness, last_seen_at)`` for a device: ``fresh``, ``stale``,
    or ``unknown`` when it has never been heard from."""
    received_at = status.get("received_at") if isinstance(status, dict) else None
    if not isinstance(received_at, (int, float)):
        return "unknown", None
    age = time.time() - float(received_at)
    state = "fresh" if age <= freshness_threshold_s(device) else "stale"
    return state, _iso(received_at)


def capability_support(
    device: Device, status: dict[str, Any] | None, capability: str
) -> dict[str, Any]:
    """The computed support state for one capability on one device.

    ``{state, reason_code?, observed_at}``. ``observed_at`` is the time
    of the report the state was read from, present even when that report
    was too old to use so an operator can see how stale the evidence
    is."""
    freshness, last_seen_at = heartbeat_freshness(device, status)
    if freshness == "unknown":
        return {
            "state": "unknown",
            "reason_code": REASON_NO_HEARTBEAT,
            "observed_at": None,
        }
    if freshness == "stale":
        return {
            "state": "unknown",
            "reason_code": REASON_STALE_HEARTBEAT,
            "observed_at": last_seen_at,
        }
    advertised = status.get(capability) if isinstance(status, dict) else None
    if advertised:
        return {"state": "supported", "observed_at": last_seen_at}
    return {
        "state": "unsupported",
        "reason_code": REASON_NOT_ADVERTISED,
        "observed_at": last_seen_at,
    }


def capability_support_map(device: Device, status: dict[str, Any] | None) -> dict[str, Any]:
    """Every capability's computed state for one device."""
    return {name: capability_support(device, status, name) for name in CAPABILITIES}


# Operator-facing wording for the states, so the Web forms that gate a
# target on a capability all explain a disabled row the same way.
SUPPORT_NOTES: dict[str, str] = {
    "supported": "",
    "unsupported": "This display hasn't reported the storage this needs.",
    "unknown": "Tesserae hasn't heard from this display recently enough to tell.",
}


def support_note(support: dict[str, Any]) -> str:
    """Short explanation for a non-supported state, or ``""``."""
    return SUPPORT_NOTES.get(str(support.get("state") or ""), "")
