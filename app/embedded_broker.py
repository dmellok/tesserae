"""Optional in-process MQTT broker built on amqtt.

Users without an existing broker can flip ``Built-in broker`` on in
Settings → Server → MQTT broker and Tesserae will run a Python broker
in a background thread. The transport then points at ``localhost`` on
the configured port and everything else stays the same.

Disabled by default — for any non-trivial deployment a real Mosquitto
host is preferable; the embedded broker is a single-host
out-of-the-box convenience.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddedBroker:
    """Run amqtt's ``Broker`` in a dedicated thread + event loop.

    The broker is fully async; we run it in a background thread so the
    Flask process can stay synchronous. ``start()`` is idempotent and
    returns after the broker has reported "started" (so callers can
    immediately connect). ``stop()`` tears the broker down cleanly."""

    def __init__(self, bind: str = "127.0.0.1", port: int = 1883) -> None:
        self._bind = bind
        self._port = port
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker: Any = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    @property
    def bind(self) -> str:
        return self._bind

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, timeout: float = 5.0) -> None:
        if self.running:
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, name="tesserae-embedded-broker", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            raise RuntimeError(
                f"embedded broker did not become ready within {timeout}s"
                + (f": {self._error}" if self._error else "")
            )
        if self._error is not None:
            raise RuntimeError(f"embedded broker failed to start: {self._error}")

    def stop(self, timeout: float = 5.0) -> None:
        if not self.running:
            return
        loop = self._loop
        broker = self._broker
        if loop is not None and broker is not None:
            future = asyncio.run_coroutine_threadsafe(broker.shutdown(), loop)
            try:
                future.result(timeout=timeout)
            except Exception:
                logger.exception("embedded broker shutdown raised")
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        self._loop = None
        self._broker = None

    def _run(self) -> None:
        from amqtt.broker import Broker

        cfg = {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"{self._bind}:{self._port}",
                },
            },
            # We accept anonymous connections — anyone reachable to the
            # bind address can connect. Users who want auth should run
            # Mosquitto and point the host setting at it.
            "auth": {"allow-anonymous": True},
            # Keep the broker quiet; amqtt is chatty at INFO.
            "topic-check": {"enabled": False},
        }
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._broker = Broker(cfg, loop=loop)
        try:
            loop.run_until_complete(self._broker.start())
            self._ready.set()
            loop.run_forever()
        except BaseException as err:  # pragma: no cover - background thread
            self._error = err
            self._ready.set()
            logger.exception("embedded broker crashed")
        finally:
            with contextlib.suppress(Exception):
                loop.close()
