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
import dataclasses
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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.state.event_log import EventLog

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
# Match Aptabase's default session timeout so each heartbeat keeps the
# current session alive for the full time the user has Tesserae running.
# Cheap (~24 events/day per install); valuable: lets the maintainer see
# session duration + DAU instead of just process-start counts.
HEARTBEAT_INTERVAL_S = 60 * 60


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
    # When True we mark events with ``isDebug: true`` so the maintainer's
    # Aptabase dashboard can filter dev traffic out of the prod view.
    # Wired from the ``--dev`` flag in ``app.main._serve``.
    is_debug: bool = False

    @property
    def post_url(self) -> str:
        # ``/api/v0/event`` (singular) is the canonical endpoint used by
        # every published Aptabase SDK *except* the experimental Python
        # one (which targets ``/events`` with a batched array). The
        # Aptabase server doesn't accept the plural form, so we follow
        # the JS / Swift / Flutter / Kotlin / MAUI lineage and POST a
        # single event object here.
        return f"{self.host.rstrip('/')}/api/v0/event"


def _system_props(app_version: str, *, is_debug: bool) -> dict[str, object]:
    """Aptabase's required ``systemProps`` block. Field set mirrors the
    JS SDK's ``sendEvent`` payload exactly — locale, isDebug,
    appVersion, sdkVersion. The Python SDK adds osName / osVersion /
    deviceModel but the Aptabase server doesn't actually require them,
    and extras have historically tripped server-side schema validation.
    Stay minimal."""
    return {
        "locale": "en-US",
        "isDebug": is_debug,
        "appVersion": app_version,
        "sdkVersion": f"aptabase-tesserae@{app_version}",
    }


def _user_agent(app_version: str) -> str:
    """Aptabase's server requires a Mozilla-like User-Agent for non-
    browser clients — see the JS SDK's ``getUserAgent``. A plain
    ``tesserae/<version>`` UA returns 2xx but never indexes."""
    platform_map = {"Darwin": "Macintosh", "Windows": "Windows", "Linux": "Linux"}
    name = platform_map.get(_platform.system(), _platform.system() or "Unknown")
    return f"Mozilla/5.0 ({name}) Not-A-Browser tesserae/{app_version}"


class Telemetry:
    """Background-thread Aptabase client.

    Construct via :meth:`from_settings`. Call :meth:`send` to enqueue an
    event; the worker thread drains the queue and posts. :meth:`shutdown`
    drains and stops; never required (the worker is a daemon thread)."""

    def __init__(self, cfg: TelemetryConfig, event_log: EventLog | None = None) -> None:
        self._cfg = cfg
        self._event_log = event_log
        self._queue: queue.Queue[tuple[str, dict[str, str]] | None] = queue.Queue(
            maxsize=QUEUE_CAPACITY
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        if self.enabled:
            self._start_workers()

    @classmethod
    def from_settings(
        cls,
        *,
        data_root: Path,
        app_version: str,
        settings_app: dict[str, Any],
        event_log: EventLog | None = None,
        is_debug: bool = False,
    ) -> Telemetry:
        """Resolve enable-flag + env-var endpoint overrides into a config.

        Host / app key come from the baked-in :data:`APTABASE_HOST` and
        :data:`APTABASE_APP_KEY`. ``TESSERAE_TELEMETRY_HOST`` and
        ``TESSERAE_TELEMETRY_APP_KEY`` override them — these exist only
        as a dev convenience for the maintainer, not as a public
        re-aiming knob. ``TESSERAE_TELEMETRY=0`` kills it hard.

        ``event_log`` (optional): when provided, each send attempt is
        recorded as a ``type="telemetry"`` row so the Events tab shows
        whether the endpoint is actually reachable.

        ``is_debug``: marks events as debug traffic so the maintainer's
        Aptabase dashboard can filter them out of the prod view. Passed
        as ``args.dev`` from ``app.main._serve``."""
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
                is_debug=is_debug,
            ),
            event_log=event_log,
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

    def set_enabled(self, on: bool) -> None:
        """Toggle the enabled state at runtime. Spins up the worker
        and heartbeat threads on the first transition to on (so a fresh
        app where startup created a disabled Telemetry doesn't have to
        be restarted after the user flips the toggle). On→off keeps the
        threads around but ``send`` drops everything, so a future
        re-enable picks up immediately."""
        can_enable = on and bool(self._cfg.host) and bool(self._cfg.app_key)
        self._cfg = dataclasses.replace(self._cfg, enabled=can_enable)
        if can_enable:
            self._stop.clear()
            self._start_workers()

    def _start_workers(self) -> None:
        """Idempotent: only spawns a worker / heartbeat thread if one
        isn't already running."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._worker, name="tesserae-telemetry", daemon=True
            )
            self._thread.start()
        if self._heartbeat_thread is None or not self._heartbeat_thread.is_alive():
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat, name="tesserae-telemetry-heartbeat", daemon=True
            )
            self._heartbeat_thread.start()

    def test_send(self) -> str | None:
        """Synchronous send for immediate UI feedback when a user flips
        the toggle on. Fires the canonical ``app.started`` event — the
        same one that would otherwise fire on next process start, so
        the maintainer's dashboard sees this install right away.
        Returns ``None`` on a 2xx response, or a short error string."""
        if not self.enabled:
            return "telemetry is disabled"
        return self._post("app.started", {})

    def shutdown(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)  # wake the worker
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=timeout)

    # -- worker ---------------------------------------------------------

    def _heartbeat(self) -> None:
        """Fire ``app.heartbeat`` every ``HEARTBEAT_INTERVAL_S`` while
        enabled. Wakes early on ``_stop`` so shutdown isn't blocked
        waiting out the full hour."""
        while not self._stop.wait(timeout=HEARTBEAT_INTERVAL_S):
            if self.enabled:
                self.send("app.heartbeat", {})

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                return
            name, props = item
            self._post(name, props)  # return value discarded

    def _post(self, event_name: str, props: dict[str, str]) -> str | None:
        """POST one event. Returns ``None`` on a 2xx response or a short
        error string on failure (HTTPError gets ``HTTP <code> <reason>``).

        Records a ``type="telemetry"`` row in the event log either way so
        the Events tab can show success / failure side-by-side with push
        history."""
        # Singular ``/api/v0/event`` takes a bare object — see JS SDK.
        body = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "sessionId": self._cfg.instance_id,
            "eventName": event_name,
            "systemProps": _system_props(self._cfg.app_version, is_debug=self._cfg.is_debug),
            "props": props,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._cfg.post_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "App-Key": self._cfg.app_key,
                "User-Agent": _user_agent(self._cfg.app_version),
            },
            method="POST",
        )
        started = datetime.now(UTC).timestamp()
        err_msg: str | None = None
        try:
            with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
                resp.read(1)  # discard body
        except urllib.error.HTTPError as err:
            err_msg = f"HTTP {err.code} {err.reason}"
            logger.debug("telemetry: send failed (%s): %s", event_name, err_msg)
        except urllib.error.URLError as err:
            err_msg = str(err.reason or err)
            logger.debug("telemetry: send failed (%s): %s", event_name, err_msg)
        except OSError as err:
            err_msg = str(err)
            logger.debug("telemetry: send error (%s): %s", event_name, err_msg)
        if self._event_log is not None:
            with contextlib.suppress(Exception):
                # Logging is best-effort — never let an event-log issue
                # take down the worker thread or the request thread.
                # We stash the exact JSON body in ``extra`` so the Events
                # tab can show the user what we shipped (or tried to
                # ship) — handy for diagnosing 400s like the
                # isDebug/sdkVersion shape one. sessionId is the only
                # identifier and it's anonymous (instance UUID), so
                # surfacing it is fine.
                self._event_log.record(
                    type="telemetry",
                    source=event_name,
                    target=self._cfg.host,
                    status="sent" if err_msg is None else "failed",
                    error=err_msg,
                    duration_s=datetime.now(UTC).timestamp() - started,
                    extra={"payload": body, "endpoint": self._cfg.post_url},
                )
        return err_msg
