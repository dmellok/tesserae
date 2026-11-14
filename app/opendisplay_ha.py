"""Push Tesserae frames to OpenDisplay tags through Home Assistant.

For each ``opendisplay_ha`` device, when its rendered frame changes this
puts the composition PNG into Home Assistant's media folder and calls the
``opendisplay.upload_image`` action (via the ha_core connection); HA's
OpenDisplay integration then pushes it to the tag over Bluetooth LE. No BLE
on the Tesserae host, and it scales to many tags (each targets its own HA
device id).

The service takes a media source, not a URL, so the frame has to be in HA's
media folder as ``tesserae/<device_id>.png``, referenced as
``media-source://media_source/local/tesserae/<device_id>.png``. It gets
there one of two ways:

* **Shared folder.** As the HA add-on (or with ``app.opendisplay_media_root``
  pointing at a mount of HA's media folder) the file is written straight to
  ``<media_root>/tesserae/``. Writes are atomic (temp + rename) so HA never
  reads a half-written frame, a deleted device's file is removed, and a
  startup sweep clears orphans left by renamed / removed devices.
* **Upload.** Anywhere else (Docker, Proxmox, bare metal) Tesserae has no
  filesystem in common with HA, so the frame is sent through HA's media
  upload endpoint with the ha_core token, which must belong to an
  administrator. The add-on also falls back to this when its media folder
  is not writable. HA's REST API cannot delete media, so a removed device
  leaves its one small PNG behind in HA.

Either way the filename is stable per device and overwritten in place, so
the folder never grows beyond one small PNG per device.

The frame is turned onto the tag's **surface** before it leaves, from the
device's Panel rotation, and inset by its underscan, so one Rotation control
means the same thing on every kind. The surface is the tag's pixel size
turned by the tag's own rotation: the SDK applies that rotation to whatever
it receives, so a frame has to be upright on the surface, not aimed at the
raw panel. ``app.opendisplay_tags`` reads the surface from HA (several tags
are taller than wide); until it has, a landscape surface is assumed, the
mapping ``pi_png`` gives a polled client. A device saved with the older
``rotate`` option (a turn applied by HA's upload action) keeps that
behaviour until the option is cleared: its upright composition is sent as
it was and HA does the turning.

Colour conversion is the device's choice. By default the frame goes out in
full colour and HA's SDK dithers it for the tag. With ``colour_conversion``
set to ``tesserae`` the frame is reduced to the device's palette here, by
the same call that draws the previews, and HA is told not to dither, so the
tag shows what the preview showed.

Each tag's last delivery (sent, or failed and why) is kept for its device
page. A failed upload is tried again on a backoff, since the usual cause is
a tag that was asleep or out of Bluetooth range for a moment.

Registered as a PushManager listener, so it reacts to every push and acts
only on the ``opendisplay_ha`` devices whose frame actually changed. What
each tag was last sent is kept on disk: the listener is rebuilt on every
transport rebuild (any device settings save) and on restart, and a publisher
that forgot would answer the next push, for any device, by repainting every
tag with the frame it already shows, the slow BLE uploads queued ahead of
the one frame that did change. mypy
--strict applies to this module (see pyproject.toml).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Flask

from app import background_loops, ha_telemetry, opendisplay_tags
from app.device_loader import Device, DeviceRegistry
from app.ha_options import is_ha_addon
from app.panel import device_panel
from app.quantizer import apply_underscan, quantize_for_gamut_to_png, rotate_png

logger = logging.getLogger(__name__)

KIND = "opendisplay_ha"
# Subdirectory under the HA media root; also the media-source path segment.
MEDIA_SUBDIR = "tesserae"
DEFAULT_MEDIA_ROOT = "/media"
# HA's local media source, the folder the upload endpoint writes under.
MEDIA_SOURCE_LOCAL = "media-source://media_source/local"
# Sending one small PNG over the LAN; generous for a slow or busy HA host.
MEDIA_UPLOAD_TIMEOUT_S = 30
# How often to pull each tag's telemetry from HA (battery / signal / fw).
# Telemetry changes independently of renders, so this is its own cadence,
# not tied to pushes. Overridable via app.opendisplay_telemetry_interval_s.
TELEMETRY_INTERVAL_S = 900
# Seconds to wait before trying a failed upload again, by attempt. A tag that
# was asleep or briefly out of range is back within minutes; one that is
# gone should not have HA hunting for it all day. The last step repeats.
RETRY_DELAYS_S: tuple[int, ...] = (60, 300, 900, 1800)
# Gamuts with no ink palette to reduce to: the frame stays full colour.
_UNQUANTISED_GAMUTS = frozenset({"rgb24", "rgb16", "kaleido3"})


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
        state_path: Path | None = None,
        run_async: bool = True,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._renders_dir = Path(renders_dir)
        self._latest = latest_render_fn
        configured_root = self._configured_root()
        self._media_root = (
            Path(media_root)
            if media_root is not None
            else Path(configured_root or DEFAULT_MEDIA_ROOT)
        )
        self._media_dir = self._media_root / MEDIA_SUBDIR
        # Write the frame to a folder only when that folder is known to be
        # HA's: an explicit root (constructor or setting), or the add-on's
        # /media mapping. On any other host a writable /media is just a
        # local directory HA never sees, so the frame is uploaded instead.
        self._shared_folder = media_root is not None or bool(configured_root) or is_ha_addon()
        # What each tag was last sent, kept across rebuilds and restarts in
        # ``state_path`` (memory only without one, as in the tests).
        self._state_path = Path(state_path) if state_path is not None else None
        self._last_sent: dict[str, str] = {}
        # Last delivery per device, for its page: {state, at, error, attempts}.
        self._status: dict[str, dict[str, Any]] = {}
        self._load_state()
        self._media_warned = False
        self._lock = threading.Lock()
        self._retry_timer: threading.Timer | None = None
        self._stopped = False
        # A BLE upload can block for tens of seconds; push listeners run
        # synchronously in the push thread, so offload to a single serial
        # worker (BLE is serial anyway) and coalesce via a dirty flag so a
        # push never stalls the pipeline. Tests set run_async=False to keep
        # on_push synchronous.
        self._run_async = run_async
        self._dirty = False
        self._worker_running = False

    def _configured_root(self) -> str:
        """The operator's ``app.opendisplay_media_root``, or "" when unset."""
        try:
            root = self._settings.get_section("app").get("opendisplay_media_root")
        except Exception:
            root = None
        return root.strip() if isinstance(root, str) else ""

    # -- push reaction ----------------------------------------------------

    def on_push(self, _result: Any = None) -> None:
        """Listener entry point (runs in the push thread). Schedules a
        background pass so a slow BLE upload never blocks the pipeline;
        never raises into the caller. Synchronous when run_async=False."""
        if self._stopped:
            return
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

    def _load_state(self) -> None:
        if self._state_path is None:
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        sent = raw.get("sent")
        if isinstance(sent, dict):
            self._last_sent = {str(k): v for k, v in sent.items() if isinstance(v, str)}
        status = raw.get("status")
        if isinstance(status, dict):
            self._status = {str(k): dict(v) for k, v in status.items() if isinstance(v, dict)}

    def _save_state(self) -> None:
        """Write the sent map and delivery status atomically. Best effort:
        losing it only costs one repeat upload per tag."""
        if self._state_path is None:
            return
        with self._lock:
            snapshot = {"sent": dict(self._last_sent), "status": dict(self._status)}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_name(self._state_path.name + ".tmp")
            tmp.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError:
            logger.debug("opendisplay_ha: could not save state", exc_info=True)

    # -- delivery status + retry -----------------------------------------

    def delivery_status(self, device_id: str) -> dict[str, Any] | None:
        """The device's last delivery: ``state`` (``sent`` / ``failed``),
        ``at`` (epoch), and for a failure ``error``, ``attempts`` and
        ``retry_at``. None before anything has been tried."""
        with self._lock:
            status = self._status.get(device_id)
            return dict(status) if status else None

    def _record_sent(self, device_id: str, sent_key: str) -> None:
        with self._lock:
            self._last_sent[device_id] = sent_key
            self._status[device_id] = {"state": "sent", "at": time.time()}
        self._save_state()

    def _record_failure(self, device_id: str, sent_key: str, error: str) -> None:
        """Note a failed delivery and line up another try."""
        now = time.time()
        with self._lock:
            previous = self._status.get(device_id) or {}
            same_frame = previous.get("state") == "failed" and previous.get("key") == sent_key
            attempts = int(previous.get("attempts") or 0) + 1 if same_frame else 1
            delay = RETRY_DELAYS_S[min(attempts, len(RETRY_DELAYS_S)) - 1]
            self._status[device_id] = {
                "state": "failed",
                "at": now,
                "error": error,
                "attempts": attempts,
                "key": sent_key,
                "retry_at": now + delay,
            }
        self._save_state()
        self._schedule_retry(delay)

    def _retry_due(self, device_id: str, sent_key: str) -> bool:
        """False while a failed frame is still waiting out its backoff, so a
        push for another device does not hammer a tag that is out of range."""
        with self._lock:
            status = self._status.get(device_id) or {}
        if status.get("state") != "failed" or status.get("key") != sent_key:
            return True
        try:
            return time.time() >= float(status.get("retry_at") or 0.0)
        except (TypeError, ValueError):
            return True

    def _schedule_retry(self, delay: float) -> None:
        if not self._run_async or self._stopped:
            return
        with self._lock:
            if self._retry_timer is not None and self._retry_timer.is_alive():
                return
            timer = threading.Timer(delay + 1.0, self.on_push)
            timer.daemon = True
            timer.name = "opendisplay-ha-retry"
            self._retry_timer = timer
        timer.start()

    def resend(self, device_id: str) -> None:
        """Forget what the tag was sent and deliver its current frame again
        now (the device page's Send again)."""
        with self._lock:
            self._last_sent.pop(device_id, None)
            self._status.pop(device_id, None)
        self._save_state()
        self.on_push()

    def stop(self) -> None:
        """Cancel the pending retry. Called when a rebuilt transport replaces
        this publisher, so a stale one does not send a second copy."""
        self._stopped = True
        with self._lock:
            timer, self._retry_timer = self._retry_timer, None
        if timer is not None:
            timer.cancel()

    def _render_time(self, device: Device) -> float:
        latest = self._latest(device.id)
        try:
            return float((latest or {}).get("timestamp") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _process_once(self) -> None:
        """Re-check every opendisplay_ha device and push the ones whose
        frame changed, the most recently rendered first: uploads are serial
        and slow, so the tag that was just sent something should not wait
        behind another tag catching up."""
        for device in sorted(self._ha_devices(), key=self._render_time, reverse=True):
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
        cfg = self._device_cfg(device.id)
        rotate = self._parse_rotate(cfg.get("rotate"))
        quarters, underscan = (0, 0) if rotate else self._tag_mapping(device, cfg)
        gamut = self._conversion_gamut(device, cfg)
        # The same composition is a different frame once the mount or the
        # colour handling changes, so both are part of "already sent".
        sent_key = f"{digest}:{quarters}:{underscan}:{rotate}:{gamut or 'ha'}"
        with self._lock:
            if self._last_sent.get(device.id) == sent_key:
                return
        if not self._retry_due(device.id, sent_key):
            return
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
        frame = src.read_bytes()
        if quarters:
            frame = rotate_png(frame, quarters=quarters)
        if underscan:
            frame = apply_underscan(frame, underscan=underscan)
        if gamut:
            frame = quantize_for_gamut_to_png(frame, gamut=gamut)
        media_id, problem = self._stage_frame(device.id, frame)
        if media_id is None:
            self._record_failure(device.id, sent_key, problem)
            return
        problem = self._call_upload(
            ha_device_id,
            media_id,
            rotate,
            dithered=bool(gamut),
            refresh_mode=self._refresh_mode(cfg),
        )
        if problem:
            self._record_failure(device.id, sent_key, problem)
            return
        self._record_sent(device.id, sent_key)
        logger.info("opendisplay_ha %s: pushed frame to HA device %s", device.id, ha_device_id)

    @staticmethod
    def _tag_mapping(device: Device, cfg: dict[str, Any]) -> tuple[int, int]:
        """Clockwise quarter-turns and underscan that put the upright
        composition onto the tag's surface, from the device's Panel settings.

        A composition whose aspect disagrees with the surface is turned onto
        it (three clockwise quarter-turns, ``pi_png``'s choice), and a
        flipped mount adds a half turn. The surface comes from HA
        (``tag_w`` / ``tag_h``, see ``apply_tag_facts``); landscape is
        assumed until it is known."""
        panel = device_panel(device)
        if panel is None:
            return 0, 0
        try:
            tag_w, tag_h = int(cfg.get("tag_w") or 0), int(cfg.get("tag_h") or 0)
        except (TypeError, ValueError):
            tag_w = tag_h = 0
        surface_tall = tag_h > tag_w if tag_w > 0 and tag_h > 0 else False
        quarters = 3 if (panel.w < panel.h) != surface_tall else 0
        if panel.flip:
            quarters = (quarters + 2) % 4
        return quarters, max(0, int(panel.underscan))

    @staticmethod
    def _conversion_gamut(device: Device, cfg: dict[str, Any]) -> str | None:
        """The palette to reduce the frame to here, or None when HA converts
        it (the default) or the panel has no ink palette."""
        if str(cfg.get("colour_conversion") or "ha").strip().lower() != "tesserae":
            return None
        panel = device_panel(device)
        gamut = str(getattr(panel, "gamut", "") or "")
        return gamut if gamut and gamut not in _UNQUANTISED_GAMUTS else None

    @staticmethod
    def _refresh_mode(cfg: dict[str, Any]) -> str:
        return "fast" if str(cfg.get("refresh_mode") or "").strip().lower() == "fast" else "full"

    @staticmethod
    def _parse_rotate(raw: Any) -> int:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return 0
        return val if val in (0, 90, 180, 270) else 0

    # -- media file -------------------------------------------------------

    def _stage_frame(self, device_id: str, frame: bytes) -> tuple[str | None, str]:
        """Get the frame into HA's media folder. Returns ``(media id, "")``,
        or ``(None, why)`` when it could not be delivered.

        A shared folder is written directly; without one, or when that write
        fails, the frame is uploaded through HA's API. A failure warns once,
        actionably, instead of a traceback per render; the flag clears on
        success so a later fix re-arms it."""
        write_error: OSError | None = None
        if self._shared_folder:
            try:
                media_id = self._write_media(device_id, frame)
            except OSError as exc:
                # Most likely the add-on's media folder isn't writable: it
                # needs the media:rw mapping and a writable /media (the
                # tesserae subdir is chowned to the app user at container
                # start). The upload below does not depend on either.
                write_error = exc
            else:
                self._media_warned = False
                return media_id, ""
        try:
            media_id = self._upload_media(device_id, frame)
        except Exception as exc:
            if not self._media_warned:
                self._media_warned = True
                self._warn_undelivered(device_id, write_error, exc)
            return None, self._staging_problem(write_error, exc)
        self._media_warned = False
        return media_id, ""

    @staticmethod
    def _staging_problem(write_error: OSError | None, upload_error: Exception) -> str:
        if isinstance(upload_error, urllib.error.HTTPError) and upload_error.code in (401, 403):
            return (
                "Home Assistant refused the picture upload. The Home Assistant Core "
                "plugin's token must belong to an administrator."
            )
        if write_error is not None:
            return f"Could not write the picture to Home Assistant's media folder ({write_error})."
        return f"Could not upload the picture to Home Assistant ({upload_error})."

    def _warn_undelivered(
        self, device_id: str, write_error: OSError | None, upload_error: Exception
    ) -> None:
        hint = ""
        if isinstance(upload_error, urllib.error.HTTPError) and upload_error.code in (401, 403):
            hint = (
                " Home Assistant only accepts media uploads from an administrator: "
                "use a long-lived token from an admin account in Settings -> Plugins "
                "-> Home Assistant Core."
            )
        if write_error is not None:
            logger.warning(
                "opendisplay_ha %s: can't write frame to %s (%s), and uploading it to "
                "Home Assistant failed too (%s).%s In the HA add-on the folder needs the "
                "media:rw mapping and a writable /media. Frames won't reach the tag "
                "until one of the two works.",
                device_id,
                self._media_dir,
                write_error,
                upload_error,
                hint,
            )
            return
        logger.warning(
            "opendisplay_ha %s: can't upload frame to Home Assistant (%s).%s Frames "
            "won't reach the tag until this is fixed.",
            device_id,
            upload_error,
            hint,
        )

    def _upload_media(self, device_id: str, frame: bytes) -> str:
        """Send the frame to HA's media folder through its upload API and
        return the media-source id HA stored it under."""
        mod = _ha_core_module(self._app)
        if mod is None or not hasattr(mod, "upload_media"):
            raise RuntimeError("ha_core plugin unavailable")
        with self._app.app_context():
            media_id = mod.upload_media(
                f"{MEDIA_SOURCE_LOCAL}/{MEDIA_SUBDIR}",
                f"{device_id}.png",
                frame,
                content_type="image/png",
                timeout=MEDIA_UPLOAD_TIMEOUT_S,
            )
        return str(media_id)

    def _write_media(self, device_id: str, frame: bytes) -> str:
        """Atomically write the frame to the device's stable media file and
        return its media-source id."""
        self._media_dir.mkdir(parents=True, exist_ok=True)
        dst = self._media_dir / f"{device_id}.png"
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(frame)
        tmp.replace(dst)
        return f"{MEDIA_SOURCE_LOCAL}/{MEDIA_SUBDIR}/{device_id}.png"

    def _call_upload(
        self,
        ha_device_id: str,
        media_id: str,
        rotate: int,
        *,
        dithered: bool = False,
        refresh_mode: str = "full",
    ) -> str:
        """Ask HA to send the staged frame to the tag. Returns "" on success,
        else the reason, worded for the device page."""
        mod = _ha_core_module(self._app)
        if mod is None or not hasattr(mod, "call_service"):
            logger.warning("opendisplay_ha: ha_core plugin unavailable; can't push")
            return "The Home Assistant Core plugin is not available."
        data: dict[str, Any] = {
            "device_id": ha_device_id,
            "image": {"media_content_id": media_id, "media_content_type": "image/png"},
        }
        if rotate:
            data["rotation"] = rotate
        if dithered:
            # Already in the tag's own inks: HA must map, not dither again.
            data["dither_mode"] = "none"
            # And must not tone-map either. Left out, HA asks the SDK for
            # "auto" tone compression, which rewrites every pixel before the
            # match. A pure ink colour is then no longer exactly itself, so
            # it is matched by nearness to the panel's measured inks instead
            # of being passed through: on a Spectra 6, whose green ink is
            # dark, pure green lands on yellow. Zero turns the step off and
            # exact ink colours go through untouched.
            data["tone_compression"] = 0
        if refresh_mode != "full":
            data["refresh_mode"] = refresh_mode
        try:
            with self._app.app_context():
                mod.call_service("opendisplay", "upload_image", data=data, timeout=UPLOAD_TIMEOUT_S)
            return ""
        except Exception as exc:
            # HA puts the integration's actual error in the response body.
            # The bare status ("HTTP Error 500") hides why the upload failed.
            detail = ""
            if hasattr(mod, "error_detail"):
                with contextlib.suppress(Exception):
                    detail = str(mod.error_detail(exc) or "")
            logger.warning(
                "opendisplay_ha: upload_image failed (%s)%s",
                exc,
                f": {detail}" if detail else "",
            )
            if detail:
                return detail
            if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
                return "Home Assistant did not finish sending the picture to the tag in time."
            return f"Home Assistant could not send the picture to the tag ({exc})."

    # -- housekeeping -----------------------------------------------------

    def cleanup_device(self, device_id: str) -> None:
        """Drop a device's media file + delivery state (call on delete).

        A shared folder's file is unlinked. An uploaded frame is taken back
        out of HA over its WebSocket API, off-thread and best effort: the
        device is gone either way, and one small PNG left behind is not
        worth failing a delete over."""
        with self._lock:
            had_frame = self._last_sent.pop(device_id, None) is not None
            self._status.pop(device_id, None)
        self._save_state()
        if not self._shared_folder:
            if had_frame:
                self._remove_uploaded(device_id)
            return
        with contextlib.suppress(OSError):
            (self._media_dir / f"{device_id}.png").unlink(missing_ok=True)

    def _remove_uploaded(self, device_id: str) -> None:
        mod = _ha_core_module(self._app)
        if mod is None or not hasattr(mod, "remove_media"):
            return
        media_id = f"{MEDIA_SOURCE_LOCAL}/{MEDIA_SUBDIR}/{device_id}.png"

        def work() -> None:
            try:
                with self._app.app_context():
                    mod.remove_media(media_id)
            except Exception as exc:
                logger.info(
                    "opendisplay_ha %s: left its picture in Home Assistant's media "
                    "folder (%s); remove tesserae/%s.png there if you want it gone",
                    device_id,
                    exc,
                    device_id,
                )

        if self._run_async:
            threading.Thread(target=work, name="opendisplay-ha-cleanup", daemon=True).start()
        else:
            work()

    def prune_orphans(self) -> int:
        """Remove media files for device ids that are no longer registered
        opendisplay_ha instances. Returns the count removed. Only a shared
        folder is swept; without one the directory is not Tesserae's."""
        if not self._shared_folder or not self._media_dir.exists():
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
        background_loops.track(self)

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
        self.refresh_tag_facts(mod)
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

    def refresh_tag_facts(self, mod: Any) -> int:
        """Re-read each tag's surface and palette from HA and store what
        changed. Returns how many devices were updated. Rides the telemetry
        cadence: a tag's config only changes when someone reconfigures it."""
        updated = 0
        try:
            known = opendisplay_tags.detect(mod, fresh=True)
        except Exception:
            logger.debug("opendisplay_ha: tag detection failed", exc_info=True)
            return 0
        for device in self._ha_devices():
            ha_device_id = str(self._device_cfg(device.id).get("ha_device_id") or "").strip()
            facts = known.get(ha_device_id)
            if not facts:
                continue
            try:
                if apply_tag_facts(self._app, device, facts):
                    updated += 1
            except Exception:
                logger.exception("opendisplay_ha: storing tag facts failed for %s", device.id)
        return updated

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


def apply_tag_facts(app: Flask, device: Device, facts: dict[str, Any]) -> bool:
    """Store what HA reported about a tag on its Tesserae device. True when
    anything changed.

    The surface (``tag_w`` / ``tag_h``) is always kept current: it is what
    the publisher turns frames onto, and not something the operator sets.
    The palette follows the tag only when the detection itself changes
    (first sight, or the tag was reconfigured), so a palette the operator
    picked afterwards is left alone. The render size is never touched here:
    it is the operator's, and the add form is where a detected size is
    offered."""
    if not facts:
        return False
    settings = app.config["SETTINGS_STORE"]
    section = settings.get_section("devices") or {}
    current = section.get(device.id) if isinstance(section, dict) else None
    entry = dict(current) if isinstance(current, dict) else {}
    changed = False
    try:
        w, h = int(facts.get("w") or 0), int(facts.get("h") or 0)
    except (TypeError, ValueError):
        w = h = 0
    if w > 0 and h > 0 and (entry.get("tag_w"), entry.get("tag_h")) != (w, h):
        entry["tag_w"], entry["tag_h"] = w, h
        changed = True
    gamut = facts.get("gamut")
    if isinstance(gamut, str) and gamut and entry.get("tag_gamut") != gamut:
        entry["tag_gamut"] = gamut
        changed = True
        _set_panel_gamut(app, device, gamut)
    if changed:
        settings.patch_section("devices", {device.id: entry})
    return changed


def _set_panel_gamut(app: Flask, device: Device, gamut: str) -> None:
    from app import device_service
    from app.quantizer import canonicalise_gamut

    panel = device.panel or {}
    if not panel or canonicalise_gamut(str(panel.get("gamut") or "")) == gamut:
        return
    result = device_service.update_instance_panel(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id=device.id,
        w=int(panel["w"]),
        h=int(panel["h"]),
        orientation=str(panel.get("orientation") or "landscape"),
        gamut=gamut,
    )
    if result.ok:
        logger.info("opendisplay_ha %s: palette set to %s, as the tag reports", device.id, gamut)
    else:
        logger.warning("opendisplay_ha %s: could not set palette: %s", device.id, result.error)


SENT_STATE_FILE = "opendisplay_ha_sent.json"


def _sent_state_path(app: Flask) -> Path | None:
    data_root = app.config.get("DATA_ROOT")
    return Path(data_root) / SENT_STATE_FILE if data_root else None


def register(app: Flask) -> None:
    """Build the publisher and wire it to the push loop. No-op if there's no
    push manager (bare test setups)."""
    push_mgr = app.config.get("PUSH_MANAGER")
    devices = app.config.get("DEVICE_REGISTRY")
    settings = app.config.get("SETTINGS_STORE")
    renders_dir = app.config.get("RENDERS_DIR")
    if push_mgr is None or devices is None or settings is None or renders_dir is None:
        return
    # register() re-runs on every transport rebuild. The publisher it
    # replaces may still hold a retry timer; left running it would send a
    # second copy of a frame from a stale view of what was delivered.
    old_pub = app.config.get("OPENDISPLAY_HA_PUBLISHER")
    if old_pub is not None:
        with contextlib.suppress(Exception):
            old_pub.stop()
    pub = OpenDisplayHaPublisher(
        app=app,
        devices=devices,
        settings=settings,
        renders_dir=renders_dir,
        latest_render_fn=push_mgr.latest_render_for,
        state_path=_sent_state_path(app),
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
        # Threadless under pytest, see transport_wiring._under_pytest.
        run_async=not (
            app.testing or app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST")
        ),
    )
    poller.start()
    app.config["OPENDISPLAY_HA_TELEMETRY_POLLER"] = poller
