"""Persistent Meural artwork delivery and encrypted account sessions."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from flask import Flask

from app.device_loader import Device
from app.meural import valid_host
from app.meural_auth import (
    AuthenticationBlocked,
    AuthResult,
    CannotConnect,
    HttpError,
    InvalidAuth,
    MeuralAuthError,
    NetgearAuthenticator,
    request_json,
)
from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)

API = "https://api.meural.com/v1/"
SESSION_FIELDS: list[dict[str, Any]] = [
    {"name": "meural_access_token", "secret": True},
    {"name": "meural_refresh_token", "secret": True},
] + [
    {"name": name}
    for name in (
        "meural_auth_failed",
        "meural_auth_retry_at",
        "meural_auth_failures",
        "meural_auth_blocked",
        "meural_expires_at",
        "meural_trust_id",
        "meural_email",
        "meural_account_id",
        "meural_devices",
    )
]


def resource_id(value: Any) -> str:
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", text):
        raise ValueError("Meural returned an invalid resource id")
    return text


def ensure_frame_available(settings: SettingsStore, device_id: str, target: str) -> None:
    if not target:
        return
    for other_id, config in (settings.get_section("devices") or {}).items():
        if (
            other_id != device_id
            and isinstance(config, dict)
            and str(config.get("cloud_device_id") or "") == target
        ):
            raise ValueError("This Meural frame is already selected by another Tesserae display.")


def frame_inventory(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inventory = []
    for device in devices:
        frame = {
            "id": resource_id(device.get("id")),
            "name": str(device.get("alias") or device.get("name") or device["id"])[:100],
        }
        host = device.get("localIp", device.get("host"))
        frame["host"] = str(host) if valid_host(host) else ""
        inventory.append(frame)
    return inventory


def save_session(
    settings: SettingsStore,
    device_id: str,
    result: AuthResult,
    *,
    email: str,
    account_id: str,
    devices: list[dict[str, Any]],
    selected_id: str | None = None,
) -> None:
    current = (settings.get_section("devices") or {}).get(device_id) or {}
    account_id = resource_id(account_id)
    inventory = frame_inventory(devices)
    chosen = next(
        (
            d
            for d in inventory
            if d["id"]
            == str(selected_id if selected_id is not None else current.get("cloud_device_id"))
        ),
        None,
    )
    if chosen is None and len(inventory) == 1:
        chosen = inventory[0]
    ensure_frame_available(settings, device_id, chosen["id"] if chosen else "")
    selection: dict[str, Any] = {
        "cloud_device_id": chosen["id"] if chosen else "",
        "meural_management_paused": False,
    }
    if chosen and (
        not current.get("host")
        or current.get("cloud_device_id") != chosen["id"]
        or current.get("meural_account_id") != account_id
    ):
        selection["host"] = chosen["host"]
    settings.update_for_namespace(
        "devices",
        device_id,
        {
            "meural_auth_failed": False,
            "meural_auth_retry_at": 0,
            "meural_auth_failures": 0,
            "meural_auth_blocked": False,
            "meural_access_token": result.access_token,
            "meural_refresh_token": result.refresh_token,
            "meural_expires_at": result.expires_at,
            "meural_trust_id": result.trust_id,
            "meural_email": email,
            "meural_account_id": account_id,
            "meural_devices": inventory,
            **selection,
        },
        [
            *SESSION_FIELDS,
            {"name": "cloud_device_id"},
            {"name": "host"},
            {"name": "meural_management_paused"},
        ],
    )


def clear_session(settings: SettingsStore, device_id: str) -> None:
    keys = ["meural_sleep_schedule"] + [
        f["name"] + ("_secret" if f.get("secret") else "") for f in SESSION_FIELDS
    ]
    settings.update_item("devices", device_id, {}, remove=keys)


def save_tokens(settings: SettingsStore, device_id: str, result: AuthResult) -> None:
    settings.update_for_namespace(
        "devices",
        device_id,
        {
            "meural_auth_failed": False,
            "meural_auth_retry_at": 0,
            "meural_auth_failures": 0,
            "meural_auth_blocked": False,
            "meural_access_token": result.access_token,
            "meural_refresh_token": result.refresh_token,
            "meural_expires_at": result.expires_at,
            "meural_trust_id": result.trust_id,
        },
        SESSION_FIELDS,
    )


class MeuralCloudClient:
    def __init__(self, settings: SettingsStore, device_id: str) -> None:
        self._settings = settings
        self._device_id = device_id
        self.lock = threading.RLock()

    def config(self) -> dict[str, Any]:
        return (self._settings.get_section("devices") or {}).get(self._device_id) or {}

    @property
    def account_id(self) -> str:
        return str(self.config().get("meural_account_id") or "")

    def reset_auth(self) -> None:
        self._settings.update_item(
            "devices",
            self._device_id,
            {
                "meural_auth_failed": False,
                "meural_auth_retry_at": 0,
                "meural_auth_failures": 0,
                "meural_auth_blocked": False,
            },
        )

    def _token(self, *, rejected: str | None = None) -> str:
        with self.lock:
            cfg = self.config()
            if cfg.get("meural_auth_failed"):
                raise InvalidAuth("Session expired, sign in again")
            if time.time() < float(cfg.get("meural_auth_retry_at") or 0):
                error = AuthenticationBlocked if cfg.get("meural_auth_blocked") else CannotConnect
                raise error(
                    "Meural requests are paused after a connection failure. Try again later."
                )
            token = str(cfg.get("meural_access_token_secret") or "")
            refresh = str(cfg.get("meural_refresh_token_secret") or "")
            expires = float(cfg.get("meural_expires_at") or 0)
            if token and token != rejected and expires > time.time() + 60:
                return token
            if not refresh:
                raise InvalidAuth("Sign in to Meural in this display's settings")
            try:
                result = NetgearAuthenticator(cfg.get("meural_trust_id")).refresh(refresh)
            except InvalidAuth:
                self._settings.update_item("devices", self._device_id, {"meural_auth_failed": True})
                raise
            except (CannotConnect, AuthenticationBlocked) as exc:
                self._backoff(blocked=isinstance(exc, AuthenticationBlocked))
                raise
            save_tokens(self._settings, self._device_id, result)
            self.reset_auth()
            return result.access_token

    def _backoff(self, *, blocked: bool = False) -> None:
        with self.lock:
            attempts = min(int(self.config().get("meural_auth_failures") or 0) + 1, 5)
            delay = min((1800 if blocked else 300) * 2 ** (attempts - 1), 3600)
            self._settings.update_item(
                "devices",
                self._device_id,
                {
                    "meural_auth_failures": attempts,
                    "meural_auth_retry_at": time.time() + delay,
                    "meural_auth_blocked": blocked,
                },
            )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        token = self._token()
        for attempt in range(2):
            headers = {
                "Authorization": f"Token {token}",
                "Accept": "application/json",
                "x-meural-api-version": "4",
                "x-meural-source-platform": "web",
                **kwargs.get("headers", {}),
            }
            try:
                result = request_json(method, API + path, **{**kwargs, "headers": headers})
                if self.config().get("meural_auth_failures"):
                    self._settings.update_item(
                        "devices",
                        self._device_id,
                        {
                            "meural_auth_retry_at": 0,
                            "meural_auth_failures": 0,
                            "meural_auth_blocked": False,
                        },
                    )
                return result
            except (CannotConnect, AuthenticationBlocked) as exc:
                self._backoff(blocked=isinstance(exc, AuthenticationBlocked))
                raise
            except HttpError as exc:
                if exc.status in (403, 429) or exc.status >= 500:
                    self._backoff(blocked=exc.status == 403)
                    raise CannotConnect(
                        "Meural is temporarily unavailable. Try again later."
                    ) from None
                if exc.status != 401:
                    raise
                if attempt:
                    self._settings.update_item(
                        "devices", self._device_id, {"meural_auth_failed": True}
                    )
                    raise InvalidAuth(
                        "Meural rejected the renewed session. Sign in again."
                    ) from exc
                token = self._token(rejected=token)
        raise InvalidAuth("Sign in to Meural again")

    @staticmethod
    def _data(payload: Any) -> Any:
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    def walk(self, path: str) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            payload = self.request("GET", f"{path}?count=100&page={page}")
            rows = self._data(payload)
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError("Meural returned an invalid list")
            yield from rows
            if not isinstance(payload, dict) or payload.get("isLast", True):
                return
            page += 1

    def upload(self, jpeg: bytes, name: str) -> str:
        boundary = uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
            f'filename="{name}"\r\nContent-Type: image/jpeg\r\n\r\n'
        ).encode()
        body += jpeg + f"\r\n--{boundary}--\r\n".encode()
        data = self._data(
            self.request(
                "POST",
                "items",
                body=body,
                timeout=90,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        )
        return resource_id(data.get("id"))

    def find_upload(self, name: str) -> str | None:
        for item in self.walk("user/items"):
            if item.get("name") in (name, name.removesuffix(".jpg")):
                return resource_id(item.get("id"))
        return None

    def _delete(self, path: str) -> None:
        try:
            self.request("DELETE", path)
        except HttpError as exc:
            if exc.status != 404:
                raise

    def delete_item(self, item: str) -> None:
        self._delete(f"items/{resource_id(item)}")

    def show_item(self, target: str, item: str) -> None:
        target = resource_id(target)
        self.request("POST", f"devices/{target}/items/{resource_id(item)}", timeout=60)
        self.request("POST", f"devices/{target}/sync", timeout=60)

    def get_playback(self, target: str) -> dict[str, Any]:
        data = self._data(self.request("GET", f"devices/{resource_id(target)}"))
        if not isinstance(data, dict) or not isinstance(data.get("frameStatus"), dict):
            raise ValueError("Meural did not return the frame's current playback")
        # Defensive defaults for empty frames; not yet checked on real hardware.
        # Unknown rotation flags are re-enabled on release instead of blocking setup.
        flags = {
            k: True if data.get(k) is None else data[k]
            for k in ("galleryRotation", "schedulerEnabled")
        }
        if not all(isinstance(value, bool) for value in flags.values()):
            raise ValueError("Meural did not return the frame's rotation settings")
        return {
            "gallery_id": resource_id(data["frameStatus"]["currentGallery"])
            if data["frameStatus"].get("currentGallery") is not None
            else None,
            "item_id": resource_id(data["frameStatus"]["currentItem"])
            if data["frameStatus"].get("currentItem") is not None
            else None,
            "settings": flags,
            "scheduler_owned": False,
        }

    def pause_rotation(self, target: str) -> None:
        self.request(
            "PUT",
            f"devices/{resource_id(target)}",
            json_data={"galleryRotation": False},
            timeout=60,
        )

    def restore_playback(self, target: str, original: dict[str, Any]) -> None:
        target = resource_id(target)
        # Older journals paused the scheduler too. New takeovers leave it alone,
        # so release must preserve any sleep changes made in the Meural app.
        keys = ["galleryRotation"]
        if original.get("scheduler_owned", True):
            keys.append("schedulerEnabled")
        flags = {key: original["settings"][key] for key in keys}
        if not all(isinstance(value, bool) for value in flags.values()):
            raise ValueError("Invalid saved Meural rotation settings")
        if original.get("gallery_id") is not None:
            try:
                self.request(
                    "POST",
                    f"devices/{target}/galleries/{resource_id(original['gallery_id'])}",
                    timeout=60,
                )
            except HttpError as exc:
                if exc.status != 404:
                    raise
        try:
            self.request("PUT", f"devices/{target}", json_data=flags, timeout=60)
            self.request("POST", f"devices/{target}/sync", timeout=60)
        except HttpError as exc:
            if exc.status != 404:
                raise


def get_cloud_client(app: Flask, device_id: str) -> MeuralCloudClient:
    clients: dict[str, MeuralCloudClient] = app.config.setdefault("MEURAL_CLOUD_CLIENTS", {})
    if device_id not in clients:
        clients[device_id] = MeuralCloudClient(app.config["SETTINGS_STORE"], device_id)
    return clients[device_id]


class CloudDelivery:
    """Show one image at a time; preserve native playback for explicit release."""

    def __init__(
        self,
        path: Path,
        client: Any,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        self.path, self.client, self.cancelled = path, client, cancelled

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(self.path)

    def _state(self, target: str) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {
                "scope": f"{self.client.account_id}:{target}",
                "owner": uuid.uuid4().hex[:12],
                "owned": [],
            }
        except (OSError, ValueError) as exc:
            raise ValueError("Could not read the saved Meural playback state") from exc
        scope = f"{self.client.account_id}:{target}"
        if not isinstance(state, dict) or state.get("scope") != scope:
            raise ValueError("The saved Meural playback state belongs to a different frame")
        return state

    def held(self, target: str) -> bool:
        try:
            return bool(self._state(resource_id(target)).get("original"))
        except ValueError:
            return True

    def release(self, target: str, *, abandon: bool = False) -> bool:
        target = resource_id(target)
        state = self._state(target)
        held = bool(state.get("original"))
        if held:
            try:
                self.client.restore_playback(target, state["original"])
            except (MeuralAuthError, HttpError, OSError, ValueError):
                if not abandon:
                    raise
            state.pop("original")
        state.update(
            paused=False,
            last_key=None,
            current_item=None,
            current_key=None,
            pending_item=None,
            pending_key=None,
            upload_started=False,
        )
        self._save(state)
        # A cleanup outage must not prevent release after playback was restored.
        for item in list(state.get("owned", [])):
            try:
                self.client.delete_item(item)
            except (MeuralAuthError, HttpError, OSError, ValueError):
                logger.warning("Meural upload cleanup failed; an image may remain in the library")
                continue
            state["owned"].remove(item)
            self._save(state)
        return held

    def deliver(self, jpeg: bytes, key: str, target: str) -> bool:
        target = resource_id(target)
        state = self._state(target)
        if self.cancelled():
            return False
        if not state.get("original"):
            state["original"] = self.client.get_playback(target)
            self._save(state)
        if not state.get("paused"):
            self.client.pause_rotation(target)
            state["paused"] = True
            self._save(state)
        if self.cancelled():
            return False
        if state.get("last_key") == key:
            self._cleanup(state)
            return False
        if state.get("pending_key") != key:
            cached = state.get("current_item") if state.get("current_key") == key else None
            state.update(pending_key=key, pending_item=cached, upload_started=False)
            self._save(state)
        item = state.get("pending_item")
        if not item:
            filename = f"tesserae-{state['owner']}-{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}.jpg"
            if state.get("upload_started"):
                item = self.client.find_upload(filename)
            if not item:
                state["upload_started"] = True
                self._save(state)
                item = self.client.upload(jpeg, filename)
            state["pending_item"] = item
            if item not in state["owned"]:
                state["owned"].append(item)
            self._save(state)
        if self.cancelled():
            return False
        self.client.show_item(target, item)
        state.update(
            last_key=key,
            current_key=key,
            current_item=item,
            pending_key=None,
            pending_item=None,
            upload_started=False,
        )
        self._save(state)
        self._cleanup(state)
        return True

    def _cleanup(self, state: dict[str, Any]) -> None:
        for old in list(state["owned"][:-2]):
            if old in {
                state.get("current_item"),
                state.get("pending_item"),
                (state.get("original") or {}).get("item_id"),
            }:
                continue
            if self.cancelled():
                return
            self.client.delete_item(old)
            state["owned"].remove(old)
            self._save(state)


def delivery_for_device(app: Flask, device: Device) -> tuple[CloudDelivery, str] | None:
    """Resolve the current account/frame journal without making network requests."""
    cfg = (app.config["SETTINGS_STORE"].get_section("devices") or {}).get(device.id) or {}
    account, target = cfg.get("meural_account_id"), cfg.get("cloud_device_id")
    if not account or not target:
        return None
    path = Path(device.data_dir) / f"meural_cloud_{resource_id(account)}_{resource_id(target)}.json"
    return CloudDelivery(path, get_cloud_client(app, device.id)), str(target)
