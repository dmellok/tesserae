"""Home Assistant MQTT autodiscovery integration.

Publishes retained config payloads under
``homeassistant/<component>/tesserae/...`` so the HA MQTT integration
auto-creates a **Tesserae hub** device plus **one HA device per
registered display** (multi-head).

Hub device entities:

* **button** per saved dashboard — pressing it calls
  ``PushManager.push(page_id)`` (fans out to every display the dashboard
  is bound to).
* **select** ``active dashboard`` — same, driven by a dropdown.
* **image** ``last render`` — the composition PNG of the most-recent
  push (covers the legacy / virtual-panel case with no devices).
* **sensor** + **binary_sensor** diagnostics: last push, pushes today,
  last error, busy.

Per-display device entities (one HA device card each, linked to the hub
via ``via_device``):

* **image** ``Frame`` — the composition PNG currently published to that
  display (URL-only; HA fetches the bytes).
* **sensor** ``Current dashboard`` — the dashboard on it right now.
* **sensor** ``Last updated`` — when it last received a frame.
* **sensor** ``Last seen`` — when it last sent a heartbeat.
* **select** ``Dashboard`` — push one of the dashboards bound to *this*
  display to *only* this display.
* lazily, where the heartbeat provides them: **Battery**, **Signal**,
  **IP address**.

The same MQTT broker the rest of Tesserae publishes to is reused — no
second connection. Default-off in settings; users opt in by toggling
``ha_discovery_enabled`` in the App section.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
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
CMD_TOPIC_PUSH_PAGE = "tesserae/ha/cmd/push_page"
CMD_TOPIC_ACTIVE_PAGE = "tesserae/ha/cmd/active_page"

# Per-display heartbeat keys we surface as their own HA sensors, mapped to
# the ESP32 firmware's field names. Published lazily — a display only gets
# the sensor once a heartbeat actually carries the key (so Pi clients,
# which don't report battery/RSSI, stay uncluttered).
_DYN_SENSORS: dict[str, dict[str, Any]] = {
    "battery_pct": {"object": "battery", "name": "Battery", "device_class": "battery", "unit": "%"},
    "rssi": {
        "object": "signal",
        "name": "Signal",
        "device_class": "signal_strength",
        "unit": "dBm",
    },
    "ip": {"object": "ip", "name": "IP address", "icon": "mdi:ip-network"},
}


def _iso(epoch_or_dt: float | datetime) -> str:
    dt = (
        epoch_or_dt
        if isinstance(epoch_or_dt, datetime)
        else datetime.fromtimestamp(epoch_or_dt, UTC)
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


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


def _availability_block() -> list[dict[str, str]]:
    return [
        {
            "topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
        }
    ]


# ---------- hub config builders ----------


def build_button_config(
    page_id: str, page_name: str, *, base_url: str
) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("button", f"page_{page_id}")
    payload = {
        "name": page_name,
        "unique_id": f"tesserae_page_{page_id}",
        "object_id": f"tesserae_page_{page_id}",
        "command_topic": CMD_TOPIC_PUSH_PAGE,
        "payload_press": page_id,
        "availability": _availability_block(),
        "device": _hub_device(base_url),
        "icon": "mdi:image",
    }
    return topic, payload


def build_select_config(page_names: list[str], *, base_url: str) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("select", "active_page")
    payload = {
        "name": "Active dashboard",
        "unique_id": "tesserae_active_page",
        "object_id": "tesserae_active_page",
        "command_topic": CMD_TOPIC_ACTIVE_PAGE,
        "state_topic": STATE_TOPIC_ACTIVE_PAGE,
        "options": page_names or ["(none)"],
        "availability": _availability_block(),
        "device": _hub_device(base_url),
        "icon": "mdi:view-dashboard",
    }
    return topic, payload


def build_image_config(*, base_url: str) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("image", "last_render")
    # HA's MQTT image schema treats ``content_type`` and ``url_topic`` as
    # mutually exclusive (``content_type`` is only valid with the bytes-
    # over-topic form, ``image_topic``). Setting both makes HA reject the
    # discovery config entirely, so the entity never appears. We're URL-
    # based — the content type is inferred from the HTTP response.
    payload = {
        "name": "Last render",
        "unique_id": "tesserae_last_render",
        "object_id": "tesserae_last_render",
        "url_topic": STATE_TOPIC_IMAGE_URL,
        "availability": _availability_block(),
        "device": _hub_device(base_url),
    }
    return topic, payload


def build_diagnostic_configs(*, base_url: str) -> list[tuple[str, dict[str, Any]]]:
    dev = _hub_device(base_url)
    return [
        (
            _discovery_topic("sensor", "last_push"),
            {
                "name": "Last push",
                "unique_id": "tesserae_last_push",
                "object_id": "tesserae_last_push",
                "state_topic": STATE_TOPIC_LAST_PUSH,
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "availability": _availability_block(),
                "device": dev,
            },
        ),
        (
            _discovery_topic("sensor", "push_count_today"),
            {
                "name": "Pushes today",
                "unique_id": "tesserae_push_count_today",
                "object_id": "tesserae_push_count_today",
                "state_topic": STATE_TOPIC_PUSH_COUNT,
                "state_class": "total_increasing",
                "entity_category": "diagnostic",
                "availability": _availability_block(),
                "device": dev,
            },
        ),
        (
            _discovery_topic("sensor", "last_error"),
            {
                "name": "Last error",
                "unique_id": "tesserae_last_error",
                "object_id": "tesserae_last_error",
                "state_topic": STATE_TOPIC_LAST_ERROR,
                "entity_category": "diagnostic",
                "availability": _availability_block(),
                "device": dev,
            },
        ),
        (
            _discovery_topic("binary_sensor", "busy"),
            {
                "name": "Busy",
                "unique_id": "tesserae_busy",
                "object_id": "tesserae_busy",
                "state_topic": STATE_TOPIC_BUSY,
                "payload_on": "1",
                "payload_off": "0",
                "entity_category": "diagnostic",
                "availability": _availability_block(),
                "device": dev,
            },
        ),
    ]


# ---------- per-display config builders ----------


def build_device_configs(
    device_id: str, device_name: str, model: str, bound_page_names: list[str]
) -> list[tuple[str, dict[str, Any]]]:
    """The always-present entities for one physical display."""
    dev = _panel_device(device_id, device_name, model)
    avail = _availability_block()
    return [
        (
            _discovery_topic("image", f"dev_{device_id}_frame"),
            # See note in build_image_config — ``content_type`` is invalid
            # alongside ``url_topic`` (HA infers it from the HTTP response).
            {
                "name": "Frame",
                "unique_id": f"tesserae_dev_{device_id}_frame",
                "object_id": f"tesserae_dev_{device_id}_frame",
                "url_topic": _dev_state(device_id, "image_url"),
                "availability": avail,
                "device": dev,
            },
        ),
        (
            _discovery_topic("sensor", f"dev_{device_id}_current"),
            {
                "name": "Current dashboard",
                "unique_id": f"tesserae_dev_{device_id}_current",
                "object_id": f"tesserae_dev_{device_id}_current",
                "state_topic": _dev_state(device_id, "current_page"),
                "icon": "mdi:view-dashboard",
                "availability": avail,
                "device": dev,
            },
        ),
        (
            _discovery_topic("sensor", f"dev_{device_id}_last_update"),
            {
                "name": "Last updated",
                "unique_id": f"tesserae_dev_{device_id}_last_update",
                "object_id": f"tesserae_dev_{device_id}_last_update",
                "state_topic": _dev_state(device_id, "last_update"),
                "device_class": "timestamp",
                "availability": avail,
                "device": dev,
            },
        ),
        (
            _discovery_topic("sensor", f"dev_{device_id}_last_seen"),
            {
                "name": "Last seen",
                "unique_id": f"tesserae_dev_{device_id}_last_seen",
                "object_id": f"tesserae_dev_{device_id}_last_seen",
                "state_topic": _dev_state(device_id, "last_seen"),
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "availability": avail,
                "device": dev,
            },
        ),
        (
            _discovery_topic("select", f"dev_{device_id}_dashboard"),
            {
                "name": "Dashboard",
                "unique_id": f"tesserae_dev_{device_id}_dashboard",
                "object_id": f"tesserae_dev_{device_id}_dashboard",
                "command_topic": _dev_cmd(device_id, "push"),
                "state_topic": _dev_state(device_id, "current_page"),
                "options": bound_page_names or ["(none bound)"],
                "icon": "mdi:dap",
                "availability": avail,
                "device": dev,
            },
        ),
    ]


def build_dynamic_sensor_config(
    device_id: str, device_name: str, model: str, key: str
) -> tuple[str, dict[str, Any]]:
    spec = _DYN_SENSORS[key]
    object_id = f"dev_{device_id}_{spec['object']}"
    payload: dict[str, Any] = {
        "name": spec["name"],
        "unique_id": f"tesserae_{object_id}",
        "object_id": f"tesserae_{object_id}",
        "state_topic": _dev_state(device_id, spec["object"]),
        "entity_category": "diagnostic",
        "availability": _availability_block(),
        "device": _panel_device(device_id, device_name, model),
    }
    for opt in ("device_class", "unit", "icon"):
        if opt in spec:
            payload["unit_of_measurement" if opt == "unit" else opt] = spec[opt]
    return _discovery_topic("sensor", object_id), payload


class HomeAssistantDiscovery:
    """Publishes HA discovery configs + relays HA commands to PushManager.

    Threading: command callbacks fire on the MQTT network thread; they
    hand off to PushManager.push() which has its own lock so reentry is
    safe. State publishes are best-effort — broker outages just mean
    stale HA sensors, not a crashed app.
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
    ) -> None:
        self._transport = transport
        self._push_manager = push_manager
        self._page_store = page_store
        self._base_url_fn = base_url_fn
        self._devices = device_registry
        self._device_status = device_status if device_status is not None else {}
        self._started = False
        self._lock = threading.Lock()
        self._push_count_day = ""
        self._push_count = 0
        # Tracks published config object-ids so stop() can blank them and
        # we can tear down entities for pages / devices since removed.
        self._published_button_ids: set[str] = set()
        self._published_device_ids: set[str] = set()
        # (device_id, dyn-key) pairs whose lazy sensor config we've sent.
        self._published_dyn: set[tuple[str, str]] = set()

    # -- device helpers -------------------------------------------------

    def _bindable_devices(self) -> list[Any]:
        if self._devices is None:
            return []
        return [d for d in self._devices.all() if d.kind_of is not None and d.panel is not None]

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
        for device in self._bindable_devices():
            self._transport.subscribe(_dev_cmd(device.id, "push"), self._on_device_push_cmd, qos=1)
        self._push_manager.add_listener(self._on_push_result)
        self._page_store.add_listener(self._on_pages_changed)
        # Seed diagnostic state so HA shows real values immediately.
        self._publish_str(STATE_TOPIC_PUSH_COUNT, str(self._push_count), retain=True)
        self._publish_str(STATE_TOPIC_LAST_ERROR, "", retain=True)
        self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
        # Drop any stale current_page / active_page retained values left
        # over from older Tesserae versions that published the raw
        # page_id (a digest or source label) instead of the resolved
        # name — those weren't in the select's options list, so HA
        # logged an "Invalid option" warning on every restart until the
        # next valid page push overwrote them. Empty retained payload
        # clears the retained message per MQTT spec; next valid push
        # repopulates with a real name (see _publish_device_push_state).
        self._publish_str(STATE_TOPIC_ACTIVE_PAGE, "", retain=True)
        for device in self._bindable_devices():
            self._publish_str(_dev_state(device.id, "current_page"), "", retain=True)
        self._seed_device_state()

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        logger.info("HA discovery: stopping")
        with contextlib.suppress(Exception):
            self._push_manager.remove_listener(self._on_push_result)
        with contextlib.suppress(Exception):
            self._page_store.remove_listener(self._on_pages_changed)
        # Wipe every entity by publishing an empty retained config payload.
        for component, object_id in self._every_object_id_kind():
            self._publish_str(_discovery_topic(component, object_id), "", retain=True)
        self._published_button_ids.clear()
        self._published_device_ids.clear()
        self._published_dyn.clear()
        self._publish_str(AVAILABILITY_TOPIC, "offline", retain=True)

    def refresh_entity_configs(self) -> None:
        """Re-publish entity configs — after the base URL changed (HTTP
        port captured) or a device was added/removed."""
        if self._started:
            self._publish_entity_configs()

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
        written when the push corresponds to a saved Page — non-page
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
        except Exception:
            logger.exception("HA discovery: failed to publish heartbeat state for %s", device_id)

    def _publish_dynamic(self, device_id: str, parsed: dict[str, Any]) -> None:
        device = self._devices.get(device_id) if self._devices is not None else None
        for key, spec in _DYN_SENSORS.items():
            value = parsed.get(key)
            if value in (None, ""):
                continue
            if (device_id, key) not in self._published_dyn and device is not None:
                topic, payload = build_dynamic_sensor_config(
                    device_id, device.display_name, self._device_model(device), key
                )
                self._publish_json(topic, payload, retain=True)
                self._published_dyn.add((device_id, key))
            self._publish_str(_dev_state(device_id, spec["object"]), str(value), retain=True)

    def _on_pages_changed(self) -> None:
        if not self._started:
            return
        try:
            self._publish_entity_configs()
        except Exception:
            logger.exception("HA discovery: failed to republish entity configs")

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

    # -- discovery publishing ------------------------------------------

    def _sweep_stale_retained(self) -> None:
        """One-shot scan at start(): blank retained discovery configs left
        on the broker by a previous Tesserae session for devices or
        dashboards that no longer exist.

        In-session deletes are already handled by ``_publish_entity_configs``
        (it diffs against ``_published_device_ids`` / ``_published_button_ids``
        and blanks the difference). But when a device is deleted while
        Tesserae is *not* running, the next session has no memory of
        previously publishing for that id, so the retained config sits
        on the broker forever — HA keeps showing a ghost device with the
        old name. This sweep closes that gap by treating the broker
        itself as source-of-truth for what's already been published.

        We collect retained configs for a short window, identify which
        ones reference current devices/pages, and blank everything else
        under our discovery namespace."""
        import time

        live_device_ids = {d.id for d in self._bindable_devices()}
        live_page_ids = {p.id for p in self._page_store.list()}
        to_blank: set[str] = set()

        def on_config(topic: str, payload: bytes) -> None:
            if not payload:
                return
            try:
                cfg = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Corrupt payloads also count as stale — blank them.
                to_blank.add(topic)
                return
            if not isinstance(cfg, dict):
                to_blank.add(topic)
                return
            device = cfg.get("device") or {}
            ids = device.get("identifiers") if isinstance(device, dict) else None
            primary = ids[0] if isinstance(ids, list) and ids else None
            if primary == "tesserae":
                # Hub-owned entity. The only hub entities that come and go are
                # per-dashboard buttons (object_id `page_<page_id>`); the
                # fixed ones (active_page, last_render, diagnostics) always
                # belong, so leave them alone.
                obj = cfg.get("object_id")
                if isinstance(obj, str) and obj.startswith("page_"):
                    page_id = obj[len("page_") :]
                    if page_id not in live_page_ids:
                        to_blank.add(topic)
                return
            if isinstance(primary, str) and primary.startswith("tesserae_dev_"):
                device_id = primary[len("tesserae_dev_") :]
                if device_id not in live_device_ids:
                    to_blank.add(topic)
                return
            # Unknown identifier shape under our prefix — leave it; HA will
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
        base = self._base_url()

        seen_ids: set[str] = set()
        for page in pages:
            topic, payload = build_button_config(page.id, page.name, base_url=base)
            self._publish_json(topic, payload, retain=True)
            seen_ids.add(page.id)
        for page_id in self._published_button_ids - seen_ids:
            self._publish_str(_discovery_topic("button", f"page_{page_id}"), "", retain=True)
        self._published_button_ids = seen_ids

        page_names = sorted({p.name for p in pages})
        topic, payload = build_select_config(page_names, base_url=base)
        self._publish_json(topic, payload, retain=True)

        topic, payload = build_image_config(base_url=base)
        self._publish_json(topic, payload, retain=True)
        for topic, payload in build_diagnostic_configs(base_url=base):
            self._publish_json(topic, payload, retain=True)

        # Per-display devices.
        seen_devices: set[str] = set()
        for device in self._bindable_devices():
            bound = sorted({p.name for p in self._pages_for_device(device.id)})
            configs = build_device_configs(
                device.id, device.display_name, self._device_model(device), bound
            )
            for topic, payload in configs:
                self._publish_json(topic, payload, retain=True)
            seen_devices.add(device.id)
        # Tear down displays that no longer exist (incl. their dyn sensors).
        for device_id in self._published_device_ids - seen_devices:
            for topic in self._device_config_topics(device_id):
                self._publish_str(topic, "", retain=True)
        self._published_dyn = {pair for pair in self._published_dyn if pair[0] in seen_devices}
        self._published_device_ids = seen_devices

    def _device_config_topics(self, device_id: str) -> list[str]:
        topics = [
            _discovery_topic("image", f"dev_{device_id}_frame"),
            _discovery_topic("sensor", f"dev_{device_id}_current"),
            _discovery_topic("sensor", f"dev_{device_id}_last_update"),
            _discovery_topic("sensor", f"dev_{device_id}_last_seen"),
            _discovery_topic("select", f"dev_{device_id}_dashboard"),
        ]
        for key, spec in _DYN_SENSORS.items():
            if (device_id, key) in self._published_dyn:
                topics.append(_discovery_topic("sensor", f"dev_{device_id}_{spec['object']}"))
        return topics

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
        """All (component, object_id) pairs we've published — used by stop()
        to blank every retained config so HA drops every entity cleanly."""
        out: list[tuple[str, str]] = []
        for page_id in self._published_button_ids:
            out.append(("button", f"page_{page_id}"))
        out.append(("select", "active_page"))
        out.append(("image", "last_render"))
        for object_id in ("last_push", "push_count_today", "last_error"):
            out.append(("sensor", object_id))
        out.append(("binary_sensor", "busy"))
        for device_id in self._published_device_ids:
            out.append(("image", f"dev_{device_id}_frame"))
            out.append(("sensor", f"dev_{device_id}_current"))
            out.append(("sensor", f"dev_{device_id}_last_update"))
            out.append(("sensor", f"dev_{device_id}_last_seen"))
            out.append(("select", f"dev_{device_id}_dashboard"))
        for device_id, key in self._published_dyn:
            out.append(("sensor", f"dev_{device_id}_{_DYN_SENSORS[key]['object']}"))
        return out

    # -- publish wrappers ----------------------------------------------

    def _publish_json(self, topic: str, payload: dict[str, Any], *, retain: bool = True) -> None:
        if not self._transport.connected:
            # Discovery configs are retained — they'll re-publish on the
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
