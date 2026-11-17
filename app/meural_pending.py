"""Short-lived, encrypted Meural accounts bound to an administrator's browser session."""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from flask import current_app, session

from app.meural_auth import AuthResult
from app.meural_cloud import frame_inventory

PENDING_TTL_S = 15 * 60


def pending_lock() -> Any:
    return current_app.config.setdefault("MEURAL_PENDING_LOCK", threading.RLock())


def pending_key(*, create: bool = False) -> str:
    key = session.get("meural_add_session", "")
    if not key and create:
        key = secrets.token_urlsafe(32)
        session["meural_add_session"] = key
    return str(key)


def discard_pending() -> None:
    with pending_lock():
        key = pending_key()
        current_app.config.setdefault("MEURAL_PENDING_ADDS", {}).pop(key, None)
        current_app.config.get("MEURAL_LOGIN_SESSIONS", {}).pop("add:" + key, None)
        session.pop("meural_add_session", None)


def pending_account() -> dict[str, Any] | None:
    with pending_lock():
        entries: dict[str, dict[str, Any]] = current_app.config.setdefault(
            "MEURAL_PENDING_ADDS", {}
        )
        for key, entry in list(entries.items()):
            if entry["expires_at"] <= time.monotonic():
                entries.pop(key, None)
        return entries.get(pending_key())


def seal_tokens(result: AuthResult) -> dict[str, Any]:
    box = current_app.config["SECRET_BOX"]
    return {
        "access": box.wrap(result.access_token),
        "refresh": box.wrap(result.refresh_token),
        "token_expires_at": result.expires_at,
        "trust_id": result.trust_id,
    }


def stage_account(
    result: AuthResult, account_id: str, email: str, frames: list[dict[str, Any]]
) -> None:
    entry = {
        **seal_tokens(result),
        "expires_at": time.monotonic() + PENDING_TTL_S,
        "account_id": account_id,
        "email": email,
        "frames": frame_inventory(frames),
        "selected": "",
    }
    with pending_lock():
        pending_account()  # Sweep expired accounts before keeping another.
        current_app.config["MEURAL_PENDING_ADDS"][pending_key(create=True)] = entry


def pending_tokens(entry: dict[str, Any]) -> AuthResult:
    box = current_app.config["SECRET_BOX"]
    return AuthResult(
        box.unwrap(entry["access"]),
        box.unwrap(entry["refresh"]),
        entry["token_expires_at"],
        entry["trust_id"],
    )


def account_state(
    config: dict[str, Any], *, saved: bool, signing_in: bool = False
) -> dict[str, Any]:
    frames = [
        {"id": str(frame["id"]), "name": str(frame["name"])}
        for frame in config.get("meural_devices", [])
    ]
    chosen = next(
        (frame for frame in frames if frame["id"] == str(config.get("cloud_device_id") or "")), None
    )
    state = "signed_out"
    if signing_in:
        state = "signing_in"
    elif config.get("meural_auth_failed"):
        state = "session_expired"
    elif config.get("meural_refresh_token_secret"):
        state = "signed_in"
    return {
        "state": state,
        "account_label": str(config.get("meural_email") or ""),
        "frames": frames,
        "chosen_frame": chosen,
        "saved": bool(saved and chosen),
        "paused": bool(config.get("meural_management_paused")),
    }


def pending_state() -> dict[str, Any]:
    entry = pending_account()
    if entry is not None:
        return account_state(
            {
                "meural_devices": entry["frames"],
                "cloud_device_id": entry["selected"],
                "meural_email": entry["email"],
                "meural_refresh_token_secret": True,
            },
            saved=False,
        )
    login = current_app.config.get("MEURAL_LOGIN_SESSIONS", {}).get("add:" + pending_key())
    return account_state(
        {}, saved=False, signing_in=bool(login and login["expires_at"] > time.monotonic())
    )
