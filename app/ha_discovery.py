"""Home Assistant MQTT autodiscovery integration.

Publishes retained config payloads under
``homeassistant/<component>/tesserae/...`` so the HA MQTT integration
auto-creates a single device with these entities:

* **button** per saved dashboard — pressing it calls
  ``PushManager.push(page_id)``.
* **select** ``active dashboard`` — same semantics as the buttons, but
  driven by a dropdown.
* **image** ``last render`` — follows the composition PNG of the
  most-recent push. URL-only (HA fetches the bytes); cheaper than
  republishing the PNG.
* **sensor** + **binary_sensor** diagnostics: last push timestamp,
  pushes today, last error, busy.

The same MQTT broker the rest of Tesserae publishes to is reused — no
second connection. Default-off in settings; users opt in by toggling
``ha_discovery_enabled`` in the App section.

Lifecycle:

* ``start()`` — publish ``availability=online`` + LWT, the entity
  configs, and the initial diagnostic state; subscribe to command
  topics; register listeners with PushManager + PageStore.
* ``stop()`` — publish ``availability=offline``, blank out the retained
  configs to remove entities from HA, drop the listeners.
* ``republish_entities()`` — fires automatically when a page is
  saved or deleted (PageStore listener).

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


def _device_info(base_url: str) -> dict[str, Any]:
    """Shared HA device-registry entry. Every entity references the same
    identifiers list so HA clusters them under a single device card."""
    return {
        "identifiers": ["tesserae"],
        "name": "Tesserae",
        "manufacturer": "tesserae",
        "model": "Composer + renderer fanout",
        "sw_version": "0.1",
        "configuration_url": base_url,
    }


def _discovery_topic(component: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{component}/{NODE_ID}/{object_id}/config"


def _availability_block() -> list[dict[str, str]]:
    return [
        {
            "topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
        }
    ]


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
        "device": _device_info(base_url),
        "icon": "mdi:image",
    }
    return topic, payload


def build_select_config(page_ids: list[str], *, base_url: str) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("select", "active_page")
    payload = {
        "name": "Active dashboard",
        "unique_id": "tesserae_active_page",
        "object_id": "tesserae_active_page",
        "command_topic": CMD_TOPIC_ACTIVE_PAGE,
        "state_topic": STATE_TOPIC_ACTIVE_PAGE,
        "options": page_ids or [""],
        "availability": _availability_block(),
        "device": _device_info(base_url),
        "icon": "mdi:view-dashboard",
    }
    return topic, payload


def build_image_config(*, base_url: str) -> tuple[str, dict[str, Any]]:
    topic = _discovery_topic("image", "last_render")
    payload = {
        "name": "Last render",
        "unique_id": "tesserae_last_render",
        "object_id": "tesserae_last_render",
        "url_topic": STATE_TOPIC_IMAGE_URL,
        "content_type": "image/png",
        "availability": _availability_block(),
        "device": _device_info(base_url),
    }
    return topic, payload


def build_diagnostic_configs(*, base_url: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = [
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
                "device": _device_info(base_url),
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
                "device": _device_info(base_url),
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
                "device": _device_info(base_url),
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
                "device": _device_info(base_url),
            },
        ),
    ]
    return out


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
    ) -> None:
        self._transport = transport
        self._push_manager = push_manager
        self._page_store = page_store
        self._base_url_fn = base_url_fn
        self._started = False
        self._lock = threading.Lock()
        # Per-UTC-day push counter. In-memory only; resets on restart
        # (the metric is "today" anyway).
        self._push_count_day = ""
        self._push_count = 0
        # Tracks which page-button configs we've published so we can
        # tear down configs for pages the user has since deleted.
        self._published_button_ids: set[str] = set()

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        logger.info("HA discovery: starting")
        self._publish_str(AVAILABILITY_TOPIC, "online", retain=True)
        self._publish_entity_configs()
        self._transport.subscribe(CMD_TOPIC_PUSH_PAGE, self._on_push_page_cmd, qos=1)
        self._transport.subscribe(CMD_TOPIC_ACTIVE_PAGE, self._on_active_page_cmd, qos=1)
        self._push_manager.add_listener(self._on_push_result)
        self._page_store.add_listener(self._on_pages_changed)
        # Seed diagnostic state so HA shows real values immediately
        # instead of 'unknown'.
        self._publish_str(STATE_TOPIC_PUSH_COUNT, str(self._push_count), retain=True)
        self._publish_str(STATE_TOPIC_LAST_ERROR, "", retain=True)
        self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        logger.info("HA discovery: stopping")
        # Detach listeners first so an in-flight push doesn't fire after stop().
        with contextlib.suppress(Exception):
            self._push_manager.remove_listener(self._on_push_result)
        with contextlib.suppress(Exception):
            self._page_store.remove_listener(self._on_pages_changed)
        # Wipe every entity by publishing an empty retained config payload.
        # HA reads the empty payload as "delete this entity".
        for component, object_id in self._every_object_id_kind():
            self._publish_str(_discovery_topic(component, object_id), "", retain=True)
        self._published_button_ids.clear()
        self._publish_str(AVAILABILITY_TOPIC, "offline", retain=True)

    def refresh_entity_configs(self) -> None:
        """Re-publish entity configs — call after the base URL has
        changed (e.g. when the HTTP port was captured from a request)
        so HA's stored image_url / configuration_url stay correct."""
        if self._started:
            self._publish_entity_configs()

    def _base_url(self) -> str:
        return self._base_url_fn().rstrip("/")

    # -- listener hooks -------------------------------------------------

    def _on_push_result(self, result: PushResult) -> None:
        try:
            now = datetime.now(UTC)
            iso = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            if result.status == "sent":
                self._publish_str(STATE_TOPIC_LAST_PUSH, iso, retain=True)
                self._publish_str(STATE_TOPIC_LAST_ERROR, "", retain=True)
                if result.composition_digest:
                    # The composition PNG is the canonical thumbnail and
                    # is always written for a successful push (see
                    # PushManager._fan_out).
                    image_url = f"{self._base_url()}/renders/{result.composition_digest}.png"
                    self._publish_str(STATE_TOPIC_IMAGE_URL, image_url, retain=True)
                today = now.strftime("%Y-%m-%d")
                if today != self._push_count_day:
                    self._push_count_day = today
                    self._push_count = 0
                self._push_count += 1
                self._publish_str(STATE_TOPIC_PUSH_COUNT, str(self._push_count), retain=True)
            elif result.status == "failed" and result.error:
                # Truncate to keep HA's sensor display readable.
                self._publish_str(STATE_TOPIC_LAST_ERROR, result.error[:240], retain=True)
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
        except Exception:
            logger.exception("HA discovery: failed to publish push state")

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
        # Tip busy on for the render; _on_push_result flips it back off.
        self._publish_str(STATE_TOPIC_BUSY, "1", retain=True)
        try:
            self._push_manager.push(page_id)
        except Exception:
            self._publish_str(STATE_TOPIC_BUSY, "0", retain=True)
            raise

    def _on_active_page_cmd(self, topic: str, payload: bytes) -> None:
        page_id = payload.decode("utf-8", errors="ignore").strip()
        if not page_id:
            return
        # Echo back to the select's state so HA shows the new value.
        self._publish_str(STATE_TOPIC_ACTIVE_PAGE, page_id, retain=True)
        self._on_push_page_cmd(topic, payload)

    # -- discovery publishing ------------------------------------------

    def _publish_entity_configs(self) -> None:
        pages = self._page_store.list()
        seen_ids: set[str] = set()

        for page in pages:
            topic, payload = build_button_config(page.id, page.name, base_url=self._base_url())
            self._publish_json(topic, payload, retain=True)
            seen_ids.add(page.id)

        # Tear down configs for pages that no longer exist.
        stale = self._published_button_ids - seen_ids
        for page_id in stale:
            self._publish_str(_discovery_topic("button", f"page_{page_id}"), "", retain=True)
        self._published_button_ids = seen_ids

        page_ids = sorted(p.id for p in pages)
        topic, payload = build_select_config(page_ids, base_url=self._base_url())
        self._publish_json(topic, payload, retain=True)

        topic, payload = build_image_config(base_url=self._base_url())
        self._publish_json(topic, payload, retain=True)

        for topic, payload in build_diagnostic_configs(base_url=self._base_url()):
            self._publish_json(topic, payload, retain=True)

    def _every_object_id_kind(self) -> list[tuple[str, str]]:
        """All (component, object_id) pairs we've ever published — used
        by stop() to blank every retained config so HA drops every
        entity cleanly."""
        out: list[tuple[str, str]] = []
        for page_id in self._published_button_ids:
            out.append(("button", f"page_{page_id}"))
        out.append(("select", "active_page"))
        out.append(("image", "last_render"))
        for object_id in ("last_push", "push_count_today", "last_error"):
            out.append(("sensor", object_id))
        out.append(("binary_sensor", "busy"))
        return out

    # -- publish wrappers ----------------------------------------------

    def _publish_json(self, topic: str, payload: dict[str, Any], *, retain: bool = True) -> None:
        try:
            self._transport.publish(
                topic, json.dumps(payload, sort_keys=True).encode("utf-8"), qos=1, retain=retain
            )
        except Exception:
            logger.warning("HA discovery: publish to %s failed", topic, exc_info=True)

    def _publish_str(self, topic: str, payload: str, *, retain: bool = False) -> None:
        try:
            self._transport.publish(topic, payload.encode("utf-8"), qos=1, retain=retain)
        except Exception:
            logger.warning("HA discovery: publish to %s failed", topic, exc_info=True)
