"""MqttTransport unit tests with a fake paho-mqtt client.

End-to-end against a real broker is manual / staging; these cover the bits we
own: publish wiring, subscribe matching (MQTT wildcards), dispatch isolation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.transport import BrokerConfig, MqttTransport, _topic_matcher


@dataclass
class _FakePublish:
    rc: int = 0


@dataclass
class _FakeMessage:
    topic: str
    payload: bytes


class _FakeClient:
    """Stands in for paho.mqtt.client.Client. Records calls so tests can
    assert on them; exposes ``deliver()`` to simulate inbound messages."""

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.published: list[tuple[str, bytes, int, bool]] = []
        self.subscribed: list[tuple[str, int]] = []
        self.connected = False
        self.username: str | None = None
        self.password: str | None = None
        self.on_connect: Any = None
        self.on_disconnect: Any = None
        self.on_message: Any = None

    def username_pw_set(self, username: str, password: str | None) -> None:
        self.username = username
        self.password = password

    def connect(self, host: str, port: int, keepalive: int) -> int:
        self.connected = True
        return 0

    def disconnect(self) -> int:
        self.connected = False
        return 0

    def loop_start(self) -> int:
        return 0

    def loop_stop(self) -> int:
        return 0

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> _FakePublish:
        self.published.append((topic, payload, qos, retain))
        return _FakePublish(rc=0)

    def subscribe(self, topic: str, qos: int) -> tuple[int, int]:
        self.subscribed.append((topic, qos))
        return (0, 1)

    def deliver(self, topic: str, payload: bytes) -> None:
        assert self.on_message is not None
        self.on_message(self, None, _FakeMessage(topic=topic, payload=payload))


@pytest.fixture
def fakes():
    holder: dict[str, _FakeClient] = {}

    def factory(client_id: str) -> _FakeClient:
        client = _FakeClient(client_id)
        holder["client"] = client
        return client

    return holder, factory


def test_publish_round_trip(fakes) -> None:
    holder, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    t.connect()
    t.publish("tesserae/pi/frame/png", b'{"url":"x"}', qos=1, retain=False)
    assert holder["client"].published == [("tesserae/pi/frame/png", b'{"url":"x"}', 1, False)]


def test_publish_before_connect_raises(fakes) -> None:
    _, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    with pytest.raises(RuntimeError, match="not connected"):
        t.publish("x", b"y")


def test_publish_propagates_paho_rc(fakes) -> None:
    """Non-recoverable rc values (anything other than NO_CONN) still raise."""
    holder, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    t.connect()

    def failing_publish(topic, payload, qos, retain):
        return _FakePublish(rc=2)  # MQTT_ERR_PROTOCOL — actually broken

    holder["client"].publish = failing_publish  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="rc=2"):
        t.publish("x", b"y")


def test_publish_no_conn_at_qos1_is_queued_not_raised(fakes) -> None:
    """rc=4 on qos>=1 means paho queued the message for replay on
    reconnect — surface as a warning, not an exception."""
    holder, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    t.connect()

    def no_conn_publish(topic, payload, qos, retain):
        return _FakePublish(rc=4)

    holder["client"].publish = no_conn_publish  # type: ignore[method-assign]
    # Should not raise.
    t.publish("x", b"y", qos=1)


def test_publish_no_conn_at_qos0_still_raises(fakes) -> None:
    """qos=0 is fire-and-forget — paho doesn't queue, so rc=4 means lost."""
    holder, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    t.connect()

    def no_conn_publish(topic, payload, qos, retain):
        return _FakePublish(rc=4)

    holder["client"].publish = no_conn_publish  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="rc=4"):
        t.publish("x", b"y", qos=0)


def test_subscribe_dispatch_matches_wildcards(fakes) -> None:
    holder, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    t.connect()

    received: list[tuple[str, bytes]] = []

    def cb(topic: str, payload: bytes) -> None:
        received.append((topic, payload))

    t.subscribe("tesserae/+/status", cb)
    holder["client"].deliver("tesserae/pi/status", b"hello")
    holder["client"].deliver("tesserae/esp32/status", b"world")
    holder["client"].deliver("tesserae/pi/frame/png", b"not me")
    assert received == [
        ("tesserae/pi/status", b"hello"),
        ("tesserae/esp32/status", b"world"),
    ]


def test_subscriber_exception_does_not_break_dispatch(fakes) -> None:
    holder, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    t.connect()

    received: list[str] = []

    def bad(topic: str, payload: bytes) -> None:
        raise RuntimeError("boom")

    def good(topic: str, payload: bytes) -> None:
        received.append(topic)

    t.subscribe("tesserae/pi/#", bad)
    t.subscribe("tesserae/pi/#", good)
    holder["client"].deliver("tesserae/pi/status", b"x")
    assert received == ["tesserae/pi/status"]


def test_subscriptions_replayed_on_reconnect(fakes) -> None:
    holder, factory = fakes
    t = MqttTransport(BrokerConfig(host="b"), client_factory=factory)
    t.connect()
    t.subscribe("tesserae/pi/#", lambda _t, _p: None)
    # Live subscribe call is recorded.
    assert ("tesserae/pi/#", 1) in holder["client"].subscribed
    holder["client"].subscribed.clear()
    # Simulate a reconnect — paho fires on_connect; we re-subscribe.
    t._on_connect(holder["client"], None, None, 0)  # type: ignore[arg-type]
    assert holder["client"].subscribed == [("tesserae/pi/#", 1)]


def test_username_pw_set_called_when_credentials_present(fakes) -> None:
    holder, factory = fakes
    MqttTransport(BrokerConfig(host="b", username="u", password="p"), client_factory=factory)
    assert holder["client"].username == "u"
    assert holder["client"].password == "p"


def test_topic_matcher_basics() -> None:
    m = _topic_matcher("tesserae/pi/frame/+")
    assert m("tesserae/pi/frame/png")
    assert m("tesserae/pi/frame/bin")
    assert not m("tesserae/pi/frame/bin/extra")
    assert not m("tesserae/esp32/frame/png")

    m = _topic_matcher("tesserae/pi/#")
    assert m("tesserae/pi/status")
    assert m("tesserae/pi/frame/png")
    assert m("tesserae/pi/anything/at/all")
    assert not m("tesserae/esp32/status")
