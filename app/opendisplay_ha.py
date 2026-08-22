"""Push Tesserae frames to OpenDisplay tags through Home Assistant.

For each ``opendisplay_ha`` device, when its rendered frame changes this
writes the composition PNG into Home Assistant's media folder and calls the
``opendisplay.upload_image`` action (via the ha_core connection); HA's
OpenDisplay integration then pushes it to the tag over Bluetooth LE. No BLE
on the Tesserae host, and it scales to many tags (each targets its own HA
device id).

The service takes a media source, not a URL, so the frame is written to
``<media_root>/tesserae/<device_id>.png`` and referenced as
``media-source://media_source/local/tesserae/<device_id>.png``. A stable
per-device filename, overwritten in place, means the folder never grows
beyond one small PNG per device: no per-push accumulation to prune. Writes
are atomic (temp + rename) so HA never reads a half-written frame, a
deleted device's file is removed, and a startup sweep clears orphans left
by renamed / removed devices.

Registered as a PushManager listener, so it reacts to every push and acts
only on the ``opendisplay_ha`` devices whose frame actually changed. mypy
--strict applies to this module (see pyproject.toml).
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import threading
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Flask

from app import ha_telemetry
from app.device_loader import Device, DeviceRegistry

logger = logging.getLogger(__name__)

KIND = "opendisplay_ha"
# Subdirectory under the HA media root; also the media-source path segment.
MEDIA_SUBDIR = "tesserae"
DEFAULT_MEDIA_ROOT = "/media"
# How often to pull each tag's telemetry from HA (battery / signal / fw).
# Telemetry changes independently of renders, so this is its own cadence,
# not tied to pushes. Overridable via app.opendisplay_telemetry_interval_s.
TELEMETRY_INTERVAL_S = 900


def _ha_core_module(app: Flask) -> Any:
    """The ha_core plugin's server module, or None when it isn't loaded."""
    registry = app.config.get("PLUGIN_REGISTRY")
    plugin = registry.get("ha_core") if registry is not None else None
    return getattr(plugin, "server_module", None) if plugin is not None else None


# HA holds the HTTP connection open until opendisplay.upload_image finishes,
# and a BLE e-paper transfer is slow (connect + chunked image + refresh).
# The default 10s HTTP timeout fired mid-push and masked HA's own error, so
# give the BLE leg room to either complete or report its real failure.
UPLOAD_TIMEOUT_S = 120


class OpenDisplayHaPublisher:
    def __init__(
        self,
        *,
        app: Flask,
        devices: DeviceRegistry,
        settings: Any,
        renders_dir: Path,
        latest_render_fn: Callable[[str], dict[str, Any] | None],
        media_root: Path | None = None,
        run_async: bool = True,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._renders_dir = Path(renders_dir)
        self._latest = latest_render_fn
        self._media_root = (
            Path(media_root) if media_root is not None else Path(self._configured_root())
        )
        self._media_dir = self._media_root / MEDIA_SUBDIR
        self._last_sent: dict[str, str] = {}
        self._media_warned = False
        self._lock = threading.Lock()
        # A BLE upload can block for tens of seconds; push listeners run
        # synchronously in the push thread, so offload to a single serial
        # worker (BLE is serial anyway) and coalesce via a dirty flag so a
        # push never stalls the pipeline. Tests set run_async=False to keep
        # on_push synchronous.
        self._run_async = run_async
        self._dirty = False
        self._worker_running = False

    def _configured_root(self) -> str:
        try:
            root = self._settings.get_section("app").get("opendisplay_media_root")
        except Exception:
            root = None
        return str(root) if isinstance(root, str) and root.strip() else DEFAULT_MEDIA_ROOT

    # -- push reaction ----------------------------------------------------

    def on_push(self, _result: Any = None) -> None:
        """Listener entry point (runs in the push thread). Schedules a
        background pass so a slow BLE upload never blocks the pipeline;
        never raises into the caller. Synchronous when run_async=False."""
        if not self._run_async:
            self._process_once()
            return
        if not self._ha_devices():
            return
        with self._lock:
            self._dirty = True
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._worker, name="opendisplay-ha", daemon=True).start()

    def _worker(self) -> None:
        """Drain pushes until nothing is pending. Any push arriving during a
        pass re-sets the dirty flag, so the newest frame always gets sent
        without overlapping uploads."""
        while True:
            with self._lock:
                if not self._dirty:
                    self._worker_running = False
                    return
                self._dirty = False
            self._process_once()

    def _process_once(self) -> None:
        """Re-check every opendisplay_ha device and push the ones whose
        frame changed."""
        for device in self._ha_devices():
            try:
                self._maybe_send(device)
            except Exception:
                logger.exception("opendisplay_ha: send failed for %s", device.id)

    def _ha_devices(self) -> list[Device]:
        return [d for d in self._devices.devices.values() if d.kind_of == KIND]

    def _device_cfg(self, device_id: str) -> dict[str, Any]:
        section = self._settings.get_section("devices") or {}
        cfg = section.get(device_id) if isinstance(section, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    def _maybe_send(self, device: Device) -> None:
        latest = self._latest(device.id)
        if not latest:
            return
        digest = str(latest.get("composition_digest") or "")
        if not digest:
            return
        with self._lock:
            if self._last_sent.get(device.id) == digest:
                return
        cfg = self._device_cfg(device.id)
        ha_device_id = str(cfg.get("ha_device_id") or "").strip()
        if not ha_device_id:
            logger.warning(
                "opendisplay_ha %s: no HA device id set (Settings -> Devices); skipping", device.id
            )
            return
        src = self._renders_dir / f"{digest}.png"
        if not src.exists():
            logger.warning("opendisplay_ha %s: composition %s missing on disk", device.id, digest)
            return
        try:
            media_id = self._write_media(device.id, src)
        except OSError as exc:
            # Most likely the HA media folder isn't writable: the add-on
            # needs the media:rw mapping and a writable /media (the
            # tesserae subdir is chowned to the app user at container
            # start). Warn once, actionably, instead of a traceback per
            # render; clear the flag on success so a later fix re-arms it.
            if not self._media_warned:
                self._media_warned = True
                logger.warning(
                    "opendisplay_ha %s: can't write frame to %s (%s). In the HA add-on "
                    "this needs the media:rw mapping and a writable /media; update the "
                    "add-on and restart. Frames won't reach the tag until this is fixed.",
                    device.id,
                    self._media_dir,
                    exc,
                )
            return
        self._media_warned = False
        rotate = self._parse_rotate(cfg.get("rotate"))
        if self._call_upload(ha_device_id, media_id, rotate):
            with self._lock:
                self._last_sent[device.id] = digest
            logger.info("opendisplay_ha %s: pushed frame to HA device %s", device.id, ha_device_id)

    @staticmethod
    def _parse_rotate(raw: Any) -> int:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return 0
        return val if val in (0, 90, 180, 270) else 0

    # -- media file -------------------------------------------------------

    def _write_media(self, device_id: str, src: Path) -> str:
        """Atomically copy the composition PNG to the device's stable media
        file and return its media-source id."""
        self._media_dir.mkdir(parents=True, exist_ok=True)
        dst = self._media_dir / f"{device_id}.png"
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        return f"media-source://media_source/local/{MEDIA_SUBDIR}/{device_id}.png"

    def _call_upload(self, ha_device_id: str, media_id: str, rotate: int) -> bool:
        registry = self._app.config.get("PLUGIN_REGISTRY")
        plugin = registry.get("ha_core") if registry is not None else None
        mod = getattr(plugin, "server_module", None) if plugin is not None else None
        if mod is None or not hasattr(mod, "call_service"):
            logger.warning("opendisplay_ha: ha_core plugin unavailable; can't push")
            return False
        data: dict[str, Any] = {
            "device_id": ha_device_id,
            "image": {"media_content_id": media_id, "media_content_type": "image/png"},
        }
        if rotate:
            data["rotation"] = rotate
        try:
            with self._app.app_context():
                mod.call_service("opendisplay", "upload_image", data=data, timeout=UPLOAD_TIMEOUT_S)
            return True
        except Exception as exc:
            # HA echoes the integration's actual error in the response body
            # (an HTTPError). The bare status ("HTTP Error 500") hides why
            # opendisplay.upload_image failed, so read the body when present.
            detail = ""
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    detail = exc.read().decode("utf-8", "replace").strip()[:500]
                except Exception:
                    detail = ""
            logger.warning(
                "opendisplay_ha: upload_image failed (%s)%s",
                exc,
                f": {detail}" if detail else "",
            )
            return False

    # -- housekeeping -----------------------------------------------------

    def cleanup_device(self, device_id: str) -> None:
        """Drop a device's media file + cached digest (call on delete)."""
        with self._lock:
            self._last_sent.pop(device_id, None)
        with contextlib.suppress(OSError):
            (self._media_dir / f"{device_id}.png").unlink(missing_ok=True)

    def prune_orphans(self) -> int:
        """Remove media files for device ids that are no longer registered
        opendisplay_ha instances. Returns the count removed."""
        if not self._media_dir.exists():
            return 0
        live = {d.id for d in self._ha_devices()}
        removed = 0
        try:
            entries = [p for p in self._media_dir.iterdir() if p.suffix == ".png"]
        except OSError:
            return 0
        for path in entries:
            if path.stem not in live:
                with contextlib.suppress(OSError):
                    path.unlink()
                    removed += 1
        return removed


class OpenDisplayHaTelemetryPoller:
    """Periodically pull each opendisplay_ha tag's battery / signal / firmware
    from Home Assistant and feed it through the normal heartbeat pipeline, so
    the device card shows the same telemetry as a device that heartbeats us
    directly. The tag never talks to Tesserae, HA does, so this reshapes HA's
    entity states into a heartbeat via app.ha_telemetry."""

    def __init__(
        self,
        *,
        app: Flask,
        devices: DeviceRegistry,
        settings: Any,
        interval_s: int = TELEMETRY_INTERVAL_S,
        run_async: bool = True,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._interval_s = max(60, int(interval_s))
        self._run_async = run_async
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._run_async or (self._thread is not None and self._thread.is_alive()):
            return
        self._thread = threading.Thread(
            target=self._loop, name="opendisplay-ha-telemetry", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Brief settle, an early first sample, then the steady interval.
        if self._stop.wait(min(30, self._interval_s)):
            return
        while True:
            try:
                self.poll_once()
            except Exception:
                logger.exception("opendisplay_ha telemetry: poll failed")
            if self._stop.wait(self._interval_s):
                return

    def _ha_devices(self) -> list[Device]:
        return [d for d in self._devices.devices.values() if d.kind_of == KIND]

    def _device_cfg(self, device_id: str) -> dict[str, Any]:
        section = self._settings.get_section("devices") or {}
        cfg = section.get(device_id) if isinstance(section, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    def poll_once(self) -> int:
        """Refresh telemetry for every configured tag. Returns the count
        recorded (useful for tests).

        Runs inside an app context: this is called from a worker thread, and
        ha_core's ``render_template`` reads configuration through
        ``current_app``. Without it every query raised "Working outside of
        application context", was swallowed at DEBUG, and the telemetry
        silently never worked for any tag."""
        mod = _ha_core_module(self._app)
        if mod is None or not hasattr(mod, "render_template"):
            return 0
        with self._app.app_context():
            return self._poll_once_locked(mod)

    def _poll_once_locked(self, mod: Any) -> int:
        recorded = 0
        attempted = 0
        for device in self._ha_devices():
            ha_device_id = str(self._device_cfg(device.id).get("ha_device_id") or "").strip()
            if not ha_device_id:
                continue
            attempted += 1
            try:
                rendered = mod.render_template(ha_telemetry.build_template(ha_device_id))
            except Exception as exc:
                logger.debug(
                    "opendisplay_ha telemetry: HA query failed for %s (%s)", device.id, exc
                )
                continue
            heartbeat = ha_telemetry.parse_telemetry(rendered)
            if not heartbeat:
                continue
            if self._record(device, heartbeat):
                recorded += 1
        # A poll where every configured tag failed is a broken integration, not
        # a slow one. The per-device causes stay at DEBUG (they repeat per
        # interval); this one line is what makes a total failure visible, which
        # it was not while the app-context bug was live.
        if attempted and not recorded:
            logger.warning(
                "opendisplay_ha telemetry: %d configured tag(s), none recorded; "
                "check the ha_core connection",
                attempted,
            )
        return recorded

    def _record(self, device: Device, heartbeat: dict[str, Any]) -> bool:
        events = self._app.config.get("EVENT_LOG")
        status_cache = self._app.config.get("DEVICE_STATUS")
        if events is None or status_cache is None:
            return False
        # Local import avoids a module-load cycle (transport_wiring imports
        # this module's register()).
        from app.transport_wiring import record_status_heartbeat

        payload = json.dumps(heartbeat).encode("utf-8")
        try:
            with self._app.app_context():
                record_status_heartbeat(
                    app=self._app,
                    device=device,
                    payload=payload,
                    status_cache=status_cache,
                    event_log=events,
                    event_target=f"ha://{device.id}/telemetry",
                )
            return True
        except Exception:
            logger.exception("opendisplay_ha telemetry: record failed for %s", device.id)
            return False


def _configured_telemetry_interval(settings: Any) -> int:
    try:
        val = settings.get_section("app").get("opendisplay_telemetry_interval_s")
    except Exception:
        val = None
    try:
        return max(60, int(val)) if val is not None else TELEMETRY_INTERVAL_S
    except (TypeError, ValueError):
        return TELEMETRY_INTERVAL_S


def register(app: Flask) -> None:
    """Build the publisher and wire it to the push loop. No-op if there's no
    push manager (bare test setups)."""
    push_mgr = app.config.get("PUSH_MANAGER")
    devices = app.config.get("DEVICE_REGISTRY")
    settings = app.config.get("SETTINGS_STORE")
    renders_dir = app.config.get("RENDERS_DIR")
    if push_mgr is None or devices is None or settings is None or renders_dir is None:
        return
    pub = OpenDisplayHaPublisher(
        app=app,
        devices=devices,
        settings=settings,
        renders_dir=renders_dir,
        latest_render_fn=push_mgr.latest_render_for,
    )
    push_mgr.add_listener(pub.on_push)
    app.config["OPENDISPLAY_HA_PUBLISHER"] = pub
    with contextlib.suppress(Exception):
        pub.prune_orphans()

    # Telemetry poller (battery / signal / firmware from HA). Stop any
    # previous one first: register() re-runs on every transport rebuild, and
    # a leaked poll thread would keep querying HA against a stale app.
    old_poller = app.config.get("OPENDISPLAY_HA_TELEMETRY_POLLER")
    if old_poller is not None:
        with contextlib.suppress(Exception):
            old_poller.stop()
    poller = OpenDisplayHaTelemetryPoller(
        app=app,
        devices=devices,
        settings=settings,
        interval_s=_configured_telemetry_interval(settings),
    )
    poller.start()
    app.config["OPENDISPLAY_HA_TELEMETRY_POLLER"] = poller
