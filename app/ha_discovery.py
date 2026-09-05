"""Home Assistant MQTT autodiscovery integration.

Publishes retained config payloads under
``homeassistant/<component>/tesserae/...`` so the HA MQTT integration
auto-creates a **Tesserae hub** device plus **one HA device per
registered display** (multi-head).

Hub device entities:

* **button** per saved dashboard, pressing it calls
  ``PushManager.push(page_id)`` (fans out to every display the dashboard
  is bound to).
* **select** ``active dashboard``, same, driven by a dropdown.
* **image** ``last render``, the composition PNG of the most-recent
  push (covers the legacy / virtual-panel case with no devices).
* **sensor** + **binary_sensor** diagnostics: last push, pushes today,
  last error, busy.
* **switch** ``Automation`` (ON = the scheduler fires, OFF = every
  automated push holds) and **switch** ``Quiet hours`` (the app-level
  window toggle).
* **switch** per lineup (its ``enabled`` flag) plus **buttons** to push
  the lineup live and step it forward / back.
* **notify** ``Notify all displays``: HA's ``notify.send_message`` renders
  the text onto every display.

Per-display device entities (one HA device card each, linked to the hub
via ``via_device``):

* **image** ``Frame``, the composition PNG currently published to that
  display (URL-only; HA fetches the bytes).
* **sensor** ``Current dashboard``, ``Last updated``, ``Last seen``,
  ``Next change`` (the next projected visible update).
* **select** ``Dashboard`` (push one bound dashboard to *only* this
  display) and **select** ``Lineup`` (which lineup the display follows).
* **number** ``Hold`` (minutes to hold the current page against the
  rotation) and, when the kind carries one, ``Wake interval``.
* **switch** ``Quiet hours override`` and **binary_sensors** ``In quiet
  hours``, ``Online``, ``Low battery``.
* **button** ``Refresh now``, **event** ``Input`` (physical buttons and
  touch gestures), **notify** (text onto this display) and, once the
  device advertises OTA, an **update** entity for its firmware.
* lazily, where the heartbeat provides them: **Battery**, **Signal**,
  **IP address**, **Firmware**, **Temperature**, **Humidity**,
  **Uptime**, **Next wake**.

Everything that needs app internals beyond the push manager and the
stores (settings, scheduler, OTA, device config) arrives through
:class:`HaHooks`, a bag of optional callables built by
:mod:`app.ha_hooks`. Any hook left ``None`` simply drops the entities
that depend on it, so the module stays testable with fakes.

The same MQTT broker the rest of Tesserae publishes to is reused, no
second connection. Default-off in settings; users opt in by toggling
``ha_discovery_enabled`` in the App section.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from app.device_loader import DeviceRegistry
from app.push import PushManager, PushResult
from app.state.page_store import PageStore
from app.transport import MqttTransport

logger = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "tesserae"
# State + command topics live under tesserae/ha/ so they stay inside the
# project's namespace and don't collide with renderer / device traffic.
AVAILABILITY_TOPIC = "tesserae/ha/availability"
STATE_TOPIC_LAST_PUSH = "tesserae/ha/state/last_push"
STATE_TOPIC_PUSH_COUNT = "tesserae/ha/state/push_count_today"
STATE_TOPIC_LAST_ERROR = "tesserae/ha/state/last_error"
STATE_TOPIC_BUSY = "tesserae/ha/state/busy"
STATE_TOPIC_IMAGE_URL = "tesserae/ha/state/image_url"
STATE_TOPIC_ACTIVE_PAGE = "tesserae/ha/state/active_page"
STATE_TOPIC_AUTOMATION = "tesserae/ha/state/automation"
STATE_TOPIC_QUIET_HOURS = "tesserae/ha/state/quiet_hours"
CMD_TOPIC_PUSH_PAGE = "tesserae/ha/cmd/push_page"
CMD_TOPIC_ACTIVE_PAGE = "tesserae/ha/cmd/active_page"
CMD_TOPIC_AUTOMATION = "tesserae/ha/cmd/automation"
CMD_TOPIC_QUIET_HOURS = "tesserae/ha/cmd/quiet_hours"
CMD_TOPIC_NOTIFY = "tesserae/ha/cmd/notify"
# One wildcard subscription each for lineup + per-display commands; the
# handler routes on the trailing leaf.
LINEUP_CMD_WILDCARD = "tesserae/ha/lineup/+/cmd/+"
DEVICE_CMD_WILDCARD = "tesserae/ha/dev/+/cmd/+"

# HA's MQTT sensor treats this payload as "state unknown".
_UNKNOWN = "None"
# The per-display Lineup select's "follow nothing" option.
LINEUP_NONE = "(none)"

# Hold entity bounds (minutes). 0 clears the hold.
HOLD_MAX_MINUTES = 720

# A display counts as online while its last heartbeat is younger than
# ``max(2 x interval + grace, floor)``; sleepy panels legitimately go
# quiet for a whole interval, so one missed beat isn't "offline".
_ONLINE_GRACE_S = 120
_ONLINE_FLOOR_S = 300

# Per-display heartbeat keys we surface as their own HA sensors, mapped to
# the ESP32 firmware's field names. Published lazily, a display only gets
# the sensor once a heartbeat actually carries the key (so Pi clients,
# which don't report battery/RSSI, stay uncluttered).
_DYN_SENSORS: dict[str, dict[str, Any]] = {
    "battery_pct": {"object": "battery", "name": "Battery", "device_class": "battery", "unit": "%"},
    "battery_mv": {
        # Native TRMNL firmware sends raw millivolts; expose them as a
        # separate diagnostic so power-curious users can wire automations
        # against the underlying voltage rather than the (derived) pct.
        "object": "battery_voltage",
        "name": "Battery voltage",
        "device_class": "voltage",
        "unit": "mV",
    },
    "rssi": {
        "object": "signal",
        "name": "Signal",
        "device_class": "signal_strength",
        "unit": "dBm",
    },
    "ip": {"object": "ip", "name": "IP address", "icon": "mdi:ip-network"},
    "fw_version": {"object": "firmware_version", "name": "Firmware", "icon": "mdi:chip"},
    "temperature_c": {
        "object": "temperature",
        "name": "Temperature",
        "device_class": "temperature",
        "unit": "°C",
    },
    "humidity_pct": {
        "object": "humidity",
        "name": "Humidity",
        "device_class": "humidity",
        "unit": "%",
    },
    "uptime_s": {
        "object": "uptime",
        "name": "Uptime",
        "device_class": "duration",
        "unit": "s",
    },
}

# Event types the per-display ``event`` entity can emit. HA drops events
# whose type isn't declared here, so keep it in sync with
# ``ButtonService._emit_*``.
_INPUT_EVENT_TYPES = ["button", "tap", "swipe", "slide"]


@dataclass
class HaHooks:
    """App-side capabilities the discovery layer can drive.

    Every field is optional. A missing hook drops the entities that
    depend on it rather than failing, which keeps the module usable in
    tests with only a transport, a push manager and a page store.

    * ``deck_store`` / ``deck_nav_store``: lineups (``Deck``) and each
      display's position in one.
    * ``settings_store``: ``get_section`` / ``patch_section``, for the
      automation pause + quiet-hours toggles.
    * ``scheduler``: ``upcoming_for_device`` (Next change sensor).
    * ``rotation_state_store``: per-display ``override_until`` (Hold).
    * ``telemetry``: ``get(device_id).predicted_next_wake_at``.
    * ``button_service``: ``add_listener`` / ``remove_listener`` for the
      input ``event`` entity.
    * ``timezone_fn``: the scheduler's timezone (quiet-hours evaluation).
    * ``invalidate_deck_fn``: drop warmed frames + nav for a deck's devices
      after it's toggled / rebound.
    * ``set_device_quiet_override_fn(device_id, enabled) -> error``.
    * ``device_config_fn(device_id) -> config doc`` and
      ``set_device_config_fn(device_id, values) -> error``.
    * ``firmware_state_fn(device_id) -> {installed_version, latest_version,
      capable, in_progress, release_url}`` and
      ``firmware_install_fn(device_id) -> error``.
    * ``notify_fn(device_id | None, message) -> error``: render text onto one
      display (``None`` = every display).
    * ``expected_interval_fn(device_id) -> seconds`` between heartbeats.
    """

    deck_store: Any = None
    deck_nav_store: Any = None
    settings_store: Any = None
    scheduler: Any = None
    rotation_state_store: Any = None
    telemetry: Any = None
    button_service: Any = None
    timezone_fn: Callable[[], tzinfo | None] | None = None
    invalidate_deck_fn: Callable[[Any], None] | None = None
    set_device_quiet_override_fn: Callable[[str, bool], str | None] | None = None
    device_config_fn: Callable[[str], dict[str, Any]] | None = None
    set_device_config_fn: Callable[[str, dict[str, Any]], str | None] | None = None
    firmware_state_fn: Callable[[str], dict[str, Any] | None] | None = None
    firmware_install_fn: Callable[[str], str | None] | None = None
    notify_fn: Callable[[str | None, str], str | None] | None = None
    expected_interval_fn: Callable[[str], int | None] | None = None


def _iso(epoch_or_dt: float | datetime) -> str:
    dt = (
        epoch_or_dt
        if isinstance(epoch_or_dt, datetime)
        else datetime.fromtimestamp(epoch_or_dt, UTC)
    )
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _hub_device(base_url: str) -> dict[str, Any]:
    """The parent HA device that owns app-level controls + diagnostics.
    Per-display devices reference it via ``via_device``."""
    return {
        "identifiers": ["tesserae"],
        "name": "Tesserae",
        "manufacturer": "tesserae",
        "model": "Composer + renderer fanout",
        "sw_version": "0.1",
        "configuration_url": base_url,
    }


def _panel_device(device_id: str, name: str, model: str) -> dict[str, Any]:
    """An HA device card for one physical display, nested under the hub."""
    return {
        "identifiers": [f"tesserae_dev_{device_id}"],
        "name": name,
        "manufacturer": "tesserae",
        "model": model,
        "via_device": "tesserae",
    }


def _discovery_topic(component: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{component}/{NODE_ID}/{object_id}/config"


def _dev_state(device_id: str, leaf: str) -> str:
    return f"tesserae/ha/dev/{device_id}/state/{leaf}"


def _dev_cmd(device_id: str, leaf: str) -> str:
    return f"tesserae/ha/dev/{device_id}/cmd/{leaf}"


def _lineup_state(deck_id: str, leaf: str) -> str:
    return f"tesserae/ha/lineup/{deck_id}/state/{leaf}"


def _lineup_cmd(deck_id: str, leaf: str) -> str:
    return f"tesserae/ha/lineup/{deck_id}/cmd/{leaf}"


def _availability_block() -> list[dict[str, str]]:
    return [
        {
            "topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
        }
    ]


def _entity(object_id: str, name: str, device: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Common discovery-payload skeleton: ids, name, availability, device."""
    payload: dict[str, Any] = {
        "name": name,
        "unique_id": f"tesserae_{object_id}",
        "object_id": f"tesserae_{object_id}",
        "availability": _availability_block(),
        "device": device,
    }
    payload.update(extra)
    return payload


# ---------- hub config builders ----------


def build_button_config(
    page_id: str, page_name: str, *, base_url: str
) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("button", f"page_{page_id}")
    payload = _entity(
        f"page_{page_id}",
        page_name,
        _hub_device(base_url),
        command_topic=CMD_TOPIC_PUSH_PAGE,
        payload_press=page_id,
        icon="mdi:image",
    )
    return topic, payload


def build_select_config(page_names: list[str], *, base_url: str) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("select", "active_page")
    payload = _entity(
        "active_page",
        "Active dashboard",
        _hub_device(base_url),
        command_topic=CMD_TOPIC_ACTIVE_PAGE,
        state_topic=STATE_TOPIC_ACTIVE_PAGE,
        options=page_names or ["(none)"],
        icon="mdi:view-dashboard",
    )
    return topic, payload


def build_image_config(*, base_url: str) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("image", "last_render")
    # HA's MQTT image schema treats ``content_type`` and ``url_topic`` as
    # mutually exclusive (``content_type`` is only valid with the bytes-
    # over-topic form, ``image_topic``). Setting both makes HA reject the
    # discovery config entirely, so the entity never appears. We're URL-
    # based, the content type is inferred from the HTTP response.
    payload = _entity(
        "last_render", "Last render", _hub_device(base_url), url_topic=STATE_TOPIC_IMAGE_URL
    )
    return topic, payload


def build_diagnostic_configs(*, base_url: str) -> list[tuple[str, dict[str, Any]]]:
    dev = _hub_device(base_url)
    return [
        (
            _discovery_topic("sensor", "last_push"),
            _entity(
                "last_push",
                "Last push",
                dev,
                state_topic=STATE_TOPIC_LAST_PUSH,
                device_class="timestamp",
                entity_category="diagnostic",
            ),
        ),
        (
            _discovery_topic("sensor", "push_count_today"),
            _entity(
                "push_count_today",
                "Pushes today",
                dev,
                state_topic=STATE_TOPIC_PUSH_COUNT,
                state_class="total_increasing",
                entity_category="diagnostic",
            ),
        ),
        (
            _discovery_topic("sensor", "last_error"),
            _entity(
                "last_error",
                "Last error",
                dev,
                state_topic=STATE_TOPIC_LAST_ERROR,
                entity_category="diagnostic",
            ),
        ),
        (
            _discovery_topic("binary_sensor", "busy"),
            _entity(
                "busy",
                "Busy",
                dev,
                state_topic=STATE_TOPIC_BUSY,
                payload_on="1",
                payload_off="0",
                entity_category="diagnostic",
            ),
        ),
    ]


def build_hub_control_configs(*, base_url: str) -> list[tuple[str, dict[str, Any]]]:
    """Automation + quiet-hours switches and the all-displays notify."""
    dev = _hub_device(base_url)
    return [
        (
            _discovery_topic("switch", "automation"),
            _entity(
                "automation",
                "Automation",
                dev,
                state_topic=STATE_TOPIC_AUTOMATION,
                command_topic=CMD_TOPIC_AUTOMATION,
                payload_on="ON",
                payload_off="OFF",
                icon="mdi:robot",
            ),
        ),
        (
            _discovery_topic("switch", "quiet_hours"),
            _entity(
                "quiet_hours",
                "Quiet hours",
                dev,
                state_topic=STATE_TOPIC_QUIET_HOURS,
                command_topic=CMD_TOPIC_QUIET_HOURS,
                payload_on="ON",
                payload_off="OFF",
                icon="mdi:moon-waning-crescent",
                entity_category="config",
            ),
        ),
        (
            _discovery_topic("notify", "notify_all"),
            _entity(
                "notify_all",
                "Notify all displays",
                dev,
                command_topic=CMD_TOPIC_NOTIFY,
                icon="mdi:message-text",
            ),
        ),
    ]


def build_lineup_configs(deck: Any, *, base_url: str) -> list[tuple[str, dict[str, Any]]]:
    """Per-lineup hub entities: the enabled switch plus action buttons.

    ``Push now`` needs a bound display; ``Next`` / ``Previous`` need at
    least two pages to step between, so they're only published when the
    lineup can actually honour them."""
    dev = _hub_device(base_url)
    deck_id = str(deck.id)
    name = str(deck.name)
    out: list[tuple[str, dict[str, Any]]] = [
        (
            _discovery_topic("switch", f"lineup_{deck_id}"),
            _entity(
                f"lineup_{deck_id}",
                name,
                dev,
                state_topic=_lineup_state(deck_id, "enabled"),
                command_topic=_lineup_cmd(deck_id, "enabled"),
                payload_on="ON",
                payload_off="OFF",
                icon="mdi:playlist-play",
            ),
        )
    ]
    if deck.device_ids:
        out.append(
            (
                _discovery_topic("button", f"lineup_{deck_id}_push"),
                _entity(
                    f"lineup_{deck_id}_push",
                    f"{name} · Push now",
                    dev,
                    command_topic=_lineup_cmd(deck_id, "action"),
                    payload_press="push",
                    icon="mdi:send",
                ),
            )
        )
        if len(deck.pages) > 1:
            out.append(
                (
                    _discovery_topic("button", f"lineup_{deck_id}_next"),
                    _entity(
                        f"lineup_{deck_id}_next",
                        f"{name} · Next",
                        dev,
                        command_topic=_lineup_cmd(deck_id, "action"),
                        payload_press="next",
                        icon="mdi:skip-next",
                    ),
                )
            )
            out.append(
                (
                    _discovery_topic("button", f"lineup_{deck_id}_prev"),
                    _entity(
                        f"lineup_{deck_id}_prev",
                        f"{name} · Previous",
                        dev,
                        command_topic=_lineup_cmd(deck_id, "action"),
                        payload_press="prev",
                        icon="mdi:skip-previous",
                    ),
                )
            )
    return out


# ---------- per-display config builders ----------


def build_device_configs(
    device_id: str,
    device_name: str,
    model: str,
    bound_page_names: list[str],
    lineup_names: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """The always-present entities for one physical display."""
    dev = _panel_device(device_id, device_name, model)
    lineups = [LINEUP_NONE, *(lineup_names or [])]
    return [
        (
            _discovery_topic("image", f"dev_{device_id}_frame"),
            # See note in build_image_config, ``content_type`` is invalid
            # alongside ``url_topic`` (HA infers it from the HTTP response).
            _entity(
                f"dev_{device_id}_frame", "Frame", dev, url_topic=_dev_state(device_id, "image_url")
            ),
        ),
        (
            _discovery_topic("sensor", f"dev_{device_id}_current"),
            _entity(
                f"dev_{device_id}_current",
                "Current dashboard",
                dev,
                state_topic=_dev_state(device_id, "current_page"),
                icon="mdi:view-dashboard",
            ),
        ),
        (
            _discovery_topic("sensor", f"dev_{device_id}_last_update"),
            _entity(
                f"dev_{device_id}_last_update",
                "Last updated",
                dev,
                state_topic=_dev_state(device_id, "last_update"),
                device_class="timestamp",
            ),
        ),
        (
            _discovery_topic("sensor", f"dev_{device_id}_last_seen"),
            _entity(
                f"dev_{device_id}_last_seen",
                "Last seen",
                dev,
                state_topic=_dev_state(device_id, "last_seen"),
                device_class="timestamp",
                entity_category="diagnostic",
            ),
        ),
        (
            _discovery_topic("select", f"dev_{device_id}_dashboard"),
            _entity(
                f"dev_{device_id}_dashboard",
                "Dashboard",
                dev,
                command_topic=_dev_cmd(device_id, "push"),
                state_topic=_dev_state(device_id, "current_page"),
                options=bound_page_names or ["(none bound)"],
                icon="mdi:dap",
            ),
        ),
        (
            _discovery_topic("select", f"dev_{device_id}_lineup"),
            _entity(
                f"dev_{device_id}_lineup",
                "Lineup",
                dev,
                command_topic=_dev_cmd(device_id, "lineup"),
                state_topic=_dev_state(device_id, "lineup"),
                options=lineups,
                icon="mdi:playlist-play",
            ),
        ),
        (
            _discovery_topic("sensor", f"dev_{device_id}_next_change"),
            _entity(
                f"dev_{device_id}_next_change",
                "Next change",
                dev,
                state_topic=_dev_state(device_id, "next_change"),
                device_class="timestamp",
                icon="mdi:timer-sand",
            ),
        ),
        (
            _discovery_topic("number", f"dev_{device_id}_hold"),
            _entity(
                f"dev_{device_id}_hold",
                "Hold",
                dev,
                command_topic=_dev_cmd(device_id, "hold"),
                state_topic=_dev_state(device_id, "hold"),
                min=0,
                max=HOLD_MAX_MINUTES,
                step=5,
                mode="box",
                unit_of_measurement="min",
                icon="mdi:pause-circle-outline",
            ),
        ),
        (
            _discovery_topic("switch", f"dev_{device_id}_quiet_override"),
            _entity(
                f"dev_{device_id}_quiet_override",
                "Quiet hours override",
                dev,
                command_topic=_dev_cmd(device_id, "quiet_override"),
                state_topic=_dev_state(device_id, "quiet_override"),
                payload_on="ON",
                payload_off="OFF",
                icon="mdi:moon-waning-crescent",
                entity_category="config",
            ),
        ),
        (
            _discovery_topic("binary_sensor", f"dev_{device_id}_quiet_now"),
            _entity(
                f"dev_{device_id}_quiet_now",
                "In quiet hours",
                dev,
                state_topic=_dev_state(device_id, "quiet_now"),
                payload_on="ON",
                payload_off="OFF",
                icon="mdi:sleep",
            ),
        ),
        (
            _discovery_topic("binary_sensor", f"dev_{device_id}_online"),
            _entity(
                f"dev_{device_id}_online",
                "Online",
                dev,
                state_topic=_dev_state(device_id, "online"),
                payload_on="ON",
                payload_off="OFF",
                device_class="connectivity",
                entity_category="diagnostic",
            ),
        ),
        (
            _discovery_topic("button", f"dev_{device_id}_refresh"),
            _entity(
                f"dev_{device_id}_refresh",
                "Refresh now",
                dev,
                command_topic=_dev_cmd(device_id, "action"),
                payload_press="refresh",
                icon="mdi:refresh",
            ),
        ),
        (
            _discovery_topic("event", f"dev_{device_id}_input"),
            _entity(
                f"dev_{device_id}_input",
                "Input",
                dev,
                state_topic=_dev_state(device_id, "event"),
                event_types=list(_INPUT_EVENT_TYPES),
                device_class="button",
            ),
        ),
        (
            _discovery_topic("notify", f"dev_{device_id}_notify"),
            _entity(
                f"dev_{device_id}_notify",
                "Notify",
                dev,
                command_topic=_dev_cmd(device_id, "notify"),
                icon="mdi:message-text",
            ),
        ),
    ]


def build_dynamic_sensor_config(
    device_id: str, device_name: str, model: str, key: str
) -> tuple[str, dict[str, Any]]:
    spec = _DYN_SENSORS[key]
    object_id = f"dev_{device_id}_{spec['object']}"
    payload = _entity(
        object_id,
        str(spec["name"]),
        _panel_device(device_id, device_name, model),
        state_topic=_dev_state(device_id, str(spec["object"])),
        entity_category="diagnostic",
    )
    for opt in ("device_class", "unit", "icon"):
        if opt in spec:
            payload["unit_of_measurement" if opt == "unit" else opt] = spec[opt]
    return _discovery_topic("sensor", object_id), payload


def build_low_battery_config(
    device_id: str, device_name: str, model: str
) -> tuple[str, dict[str, Any]]:
    object_id = f"dev_{device_id}_low_battery"
    payload = _entity(
        object_id,
        "Low battery",
        _panel_device(device_id, device_name, model),
        state_topic=_dev_state(device_id, "low_battery"),
        payload_on="ON",
        payload_off="OFF",
        device_class="battery",
        entity_category="diagnostic",
    )
    return _discovery_topic("binary_sensor", object_id), payload


def build_next_wake_config(
    device_id: str, device_name: str, model: str
) -> tuple[str, dict[str, Any]]:
    object_id = f"dev_{device_id}_next_wake"
    payload = _entity(
        object_id,
        "Next wake",
        _panel_device(device_id, device_name, model),
        state_topic=_dev_state(device_id, "next_wake"),
        device_class="timestamp",
        entity_category="diagnostic",
        icon="mdi:alarm",
    )
    return _discovery_topic("sensor", object_id), payload


def build_wake_interval_config(
    device_id: str, device_name: str, model: str, *, minimum: int, maximum: int
) -> tuple[str, dict[str, Any]]:
    object_id = f"dev_{device_id}_wake_interval"
    payload = _entity(
        object_id,
        "Wake interval",
        _panel_device(device_id, device_name, model),
        command_topic=_dev_cmd(device_id, "wake_interval"),
        state_topic=_dev_state(device_id, "wake_interval"),
        min=minimum,
        max=maximum,
        step=1,
        mode="box",
        unit_of_measurement="s",
        device_class="duration",
        entity_category="config",
        icon="mdi:timer-outline",
    )
    return _discovery_topic("number", object_id), payload


def build_update_config(device_id: str, device_name: str, model: str) -> tuple[str, dict[str, Any]]:
    object_id = f"dev_{device_id}_firmware"
    payload = _entity(
        object_id,
        "Firmware",
        _panel_device(device_id, device_name, model),
        state_topic=_dev_state(device_id, "firmware"),
        command_topic=_dev_cmd(device_id, "firmware_install"),
        payload_install="install",
        device_class="firmware",
        entity_category="config",
    )
    return _discovery_topic("update", object_id), payload


class HomeAssistantDiscovery:
    """Publishes HA discovery configs + relays HA commands to PushManager.

    Threading: command callbacks fire on the MQTT network thread; they
    hand off to PushManager.push() which has its own lock so reentry is
    safe. State publishes are best-effort, broker outages just mean
    stale HA sensors, not a crashed app. A small ticker thread refreshes
    the time-derived states (online, in quiet hours, hold remaining, next
    change) every ``state_refresh_s`` seconds.
    """

    def __init__(
        self,
        *,
        transport: MqttTransport,
        push_manager: PushManager,
        page_store: PageStore,
        base_url_fn: Callable[[], str],
        device_registry: DeviceRegistry | None = None,
        device_status: dict[str, dict[str, Any]] | None = None,
        hooks: HaHooks | None = None,
        state_refresh_s: float = 60.0,
    ) -> None:
        self._transport = transport
        self._push_manager = push_manager
        self._page_store = page_store
        self._base_url_fn = base_url_fn
        self._devices = device_registry
        self._device_status = device_status if device_status is not None else {}
        self._hooks = hooks or HaHooks()
        self._state_refresh_s = state_refresh_s
        self._started = False
        self._lock = threading.Lock()
        self._push_count_day = ""
        self._push_count = 0
        # Tracks published config object-ids so stop() can blank them and
        # we can tear down entities for pages / devices since removed.
        self._published_button_ids: set[str] = set()
        self._published_device_ids: set[str] = set()
        self._published_lineup_ids: set[str] = set()
        # (device_id, dyn-key) pairs whose lazy sensor config we've sent.
        self._published_dyn: set[tuple[str, str]] = set()
        # Every (component, object_id) config we've published this session,
        # so teardown never has to enumerate entity kinds by hand.
        self._published_configs: set[tuple[str, str]] = set()
        self._stop_event = threading.Event()
        self._ticker: threading.Thread | None = None

    # -- device helpers -------------------------------------------------

    def _bindable_devices(self) -> list[Any]:
        if self._devices is None:
            return []
        return [d for d in self._devices.all() if d.kind_of is not None and d.panel is not None]

    def _device(self, device_id: str) -> Any | None:
        if self._devices is None:
            return None
        device = self._devices.get(device_id)
        if device is None or getattr(device, "kind_of", None) is None:
            return None
        return device

    def _device_model(self, device: Any) -> str:
        panel = device.panel or {}
        w, h = panel.get("w"), panel.get("h")
        kind = device.kind_of or "device"
        return f"{kind} · {w}×{h}" if w and h else str(kind)

    def _pages_for_device(self, device_id: str) -> list[Any]:
        return [p for p in self._page_store.list() if device_id in getattr(p, "device_ids", [])]

    @staticmethod
    def _device_id_for_renderer(renderer_id: str) -> str | None:
        """Per-instance renderer ids are ``<base>__<instance>``; the suffix
        is the display's device id. Base renderers (no suffix) aren't
        bound to a specific display."""
        return renderer_id.split("__", 1)[1] if "__" in renderer_id else None

    def _page_name(self, page_id: str) -> str:
        page = self._page_store.get(page_id)
        return page.name if page is not None else page_id

    def _resolve_page_name(self, name: str, device_id: str | None = None) -> str | None:
        """Map a dashboard name (what a select sends) back to a page id.
        Restricted to a device's bound pages when ``device_id`` is set."""
        pages = self._pages_for_device(device_id) if device_id else self._page_store.list()
        for page in pages:
            if page.name == name:
                return str(page.id)
        return None

    # -- lineup helpers -------------------------------------------------

    def _decks(self) -> list[Any]:
        store = self._hooks.deck_store
        if store is None:
            return []
        try:
            return list(store.all())
        except Exception:
            logger.exception("HA discovery: deck store read failed")
            return []

    def _deck(self, deck_id: str) -> Any | None:
        for deck in self._decks():
            if str(deck.id) == deck_id:
                return deck
        return None

    def _deck_by_name(self, name: str) -> Any | None:
        for deck in self._decks():
            if str(deck.name) == name:
                return deck
        return None

    def _lineup_for_device(self, device_id: str) -> Any | None:
        """The lineup a display is following: the one it's navigating (nav
        store) when that deck still binds it, else the first enabled deck
        bound to it."""
        decks = self._decks()
        nav = self._hooks.deck_nav_store
        if nav is not None:
            rec = None
            with contextlib.suppress(Exception):
                rec = nav.get(device_id)
            if rec and rec.get("deck_id"):
                for deck in decks:
                    if str(deck.id) == rec["deck_id"] and device_id in deck.device_ids:
                        return deck
        for deck in decks:
            if deck.enabled and device_id in deck.device_ids:
                return deck
        return None

    def _tz(self) -> tzinfo | None:
        fn = self._hooks.timezone_fn
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    def _app_settings(self) -> dict[str, Any]:
        store = self._hooks.settings_store
        if store is None:
            return {}
        try:
            section = store.get_section("app")
        except Exception:
            return {}
        return dict(section) if isinstance(section, dict) else {}

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        logger.info("HA discovery: starting")
        self._publish_str(AVAILABILITY_TOPIC, "online", retain=True)
        self._sweep_stale_retained()
        self._publish_entity_configs()
        self._transport.subscribe(CMD_TOPIC_PUSH_PAGE, self._on_push_page_cmd, qos=1)
        self._transport.subscribe(CMD_TOPIC_ACTIVE_PAGE, self._on_active_page_cmd, qos=1)
        self._transport.subscribe(CMD_TOPIC_AUTOMATION, self._on_automation_cmd, qos=1)
        self._transport.subscribe(CMD_TOPIC_QUIET_HOURS, self._on_quiet_hours_cmd, qos=1)
        self._transport.subscribe(CMD_TOPIC_NOTIFY, self._on_notify_all_cmd, qos=1)
        self._transport.subscribe(LINEUP_CMD_WILDCARD, self._on_lineup_cmd, qos=1)
        self._transport.subscribe(DEVICE_CMD_WILDCARD, self._on_device_cmd, qos=1)
        self._push_manager.add_listener(self._on_push_result)
        self._page_store.add_listener(self._on_pages_changed)
        deck_store = self._hooks.deck_store
        if deck_store is not None and hasattr(deck_store, "add_listener"):
            with contextlib.suppress(Exception):
                deck_store.add_listener(self._on_decks_changed)
        button_service = self._hooks.button_service
        if button_service is not None and hasattr(button_service, "add_listener"):
            with contextlib.suppress(Exception):
                button_service.add_listener(self._on_input_event)
        # Seed diagnostic state so HA shows real values immediately.
        self._publish_str(STATE_TOPIC_PUSH_COUNT, str(self._push_count), retain=True)
        self._publish_str(STATE_TOPIC_LAST_ERROR, "", retain=True)
        self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
        # Drop any stale current_page / active_page retained values left
        # over from older Tesserae versions that published the raw
        # page_id (a digest or source label) instead of the resolved
        # name, those weren't in the select's options list, so HA
        # logged an "Invalid option" warning on every restart until the
        # next valid page push overwrote them. Empty retained payload
        # clears the retained message per MQTT spec; next valid push
        # repopulates with a real name (see _publish_device_push_state).
        self._publish_str(STATE_TOPIC_ACTIVE_PAGE, "", retain=True)
        for device in self._bindable_devices():
            self._publish_str(_dev_state(device.id, "current_page"), "", retain=True)
        self._seed_device_state()
        self._publish_hub_control_state()
        self._publish_lineup_states()
        for device in self._bindable_devices():
            self._publish_device_runtime(device)
        if self._state_refresh_s > 0:
            self._stop_event.clear()
            self._ticker = threading.Thread(
                target=self._tick_loop, name="ha-discovery-ticker", daemon=True
            )
            self._ticker.start()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        logger.info("HA discovery: stopping")
        self._stop_event.set()
        ticker, self._ticker = self._ticker, None
        if ticker is not None and ticker.is_alive():
            ticker.join(timeout=2.0)
        with contextlib.suppress(Exception):
            self._push_manager.remove_listener(self._on_push_result)
        with contextlib.suppress(Exception):
            self._page_store.remove_listener(self._on_pages_changed)
        deck_store = self._hooks.deck_store
        if deck_store is not None and hasattr(deck_store, "remove_listener"):
            with contextlib.suppress(Exception):
                deck_store.remove_listener(self._on_decks_changed)
        button_service = self._hooks.button_service
        if button_service is not None and hasattr(button_service, "remove_listener"):
            with contextlib.suppress(Exception):
                button_service.remove_listener(self._on_input_event)
        # Wipe every entity by publishing an empty retained config payload.
        for component, object_id in self._every_object_id_kind():
            self._publish_str(_discovery_topic(component, object_id), "", retain=True)
        self._published_button_ids.clear()
        self._published_device_ids.clear()
        self._published_lineup_ids.clear()
        self._published_dyn.clear()
        self._published_configs.clear()
        self._publish_str(AVAILABILITY_TOPIC, "offline", retain=True)

    def refresh_entity_configs(self) -> None:
        """Re-publish entity configs, after the base URL changed (HTTP
        port captured) or a device was added/removed."""
        if self._started:
            self._publish_entity_configs()

    def refresh_state(self) -> None:
        """Re-publish every time- or settings-derived state (the ticker's
        job, exposed for callers that just changed something)."""
        if not self._started:
            return
        try:
            self._publish_hub_control_state()
            self._publish_lineup_states()
            for device in self._bindable_devices():
                self._publish_device_runtime(device)
        except Exception:
            logger.exception("HA discovery: state refresh failed")

    def _tick_loop(self) -> None:
        while not self._stop_event.wait(self._state_refresh_s):
            if not self._started:
                return
            self.refresh_state()

    def _base_url(self) -> str:
        return self._base_url_fn().rstrip("/")

    # -- listener hooks -------------------------------------------------

    def _on_push_result(self, result: PushResult) -> None:
        try:
            now = datetime.now(UTC)
            iso = _iso(now)
            if result.status == "sent":
                self._publish_str(STATE_TOPIC_LAST_PUSH, iso, retain=True)
                self._publish_str(STATE_TOPIC_LAST_ERROR, "", retain=True)
                if result.composition_digest:
                    image_url = f"{self._base_url()}/renders/{result.composition_digest}.png"
                    self._publish_str(STATE_TOPIC_IMAGE_URL, image_url, retain=True)
                today = now.strftime("%Y-%m-%d")
                if today != self._push_count_day:
                    self._push_count_day = today
                    self._push_count = 0
                self._push_count += 1
                self._publish_str(STATE_TOPIC_PUSH_COUNT, str(self._push_count), retain=True)
                self._publish_device_push_state(result, iso)
            elif result.status == "failed" and result.error:
                self._publish_str(STATE_TOPIC_LAST_ERROR, result.error[:240], retain=True)
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
        except Exception:
            logger.exception("HA discovery: failed to publish push state")

    def _publish_device_push_state(self, result: PushResult, iso: str) -> None:
        """Update each display the push actually reached: current dashboard,
        frame image, and last-updated timestamp.

        ``current_page`` and the hub-level ``active_page`` only get
        written when the push corresponds to a saved Page, non-page
        pushes (file uploads, ``push_image``, ``push_webpage``) carry a
        ``page_id`` that's actually a source label or URL, which would
        otherwise land on the select's state topic as an invalid option
        (HA logs a warning per push). Frame image + last-update still
        fire for every push, since those are informational, not
        constrained to a fixed options list."""
        saved_page = self._page_store.get(result.page_id)
        page_name = saved_page.name if saved_page is not None else None
        image_url = (
            f"{self._base_url()}/renders/{result.composition_digest}.png"
            if result.composition_digest
            else ""
        )
        if page_name is not None:
            # Keep the hub-level active-dashboard select in sync. Without
            # this, a push fired from Tesserae's own UI leaves HA's
            # select showing whatever the user last picked there.
            self._publish_str(STATE_TOPIC_ACTIVE_PAGE, page_name, retain=True)
        seen: set[str] = set()
        for renderer in result.renderers:
            if renderer.error:
                continue
            device_id = self._device_id_for_renderer(renderer.renderer_id)
            if device_id is None or device_id in seen:
                continue
            seen.add(device_id)
            if page_name is not None:
                self._publish_str(_dev_state(device_id, "current_page"), page_name, retain=True)
            self._publish_str(_dev_state(device_id, "last_update"), iso, retain=True)
            if image_url:
                self._publish_str(_dev_state(device_id, "image_url"), image_url, retain=True)
            device = self._device(device_id)
            if device is not None:
                self._publish_device_runtime(device)

    def note_device_heartbeat(self, device_id: str, parsed: dict[str, Any]) -> None:
        """Called from the MQTT status subscriber on each heartbeat: publish
        last-seen + any battery / signal / IP values (lazily creating their
        sensor configs the first time a key appears)."""
        if not self._started:
            return
        try:
            self._publish_str(
                _dev_state(device_id, "last_seen"), _iso(datetime.now(UTC)), retain=True
            )
            self._publish_dynamic(device_id, parsed)
            device = self._device(device_id)
            if device is not None:
                self._publish_device_runtime(device)
        except Exception:
            logger.exception("HA discovery: failed to publish heartbeat state for %s", device_id)

    def _publish_dynamic(self, device_id: str, parsed: dict[str, Any]) -> None:
        device = self._devices.get(device_id) if self._devices is not None else None
        # Apply the per-device battery-display offset before publishing
        # so HA's Battery / Battery voltage sensors read the same
        # calibrated value the dashboard + topbar indicator show. Raw
        # firmware values still live in ``parsed`` (and on the SQLite
        # history rows); the offset is a display-layer concern, applied
        # to every read site that surfaces battery to a human.
        adjusted = dict(parsed)
        if device is not None:
            from app.battery_offset import apply_to_mv, apply_to_pct, get_offset

            mv_off, pct_off = get_offset(device.manifest)
            if mv_off != 0 or pct_off != 0:
                raw_pct = parsed.get("battery_pct")
                raw_mv = parsed.get("battery_mv")
                try:
                    raw_pct_int = int(raw_pct) if raw_pct is not None else None
                except (TypeError, ValueError):
                    raw_pct_int = None
                try:
                    raw_mv_int = int(raw_mv) if raw_mv is not None else None
                except (TypeError, ValueError):
                    raw_mv_int = None
                adj_pct = apply_to_pct(raw_pct_int, mv_off, pct_off, raw_mv=raw_mv_int)
                adj_mv = apply_to_mv(raw_mv_int, mv_off)
                if adj_pct is not None:
                    adjusted["battery_pct"] = adj_pct
                if adj_mv is not None:
                    adjusted["battery_mv"] = adj_mv
        for key, spec in _DYN_SENSORS.items():
            value = adjusted.get(key)
            if value in (None, ""):
                continue
            if (device_id, key) not in self._published_dyn and device is not None:
                topic, payload = build_dynamic_sensor_config(
                    device_id, device.display_name, self._device_model(device), key
                )
                self._publish_config(topic, payload)
                self._published_dyn.add((device_id, key))
            self._publish_str(_dev_state(device_id, spec["object"]), str(value), retain=True)
        self._publish_low_battery(device_id, device, adjusted.get("battery_pct"))

    def _publish_low_battery(self, device_id: str, device: Any | None, pct: Any) -> None:
        if pct in (None, "") or device is None:
            return
        try:
            pct_int = int(float(pct))
        except (TypeError, ValueError):
            return
        threshold_raw = self._app_settings().get("low_battery_threshold", 15)
        try:
            threshold = int(threshold_raw)
        except (TypeError, ValueError):
            threshold = 15
        if (device_id, "low_battery") not in self._published_dyn:
            topic, payload = build_low_battery_config(
                device_id, device.display_name, self._device_model(device)
            )
            self._publish_config(topic, payload)
            self._published_dyn.add((device_id, "low_battery"))
        self._publish_str(
            _dev_state(device_id, "low_battery"),
            "ON" if pct_int <= threshold else "OFF",
            retain=True,
        )

    def _on_pages_changed(self) -> None:
        if not self._started:
            return
        try:
            self._publish_entity_configs()
        except Exception:
            logger.exception("HA discovery: failed to republish entity configs")

    def _on_decks_changed(self) -> None:
        if not self._started:
            return
        try:
            self._publish_entity_configs()
            self._publish_lineup_states()
            for device in self._bindable_devices():
                self._publish_device_runtime(device)
        except Exception:
            logger.exception("HA discovery: failed to republish lineup configs")

    def _on_input_event(self, event: dict[str, Any]) -> None:
        """A physical button / touch was handled: fire the display's
        ``event`` entity. Not retained, an event is a moment."""
        if not self._started:
            return
        device_id = str(event.get("device_id") or "")
        kind = str(event.get("kind") or "button")
        if not device_id or kind not in _INPUT_EVENT_TYPES:
            return
        payload: dict[str, Any] = {"event_type": kind}
        for key in ("name", "outcome", "action", "value", "page_id"):
            if event.get(key) is not None:
                payload[key] = event[key]
        page_id = event.get("page_id")
        if isinstance(page_id, str) and page_id:
            payload["page_name"] = self._page_name(page_id)
        self._publish_json(_dev_state(device_id, "event"), payload, retain=False)

    # -- hub commands ---------------------------------------------------

    def _on_push_page_cmd(self, _topic: str, payload: bytes) -> None:
        page_id = payload.decode("utf-8", errors="ignore").strip()
        if not page_id:
            return
        logger.info("HA discovery: HA requested push of page %r", page_id)
        self._publish_str(STATE_TOPIC_BUSY, "1", retain=True)
        try:
            self._push_manager.push(page_id, source="home_assistant")
        except Exception:
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
            raise

    def _on_active_page_cmd(self, topic: str, payload: bytes) -> None:
        name = payload.decode("utf-8", errors="ignore").strip()
        page_id = self._resolve_page_name(name)
        if page_id is None:
            return
        self._publish_str(STATE_TOPIC_ACTIVE_PAGE, name, retain=True)
        self._on_push_page_cmd(topic, page_id.encode("utf-8"))

    @staticmethod
    def _on_off(payload: bytes) -> bool | None:
        text = payload.decode("utf-8", errors="ignore").strip().upper()
        if text in ("ON", "1", "TRUE"):
            return True
        if text in ("OFF", "0", "FALSE"):
            return False
        return None

    def _on_automation_cmd(self, _topic: str, payload: bytes) -> None:
        """Automation switch: ON = scheduler runs, OFF = paused."""
        on = self._on_off(payload)
        store = self._hooks.settings_store
        if on is None or store is None:
            return
        logger.info("HA discovery: automation %s", "resumed" if on else "paused")
        try:
            store.patch_section("app", {"automation_paused": not on})
        except Exception:
            logger.exception("HA discovery: automation toggle failed")
            return
        self._publish_hub_control_state()

    def _on_quiet_hours_cmd(self, _topic: str, payload: bytes) -> None:
        on = self._on_off(payload)
        store = self._hooks.settings_store
        if on is None or store is None:
            return
        logger.info("HA discovery: quiet hours %s", "enabled" if on else "disabled")
        try:
            store.patch_section("app", {"quiet_hours_enabled": on})
        except Exception:
            logger.exception("HA discovery: quiet hours toggle failed")
            return
        self._publish_hub_control_state()
        for device in self._bindable_devices():
            self._publish_device_runtime(device)

    def _on_notify_all_cmd(self, _topic: str, payload: bytes) -> None:
        message = payload.decode("utf-8", errors="ignore").strip()
        self._notify(None, message)

    def _notify(self, device_id: str | None, message: str) -> None:
        fn = self._hooks.notify_fn
        if fn is None or not message:
            return
        logger.info("HA discovery: notify %s", device_id or "all displays")
        self._publish_str(STATE_TOPIC_BUSY, "1", retain=True)
        try:
            err = fn(device_id, message)
        except Exception:
            logger.exception("HA discovery: notify failed")
            err = "notify failed"
        finally:
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
        if err:
            self._publish_str(STATE_TOPIC_LAST_ERROR, err[:240], retain=True)

    # -- lineup commands ------------------------------------------------

    def _on_lineup_cmd(self, topic: str, payload: bytes) -> None:
        """Topic is ``tesserae/ha/lineup/<deck_id>/cmd/<leaf>``."""
        try:
            rest = topic.split("/lineup/", 1)[1]
            deck_id, leaf = rest.split("/cmd/", 1)
        except (IndexError, ValueError):
            return
        deck = self._deck(deck_id)
        if deck is None:
            return
        text = payload.decode("utf-8", errors="ignore").strip()
        try:
            if leaf == "enabled":
                on = self._on_off(payload)
                if on is not None:
                    self._set_lineup_enabled(deck, on)
            elif leaf == "action":
                if text == "push":
                    self._lineup_go_live(deck, set(deck.device_ids))
                elif text in ("next", "prev"):
                    self._lineup_step(deck, 1 if text == "next" else -1)
        except Exception:
            logger.exception("HA discovery: lineup command %s/%s failed", deck_id, leaf)

    def _set_lineup_enabled(self, deck: Any, enabled: bool) -> None:
        store = self._hooks.deck_store
        if store is None or bool(deck.enabled) == enabled:
            return
        logger.info("HA discovery: lineup %r %s", deck.id, "enabled" if enabled else "disabled")
        updated = deck.model_copy(update={"enabled": enabled})
        store.upsert(updated)
        if self._hooks.invalidate_deck_fn is not None:
            with contextlib.suppress(Exception):
                self._hooks.invalidate_deck_fn(updated)
        # The store listener republishes configs + states; publish the
        # switch state directly too so a store without listeners still
        # reflects the change immediately.
        self._publish_str(
            _lineup_state(str(deck.id), "enabled"), "ON" if enabled else "OFF", retain=True
        )

    def _lineup_go_live(self, deck: Any, device_ids: set[str]) -> None:
        """Warm every page for the devices, push the entry page, and record
        the nav position: the same "make this lineup live" action the Lineups
        page offers."""
        if not device_ids or not deck.pages:
            return
        pusher = self._push_manager
        self._publish_str(STATE_TOPIC_BUSY, "1", retain=True)
        try:
            warm = getattr(pusher, "warm_deck_page", None)
            if callable(warm):
                for device_id in device_ids:
                    for page in deck.pages:
                        with contextlib.suppress(Exception):
                            warm(page.page_id, device_id)
            entry = getattr(deck, "resolved_entry_page_id", None) or deck.pages[0].page_id
            pusher.push(
                entry,
                device_ids=set(device_ids),
                respect_quiet_hours=False,
                force_publish=True,
                source="home_assistant",
            )
            nav = self._hooks.deck_nav_store
            if nav is not None:
                for device_id in device_ids:
                    with contextlib.suppress(Exception):
                        nav.set(device_id, deck.id, entry)
        except Exception:
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
            raise
        for device_id in device_ids:
            device = self._device(device_id)
            if device is not None:
                self._publish_device_runtime(device)

    def _lineup_step(self, deck: Any, delta: int) -> None:
        """Move each bound display one page through the lineup order,
        promoting the warmed frame when there is one (mirrors the Lineups
        page stepper)."""
        if not deck.device_ids or not deck.pages:
            return
        order = [dp.page_id for dp in deck.pages]
        nav = self._hooks.deck_nav_store
        pusher = self._push_manager
        for device_id in deck.device_ids:
            rec = None
            if nav is not None:
                with contextlib.suppress(Exception):
                    rec = nav.get(device_id)
            current = rec.get("page_id") if rec and rec.get("deck_id") == deck.id else None
            try:
                idx = order.index(current) if current is not None else (-delta if delta > 0 else 0)
            except ValueError:
                idx = 0
            target = order[(idx + delta) % len(order)]
            promoter = getattr(pusher, "promote_deck_page", None)
            promoted = bool(callable(promoter) and promoter(device_id, target))
            if not promoted:
                result = pusher.push(
                    target,
                    device_ids={device_id},
                    respect_quiet_hours=False,
                    source="home_assistant",
                )
                if result.status == "failed":
                    continue
            if nav is not None:
                with contextlib.suppress(Exception):
                    nav.set(device_id, deck.id, target)
            device = self._device(device_id)
            if device is not None:
                self._publish_device_runtime(device)

    # -- per-display commands -------------------------------------------

    def _on_device_cmd(self, topic: str, payload: bytes) -> None:
        """Topic is ``tesserae/ha/dev/<device_id>/cmd/<leaf>``; route on leaf."""
        try:
            rest = topic.split("/dev/", 1)[1]
            device_id, leaf = rest.split("/cmd/", 1)
        except (IndexError, ValueError):
            return
        if leaf == "push":
            self._on_device_push_cmd(topic, payload)
            return
        device = self._device(device_id)
        if device is None:
            return
        text = payload.decode("utf-8", errors="ignore").strip()
        try:
            if leaf == "lineup":
                self._set_device_lineup(device, text)
            elif leaf == "hold":
                self._set_device_hold(device, text)
            elif leaf == "quiet_override":
                on = self._on_off(payload)
                if on is not None:
                    self._set_device_quiet_override(device, on)
            elif leaf == "action":
                if text == "refresh":
                    self._refresh_device(device)
            elif leaf == "wake_interval":
                self._set_wake_interval(device, text)
            elif leaf == "firmware_install":
                self._install_firmware(device)
            elif leaf == "notify":
                self._notify(device.id, text)
        except Exception:
            logger.exception("HA discovery: device command %s/%s failed", device_id, leaf)

    def _on_device_push_cmd(self, topic: str, payload: bytes) -> None:
        """A per-display Dashboard select fired: push the chosen dashboard
        to ONLY that display. Topic is ``tesserae/ha/dev/<id>/cmd/push``."""
        try:
            device_id = topic.split("/dev/", 1)[1].split("/", 1)[0]
        except IndexError:
            return
        name = payload.decode("utf-8", errors="ignore").strip()
        page_id = self._resolve_page_name(name, device_id=device_id)
        if page_id is None:
            return
        logger.info("HA discovery: push %r to display %r", name, device_id)
        self._publish_str(STATE_TOPIC_BUSY, "1", retain=True)
        try:
            self._push_manager.push(page_id, device_ids={device_id}, source="home_assistant")
        except Exception:
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
            raise

    def _set_device_lineup(self, device: Any, name: str) -> None:
        """Lineup select: bind the display to the named lineup and make it
        live there; ``(none)`` unbinds it from every lineup."""
        store = self._hooks.deck_store
        if store is None:
            return
        device_id = str(device.id)
        if name == LINEUP_NONE:
            for deck in self._decks():
                if device_id in deck.device_ids:
                    updated = deck.model_copy(
                        update={"device_ids": [d for d in deck.device_ids if d != device_id]}
                    )
                    store.upsert(updated)
                    if self._hooks.invalidate_deck_fn is not None:
                        with contextlib.suppress(Exception):
                            self._hooks.invalidate_deck_fn(deck)
            nav = self._hooks.deck_nav_store
            if nav is not None:
                with contextlib.suppress(Exception):
                    nav.clear(device_id)
            self._publish_device_runtime(device)
            return
        deck = self._deck_by_name(name)
        if deck is None:
            return
        logger.info("HA discovery: display %r follows lineup %r", device_id, deck.id)
        if device_id not in deck.device_ids:
            deck = deck.model_copy(update={"device_ids": [*deck.device_ids, device_id]})
            store.upsert(deck)
        self._lineup_go_live(deck, {device_id})

    def _set_device_hold(self, device: Any, text: str) -> None:
        """Hold number: N minutes holds the current page against the
        rotation (``override_until``); 0 clears the hold."""
        store = self._hooks.rotation_state_store
        if store is None:
            return
        try:
            minutes = max(0, min(int(float(text)), HOLD_MAX_MINUTES))
        except (TypeError, ValueError):
            return
        from app.state.device_rotation_state_model import DeviceRotationState

        state = store.get(device.id) or DeviceRotationState(device_id=device.id)
        until = datetime.now(UTC) + timedelta(minutes=minutes) if minutes > 0 else None
        store.upsert(state.model_copy(update={"override_until": until}))
        logger.info("HA discovery: hold %r for %d min", device.id, minutes)
        self._publish_device_runtime(device)

    def _set_device_quiet_override(self, device: Any, enabled: bool) -> None:
        fn = self._hooks.set_device_quiet_override_fn
        if fn is None:
            return
        err = fn(device.id, enabled)
        if err:
            self._publish_str(STATE_TOPIC_LAST_ERROR, err[:240], retain=True)
        # The instance file reload replaces the Device object; re-resolve.
        fresh = self._device(device.id) or device
        self._publish_device_runtime(fresh)

    def _refresh_device(self, device: Any) -> None:
        """Re-render whatever the display is showing and force a repaint."""
        page_id: str | None = None
        latest = getattr(self._push_manager, "latest_render_for", None)
        if callable(latest):
            info = latest(device.id)
            if isinstance(info, dict) and isinstance(info.get("page_id"), str):
                page_id = info["page_id"]
        if page_id is None:
            deck = self._lineup_for_device(device.id)
            nav = self._hooks.deck_nav_store
            if deck is not None and nav is not None:
                with contextlib.suppress(Exception):
                    rec = nav.get(device.id)
                    if rec and rec.get("deck_id") == deck.id:
                        page_id = rec.get("page_id")
        if page_id is None:
            bound = self._pages_for_device(device.id)
            page_id = str(bound[0].id) if bound else None
        if page_id is None or self._page_store.get(page_id) is None:
            return
        logger.info("HA discovery: refresh %r on display %r", page_id, device.id)
        self._publish_str(STATE_TOPIC_BUSY, "1", retain=True)
        try:
            self._push_manager.push(
                page_id,
                device_ids={device.id},
                force_publish=True,
                bypass_coalesce=True,
                source="home_assistant",
            )
        except Exception:
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
            raise

    def _set_wake_interval(self, device: Any, text: str) -> None:
        fn = self._hooks.set_device_config_fn
        bounds = self._wake_interval_bounds(device)
        if fn is None or bounds is None:
            return
        try:
            seconds = int(float(text))
        except (TypeError, ValueError):
            return
        seconds = max(bounds[0], min(seconds, bounds[1]))
        err = fn(device.id, {"sleep_interval_s": seconds})
        if err:
            self._publish_str(STATE_TOPIC_LAST_ERROR, err[:240], retain=True)
        self._publish_device_runtime(device)

    def _install_firmware(self, device: Any) -> None:
        fn = self._hooks.firmware_install_fn
        if fn is None:
            return
        logger.info("HA discovery: firmware install requested for %r", device.id)
        err = fn(device.id)
        if err:
            self._publish_str(STATE_TOPIC_LAST_ERROR, err[:240], retain=True)
        self._publish_device_runtime(device)

    # -- state publishing -----------------------------------------------

    def _publish_hub_control_state(self) -> None:
        if self._hooks.settings_store is None:
            return
        app = self._app_settings()
        self._publish_str(
            STATE_TOPIC_AUTOMATION,
            "OFF" if app.get("automation_paused") else "ON",
            retain=True,
        )
        self._publish_str(
            STATE_TOPIC_QUIET_HOURS,
            "ON" if app.get("quiet_hours_enabled") else "OFF",
            retain=True,
        )

    def _publish_lineup_states(self) -> None:
        for deck in self._decks():
            self._publish_str(
                _lineup_state(str(deck.id), "enabled"),
                "ON" if deck.enabled else "OFF",
                retain=True,
            )

    def _wake_interval_bounds(self, device: Any) -> tuple[int, int] | None:
        schema = getattr(device, "config_schema", None) or {}
        spec = schema.get("sleep_interval_s") if isinstance(schema, dict) else None
        if not isinstance(spec, dict):
            return None
        try:
            lo = int(spec.get("min", 5))
            hi = int(spec.get("max", 86_400))
        except (TypeError, ValueError):
            lo, hi = 5, 86_400
        return (max(1, lo), max(lo + 1, hi))

    def _is_online(self, device: Any) -> bool | None:
        status = self._device_status.get(device.id) or {}
        received_at = status.get("received_at")
        if not isinstance(received_at, (int, float)):
            return None
        interval: int | None = None
        fn = self._hooks.expected_interval_fn
        if fn is not None:
            with contextlib.suppress(Exception):
                interval = fn(device.id)
        window = max(2 * (interval or 3600) + _ONLINE_GRACE_S, _ONLINE_FLOOR_S)
        return (datetime.now(UTC).timestamp() - float(received_at)) < window

    def _publish_device_runtime(self, device: Any) -> None:
        """Everything about a display that isn't a heartbeat field or a push
        result: lineup, hold, quiet hours, online, next change / wake,
        wake interval and firmware."""
        device_id = str(device.id)
        name, model = device.display_name, self._device_model(device)
        now = datetime.now(UTC)

        if self._hooks.deck_store is not None:
            deck = self._lineup_for_device(device_id)
            self._publish_str(
                _dev_state(device_id, "lineup"),
                str(deck.name) if deck is not None else LINEUP_NONE,
                retain=True,
            )

        if self._hooks.rotation_state_store is not None:
            remaining = 0
            with contextlib.suppress(Exception):
                state = self._hooks.rotation_state_store.get(device_id)
                until = getattr(state, "override_until", None) if state is not None else None
                if isinstance(until, datetime):
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=UTC)
                    remaining = max(0, int((until - now).total_seconds() // 60))
            self._publish_str(_dev_state(device_id, "hold"), str(remaining), retain=True)

        if self._hooks.settings_store is not None:
            from app.quiet_hours import device_is_quiet

            manifest = getattr(device, "manifest", None) or {}
            override = manifest.get("quiet_hours") if isinstance(manifest, dict) else None
            override_on = bool(isinstance(override, dict) and override.get("enabled"))
            self._publish_str(
                _dev_state(device_id, "quiet_override"), "ON" if override_on else "OFF", retain=True
            )
            quiet = False
            with contextlib.suppress(Exception):
                quiet = bool(device_is_quiet(self._app_settings(), device, now, self._tz()))
            self._publish_str(
                _dev_state(device_id, "quiet_now"), "ON" if quiet else "OFF", retain=True
            )

        online = self._is_online(device)
        if online is not None:
            self._publish_str(
                _dev_state(device_id, "online"), "ON" if online else "OFF", retain=True
            )

        scheduler = self._hooks.scheduler
        if scheduler is not None and hasattr(scheduler, "upcoming_for_device"):
            next_change = _UNKNOWN
            try:
                from app.quiet_hours import resolve_quiet_hours

                window = resolve_quiet_hours(self._app_settings(), device)
                events = scheduler.upcoming_for_device(
                    device_id, now=now, limit=1, quiet_window=window
                )
                if events:
                    next_change = _iso(events[0].scheduled_at)
            except Exception:
                logger.debug("HA discovery: upcoming projection failed for %s", device_id)
            self._publish_str(_dev_state(device_id, "next_change"), next_change, retain=True)

        telemetry = self._hooks.telemetry
        if telemetry is not None:
            predicted = None
            with contextlib.suppress(Exception):
                rec = telemetry.get(device_id)
                predicted = getattr(rec, "predicted_next_wake_at", None) if rec else None
            if isinstance(predicted, (int, float)):
                if (device_id, "next_wake") not in self._published_dyn:
                    topic, payload = build_next_wake_config(device_id, name, model)
                    self._publish_config(topic, payload)
                    self._published_dyn.add((device_id, "next_wake"))
                self._publish_str(_dev_state(device_id, "next_wake"), _iso(predicted), retain=True)

        bounds = self._wake_interval_bounds(device)
        if bounds is not None and self._hooks.device_config_fn is not None:
            if (device_id, "wake_interval") not in self._published_dyn:
                topic, payload = build_wake_interval_config(
                    device_id, name, model, minimum=bounds[0], maximum=bounds[1]
                )
                self._publish_config(topic, payload)
                self._published_dyn.add((device_id, "wake_interval"))
            current: Any = None
            with contextlib.suppress(Exception):
                current = (self._hooks.device_config_fn(device_id) or {}).get("sleep_interval_s")
            if current not in (None, ""):
                self._publish_str(_dev_state(device_id, "wake_interval"), str(current), retain=True)

        fw_fn = self._hooks.firmware_state_fn
        if fw_fn is not None:
            info = None
            with contextlib.suppress(Exception):
                info = fw_fn(device_id)
            if isinstance(info, dict) and info.get("capable"):
                if (device_id, "firmware") not in self._published_dyn:
                    topic, payload = build_update_config(device_id, name, model)
                    self._publish_config(topic, payload)
                    self._published_dyn.add((device_id, "firmware"))
                installed = info.get("installed_version")
                latest = info.get("latest_version") or installed
                fw_state: dict[str, Any] = {
                    "installed_version": str(installed) if installed else _UNKNOWN,
                    "latest_version": str(latest) if latest else _UNKNOWN,
                    "title": f"{device.kind_of} firmware",
                    "in_progress": bool(info.get("in_progress")),
                }
                if info.get("release_url"):
                    fw_state["release_url"] = str(info["release_url"])
                self._publish_json(_dev_state(device_id, "firmware"), fw_state, retain=True)

    # -- discovery publishing ------------------------------------------

    def _sweep_stale_retained(self) -> None:
        """One-shot scan at start(): blank retained discovery configs left
        on the broker by a previous Tesserae session for devices, lineups
        or dashboards that no longer exist.

        In-session deletes are already handled by ``_publish_entity_configs``
        (it diffs against the ``_published_*`` sets and blanks the
        difference). But when a device is deleted while Tesserae is *not*
        running, the next session has no memory of previously publishing
        for that id, so the retained config sits on the broker forever, HA
        keeps showing a ghost device with the old name. This sweep closes
        that gap by treating the broker itself as source-of-truth for what's
        already been published.

        We collect retained configs for a short window, identify which
        ones reference current devices/pages/lineups, and blank everything
        else under our discovery namespace."""
        import time

        live_device_ids = {d.id for d in self._bindable_devices()}
        live_page_ids = {p.id for p in self._page_store.list()}
        live_deck_ids = {str(d.id) for d in self._decks()}
        to_blank: set[str] = set()

        def lineup_is_live(rest: str) -> bool:
            if rest in live_deck_ids:
                return True
            for suffix in ("_push", "_next", "_prev"):
                if rest.endswith(suffix) and rest[: -len(suffix)] in live_deck_ids:
                    return True
            return False

        def on_config(topic: str, payload: bytes) -> None:
            if not payload:
                return
            try:
                cfg = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Corrupt payloads also count as stale, blank them.
                to_blank.add(topic)
                return
            if not isinstance(cfg, dict):
                to_blank.add(topic)
                return
            device = cfg.get("device") or {}
            ids = device.get("identifiers") if isinstance(device, dict) else None
            primary = ids[0] if isinstance(ids, list) and ids else None
            if primary == "tesserae":
                # Hub-owned entity. The hub entities that come and go are the
                # per-dashboard buttons (``page_<page_id>``) and the per-lineup
                # switch + buttons (``lineup_<deck_id>[_push|_next|_prev]``);
                # the fixed ones always belong, so leave them alone.
                obj = cfg.get("object_id")
                if not isinstance(obj, str):
                    return
                obj = obj.removeprefix("tesserae_")
                if obj.startswith("page_"):
                    if obj[len("page_") :] not in live_page_ids:
                        to_blank.add(topic)
                elif obj.startswith("lineup_") and not lineup_is_live(obj[len("lineup_") :]):
                    to_blank.add(topic)
                return
            if isinstance(primary, str) and primary.startswith("tesserae_dev_"):
                device_id = primary[len("tesserae_dev_") :]
                if device_id not in live_device_ids:
                    to_blank.add(topic)
                return
            # Unknown identifier shape under our prefix, leave it; HA will
            # ignore it if it's not ours.

        wildcard = f"{DISCOVERY_PREFIX}/+/{NODE_ID}/+/config"
        try:
            self._transport.subscribe(wildcard, on_config, qos=0)
        except Exception:
            logger.warning("HA discovery: sweep subscribe failed", exc_info=True)
            return
        # Brokers deliver retained messages immediately on subscribe; 1.5s
        # is comfortably over the round-trip even on slow LAN.
        time.sleep(1.5)
        try:
            self._transport.unsubscribe(on_config)
        except Exception:
            logger.warning("HA discovery: sweep unsubscribe failed", exc_info=True)
        for topic in to_blank:
            self._publish_str(topic, "", retain=True)
        if to_blank:
            logger.info("HA discovery: cleared %d stale retained config(s)", len(to_blank))

    def _publish_entity_configs(self) -> None:
        pages = self._page_store.list()
        decks = self._decks()
        base = self._base_url()

        seen_ids: set[str] = set()
        for page in pages:
            topic, payload = build_button_config(page.id, page.name, base_url=base)
            self._publish_config(topic, payload)
            seen_ids.add(page.id)
        for page_id in self._published_button_ids - seen_ids:
            self._blank_config("button", f"page_{page_id}")
        self._published_button_ids = seen_ids

        page_names = sorted({p.name for p in pages})
        topic, payload = build_select_config(page_names, base_url=base)
        self._publish_config(topic, payload)

        topic, payload = build_image_config(base_url=base)
        self._publish_config(topic, payload)
        for topic, payload in build_diagnostic_configs(base_url=base):
            self._publish_config(topic, payload)
        if self._hooks.settings_store is not None or self._hooks.notify_fn is not None:
            for topic, payload in build_hub_control_configs(base_url=base):
                # Switches need the settings store, notify needs its hook.
                if "/notify/" in topic and self._hooks.notify_fn is None:
                    continue
                if "/switch/" in topic and self._hooks.settings_store is None:
                    continue
                self._publish_config(topic, payload)

        # Per-lineup hub entities.
        seen_decks: set[str] = set()
        for deck in decks:
            for topic, payload in build_lineup_configs(deck, base_url=base):
                self._publish_config(topic, payload)
            seen_decks.add(str(deck.id))
        for deck_id in self._published_lineup_ids - seen_decks:
            self._blank_configs_with_prefix(f"lineup_{deck_id}")
        # A lineup that lost its displays / pages drops its action buttons.
        live_lineup_objects = {
            topic.split("/")[3]
            for deck in decks
            for topic, _payload in build_lineup_configs(deck, base_url=base)
        }
        for component, object_id in list(self._published_configs):
            if object_id.startswith("lineup_") and object_id not in live_lineup_objects:
                self._blank_config(component, object_id)
        self._published_lineup_ids = seen_decks

        # Per-display devices.
        lineup_names = sorted({str(d.name) for d in decks}) if self._hooks.deck_store else None
        seen_devices: set[str] = set()
        for device in self._bindable_devices():
            bound = sorted({p.name for p in self._pages_for_device(device.id)})
            configs = build_device_configs(
                device.id, device.display_name, self._device_model(device), bound, lineup_names
            )
            for topic, payload in configs:
                # Entities that need a hook to do anything stay unpublished
                # without it, so a bare install doesn't show dead controls.
                object_id = topic.split("/")[3]
                if object_id.endswith("_lineup") and self._hooks.deck_store is None:
                    continue
                if object_id.endswith("_hold") and self._hooks.rotation_state_store is None:
                    continue
                if (
                    object_id.endswith(("_quiet_override", "_quiet_now"))
                    and self._hooks.settings_store is None
                ):
                    continue
                if object_id.endswith("_next_change") and self._hooks.scheduler is None:
                    continue
                if object_id.endswith("_notify") and self._hooks.notify_fn is None:
                    continue
                if object_id.endswith("_input") and self._hooks.button_service is None:
                    continue
                self._publish_config(topic, payload)
            seen_devices.add(device.id)
        # Tear down displays that no longer exist (incl. their dyn sensors).
        for device_id in self._published_device_ids - seen_devices:
            self._blank_configs_with_prefix(f"dev_{device_id}_")
        self._published_dyn = {pair for pair in self._published_dyn if pair[0] in seen_devices}
        self._published_device_ids = seen_devices

    def _device_config_topics(self, device_id: str) -> list[str]:
        prefix = f"dev_{device_id}_"
        return [
            _discovery_topic(component, object_id)
            for component, object_id in sorted(self._published_configs)
            if object_id.startswith(prefix)
        ]

    def _seed_device_state(self) -> None:
        """Publish current heartbeat-derived state for displays we already
        have a cached status for, so HA isn't blank until the next (maybe
        far-off) heartbeat from a sleepy device."""
        for device in self._bindable_devices():
            status = self._device_status.get(device.id)
            if not status:
                continue
            received_at = status.get("received_at")
            if isinstance(received_at, (int, float)):
                self._publish_str(
                    _dev_state(device.id, "last_seen"), _iso(received_at), retain=True
                )
            parsed = status.get("parsed")
            if isinstance(parsed, dict):
                self._publish_dynamic(device.id, parsed)

    def _every_object_id_kind(self) -> list[tuple[str, str]]:
        """All (component, object_id) pairs we've published, used by stop()
        to blank every retained config so HA drops every entity cleanly."""
        return sorted(self._published_configs)

    # -- publish wrappers ----------------------------------------------

    def _publish_config(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish a retained discovery config and remember its identity."""
        parts = topic.split("/")
        if len(parts) >= 4:
            self._published_configs.add((parts[1], parts[3]))
        self._publish_json(topic, payload, retain=True)

    def _blank_config(self, component: str, object_id: str) -> None:
        self._published_configs.discard((component, object_id))
        self._publish_str(_discovery_topic(component, object_id), "", retain=True)

    def _blank_configs_with_prefix(self, prefix: str) -> None:
        for component, object_id in sorted(self._published_configs):
            if object_id.startswith(prefix):
                self._blank_config(component, object_id)

    def _publish_json(self, topic: str, payload: dict[str, Any], *, retain: bool = True) -> None:
        if not self._transport.connected:
            # Discovery configs are retained, they'll re-publish on the
            # next start()/refresh after reconnect. Dropping silently
            # keeps shutdown / settings-swap from dumping tracebacks.
            logger.debug("HA discovery: skipping publish to %s (broker disconnected)", topic)
            return
        try:
            self._transport.publish(
                topic, json.dumps(payload, sort_keys=True).encode("utf-8"), qos=1, retain=retain
            )
        except RuntimeError as err:
            logger.debug("HA discovery: publish to %s skipped: %s", topic, err)
        except Exception:
            logger.warning("HA discovery: publish to %s failed", topic, exc_info=True)

    def _publish_str(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if not self._transport.connected:
            logger.debug("HA discovery: skipping publish to %s (broker disconnected)", topic)
            return
        try:
            self._transport.publish(topic, payload.encode("utf-8"), qos=1, retain=retain)
        except RuntimeError as err:
            logger.debug("HA discovery: publish to %s skipped: %s", topic, err)
        except Exception:
            logger.warning("HA discovery: publish to %s failed", topic, exc_info=True)
