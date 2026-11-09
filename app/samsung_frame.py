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

With a Home Assistant power switch set (a smart plug, say), Tesserae also
switches the TV off for quiet hours that sleep through, once it is showing
Art Mode or not answering (a programme being watched is left alone), and back
on when they end, when Tesserae switched it off itself. A frame pushed while
the switch is off (outside quiet hours) switches it on, waits for the TV to
start and puts it in Art Mode before sending. A TV switched off by hand stays
off until something is pushed. The device card's Power tile says what the
screen is doing: Art Mode, On, Starting or Off.

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
from app.samsung_frame_art import (
    FrameArtClient,
    FrameArtError,
    FramePairingError,
    hold_power_key,
    wake_on_lan,
)

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
# A TV switched on at the plug takes about 45 s to answer; allow a slow boot.
BOOT_TIMEOUT_S = 180
BOOT_POLL_S = 5
# Wake-on-LAN is repeated while waiting; a plug-powered TV can boot to standby.
WOL_REPEAT_S = 15
POWER_MODES = ("tv", "switch", "none")

ClientFactory = Callable[..., FrameArtClient]


class TvInUse(Exception):
    """The TV is showing something other than Art Mode: someone is using it,
    so the frame waits."""


class PowerSwitch:
    """A Home Assistant switch (``switch.*``, ``light.*`` or ``input_boolean.*``)
    that powers the TV, through the ha_core plugin."""

    def __init__(self, app: Flask) -> None:
        self._app = app

    def _module(self) -> Any:
        registry = self._app.config.get("PLUGIN_REGISTRY")
        plugin = registry.get("ha_core") if registry is not None else None
        return getattr(plugin, "server_module", None) if plugin is not None else None

    def available(self) -> bool:
        """Whether Tesserae has a working Home Assistant connection."""
        mod = self._module()
        if mod is None or not hasattr(mod, "is_configured"):
            return False
        try:
            with self._app.app_context():
                return bool(mod.is_configured())
        except Exception:
            return False

    def state(self, entity_id: str) -> str | None:
        """ "on", "off", or None when Home Assistant can't say."""
        mod = self._module()
        if mod is None or not hasattr(mod, "get_state"):
            return None
        try:
            with self._app.app_context():
                value = str((mod.get_state(entity_id) or {}).get("state") or "")
        except Exception:
            return None
        return value if value in ("on", "off") else None

    def turn(self, entity_id: str, on: bool) -> bool:
        mod = self._module()
        if mod is None or not hasattr(mod, "call_service"):
            logger.warning("samsung_frame: the Home Assistant Core plugin isn't available")
            return False
        try:
            with self._app.app_context():
                mod.call_service(
                    "homeassistant", "turn_on" if on else "turn_off", data={"entity_id": entity_id}
                )
            return True
        except Exception as exc:
            logger.warning(
                "samsung_frame: switching %s %s failed: %s", entity_id, "on" if on else "off", exc
            )
            return False


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


# Shared by every publisher instance: a transport rebuild makes a new one while
# the old one may still be mid-send, and both must serialise on the same state
# file and see the same back-off.
_STATE_LOCK = threading.RLock()
_BACKOFF: dict[str, tuple[float, float]] = {}


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
        power: PowerSwitch | None = None,
        quiet_fn: Callable[[Device], bool] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        power_key_fn: Callable[..., None] = hold_power_key,
        wol_fn: Callable[[str, str | None], None] = wake_on_lan,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._renders_dir = Path(renders_dir)
        self._latest = latest_render_fn
        self._run_async = run_async
        self._client_factory = client_factory
        self._rest_info = rest_info_fn
        self._power = power if power is not None else PowerSwitch(app)
        self._quiet_fn = quiet_fn if quiet_fn is not None else self._quiet_sleeps
        self._sleep = sleep_fn
        self._power_key = power_key_fn
        self._wol = wol_fn
        self._lock = threading.Lock()
        # Serialises every read-modify-write of a device's state file: the push
        # worker, the status poller and device removal all touch it.
        self._state_lock = _STATE_LOCK
        self._dirty = False
        self._worker_running = False
        # device id -> (monotonic time of next allowed attempt, current back-off)
        self._backoff = _BACKOFF
        self._warned: dict[str, str] = {}
        # Polls in a row each TV hasn't answered.
        self._missed: dict[str, int] = {}
        # The last screen state recorded for a TV that didn't answer, so a TV
        # that stays unreachable isn't recorded (and seen) every minute.
        self._last_silent: dict[str, tuple[str, str]] = {}

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

    # -- power ------------------------------------------------------------

    @staticmethod
    def _switch(cfg: dict[str, Any]) -> str:
        return str(cfg.get("power_switch") or "").strip()

    def _mode(self, cfg: dict[str, Any]) -> str:
        """How Tesserae powers the TV: "tv" (its own Power key and
        Wake-on-LAN, the default), "switch" (a Home Assistant switch) or
        "none" (left as it is)."""
        mode = str(cfg.get("power_control") or "").strip()
        if mode == "switch" and not (self._switch(cfg) and self._power.available()):
            return "tv"
        if mode in POWER_MODES:
            return mode
        return "switch" if self._switch(cfg) and self._power.available() else "tv"

    @staticmethod
    def _in_use_limit_s(cfg: dict[str, Any]) -> float | None:
        """Seconds a TV may stay in use into quiet hours before it is put to
        sleep anyway; None leaves it on."""
        raw = str(cfg.get("sleep_when_in_use") or "60")
        if raw == "never":
            return None
        try:
            return max(0, int(raw)) * 60.0
        except ValueError:
            return 3600.0

    @staticmethod
    def _standby(info: dict[str, Any] | None) -> bool:
        return info is not None and str(info.get("PowerState") or "") == "standby"

    def _quiet_sleeps(self, device: Device) -> bool:
        """Whether the device is inside quiet hours that sleep through."""
        from datetime import UTC, datetime

        from app.quiet_hours import is_in_window, resolve_quiet_hours

        try:
            window = resolve_quiet_hours(self._settings.get_section("app") or {}, device)
        except Exception:
            return False
        if window is None or not window.sleep_through:
            return False
        from app.tz_resolve import app_timezone

        with self._app.app_context():
            tz = app_timezone()  # "system" means the host's own zone
        return is_in_window(window, datetime.now(UTC), tz)

    def _put_to_sleep(self, device: Device, cfg: dict[str, Any], mode: str, host: str) -> bool:
        if mode == "switch":
            return self._power.turn(self._switch(cfg), False)
        # Not paired yet: the remote channel would ask for Allow on screen,
        # and with no known MAC Wake-on-LAN couldn't bring it back.
        if not self._token(cfg):
            return False
        if not self.load_state(device).get("mac"):
            self._warn_once(
                device.id,
                "not putting the TV in standby: its MAC for Wake-on-LAN isn't known "
                "(a wired TV doesn't report it; use a Home Assistant switch)",
            )
            return False
        try:
            self._power_key(host, token=self._token(cfg), name=CLIENT_NAME)
            return True
        except (FrameArtError, OSError) as exc:
            logger.info("samsung_frame %s: couldn't put the TV in standby: %s", device.id, exc)
            return False

    def _wake_and_wait(self, device: Device, cfg: dict[str, Any], mode: str, host: str) -> bool:
        """Wake the TV (switch on, Wake-on-LAN, or both) and wait, up to
        BOOT_TIMEOUT_S, for it to answer as on."""
        switch = self._switch(cfg)
        if mode == "switch" and self._power.state(switch) != "on":
            if not self._power.turn(switch, True):
                return False
            logger.info("samsung_frame %s: switched %s on", device.id, switch)
        mac = str(self.load_state(device).get("mac") or "")
        if mode == "tv" and not mac:
            self._warn_once(
                device.id, "can't wake the TV: its network address isn't known until it is seen on"
            )
            return False
        # A push or the morning woke it: it was not left off for quiet hours any more.
        self.update_state(device, {"starting_at": int(time.time()), "slept_by_tesserae": None})
        self._record(device, {"screen": "starting", "screen_why": "waking up"})
        start = time.monotonic()
        # A plug gets ~30 s to boot the TV before Wake-on-LAN helps it along.
        next_wol = start if mode == "tv" else start + 30
        while time.monotonic() - start < BOOT_TIMEOUT_S:
            info = self._rest_info(host)
            if info is not None and not self._standby(info):
                return True
            if mac and time.monotonic() >= next_wol:
                with contextlib.suppress(OSError, ValueError):
                    self._wol(mac, host)
                next_wol = time.monotonic() + WOL_REPEAT_S
            self._sleep(BOOT_POLL_S)
        return False

    def _tv_is_off(self, cfg: dict[str, Any], mode: str, host: str) -> bool:
        """Whether the TV is off, as opposed to one missed answer: the switch
        is off, the TV says standby, or it misses two answers in a row."""
        if mode == "switch" and self._power.state(self._switch(cfg)) == "off":
            return True
        info = self._rest_info(host)
        if info is not None:
            return self._standby(info)
        self._sleep(BOOT_POLL_S)
        info = self._rest_info(host)
        return info is None or self._standby(info)

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
            self._warn_once(device.id, f"composition {digest} missing on disk")
            return
        mode = self._mode(cfg)
        if mode != "none" and self._quiet_fn(device):
            return  # held: the TV is asleep for quiet hours; sent when they end
        woke = False
        # Only a TV that is really off is woken; a missed answer from a TV
        # that is on is not, so Art Mode is never forced over a programme.
        if mode != "none" and self._tv_is_off(cfg, mode, host):
            if not self._wake_and_wait(device, cfg, mode, host):
                self._failed(device.id)
                self._warn_once(device.id, "tried to wake the TV but it didn't answer")
                return
            woke = True
        jpeg = to_jpeg(src)
        state = self.load_state(device)
        try:
            show = self._send(device, cfg, host, digest, jpeg, state, woke=woke)
        except TvInUse:
            logger.info("samsung_frame %s: the TV is in use; frame %s waits", device.id, digest)
            self._record(device, {"screen": "on", "screen_why": "in use, new frame waiting"})
            return
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
        self._record(
            device,
            {"screen": "art" if show else "on", "screen_why": "", "last_upload": int(time.time())},
        )

    def _send(
        self,
        device: Device,
        cfg: dict[str, Any],
        host: str,
        digest: str,
        jpeg: bytes,
        state: dict[str, Any],
        *,
        woke: bool = False,
    ) -> bool:
        """Upload and select the frame; returns whether Art Mode shows it."""
        token = self._token(cfg)
        client = self._client_factory(host, token=token, name=CLIENT_NAME)
        try:
            # Just switched on, the Art app can take a little longer than the
            # TV's info endpoint to answer. A refused pairing is not retried.
            attempts = 3 if woke else 1
            for attempt in range(attempts):
                try:
                    client.connect()
                    self._keep_token(device.id, client, token)
                    break
                except FramePairingError:
                    raise
                except FrameArtError:
                    if attempt == attempts - 1:
                        raise
                    self._sleep(BOOT_POLL_S * 2)
            if woke:
                try:
                    if not client.art_mode_on():
                        client.set_art_mode(True)
                except FrameArtError as exc:
                    logger.info("samsung_frame %s: couldn't turn Art Mode on: %s", device.id, exc)
            else:
                # Someone watching TV (or using an app) isn't disturbed: the
                # frame waits until Art Mode is back or the TV is switched off.
                with contextlib.suppress(FrameArtError):
                    if not client.art_mode_on():
                        raise TvInUse
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
            return show
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

    def _keep_token(self, device_id: str, client: Any, token: str | None) -> None:
        """Store the token the TV handed over on connecting, straight away:
        on first pairing the owner presses Allow, and a send that then stops
        (the TV is in use) must not lose it and prompt again next time."""
        if client.token and client.token != token:
            self._store_token(device_id, client.token)

    def _store_token(self, device_id: str, token: str) -> None:
        try:
            self._settings.update_for_namespace(
                "devices", device_id, {"token": token}, [{"name": "token", "secret": True}]
            )
        except Exception:
            logger.exception("samsung_frame %s: couldn't store the TV's pairing token", device_id)

    # -- status -----------------------------------------------------------

    def poll_once(self) -> None:
        """Record what each TV's screen is doing; sleep and wake TVs that have
        a power switch for quiet hours; send a missed frame to a TV that is
        reachable again."""
        resend = False
        for device in self._frames():
            try:
                if self._poll_device(device):
                    resend = True
            except Exception:
                logger.exception("samsung_frame %s: poll failed", device.id)
        if resend:
            self.on_push()

    def _poll_device(self, device: Device) -> bool:
        cfg = self._cfg(device.id)
        host = str(cfg.get("host") or "").strip()
        if not host:
            return False
        mode = self._mode(cfg)
        switch = self._switch(cfg)
        switch_state = self._power.state(switch) if mode == "switch" else None
        info = self._rest_info(host)
        standby = self._standby(info)
        awake = info is not None and not standby
        self._missed[device.id] = 0 if info is not None else self._missed.get(device.id, 0) + 1
        state = self.load_state(device)
        if info is not None:
            # wifiMac is the Wi-Fi radio's; on a wired TV it isn't the NIC that
            # Wake-on-LAN must reach, so it is kept only for a wireless one.
            wireless = str(info.get("networkType") or "").lower() == "wireless"
            mac = str(info.get("wifiMac") or "") if wireless else ""
            if mac != str(state.get("mac") or ""):
                state = self.update_state(device, {"mac": mac or None})
        screen = self._screen(device, cfg, host) if awake else None
        off = switch_state == "off" or standby or self._missed[device.id] >= 2
        if mode != "none" and self._quiet_fn(device):
            # Asleep for quiet hours: at once in Art Mode. A TV in use (or
            # whose Art app doesn't answer, so maybe showing a programme) is
            # left on, unless it is still in use after the device's "Still in
            # use at quiet hours" time.
            in_use = not off and screen != "art"
            since = float(state.get("in_use_since") or 0) if in_use else 0.0
            if in_use and not since:
                since = time.time()
                self.update_state(device, {"in_use_since": int(since)})
            elif not in_use and state.get("in_use_since"):
                self.update_state(device, {"in_use_since": None})
            limit = self._in_use_limit_s(cfg)
            overdue = in_use and limit is not None and time.time() - since >= limit
            if (
                not off
                and (screen == "art" or overdue)
                and self._put_to_sleep(device, cfg, mode, host)
            ):
                self.update_state(device, {"slept_by_tesserae": True, "in_use_since": None})
                logger.info(
                    "samsung_frame %s: TV off for quiet hours%s",
                    device.id,
                    " (still in use)" if overdue else "",
                )
                off = True
            if in_use and not off:
                self._record(device, {"screen": "on", "screen_why": "in use in quiet hours"})
                return False
            if off:
                self._record(device, {"screen": "off", "screen_why": "quiet hours"})
                return False
        elif mode != "none" and off and state.get("slept_by_tesserae"):
            # Morning: back on only if Tesserae switched it off; the flag stays
            # until the TV really answers, so a failure retries next poll.
            logger.info("samsung_frame %s: quiet hours over, waking the TV", device.id)
            if self._wake_and_wait(device, cfg, mode, host):
                self.update_state(device, {"needs_art_mode": True})
                info, awake, off = self._rest_info(host), True, False
                switch_state = "on" if mode == "switch" else None
                screen = self._screen(device, cfg, host)
            else:
                self.update_state(device, {"slept_by_tesserae": True})
                self._record(device, {"screen": "off", "screen_why": "didn't wake"})
                return False
        elif awake and state.get("slept_by_tesserae"):
            # Switched on by someone else since: a later switch-off is theirs.
            self.update_state(device, {"slept_by_tesserae": None})
        if state.get("in_use_since") and not self._quiet_fn(device):
            self.update_state(device, {"in_use_since": None})
        if mode == "switch" and switch_state == "off":
            self._record(device, {"screen": "off", "screen_why": "power switch off"})
            return False
        if not awake:
            started = float(state.get("starting_at") or 0)
            if info is None and time.time() - started < BOOT_TIMEOUT_S:
                screen_now, why = "starting", "waking up"
            else:
                screen_now, why = "off", "standby" if standby else "not answering"
            if info is not None or self._last_silent.get(device.id) != (screen_now, why):
                self._record(device, {"screen": screen_now, "screen_why": why})
            if info is None:
                self._last_silent[device.id] = (screen_now, why)
            return False
        self._last_silent.pop(device.id, None)
        if self.load_state(device).get("needs_art_mode"):
            # Woken after quiet hours: a boot may land in TV mode, which would
            # read as someone watching from then on.
            if screen == "on" and self._set_art_mode(device, cfg, host):
                screen = "art"
            self.update_state(device, {"needs_art_mode": None})
        self._record(
            device,
            {
                "screen": screen or "on",
                "screen_why": "",
                "power": str((info or {}).get("PowerState") or "on"),
                "model": str((info or {}).get("modelName") or ""),
            },
        )
        # A frame waiting while the TV was in use goes once Art Mode is back.
        waiting = bool(self.pending_digest(device)) and not self._backing_off(device.id)
        return waiting and screen != "on"

    def _screen(self, device: Device, cfg: dict[str, Any], host: str) -> str | None:
        """ "art" or "on" for a TV that answers; None when the Art app doesn't
        or the TV isn't paired yet (no prompt is shown from the poller)."""
        token = self._token(cfg)
        if not token:
            return None
        client = self._client_factory(host, token=token, name=CLIENT_NAME)
        try:
            client.connect(pairing_timeout=10)
            self._keep_token(device.id, client, token)
            return "art" if client.art_mode_on() else "on"
        except (FrameArtError, OSError):
            return None
        finally:
            client.close()

    def _set_art_mode(self, device: Device, cfg: dict[str, Any], host: str) -> bool:
        token = self._token(cfg)
        client = self._client_factory(host, token=token, name=CLIENT_NAME)
        try:
            client.connect(pairing_timeout=10)
            self._keep_token(device.id, client, token)
            client.set_art_mode(True)
            return True
        except (FrameArtError, OSError) as exc:
            logger.info("samsung_frame %s: couldn't turn Art Mode on: %s", device.id, exc)
            return False
        finally:
            client.close()

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

    def remove_uploads(
        self,
        device: Device,
        cfg: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        """Best effort: delete a removed device's uploads from its TV. The
        removal hook passes ``cfg`` and ``state`` read before the device's
        settings are wiped."""
        cfg = self._cfg(device.id) if cfg is None else cfg
        host = str(cfg.get("host") or "").strip()
        state = self.load_state(device) if state is None else state
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
        # Read now: the caller wipes the device's settings right after.
        cfg, state = dict(pub._cfg(device.id)), pub.load_state(device)
        threading.Thread(
            target=pub.remove_uploads,
            args=(device, cfg, state),
            name="samsung-frame-remove",
            daemon=True,
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
