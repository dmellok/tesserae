"""Anonymous, opt-in usage telemetry.

When enabled, posts two events to the maintainer's analytics backend
(running the open-source ``aptabase/aptabase``):

* ``app.started`` — once per process start (Tesserae version, Python
  version, platform are in ``systemProps``; no custom props).
* ``update.applied`` — when the in-app updater successfully applies a
  new revision; props carry the from/to short SHAs and channel.

What it does **not** send: IP addresses, hostnames, paths, settings,
secrets, push contents, dashboard layouts, or anything tied to a real
identity. The only stable identifier is a random UUID generated on first
run and persisted to ``data/core/.instance_id`` so the same install
counts as one across restarts.

The endpoint and app key are **baked into this module** — users cannot
re-aim Tesserae's telemetry at a different server. That's deliberate:
the whole point is so the maintainer can count installs, which only
works if every opted-in instance reports to the same place. Users
control whether to send (the toggle) but not where it goes.

Default: **off**. Users opt in via Settings → Server → App, or
configuration. ``TESSERAE_TELEMETRY=0`` disables hard regardless of
stored settings.

Failure-silent: a network/endpoint failure logs at DEBUG and drops the
event. The send happens on a daemon background thread so nothing ever
blocks startup or a request.

``TESSERAE_TELEMETRY_HOST`` / ``TESSERAE_TELEMETRY_APP_KEY`` env vars
are honoured **only** as a development convenience (so the maintainer
can point a dev build at a staging backend without committing the
production values). Users on a release won't have those set; the baked
defaults apply.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform as _platform
import queue
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Baked-in endpoint ------------------------------------------------
# The maintainer's self-hosted Aptabase deployment. Users cannot re-aim
# Tesserae's telemetry; the toggle controls whether to send, not where it
# goes — that way opted-in installs add up to a real total. The App-Key
# is a write-only routing identifier intended to ship in clients, not a
# secret. Empty strings disable telemetry even if the toggle is on.
APTABASE_HOST: str = "https://aptabase.dmello.io"
APTABASE_APP_KEY: str = "A-SH-4940410345"

INSTANCE_ID_FILE = "core/.instance_id"
SEND_TIMEOUT_S = 4.0
QUEUE_CAPACITY = 64  # Bounded — drop new events when backlogged.


def _ensure_instance_id(data_root: Path) -> str:
    """Generate-once anonymous instance UUID. Persists across restarts;
    a fresh ``data/`` (new install) gets a fresh id."""
    path = data_root / INSTANCE_ID_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
    new_id = str(uuid.uuid4())
    try:
        path.write_text(new_id, encoding="utf-8")
    except OSError as err:
        logger.warning("telemetry: couldn't persist instance id: %s", err)
    return new_id


def _env_off() -> bool:
    """``TESSERAE_TELEMETRY=0`` (or any falsy value) is a hard kill switch
    regardless of stored settings — for unattended installs, CI, etc."""
    raw = os.environ.get("TESSERAE_TELEMETRY", "").strip().lower()
    return raw in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool
    host: str  # endpoint base URL, e.g. "https://analytics.example.com"
    app_key: str
    instance_id: str
    app_version: str

    @property
    def post_url(self) -> str:
        return f"{self.host.rstrip('/')}/api/v0/event"


def _system_props(app_version: str) -> dict[str, str]:
    """Aptabase's required ``systemProps`` block. All values are stable
    OS/runtime facts, no identifiers."""
    return {
        "isDebug": "false",
        "osName": _platform.system() or "Unknown",
        "osVersion": _platform.release() or "",
        "locale": "en-US",
        "appVersion": app_version,
        "appBuildNumber": "0",
        "sdkVersion": "tesserae/0.1",
        "engineName": "cpython",
        "engineVersion": _platform.python_version(),
    }


class Telemetry:
    """Background-thread Aptabase client.

    Construct via :meth:`from_settings`. Call :meth:`send` to enqueue an
    event; the worker thread drains the queue and posts. :meth:`shutdown`
    drains and stops; never required (the worker is a daemon thread)."""

    def __init__(self, cfg: TelemetryConfig) -> None:
        self._cfg = cfg
        self._queue: queue.Queue[tuple[str, dict[str, str]] | None] = queue.Queue(
            maxsize=QUEUE_CAPACITY
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if self.enabled:
            self._thread = threading.Thread(
                target=self._worker, name="tesserae-telemetry", daemon=True
            )
            self._thread.start()

    @classmethod
    def from_settings(
        cls,
        *,
        data_root: Path,
        app_version: str,
        settings_app: dict,
    ) -> Telemetry:
        """Resolve enable-flag + env-var endpoint overrides into a config.

        Host / app key come from the baked-in :data:`APTABASE_HOST` and
        :data:`APTABASE_APP_KEY`. ``TESSERAE_TELEMETRY_HOST`` and
        ``TESSERAE_TELEMETRY_APP_KEY`` override them — these exist only
        as a dev convenience for the maintainer, not as a public
        re-aiming knob. ``TESSERAE_TELEMETRY=0`` kills it hard."""
        enabled_setting = bool(settings_app.get("telemetry_enabled", False))
        host = os.environ.get("TESSERAE_TELEMETRY_HOST", "").strip() or APTABASE_HOST
        key = os.environ.get("TESSERAE_TELEMETRY_APP_KEY", "").strip() or APTABASE_APP_KEY
        enabled = enabled_setting and bool(host) and bool(key) and not _env_off()
        return cls(
            TelemetryConfig(
                enabled=enabled,
                host=host,
                app_key=key,
                instance_id=_ensure_instance_id(data_root),
                app_version=app_version,
            )
        )

    @classmethod
    def disabled(cls) -> Telemetry:
        """Inert client for tests / ``--testing`` mode — never sends, never
        creates an instance-id file."""
        return cls(
            TelemetryConfig(
                enabled=False,
                host="",
                app_key="",
                instance_id="00000000-0000-0000-0000-000000000000",
                app_version="0",
            )
        )

    # -- public API -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled and bool(self._cfg.host) and bool(self._cfg.app_key)

    @property
    def instance_id(self) -> str:
        return self._cfg.instance_id

    @property
    def endpoint(self) -> str:
        return self._cfg.post_url if self.enabled else ""

    def send(self, event_name: str, props: dict[str, str] | None = None) -> None:
        """Enqueue an event. Drops silently if disabled or the queue is
        full — never blocks the caller."""
        if not self.enabled:
            return
        try:
            self._queue.put_nowait((event_name, props or {}))
        except queue.Full:
            logger.debug("telemetry: queue full, dropping %s", event_name)

    def shutdown(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)  # wake the worker
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # -- worker ---------------------------------------------------------

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                return
            name, props = item
            self._post(name, props)

    def _post(self, event_name: str, props: dict[str, str]) -> None:
        body = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "sessionId": self._cfg.instance_id,
            "eventName": event_name,
            "systemProps": _system_props(self._cfg.app_version),
            "props": props,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._cfg.post_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "App-Key": self._cfg.app_key,
                "User-Agent": f"tesserae/{self._cfg.app_version}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
                resp.read(1)  # discard body
        except urllib.error.URLError as err:
            logger.debug("telemetry: send failed (%s): %s", event_name, err)
        except OSError as err:
            logger.debug("telemetry: send error (%s): %s", event_name, err)
