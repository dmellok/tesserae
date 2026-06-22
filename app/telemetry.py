"""Anonymous, opt-in usage telemetry.

When enabled, posts a small set of events to the maintainer's PostHog
Cloud project:

* ``app.started``, once per process start (Tesserae version, Python
  version, platform).
* ``app.heartbeat``, every hour while the process is running. Props
  carry fleet-shape counts (``n_devices``, ``device_kinds``, ``n_pages``,
  ``n_user_themes``) and activity counters since the previous heartbeat
  (``n_pushes_since_last``, ``n_push_failures_since_last``,
  ``n_widget_errors_since_last``). The provider closure is registered
  by ``app_factory`` so the telemetry module stays free of any direct
  knowledge of devices / pages / push internals.
* ``update.applied``, when the in-app updater successfully applies a
  new revision; props carry the from/to short SHAs and channel.
* ``theme.user_created``, first time the user persists a custom theme.
  Fires once per instance, so the maintainer sees how often the theme
  builder is actually reached. Props carry no theme content.

What it does **not** send: IP addresses, hostnames, paths, settings,
secrets, push contents, dashboard layouts, theme palettes, widget data,
or anything tied to a real identity. The only stable identifier is a
random UUID generated on first run and persisted to
``data/core/.instance_id`` so the same install counts as one across
restarts.

PostHog privacy configuration baked into every event:
* ``$ip: ""`` — request IP not stored on the event. PostHog still uses
  it server-side at ingestion to derive the country + region columns
  the maintainer needs to see roughly where Tesserae is running, then
  drops it. No IP ever lands on the stored event row.
* ``$process_person_profile: false`` — no person profile created or
  updated; the install UUID is the only identity surface and it never
  gets enriched.

Country/region IS recorded. Earlier privacy posture was "no geo
enrichment at all" (``$geoip_disable: true``), but the maintainer
wants the country breakdown to plan hardware support and prioritise
docs translations. Country + region is a coarser identifier than IP
or city, and the IP itself is never stored.

Pre-v0.64 the backend was a self-hosted Aptabase deployment at
``aptabase.dmello.io``. v0.64.0 moved to PostHog Cloud (US region;
project ``phc_rRc7…``) because the Aptabase dashboards weren't giving
the maintainer the cohort + funnel views needed to actually answer
questions about how Tesserae is used. The data footprint is unchanged
— same three events, same install UUID, no PII.

The endpoint and project key are **baked into this module**; users
cannot re-aim Tesserae's telemetry at a different server. That's
deliberate: the whole point is so the maintainer can count installs,
which only works if every opted-in instance reports to the same place.

Default: **off**. Users opt in via Settings → Server → App, or
configuration. ``TESSERAE_TELEMETRY=0`` disables hard regardless of
stored settings.

Failure-silent: a network/endpoint failure logs at DEBUG and drops the
event. The send happens on a daemon background thread so nothing ever
blocks startup or a request.

``TESSERAE_TELEMETRY_HOST`` / ``TESSERAE_TELEMETRY_PROJECT_KEY`` env
vars are honoured **only** as a development convenience (so the
maintainer can point a dev build at a staging project without
committing throwaway keys). Users on a release won't have those set;
the baked defaults apply.
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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.state.event_log import EventLog

logger = logging.getLogger(__name__)

# --- Baked-in endpoint ------------------------------------------------
# Maintainer-managed reverse proxy that forwards to PostHog Cloud US.
# The proxy lives at https://t.dmello.io and forwards both the
# ``/i/v0/e/`` event ingest path and the ``/static/array.js`` asset
# bundle to ``us.i.posthog.com`` / ``us-assets.i.posthog.com``. Using
# a proxy lets the endpoint look like a first-party domain to network-
# level ad-blockers that silently drop requests to known analytics
# hosts (uBlock Origin's default lists, Pi-hole, NextDNS); the events
# arriving at PostHog itself are byte-identical.
# Users cannot re-aim Tesserae's telemetry; the setting toggles
# whether to send, not where it goes, so opted-in installs add up to
# a real total. The project key is a write-only routing identifier
# intended to ship in clients (not a secret — same model as PostHog's
# ``posthog.init(...)`` snippet on a public website). Empty strings
# disable telemetry even if the user has the toggle on.
POSTHOG_HOST: str = "https://t.dmello.io"
POSTHOG_PROJECT_KEY: str = "phc_rRc7y27hcc369mcDb6VXEsDYs3wwQvzuzqJKn2DTxm32"

INSTANCE_ID_FILE = "core/.instance_id"
SEND_TIMEOUT_S = 4.0
QUEUE_CAPACITY = 64  # Bounded, drop new events when backlogged.
# Cheap (~24 events/day per install); valuable: lets the maintainer
# see returning installs + a live count via PostHog's hourly
# breakdown without per-action instrumentation.
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
    regardless of stored settings, for unattended installs, CI, etc."""
    raw = os.environ.get("TESSERAE_TELEMETRY", "").strip().lower()
    return raw in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool
    host: str  # endpoint base URL, e.g. "https://t.dmello.io"
    project_key: str
    instance_id: str
    app_version: str
    # Routes events through the same PostHog project but tags them so the
    # maintainer can filter dev traffic out of the production view.
    # Wired from the ``--dev`` flag in ``app.main._serve``.
    is_debug: bool = False

    @property
    def post_url(self) -> str:
        # PostHog's server-side capture endpoint. The same path is used
        # by every official PostHog SDK; the JS one POSTs to the same
        # URL just with credentials.mode=include and a slightly larger
        # batch.
        return f"{self.host.rstrip('/')}/i/v0/e/"


def _platform_props(app_version: str) -> dict[str, object]:
    """Server-side platform context. Picked to mirror what the legacy
    Aptabase ``systemProps`` block carried (locale + appVersion + sdk
    name/version) plus what PostHog's other server SDKs send by
    default. PostHog convention prefixes its own properties with ``$``;
    Tesserae's own properties have no prefix so they sort separately in
    the event-explorer UI."""
    return {
        # PostHog standard property names (the $-prefixed ones get
        # surfaced as first-class columns in the event browser).
        "$lib": "posthog-tesserae",
        "$lib_version": app_version,
        "$os": _platform.system() or "Unknown",
        # Tesserae-specific.
        "version": app_version,
        "python_version": _platform.python_version(),
        "platform_name": _platform.platform(),
    }


def _privacy_props() -> dict[str, object]:
    """PostHog privacy kill-switches sent on EVERY event so a future
    SDK default-flip can't quietly re-enable IP storage or person-
    profile creation. ``$geoip_disable`` is deliberately *not* set —
    the maintainer wants country + region columns to see where
    Tesserae is running. PostHog still uses the request IP at
    ingestion to derive those columns, then drops the IP itself
    (``$ip: ""`` below)."""
    return {
        # Empty ``$ip`` blocks PostHog from filling it in from the
        # request socket — the IP is used server-side for the geo
        # lookup at ingestion, then NOT written to the stored event.
        # No IP ever lands on disk.
        "$ip": "",
        # Don't create or update a person profile for the install UUID.
        # Without this PostHog would otherwise track the install as an
        # anonymous person across events, which is more identity-
        # adjacent than we want.
        "$process_person_profile": False,
    }


class Telemetry:
    """Background-thread PostHog Cloud client.

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
        # Optional closure that returns rich props to fold into each
        # ``app.heartbeat``. Registered by app_factory after PushManager
        # + the registries exist (this module knows nothing about them).
        self._heartbeat_props_fn: Callable[[], dict[str, str]] | None = None
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

        Host / project key come from the baked-in :data:`POSTHOG_HOST`
        and :data:`POSTHOG_PROJECT_KEY`. ``TESSERAE_TELEMETRY_HOST`` and
        ``TESSERAE_TELEMETRY_PROJECT_KEY`` override them; these exist
        only as a dev convenience for the maintainer, not as a public
        re-aiming knob. ``TESSERAE_TELEMETRY=0`` kills it hard.

        ``event_log`` (optional): when provided, each send attempt is
        recorded as a ``type="telemetry"`` row so the Events tab shows
        whether the endpoint is actually reachable.

        ``is_debug``: marks events as debug traffic so the maintainer's
        PostHog dashboard can filter them out of the prod view. Passed
        as ``args.dev`` from ``app.main._serve``."""
        enabled_setting = bool(settings_app.get("telemetry_enabled", False))
        host = os.environ.get("TESSERAE_TELEMETRY_HOST", "").strip() or POSTHOG_HOST
        key = os.environ.get("TESSERAE_TELEMETRY_PROJECT_KEY", "").strip() or POSTHOG_PROJECT_KEY
        enabled = enabled_setting and bool(host) and bool(key) and not _env_off()
        return cls(
            TelemetryConfig(
                enabled=enabled,
                host=host,
                project_key=key,
                instance_id=_ensure_instance_id(data_root),
                app_version=app_version,
                is_debug=is_debug,
            ),
            event_log=event_log,
        )

    @classmethod
    def disabled(cls) -> Telemetry:
        """Inert client for tests / ``--testing`` mode, never sends, never
        creates an instance-id file."""
        return cls(
            TelemetryConfig(
                enabled=False,
                host="",
                project_key="",
                instance_id="00000000-0000-0000-0000-000000000000",
                app_version="0",
            )
        )

    # -- public API -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled and bool(self._cfg.host) and bool(self._cfg.project_key)

    @property
    def instance_id(self) -> str:
        return self._cfg.instance_id

    @property
    def endpoint(self) -> str:
        return self._cfg.post_url if self.enabled else ""

    def send(self, event_name: str, props: dict[str, str] | None = None) -> None:
        """Enqueue an event. Drops silently if disabled or the queue is
        full, never blocks the caller."""
        if not self.enabled:
            return
        try:
            self._queue.put_nowait((event_name, props or {}))
        except queue.Full:
            logger.debug("telemetry: queue full, dropping %s", event_name)

    def set_heartbeat_props_provider(self, fn: Callable[[], dict[str, str]] | None) -> None:
        """Register a closure that contributes fleet-shape + activity
        props to each ``app.heartbeat``. ``None`` removes the provider.
        Errors thrown inside the provider are logged + ignored, a bad
        provider must never silence the heartbeat itself, since the
        heartbeat doubles as a liveness signal."""
        self._heartbeat_props_fn = fn

    def set_enabled(self, on: bool) -> None:
        """Toggle the enabled state at runtime. Spins up the worker
        and heartbeat threads on the first transition to on (so a fresh
        app where startup created a disabled Telemetry doesn't have to
        be restarted after the user flips the toggle). On→off keeps the
        threads around but ``send`` drops everything, so a future
        re-enable picks up immediately."""
        can_enable = on and bool(self._cfg.host) and bool(self._cfg.project_key)
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
        the toggle on. Fires the canonical ``app.started`` event, the
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
        waiting out the full hour. Props from the registered provider
        (fleet shape + activity counters) get folded in here; provider
        failure leaves the heartbeat bare but still firing."""
        while not self._stop.wait(timeout=HEARTBEAT_INTERVAL_S):
            if not self.enabled:
                continue
            props: dict[str, str] = {}
            if self._heartbeat_props_fn is not None:
                try:
                    props = dict(self._heartbeat_props_fn())
                except Exception:
                    logger.debug("telemetry: heartbeat props provider raised", exc_info=True)
                    props = {}
            self.send("app.heartbeat", props)

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
        """POST one event to PostHog's capture endpoint. Returns ``None``
        on a 2xx response or a short error string on failure (HTTPError
        gets ``HTTP <code> <reason>``).

        Records a ``type="telemetry"`` row in the event log either way so
        the Events tab can show success / failure side-by-side with push
        history."""
        # PostHog's /i/v0/e/ takes a single event object (or a batched
        # ``{batch: [...]}``). The fields are flat: ``api_key`` for
        # routing, ``event`` for the name, ``distinct_id`` for the
        # install identity, ``properties`` for everything else.
        # ``timestamp`` is optional but pinning it client-side stops
        # PostHog from using the wall-clock-at-receive time which can
        # be skewed when a heartbeat batches across a network blip.
        all_props: dict[str, object] = {}
        all_props.update(_platform_props(self._cfg.app_version))
        all_props.update(_privacy_props())
        # Maintainer's debug filter — flagged at the event level so
        # dashboards can filter ``properties.is_debug = false`` to see
        # production traffic only.
        all_props["is_debug"] = self._cfg.is_debug
        # Caller props win against the defaults; in practice they don't
        # collide (caller props are fleet shape / activity counters).
        for k, v in props.items():
            all_props[k] = v
        body = {
            "api_key": self._cfg.project_key,
            "event": event_name,
            "distinct_id": self._cfg.instance_id,
            "properties": all_props,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._cfg.post_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                # PostHog accepts the project key either in the body
                # (api_key field) or in this header. We send it both
                # ways so a future SDK / proxy that strips one still
                # routes correctly.
                "Authorization": f"Bearer {self._cfg.project_key}",
                "User-Agent": f"tesserae-telemetry/{self._cfg.app_version}",
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
                # Logging is best-effort, never let an event-log issue
                # take down the worker thread or the request thread.
                # We stash the exact JSON body (sans the project key,
                # which is fine to log since it's not a secret but is
                # noisy in the events table) in ``extra`` so the Events
                # tab can show the user what we shipped. distinct_id is
                # the only identifier and it's the install UUID, so
                # surfacing it is fine.
                redacted_body = {**body, "api_key": "phc_…"}
                self._event_log.record(
                    type="telemetry",
                    source=event_name,
                    target=self._cfg.host,
                    status="sent" if err_msg is None else "failed",
                    error=err_msg,
                    duration_s=datetime.now(UTC).timestamp() - started,
                    extra={"payload": redacted_body, "endpoint": self._cfg.post_url},
                )
        return err_msg
