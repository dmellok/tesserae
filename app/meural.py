"""Stock Meural LAN control and renewable postcard delivery.

The postcard is a temporary preview, not a library upload. Keep renewing it
while Tesserae is running; when renewal stops the frame returns to its artwork.
The protocol is documented by GuySie/ha-meural (MIT). No cloud login is used.
"""

from __future__ import annotations

import contextlib
import io
import ipaddress
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask
from PIL import Image

from app import background_loops
from app.device_loader import Device, DeviceRegistry
from app.meural_http import open_request, read_response
from app.panel import device_panel
from app.quantizer import apply_underscan, rotate_png

logger = logging.getLogger(__name__)
KIND = "meural"
POLL_INTERVAL_S = 5
DEFAULT_REFRESH_S = 15
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def valid_host(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        address = ipaddress.ip_address(value)
        return isinstance(address, ipaddress.IPv4Address) and not (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
        )
    except ValueError:
        return bool(
            _HOSTNAME.fullmatch(value)
            and value.lower() != "localhost"
            and not value.lower().endswith(".localhost")
            and not re.fullmatch(r"[0-9.]+", value)
        )


class MeuralError(Exception):
    """A failed LAN request or a reply the frame rejected."""


class MeuralClient:
    def __init__(self, host: str) -> None:
        if not valid_host(host):
            raise MeuralError("Frame address must be an IPv4 address or host name")
        self._base = f"http://{host}/remote/"

    def _request(self, path: str, *, body: bytes | None = None, content_type: str = "") -> Any:
        headers = {"Connection": "close"}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self._base + path, data=body, headers=headers)
        try:
            with open_request(request, timeout=12) as response:
                payload = json.loads(read_response(response))
        except urllib.error.HTTPError as exc:
            exc.close()
            raise MeuralError(f"Frame request failed (HTTP {exc.code})") from None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            # The system reply contains Wi-Fi credentials; never log a raw reply.
            raise MeuralError(f"Frame request failed ({type(exc).__name__})") from None
        if not isinstance(payload, dict) or payload.get("status") != "pass":
            raise MeuralError("Frame rejected the request")
        return payload.get("response")

    def upload(self, jpeg: bytes) -> None:
        boundary = uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
                'filename="tesserae.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
            ).encode()
            + jpeg
            + f"\r\n--{boundary}--\r\n".encode()
        )
        self._request(
            "postcard/", body=body, content_type=f"multipart/form-data; boundary={boundary}"
        )

    def is_sleeping(self) -> bool:
        value = self._request("control_check/sleep/")
        if not isinstance(value, bool):
            raise MeuralError("Frame returned an invalid sleep status")
        return value

    def set_sleep(self, sleeping: bool) -> None:
        self._request("control_command/" + ("suspend" if sleeping else "resume"))

    def set_orientation(self, orientation: str) -> None:
        if orientation not in ("portrait", "landscape"):
            raise MeuralError("Unknown frame orientation")
        self._request(f"control_command/set_orientation/{orientation}")

    def status(self) -> dict[str, Any]:
        raw = self._request("control_check/system/")
        if not isinstance(raw, dict):
            raise MeuralError("Frame returned an invalid system status")
        out: dict[str, Any] = {}
        for source, target in (("version", "fw_version"), ("orientation", "orientation")):
            if isinstance(raw.get(source), str):
                out[target] = raw[source][:40]
        wifi = raw.get("wifi_status")
        values = {"rssi": wifi.get("signal") if isinstance(wifi, dict) else None}
        values.update({key: raw.get(key) for key in ("lux", "backlight", "free_space")})
        for key, value in values.items():
            if value is None:
                continue
            with contextlib.suppress(ValueError, TypeError, OverflowError):
                out[key] = int(value)
        return out


class MeuralPublisher:
    """One worker owns uploads, renewals and quiet-hours transitions."""

    def __init__(
        self,
        *,
        app: Flask,
        devices: DeviceRegistry,
        settings: Any,
        renders_dir: Path,
        latest_render_fn: Callable[[str], dict[str, Any] | None],
        client_factory: Callable[[str], Any] = MeuralClient,
        clock: Callable[[], float] = time.monotonic,
        quiet_fn: Callable[[Device], bool] | None = None,
        run_async: bool = True,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._renders_dir = Path(renders_dir)
        self._latest = latest_render_fn
        self._client = client_factory
        self._clock = clock
        self._quiet = quiet_fn or self._quiet_sleeps
        self._run_async = run_async
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = app.config.setdefault("MEURAL_IO_LOCK", threading.Lock())
        self._thread: threading.Thread | None = None
        self._sent: dict[str, tuple[str, float]] = {}
        self._images: dict[str, tuple[str, bytes]] = {}
        self._retry_at: dict[str, float] = {}
        self._telemetry: dict[str, tuple[float, dict[str, Any]]] = {}

    def start(self) -> None:
        if not self._run_async or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="meural-poll", daemon=True)
        self._thread.start()
        background_loops.track(self)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def on_push(self, _result: Any = None) -> None:
        if self._run_async:
            self._wake.set()
        else:
            self.poll_once()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self.poll_once()
            self._wake.wait(POLL_INTERVAL_S)

    def _quiet_sleeps(self, device: Device) -> bool:
        from app.quiet_hours import is_in_window, resolve_quiet_hours
        from app.tz_resolve import app_timezone

        window = resolve_quiet_hours(self._settings.get_section("app") or {}, device)
        if window is None or not window.sleep_through:
            return False
        with self._app.app_context():
            return is_in_window(window, datetime.now(UTC), app_timezone())

    @staticmethod
    def _sleep_path(device: Device) -> Path:
        return Path(device.data_dir) / "meural_sleep.json"

    def _owns_sleep(self, device: Device, host: str) -> bool:
        try:
            return bool(json.loads(self._sleep_path(device).read_text()) == {"host": host})
        except (OSError, ValueError):
            return False

    def _save_sleep(self, device: Device, host: str | None) -> None:
        path = self._sleep_path(device)
        if host is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"host": host}))
            temporary.replace(path)

    def poll_once(self) -> None:
        with self._lock:
            frames = [d for d in self._devices.devices.values() if d.kind_of == KIND]
            live = {d.id for d in frames}
            for cache in (self._sent, self._images, self._retry_at, self._telemetry):
                for key in set(cache) - live:
                    cache.pop(key, None)
            for device in frames:
                if self._stop.is_set():
                    return
                if self._clock() < self._retry_at.get(device.id, 0):
                    continue
                try:
                    self._visit(device)
                    self._retry_at.pop(device.id, None)
                except Exception as exc:
                    self._retry_at[device.id] = self._clock() + POLL_INTERVAL_S
                    logger.warning("meural %s: %s", device.id, exc)
                    events = self._app.config.get("EVENT_LOG")
                    if events is not None:
                        try:
                            events.record(
                                type="device",
                                source=device.id,
                                target=f"meural://{device.id}",
                                status="error",
                                error=str(exc),
                            )
                        except Exception:
                            logger.exception("meural %s: recording failure failed", device.id)

    def _visit(self, device: Device) -> None:
        cfg = (self._settings.get_section("devices") or {}).get(device.id) or {}
        host = str(cfg.get("host") or "").strip()
        if not host:
            return
        client = self._client(host)
        sleeping = client.is_sleeping()
        if self._stop.is_set() or self._devices.devices.get(device.id) is not device:
            return
        owned = self._owns_sleep(device, host)
        quiet = self._quiet(device)
        if quiet and not sleeping:
            client.set_sleep(True)
            self._save_sleep(device, host)
            sleeping, owned = True, True
        elif not quiet and owned:
            if sleeping:
                client.set_sleep(False)
            self._save_sleep(device, None)
            sleeping = False
            self._sent.pop(device.id, None)

        now = self._clock()
        sampled, telemetry = self._telemetry.get(device.id, (-60.0, {}))
        if now - sampled >= 60:
            with contextlib.suppress(MeuralError):
                telemetry = client.status()
                self._telemetry[device.id] = (now, telemetry)
        self._record(
            device,
            {
                **telemetry,
                "screen": "off" if sleeping else "on",
                "screen_why": "quiet hours"
                if quiet and owned
                else ("sleeping" if sleeping else ""),
            },
        )
        if sleeping or quiet or self._stop.is_set():
            return
        latest = self._latest(device.id) or {}
        digest = str(latest.get("composition_digest") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", digest):
            return
        panel = device_panel(device)
        orientation = "portrait" if panel and panel.h > panel.w else "landscape"
        flip = bool(panel and panel.flip)
        underscan = panel.underscan if panel else 0
        key = f"{host}:{digest}:{orientation}:{flip}:{underscan}"
        sent_key, sent_at = self._sent.get(device.id, ("", 0.0))
        interval = int(cfg.get("refresh_interval_s") or DEFAULT_REFRESH_S)
        if key == sent_key and now - sent_at < interval:
            return
        image_key = f"{digest}:{flip}:{underscan}"
        cached_key, jpeg = self._images.get(device.id, ("", b""))
        if cached_key != image_key:
            path = self._renders_dir / f"{digest}.png"
            if not path.is_file():
                return
            png = path.read_bytes()
            if flip:
                png = rotate_png(png, quarters=2)
            if underscan:
                png = apply_underscan(png, underscan=underscan)
            with Image.open(io.BytesIO(png)) as source:
                image = source.convert("RGB")
                image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, "JPEG", quality=90)
                jpeg = output.getvalue()
            self._images[device.id] = (image_key, jpeg)
        if key != sent_key:
            client.set_orientation(orientation)
        if self._stop.is_set() or self._devices.devices.get(device.id) is not device:
            return
        client.upload(jpeg)
        self._sent[device.id] = (key, self._clock())
        if key != sent_key:
            logger.info("meural %s: sent frame %s over LAN", device.id, digest)

    def _record(self, device: Device, heartbeat: dict[str, Any]) -> None:
        from app.transport_wiring import record_status_heartbeat

        if (
            self._app.config.get("DEVICE_STATUS") is None
            or self._app.config.get("EVENT_LOG") is None
        ):
            return
        try:
            with self._app.app_context():
                record_status_heartbeat(
                    app=self._app,
                    device=device,
                    payload=json.dumps(heartbeat).encode(),
                    status_cache=self._app.config["DEVICE_STATUS"],
                    event_log=self._app.config["EVENT_LOG"],
                    event_target=f"meural://{device.id}",
                )
        except Exception:
            logger.exception("meural %s: recording status failed", device.id)


def register(app: Flask) -> None:
    push = app.config.get("PUSH_MANAGER")
    if push is None:
        return
    old = app.config.get("MEURAL_PUBLISHER")
    if old is not None:
        old.stop()
        push.remove_listener(old.on_push)
    pub = MeuralPublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=push.latest_render_for,
        run_async=not (app.testing or os.environ.get("PYTEST_CURRENT_TEST")),
    )
    push.add_listener(pub.on_push)
    app.config["MEURAL_PUBLISHER"] = pub
    pub.start()
