"""Atlas Cloud Nano Banana tasks, persisted before waiting for a paid picture.

Same shape as :mod:`app.kie_images`: a task id is saved before the wait, so a
picture that was paid for is collected on retry and never bought twice.
API shapes: https://www.atlascloud.ai/docs/models/image and
https://www.atlascloud.ai/docs/predictions. Prices come from
https://api.atlascloud.ai/api/v1/models (November 2026): Nano Banana 3.8
cents, Nano Banana Pro 14 cents.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app import kie_images
from app.kie_images import _forget, _locked, _write, clear_unfinished

__all__ = ["clear_unfinished"]

BASE = "https://api.atlascloud.ai/api/v1"
POLL_S = 4.0
# A submission whose outcome is unknown has no task id to collect, so after
# this long it is given up on and the picture may be asked for again.
UNKNOWN_MAX_AGE_S = kie_images.UNKNOWN_MAX_AGE_S
PENDING_MAX_AGE_S = kie_images.PENDING_MAX_AGE_S
logger = logging.getLogger(__name__)
PROVIDER = "atlascloud"


class AtlasError(Exception):
    """A provider error suitable for the picture's error card."""


class AtlasRefused(AtlasError):
    """Atlas Cloud definitely did not take the request: it answered with a
    refusal, or the request never left. Nothing was bought."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def configuration() -> tuple[str, str]:
    """The chosen image service and the Atlas Cloud key (settings, else the
    ``ATLASCLOUD_API_KEY`` environment variable)."""
    store = kie_images._BOUND_STORE
    with contextlib.suppress(Exception):
        from flask import current_app

        store = current_app.config.get("SETTINGS_STORE") or store
    vals: dict[str, Any] = {}
    if store is not None:
        # A key that can't be decrypted must not take the artwork pages down.
        try:
            vals = store.get_for_runtime("app", "images", kie_images.FIELDS)
        except Exception:
            logger.exception("Atlas Cloud: the image settings could not be read")
    service = str(vals.get("image_service") or "auto")
    key = str(vals.get("atlascloud_api_key") or os.environ.get("ATLASCLOUD_API_KEY") or "").strip()
    return service if service in kie_images.SERVICES else "auto", key


def _request(
    url: str,
    body: bytes | None = None,
    key: str = "",
    timeout: float = 60,
    content_type: str = "application/json",
) -> dict[str, Any]:
    # The gateway sits behind Cloudflare, which turns the default Python
    # user agent away, so one is always set.
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": content_type,
            "User-Agent": "tesserae/artwork",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            answer = json.load(resp)
    except urllib.error.HTTPError as err:
        with contextlib.suppress(Exception):
            err.close()
        if err.code in (401, 403):
            raise AtlasRefused("Atlas Cloud refused the key", err.code) from err
        # A 4xx is an answer: the request was turned down (429 included).
        # A 5xx or a 408 says nothing about whether it was acted on.
        if 400 <= err.code < 500 and err.code != 408:
            raise AtlasRefused(f"Atlas Cloud answered HTTP {err.code}", err.code) from err
        raise AtlasError(f"Atlas Cloud answered HTTP {err.code}") from err
    except urllib.error.URLError as err:
        # No address, or nothing listening: the request never left.
        if isinstance(err.reason, (socket.gaierror, ConnectionRefusedError)):
            raise AtlasRefused(
                f"Atlas Cloud could not be reached ({type(err.reason).__name__})"
            ) from err
        raise AtlasError(f"Atlas Cloud could not be reached ({type(err).__name__})") from err
    except (OSError, ValueError) as err:
        raise AtlasError(f"Atlas Cloud could not be reached ({type(err).__name__})") from err
    if not isinstance(answer, dict):
        raise AtlasError("Atlas Cloud sent an invalid answer")
    if answer.get("code", 200) != 200:
        raise AtlasRefused(
            "Atlas Cloud: "
            + str(answer.get("msg") or answer.get("message") or "request refused")[:200],
            int(answer["code"]) if isinstance(answer.get("code"), int) else 0,
        )
    return answer


def _json(
    url: str, body: dict[str, Any] | None = None, key: str = "", timeout: float = 60
) -> dict[str, Any]:
    return _request(url, json.dumps(body).encode() if body is not None else None, key, timeout)


def check_key(key: str) -> tuple[bool, str]:
    """Atlas Cloud has no free balance call, so this asks after a prediction
    that does not exist: a good key is answered 404, a bad one 401."""
    if not key.strip():
        return False, "No key to check."
    try:
        _json(f"{BASE}/model/prediction/tesserae-key-check", key=key, timeout=15)
    except AtlasRefused as err:
        if err.status == 404:
            return True, "Atlas Cloud accepted the key."
        return False, str(err)
    except AtlasError as err:
        return False, str(err)
    return True, "Atlas Cloud accepted the key."


def _upload(ref: str, key: str) -> str:
    """A reference as a URL Atlas Cloud can read: data URIs are uploaded."""
    if not ref.startswith("data:"):
        return ref
    head, _, payload = ref.partition(",")
    try:
        raw = base64.b64decode(payload, validate=True) if ";base64" in head else b""
    except (binascii.Error, ValueError) as err:
        raise AtlasError("the reference picture could not be read") from err
    if not raw:
        raise AtlasError("the reference picture could not be read")
    mime = head[5:].split(";")[0] or "image/png"
    ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="reference.{ext}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        + raw
        + f"\r\n--{boundary}--\r\n".encode()
    )
    data = (
        _request(
            f"{BASE}/model/uploadMedia",
            body,
            key,
            content_type=f"multipart/form-data; boundary={boundary}",
        ).get("data")
        or {}
    )
    url = data.get("download_url") or data.get("url")
    if not url:
        raise AtlasError("Atlas Cloud upload returned no URL")
    return str(url)


def _download(url: str) -> bytes:
    from app import net_guard

    try:
        raw, _ = net_guard.fetch_bytes(url, timeout=60, max_bytes=30 * 1024 * 1024)
    except Exception as err:
        raise AtlasError(
            f"Atlas Cloud picture could not be fetched ({type(err).__name__})"
        ) from err
    if not raw:
        raise AtlasError("Atlas Cloud sent an empty picture")
    return raw


def generate(
    key: str,
    prompt: str,
    *,
    refs: Sequence[str],
    aspect: str,
    pro: bool,
    folder: Path,
    slot: int,
    charge: Callable[[int, str], None],
    timeout: float,
    stop: threading.Event | None = None,
    refund: Callable[[int, str], None] | None = None,
) -> bytes:
    """Resume a saved task, or submit exactly once. A submission whose outcome
    is unknown is not repeated until it is :data:`UNKNOWN_MAX_AGE_S` old; one
    Atlas Cloud definitely refused is forgotten and its charge handed back
    through ``refund``. Keep completed bytes until the caller has saved its
    whole artwork job.
    """
    if not key:
        raise AtlasError("no Atlas Cloud key")
    folder.mkdir(parents=True, exist_ok=True)
    with _locked(folder):
        return _generate(
            key,
            prompt,
            refs=refs,
            aspect=aspect,
            pro=pro,
            folder=folder,
            slot=slot,
            charge=charge,
            timeout=timeout,
            stop=stop,
            refund=refund,
        )


def _saved(path: Path, picture: Path, identity: str) -> dict[str, Any] | None:
    """The saved task to carry on with, or None to start afresh. Raises
    when a recent submission's outcome is unknown."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        record = None
    if not isinstance(record, dict):
        # Unreadable state might describe a paid task, so it fails closed,
        # but not for ever.
        if age < UNKNOWN_MAX_AGE_S:
            raise AtlasError(
                "Atlas Cloud saved task could not be read; check the account before retrying"
            )
        _forget(path, picture)
        return None
    if record.get("identity") != identity:
        # The prompt or the model changed, so the saved task's picture is
        # not the one being asked for and nothing would ever collect it.
        _forget(path, picture)
        return None
    if picture.exists():
        return record
    if not record.get("task_id"):
        if age < UNKNOWN_MAX_AGE_S:
            raise AtlasError(
                "Atlas Cloud submission status is unknown; check the account before retrying"
            )
        _forget(path, picture)
        return None
    if age > PENDING_MAX_AGE_S:
        _forget(path, picture)
        return None
    return record


class _Failed(AtlasError):
    """Atlas Cloud finished the task without a picture."""


def _generate(
    key: str,
    prompt: str,
    *,
    refs: Sequence[str],
    aspect: str,
    pro: bool,
    folder: Path,
    slot: int,
    charge: Callable[[int, str], None],
    timeout: float,
    stop: threading.Event | None,
    refund: Callable[[int, str], None] | None = None,
) -> bytes:
    path, picture = folder / f"{slot}.json", folder / f"{slot}.png"
    identity = hashlib.sha256(json.dumps([prompt, aspect, pro, bool(refs)]).encode()).hexdigest()
    record = _saved(path, picture, identity) if path.exists() else None
    if record is not None:
        if picture.exists():
            return picture.read_bytes()
    else:
        if stop is not None and stop.is_set():
            raise AtlasError("stopped")
        cost, model = (
            (14, "nano_banana_pro_atlas")
            if pro
            else (4, "nano_banana_atlas" if refs else "nano_banana_atlas_t2i")
        )
        urls = [_upload(ref, key) for ref in refs]
        body: dict[str, Any] = {
            "model": ("google/nano-banana-pro" if pro else "google/nano-banana")
            + ("/edit" if urls else "/text-to-image"),
            "prompt": prompt,
            "aspect_ratio": aspect,
            "output_format": "png",
        }
        if urls:
            body["images"] = urls
        if pro:
            body["resolution"] = "1k"
        record = {"identity": identity, "provider": PROVIDER, "state": "submitting"}
        _write(path, json.dumps(record).encode())
        try:
            charge(cost, model)
        except Exception:
            path.unlink()  # No request was submitted when a ceiling refused it.
            raise
        try:
            data = _json(f"{BASE}/model/generateImage", body, key).get("data") or {}
            tid = data.get("id")
            if not isinstance(tid, str) or not tid:
                raise AtlasError(
                    "Atlas Cloud returned no task id; check the account before retrying"
                )
            record.update(task_id=tid, state="pending")
            _write(path, json.dumps(record).encode())
        except AtlasRefused as err:
            # Turned down, so nothing was bought: forget the attempt and
            # hand the charge back, and the next attempt submits again.
            _forget(path, picture)
            if refund is not None:
                with contextlib.suppress(Exception):
                    refund(cost, model)
            raise AtlasError(str(err)) from err
        except (AtlasError, OSError) as err:
            raise AtlasError(
                f"Atlas Cloud submission status is unknown ({err}); check the account before retrying"
            ) from err
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            raise AtlasError("stopped; Atlas Cloud paid task is saved for collection")
        try:
            data = (
                _json(
                    f"{BASE}/model/prediction/{urllib.parse.quote(str(record['task_id']), safe='')}",
                    key=key,
                    timeout=max(0.001, min(30, deadline - time.monotonic())),
                ).get("data")
                or {}
            )
            status = str(data.get("status") or "").lower()
            if status in ("failed", "canceled", "cancelled"):
                # Nothing to collect. The record goes, so a Repaint starts
                # a new task; the caller's error card stops automatic retries.
                _forget(path, picture)
                raise _Failed("Atlas Cloud: " + str(data.get("error") or "generation failed")[:200])
            if status in ("completed", "succeeded", "success"):
                outputs = data.get("outputs") or []
                if not outputs:
                    raise AtlasError("Atlas Cloud finished without a picture URL")
                raw = _download(str(outputs[0]))
                _write(picture, raw)
                return raw
        except _Failed:
            raise
        except (AtlasError, ValueError, OSError) as err:
            last = str(err)
        remaining = max(0, deadline - time.monotonic())
        if stop is not None:
            stop.wait(min(POLL_S, remaining))
        else:
            time.sleep(min(POLL_S, remaining))
    raise AtlasError(
        f"Atlas Cloud paid task {record['task_id']} is saved; collect it on retry"
        + (f" ({last})" if last else "")
    )
