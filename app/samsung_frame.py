"""Push Tesserae frames to Samsung The Frame TVs through their Art Mode.

For each ``samsung_frame`` device, when its rendered frame changes this
converts the composition PNG to a 4K JPEG, uploads it to the TV's Art app
(``app.samsung_frame_art``), makes it the current artwork and deletes the
device's older uploads beyond its "Frames kept on the TV" setting. Only
uploads this module made are ever deleted; the owner's own Art Mode photos
stay. Art Mode itself keeps doing the display (switching on with the TV
"off", the brightness sensor, the mat).

A poller checks each TV's REST info endpoint once a minute. It feeds the
device card's status and, when a TV comes back (switched on, plugged back
in) with a frame it missed, sends that frame. Failed sends back off so a TV
whose Art app is not answering isn't hammered.

Per-device state (the uploaded content ids and the last frame sent) lives in
``<device data dir>/samsung_frame.json``; the TV's pairing token is stored
encrypted in the device's settings.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Flask
from PIL import Image, ImageOps

from app import background_loops
from app.device_loader import Device, DeviceRegistry
from app.samsung_frame_art import FrameArtClient, FrameArtError, FramePairingError

logger = logging.getLogger(__name__)

KIND = "samsung_frame"
CLIENT_NAME = "Tesserae"
STATE_FILE = "samsung_frame.json"
WIDTH, HEIGHT = 3840, 2160
JPEG_QUALITY = 85
DEFAULT_KEEP = 2
POLL_INTERVAL_S = 60
REST_TIMEOUT_S = 4.0
# Back-off after a failed send: 2, 4, 8 ... minutes, capped at 30.
BACKOFF_START_S = 120
BACKOFF_MAX_S = 1800

ClientFactory = Callable[..., FrameArtClient]


def is_samsung_frame_device(device: Any) -> bool:
    return getattr(device, "kind_of", None) == KIND or getattr(device, "id", None) == KIND


def matte_id(cfg: dict[str, Any]) -> str:
    style = str(cfg.get("matte_style") or "none")
    if style == "none":
        return "none"
    return f"{style}_{cfg.get('matte_colour') or 'polar'}"


def to_jpeg(png: Path) -> bytes:
    """The composition as a baseline 4:2:0 JPEG at exactly 3840x2160: the
    smallest file the TV's decoder is sure to take, and what Art Mode shows
    without rescaling. A panel set to another size is cropped to fill."""
    with Image.open(png) as src:
        img = src.convert("RGB")
    if img.size != (WIDTH, HEIGHT):
        img = ImageOps.fit(img, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=False, subsampling=2)
    return out.getvalue()


def rest_info(host: str, timeout: float = REST_TIMEOUT_S) -> dict[str, Any] | None:
    """The TV's ``/api/v2/`` device info, or None if it doesn't answer."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{host}:8001/api/v2/", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    device = body.get("device") if isinstance(body, dict) else None
    return device if isinstance(device, dict) else None


class SamsungFramePublisher:
    def __init__(
        self,
        *,
        app: Flask,
        devices: DeviceRegistry,
        settings: Any,
        renders_dir: Path,
        latest_render_fn: Callable[[str], dict[str, Any] | None],
        run_async: bool = True,
        client_factory: ClientFactory = FrameArtClient,
        rest_info_fn: Callable[[str], dict[str, Any] | None] = rest_info,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._renders_dir = Path(renders_dir)
        self._latest = latest_render_fn
        self._run_async = run_async
        self._client_factory = client_factory
        self._rest_info = rest_info_fn
        self._lock = threading.Lock()
        # Serialises every read-modify-write of a device's state file: the push
        # worker, the status poller and device removal all touch it.
        self._state_lock = threading.RLock()
        self._dirty = False
        self._worker_running = False
        # device id -> (monotonic time of next allowed attempt, current back-off)
        self._backoff: dict[str, tuple[float, float]] = {}
        self._warned: dict[str, str] = {}

    # -- push reaction ----------------------------------------------------

    def on_push(self, _result: Any = None) -> None:
        """PushManager listener (runs in the push thread). An upload takes a
        few seconds, so the work goes to one serial background worker; a
        push arriving meanwhile re-arms it so the newest frame is sent."""
        if not self._frames():
            return
        if not self._run_async:
            self._process_once()
            return
        with self._lock:
            self._dirty = True
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._worker, name="samsung-frame", daemon=True).start()

    def _worker(self) -> None:
        try:
            while True:
                with self._lock:
                    if not self._dirty:
                        return
                    self._dirty = False
                self._process_once()
        finally:
            with self._lock:
                self._worker_running = False

    def _process_once(self) -> None:
        for device in self._frames():
            try:
                self._maybe_send(device)
            except Exception:
                # Anything unexpected (an unreadable composition, a full disk)
                # backs off like a TV error, so the poller doesn't retry it
                # every minute.
                self._failed(device.id)
                logger.exception("samsung_frame %s: send failed", device.id)

    def _frames(self) -> list[Device]:
        return [d for d in self._devices.devices.values() if d.kind_of == KIND]

    def _cfg(self, device_id: str) -> dict[str, Any]:
        section = self._settings.get_section("devices") or {}
        cfg = section.get(device_id) if isinstance(section, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    # -- state ------------------------------------------------------------

    @staticmethod
    def _state_path(device: Device) -> Path:
        return Path(device.data_dir) / STATE_FILE

    def load_state(self, device: Device) -> dict[str, Any]:
        with self._state_lock:
            return self._read_state(device)

    def _read_state(self, device: Device) -> dict[str, Any]:
        try:
            state = json.loads(self._state_path(device).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        state.setdefault("uploads", [])
        state.setdefault("pending_delete", [])
        return state

    def _save_state(self, device: Device, state: dict[str, Any]) -> None:
        path = self._state_path(device)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(state, indent=2) + "\n")
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def update_state(self, device: Device, changes: dict[str, Any]) -> dict[str, Any]:
        """Merge ``changes`` into the state file under the lock (a None value
        removes the key) and return the result."""
        with self._state_lock:
            state = self._read_state(device)
            for key, value in changes.items():
                if value is None:
                    state.pop(key, None)
                else:
                    state[key] = value
            self._save_state(device, state)
            return state

    def pending_digest(self, device: Device) -> str | None:
        """The frame the TV should show but hasn't been sent, if any."""
        latest = self._latest(device.id)
        digest = str((latest or {}).get("composition_digest") or "")
        if not digest or self.load_state(device).get("last_digest") == digest:
            return None
        return digest

    # -- sending ----------------------------------------------------------

    def _warn_once(self, device_id: str, message: str) -> None:
        if self._warned.get(device_id) != message:
            self._warned[device_id] = message
            logger.warning("samsung_frame %s: %s", device_id, message)

    def _backing_off(self, device_id: str) -> bool:
        until, _ = self._backoff.get(device_id, (0.0, 0.0))
        return time.monotonic() < until

    def _failed(self, device_id: str) -> None:
        _, delay = self._backoff.get(device_id, (0.0, 0.0))
        delay = min(BACKOFF_MAX_S, delay * 2 if delay else BACKOFF_START_S)
        self._backoff[device_id] = (time.monotonic() + delay, delay)

    def _maybe_send(self, device: Device) -> None:
        digest = self.pending_digest(device)
        if digest is None or self._backing_off(device.id):
            return
        cfg = self._cfg(device.id)
        host = str(cfg.get("host") or "").strip()
        if not host:
            self._warn_once(device.id, "no TV address set (Settings -> Devices); skipping")
            return
        src = self._renders_dir / f"{digest}.png"
        if not src.exists():
            logger.warning("samsung_frame %s: composition %s missing on disk", device.id, digest)
            return
        jpeg = to_jpeg(src)
        state = self.load_state(device)
        try:
            self._send(device, cfg, host, digest, jpeg, state)
        except FramePairingError as exc:
            self._failed(device.id)
            self._warn_once(device.id, f"{exc}. Tesserae will try again in a few minutes.")
            return
        except (FrameArtError, OSError) as exc:
            self._failed(device.id)
            self._warn_once(device.id, f"couldn't send the frame to {host}: {exc}")
            return
        finally:
            # Only the keys a send owns, merged into the file as it is now, so
            # nothing written meanwhile (by the poller) is lost.
            self.update_state(
                device,
                {k: state[k] for k in ("uploads", "pending_delete", "last_digest") if k in state},
            )
        self._backoff.pop(device.id, None)
        self._warned.pop(device.id, None)
        logger.info(
            "samsung_frame %s: showing frame %s (%d KB)", device.id, digest, len(jpeg) // 1024
        )
        self._record(device, {"power": "on", "last_upload": int(time.time())})

    def _send(
        self,
        device: Device,
        cfg: dict[str, Any],
        host: str,
        digest: str,
        jpeg: bytes,
        state: dict[str, Any],
    ) -> None:
        token = self._token(cfg)
        client = self._client_factory(host, token=token, name=CLIENT_NAME)
        try:
            client.connect()
            if client.token and client.token != token:
                self._store_token(device.id, client.token)
            if cfg.get("take_over_slideshow", "off") != "keep":
                try:
                    client.slideshow_off()
                except FrameArtError as exc:
                    logger.info("samsung_frame %s: %s", device.id, exc)
            # Ids already on the TV, so an upload confirmation without an id
            # (2022+ Frames) can't be mistaken for another app's photo.
            existing = {str(item.get("content_id")) for item in client.list_content()}
            content_id = client.upload_jpeg(
                jpeg, matte=matte_id(cfg), portrait_matte=matte_id(cfg), existing=existing
            )
            state["uploads"].append(
                {"content_id": content_id, "digest": digest, "at": int(time.time())}
            )
            try:
                show = client.art_mode_on()
            except FrameArtError:
                show = False
            # show=False keeps a programme the owner is watching on screen;
            # Art Mode picks the new artwork up when it is next on.
            client.select(content_id, show=show)
            state["last_digest"] = digest
            keep = self._keep(cfg)
            old = [u["content_id"] for u in state["uploads"][:-keep]]
            state["uploads"] = state["uploads"][-keep:]
            doomed = list(dict.fromkeys(state["pending_delete"] + old))
            if doomed:
                try:
                    client.delete(doomed)
                    state["pending_delete"] = []
                except FrameArtError as exc:
                    state["pending_delete"] = doomed
                    logger.info(
                        "samsung_frame %s: will retry deleting old frames: %s", device.id, exc
                    )
        finally:
            client.close()

    @staticmethod
    def _token(cfg: dict[str, Any]) -> str | None:
        # Stored encrypted under the secret-field convention (token_secret).
        return str(cfg.get("token_secret") or cfg.get("token") or "") or None

    @staticmethod
    def _keep(cfg: dict[str, Any]) -> int:
        try:
            keep = int(cfg.get("keep_on_tv") or DEFAULT_KEEP)
        except (TypeError, ValueError):
            keep = DEFAULT_KEEP
        return max(1, min(10, keep))

    def _store_token(self, device_id: str, token: str) -> None:
        try:
            self._settings.update_for_namespace(
                "devices", device_id, {"token": token}, [{"name": "token", "secret": True}]
            )
        except Exception:
            logger.exception("samsung_frame %s: couldn't store the TV's pairing token", device_id)

    # -- status -----------------------------------------------------------

    def poll_once(self) -> None:
        """Record each TV's power state; send a missed frame to a TV that is
        reachable again."""
        resend = False
        for device in self._frames():
            host = str(self._cfg(device.id).get("host") or "").strip()
            if not host:
                continue
            info = self._rest_info(host)
            if info is None:
                continue
            self._record(
                device,
                {
                    "power": str(info.get("PowerState") or "on"),
                    "model": str(info.get("modelName") or ""),
                },
            )
            if self.pending_digest(device) and not self._backing_off(device.id):
                resend = True
        if resend:
            self.on_push()

    def _record(self, device: Device, heartbeat: dict[str, Any]) -> None:
        events = self._app.config.get("EVENT_LOG")
        status_cache = self._app.config.get("DEVICE_STATUS")
        if events is None or status_cache is None:
            return
        from app.transport_wiring import record_status_heartbeat

        try:
            with self._app.app_context():
                record_status_heartbeat(
                    app=self._app,
                    device=device,
                    payload=json.dumps(heartbeat).encode("utf-8"),
                    status_cache=status_cache,
                    event_log=events,
                    event_target=f"samsung-frame://{device.id}",
                )
        except Exception:
            logger.exception("samsung_frame %s: recording status failed", device.id)

    # -- housekeeping -----------------------------------------------------

    def remove_uploads(self, device: Device) -> None:
        """Best effort: delete a removed device's uploads from its TV."""
        cfg = self._cfg(device.id)
        host = str(cfg.get("host") or "").strip()
        state = self.load_state(device)
        ids = [u["content_id"] for u in state["uploads"]] + state["pending_delete"]
        if host and ids:
            client = self._client_factory(host, token=self._token(cfg), name=CLIENT_NAME)
            try:
                client.connect(pairing_timeout=10)
                client.delete(ids)
            except (FrameArtError, OSError) as exc:
                logger.info(
                    "samsung_frame %s: couldn't remove its frames from the TV: %s", device.id, exc
                )
            finally:
                client.close()
        with self._state_lock, contextlib.suppress(OSError):
            self._state_path(device).unlink(missing_ok=True)


class SamsungFramePoller:
    def __init__(
        self, publisher: SamsungFramePublisher, *, interval_s: int = POLL_INTERVAL_S
    ) -> None:
        self._publisher = publisher
        self._interval_s = max(15, int(interval_s))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="samsung-frame-poll", daemon=True)
        self._thread.start()
        background_loops.track(self)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(min(20, self._interval_s)):
            return
        while True:
            try:
                self._publisher.poll_once()
            except Exception:
                logger.exception("samsung_frame: poll failed")
            if self._stop.wait(self._interval_s):
                return


def _register_hooks(app: Flask) -> None:
    from app import device_service

    def removed(device: Any) -> None:
        pub = app.config.get("SAMSUNG_FRAME_PUBLISHER")
        if pub is None:
            return
        threading.Thread(
            target=pub.remove_uploads, args=(device,), name="samsung-frame-remove", daemon=True
        ).start()

    device_service.register_kind_hooks(
        KIND, device_service.KindHooks(applies_to=is_samsung_frame_device, device_removed=removed)
    )


def register(app: Flask) -> None:
    """Build the publisher, wire it to the push loop and start the status
    poller. Runs again on every transport rebuild, so the old poller is
    stopped first."""
    push_mgr = app.config.get("PUSH_MANAGER")
    devices = app.config.get("DEVICE_REGISTRY")
    settings = app.config.get("SETTINGS_STORE")
    renders_dir = app.config.get("RENDERS_DIR")
    if push_mgr is None or devices is None or settings is None or renders_dir is None:
        return
    import os

    under_test = bool(
        app.testing or app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST")
    )
    pub = SamsungFramePublisher(
        app=app,
        devices=devices,
        settings=settings,
        renders_dir=renders_dir,
        latest_render_fn=push_mgr.latest_render_for,
        run_async=not under_test,
    )
    push_mgr.add_listener(pub.on_push)
    app.config["SAMSUNG_FRAME_PUBLISHER"] = pub
    _register_hooks(app)

    old = app.config.get("SAMSUNG_FRAME_POLLER")
    if old is not None:
        with contextlib.suppress(Exception):
            old.stop()
    poller = SamsungFramePoller(pub)
    if not under_test:
        poller.start()
    app.config["SAMSUNG_FRAME_POLLER"] = poller
