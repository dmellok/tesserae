"""Optional in-process MQTT broker built on amqtt.

Users without an existing broker can flip ``Built-in broker`` on in
Settings → Server → MQTT broker and Tesserae will run a Python broker
in a background thread. The transport then points at ``localhost`` on
the configured port and everything else stays the same.

Disabled by default, for any non-trivial deployment a real Mosquitto
host is preferable; the embedded broker is a single-host
out-of-the-box convenience.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddedBroker:
    """Run amqtt's ``Broker`` in a dedicated thread + event loop.

    The broker is fully async; we run it in a background thread so the
    Flask process can stay synchronous. ``start()`` is idempotent and
    returns after the broker has reported "started" (so callers can
    immediately connect). ``stop()`` tears the broker down cleanly.

    Auth: pass ``username`` + ``password`` to require credentials. A
    SHA-512 password file is written to ``passwd_path`` (or a temp file
    next to it) for amqtt's file auth plugin to consume. With no
    creds, the broker accepts anonymous connections, combine that
    with the default ``127.0.0.1`` bind to keep the broker safe."""

    def __init__(
        self,
        bind: str = "127.0.0.1",
        port: int = 1883,
        *,
        username: str | None = None,
        password: str | None = None,
        passwd_path: Path | None = None,
    ) -> None:
        self._bind = bind
        self._port = port
        self._username = (username or "").strip() or None
        self._password = password or None
        self._passwd_path = passwd_path
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

    def _write_passwd_file(self) -> Path | None:
        """Write a passlib-formatted ``user:hash`` password file amqtt's
        FileAuthPlugin can read. Returns the path (or None if no creds
        set).

        amqtt 0.11.x verifies passwords via
        ``passlib.apps.custom_app_context.verify``, that means the hash
        must be in a passlib-recognised format (``$pbkdf2-sha256$...``,
        ``$6$...``, etc.), not a raw SHA-512 hex digest. The old
        ``hashlib.sha512().hexdigest()`` we used to write produced a
        string passlib couldn't identify, so every authenticated
        connection was silently rejected with "hash could not be
        identified" in the broker thread, the user just saw "can't
        connect" client-side."""
        if not (self._username and self._password):
            return None
        if self._passwd_path is None:
            raise RuntimeError("embedded broker: passwd_path required when credentials are set")
        # Match the verifier amqtt itself uses; importing it at write
        # time keeps the broker module light when no creds are set.
        from passlib.apps import custom_app_context as pwd_context

        digest = pwd_context.hash(self._password)
        self._passwd_path.parent.mkdir(parents=True, exist_ok=True)
        # Mode 600 so the password hash isn't world-readable on a
        # multi-user host.
        self._passwd_path.write_text(f"{self._username}:{digest}\n", encoding="utf-8")
        with contextlib.suppress(Exception):
            self._passwd_path.chmod(0o600)
        return self._passwd_path

    def _run(self) -> None:
        from amqtt.broker import Broker

        listeners: dict[str, Any] = {
            "default": {
                "type": "tcp",
                "bind": f"{self._bind}:{self._port}",
            },
        }
        passwd_file = self._write_passwd_file()
        # amqtt 0.11.x deprecated the top-level ``auth`` / ``topic-check``
        # blocks in favour of a single ``plugins`` dict keyed by the
        # dotted import path of each plugin's class. With ``auth`` still
        # specified, amqtt silently drops the password-file lookup and
        # the sys plugin's interval, which surfaced as
        #   * "Configuration parameter 'password-file' not found"
        #   * TypeError: '>' not supported between NoneType and int
        # , and as a broker that listened but rejected every client.
        # See amqtt/contexts.py:default_broker_plugins for the canonical
        # shape. Config keys are underscore-separated, not hyphen.
        plugins: dict[str, dict[str, Any]] = {
            "amqtt.plugins.sys.broker.BrokerSysPlugin": {"sys_interval": 20},
        }
        if passwd_file is not None:
            plugins["amqtt.plugins.authentication.FileAuthPlugin"] = {
                "password_file": str(passwd_file),
            }
        else:
            plugins["amqtt.plugins.authentication.AnonymousAuthPlugin"] = {
                "allow_anonymous": True,
            }
        cfg: dict[str, Any] = {
            "listeners": listeners,
            "plugins": plugins,
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
