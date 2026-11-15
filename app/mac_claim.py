"""One-shot MAC claim for ``POST /api/v1/device/discover`` (#515).

Zero-touch pairing hands a device its token by matching the MAC it
announces against a registered instance. A MAC is not a secret: anyone on
the network can read it from an ARP table and announce it. So the match
works once. The first request the device makes *with* its token closes the
claim, and from then on a tokenless announce from that MAC gets no token.
It is recorded as a request to pair again, which the owner allows from the
device's page (after a re-flash or a factory reset, say).

Closing on the first authenticated request, not when the token is handed
out, means a lost discover response does not strand a device: it can ask
again until it has proved it holds the token.

The flag lives on the instance manifest (``mac_claim_closed``). A device
registered before this existed has no flag and closes on its next wake.
Pending requests are kept in memory only: a device that still wants to pair
asks again every half minute or so.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from flask import Flask

from app.device_loader import Device
from app.device_service import INSTANCE_FILE_LOCK

logger = logging.getLogger(__name__)

CLOSED_KEY = "mac_claim_closed"
_REQUESTS_KEY = "MAC_REPAIR_REQUESTS"
# A request older than this is dropped: the device gave up or was allowed.
REQUEST_TTL_S = 15 * 60


def is_closed(device: Device) -> bool:
    return bool(device.manifest.get(CLOSED_KEY))


def _write_flag(device: Device, closed: bool) -> bool:
    """Set or drop the flag on disk and in memory. False when the instance
    file could not be rewritten; the in-memory manifest is left as it was."""
    with INSTANCE_FILE_LOCK:
        try:
            raw = json.loads(device.path.read_text(encoding="utf-8"))
            if closed:
                raw[CLOSED_KEY] = True
            else:
                raw.pop(CLOSED_KEY, None)
            device.path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            logger.exception("mac claim: couldn't update %s", device.id)
            return False
        if closed:
            device.manifest[CLOSED_KEY] = True
        else:
            device.manifest.pop(CLOSED_KEY, None)
        return True


def close_if_open(device: Device) -> None:
    """The device just authenticated with its token, so it holds it. Never
    raises: a failed write must not fail the request that proved it."""
    if is_closed(device):
        return
    try:
        _write_flag(device, True)
    except Exception:
        logger.exception("mac claim: close failed for %s", device.id)


def reopen(app: Flask, device: Device) -> bool:
    """Let the next announce from this device's MAC collect its token."""
    ok = _write_flag(device, False)
    if ok:
        _requests(app).pop(device.id, None)
    return ok


def _requests(app: Flask) -> dict[str, dict[str, Any]]:
    found = app.config.get(_REQUESTS_KEY)
    if not isinstance(found, dict):
        found = {}
        app.config[_REQUESTS_KEY] = found
    return found


def note_request(app: Flask, device_id: str, ip: str | None) -> None:
    _requests(app)[device_id] = {"at": time.time(), "ip": ip or ""}


def pending_request(app: Flask, device_id: str) -> dict[str, Any] | None:
    """The live request to pair again for one device, or None."""
    entry = _requests(app).get(device_id)
    if entry is None:
        return None
    if time.time() - float(entry.get("at") or 0) > REQUEST_TTL_S:
        _requests(app).pop(device_id, None)
        return None
    return entry
