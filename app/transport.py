"""MQTT publish/subscribe primitive.

One job — talk to the broker. Anything device-specific (parsing status
heartbeats, validating config) belongs in the device plugin, not here.

Subscribers register a per-topic callback with ``subscribe(topic, callback)``.
On every incoming message the transport dispatches to every callback whose
subscription pattern matches the topic (supports MQTT-style ``+`` and ``#``).

This replaces inky-dash's MqttBridge + per-feature ``Manager`` wrappers.
Same primitive, no wrappers.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# paho-mqtt rc=4 is MQTT_ERR_NO_CONN. With qos>=1 the message is queued
# in paho's outbound store and replayed on reconnect, so it's not a real
# delivery failure — only a "didn't go out on this call" signal.
_MQTT_ERR_NO_CONN = 4


SubscribeCallback = Callable[[str, bytes], None]


class _MqttClientLike(Protocol):
    """The slice of paho.mqtt.client.Client this module touches.

    Stated explicitly so tests can pass a hand-rolled fake without depending
    on paho-mqtt internals."""

    on_connect: Any
    on_disconnect: Any
    on_message: Any

    def username_pw_set(self, username: str, password: str | None) -> None: ...
    def connect(self, host: str, port: int, keepalive: int) -> int: ...
    def disconnect(self) -> int: ...
    def loop_start(self) -> int: ...
    def loop_stop(self) -> int: ...
    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> Any: ...
    def subscribe(self, topic: str, qos: int) -> Any: ...


@dataclass(frozen=True)
class BrokerConfig:
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None
    keepalive: int = 60
    client_id: str = "tesserae"


@dataclass
class _Subscription:
    topic: str
    callback: SubscribeCallback
    matcher: Callable[[str], bool] = field(repr=False)


def _topic_matcher(pattern: str) -> Callable[[str], bool]:
    """Compile an MQTT subscription pattern (``+`` and ``#`` wildcards) into
    a function that matches concrete topics. The MQTT spec: ``+`` matches
    exactly one level; ``#`` must be the last segment and matches every
    remaining level."""
    parts = pattern.split("/")
    has_hash = parts and parts[-1] == "#"
    fixed = parts[:-1] if has_hash else parts

    def matches(topic: str) -> bool:
        topic_parts = topic.split("/")
        if has_hash:
            if len(topic_parts) < len(fixed):
                return False
        elif len(topic_parts) != len(fixed):
            return False
        for left, right in zip(fixed, topic_parts, strict=False):
            if left == "+":
                continue
            if left != right:
                return False
        return True

    return matches


class MqttTransport:
    """Publish/subscribe to an MQTT broker.

    Wraps paho-mqtt by default; tests pass ``client_factory=`` to inject a
    fake implementing ``_MqttClientLike``. The broker connection is opt-in
    via ``connect()`` — the transport is safe to construct without a live
    broker, and ``publish()`` raises a clear error if called before connect.
    """

    def __init__(
        self,
        config: BrokerConfig,
        *,
        client_factory: Callable[[str], _MqttClientLike] | None = None,
    ) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._subscriptions: list[_Subscription] = []
        self._connected = False
        if client_factory is None:
            client_factory = _paho_client_factory
        self._client: _MqttClientLike = client_factory(config.client_id)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        if config.username is not None:
            self._client.username_pw_set(config.username, config.password)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def topic_subscriptions(self) -> list[str]:
        with self._lock:
            return [s.topic for s in self._subscriptions]

    def connect(self) -> None:
        """Open the broker connection and start the network loop in a
        background thread. Idempotent — calling twice is a no-op."""
        if self._connected:
            return
        self._client.connect(self._config.host, self._config.port, self._config.keepalive)
        self._client.loop_start()
        self._connected = True
        logger.info("MQTT connected: %s:%d", self._config.host, self._config.port)

    def disconnect(self) -> None:
        if not self._connected:
            return
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False
        logger.info("MQTT disconnected")

    def publish(self, topic: str, payload: bytes, *, qos: int = 1, retain: bool = False) -> None:
        if not self._connected:
            raise RuntimeError(f"transport not connected; can't publish to {topic!r}")
        result = self._client.publish(topic, payload, qos=qos, retain=retain)
        rc = getattr(result, "rc", 0)
        if rc == _MQTT_ERR_NO_CONN and qos >= 1:
            # Broker not currently connected, but paho has queued the
            # message and will replay it on reconnect. With qos=0 the
            # publish would be lost, so we keep raising in that case.
            logger.warning(
                "MQTT publish %s queued — broker not connected (qos=%d, %d bytes)",
                topic,
                qos,
                len(payload),
            )
            return
        if rc != 0:
            raise RuntimeError(f"mqtt publish to {topic!r} failed: rc={rc}")
        logger.debug(
            "MQTT publish %s (%d bytes, qos=%d, retain=%s)", topic, len(payload), qos, retain
        )

    def subscribe(self, topic: str, callback: SubscribeCallback, *, qos: int = 1) -> None:
        """Register ``callback`` for messages whose topic matches ``topic``.
        Supports MQTT-style ``+`` and ``#`` wildcards in ``topic``."""
        sub = _Subscription(topic=topic, callback=callback, matcher=_topic_matcher(topic))
        with self._lock:
            self._subscriptions.append(sub)
        # Subscribe on the broker too if connected — paho silently replays
        # subscriptions on reconnect, so we only need this one call.
        if self._connected:
            self._client.subscribe(topic, qos)

    def unsubscribe(self, callback: SubscribeCallback) -> None:
        with self._lock:
            self._subscriptions = [s for s in self._subscriptions if s.callback is not callback]

    # -- paho callbacks (kept tiny — real work happens in dispatch) --------

    def _on_connect(
        self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None
    ) -> None:
        logger.debug("MQTT on_connect rc=%s", rc)
        # Replay every registered subscription so a reconnect doesn't drop
        # them. paho will not re-subscribe on its own.
        with self._lock:
            subs = list(self._subscriptions)
        for sub in subs:
            self._client.subscribe(sub.topic, 1)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any, **kwargs: Any) -> None:
        logger.warning("MQTT disconnected unexpectedly")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        topic = str(message.topic)
        payload: bytes = bytes(message.payload)
        with self._lock:
            subs = list(self._subscriptions)
        for sub in subs:
            if not sub.matcher(topic):
                continue
            try:
                sub.callback(topic, payload)
            except Exception:
                # A subscriber raising must not kill dispatch for other
                # subscribers — log loudly and continue.
                logger.exception("MQTT subscriber for %r raised on topic %r", sub.topic, topic)


def _paho_client_factory(client_id: str) -> _MqttClientLike:
    """Lazy import so tests don't pay the paho-mqtt cost when injecting a fake."""
    from paho.mqtt import client as mqtt_client
    from paho.mqtt.enums import CallbackAPIVersion

    # CallbackAPIVersion.VERSION2 matches the on_connect/on_disconnect
    # signatures we declared above.
    client: _MqttClientLike = mqtt_client.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=client_id,
    )
    return client
