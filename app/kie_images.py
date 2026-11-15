"""kie.ai Nano Banana tasks, persisted before waiting for a paid picture.

API shapes: https://docs.kie.ai/market/google/nano-banana-edit
Prices: https://kie.ai/nano-banana (November 2026), 2 cents, Pro 9 cents.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

BASE = "https://api.kie.ai/api/v1"
UPLOAD_URLS = (
    "https://kieai.redpandaai.co/api/file-base64-upload",
    "https://api.kie.ai/api/file-base64-upload",
)
POLL_S = 4.0
FIELDS = [
    {"name": "kie_api_key", "secret": True},
    {"name": "image_service", "type": "string", "default": "auto"},
]
SERVICES = ("auto", "fal", "gemini", "openai", "kie")
_BOUND_STORE: Any = None
_TASK_ROOT: Path | None = None


class KieError(Exception):
    """A provider error suitable for the picture's error card."""


def bind_store(store: Any, task_root: Path | None = None) -> None:
    global _BOUND_STORE, _TASK_ROOT
    _BOUND_STORE, _TASK_ROOT = store, task_root


def configuration() -> tuple[str, str]:
    store = _BOUND_STORE
    with contextlib.suppress(Exception):
        from flask import current_app

        store = current_app.config.get("SETTINGS_STORE") or store
    vals = store.get_for_runtime("app", "images", FIELDS) if store is not None else {}
    service = str(vals.get("image_service") or "auto")
    key = str(vals.get("kie_api_key") or os.environ.get("KIE_API_KEY") or "").strip()
    return service if service in SERVICES else "auto", key


def task_root() -> Path:
    if _TASK_ROOT is None:
        raise KieError("kie.ai task storage is unavailable")
    return _TASK_ROOT


def _json(
    url: str, body: dict[str, Any] | None = None, key: str = "", timeout: float = 60
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
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
            raise KieError("kie.ai refused the key") from err
        raise KieError(f"kie.ai answered HTTP {err.code}") from err
    except (OSError, ValueError) as err:
        raise KieError(f"kie.ai could not be reached ({type(err).__name__})") from err
    if not isinstance(answer, dict):
        raise KieError("kie.ai sent an invalid answer")
    if answer.get("code", 200) != 200:
        raise KieError(
            "kie.ai: " + str(answer.get("msg") or answer.get("message") or "request refused")[:200]
        )
    return answer


def credits(key: str) -> float:
    value = _json(f"{BASE}/chat/credit", key=key, timeout=15).get("data")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KieError("kie.ai did not report credits")
    return float(value)


def check_key(key: str) -> tuple[bool, str]:
    if not key.strip():
        return False, "No key to check."
    try:
        value = credits(key)
    except KieError as err:
        return False, str(err)
    return True, f"kie.ai accepted the key. {value:g} credits available."


def _upload(ref: str, key: str) -> str:
    if not ref.startswith("data:"):
        return ref
    last = "kie.ai upload returned no URL"
    for url in UPLOAD_URLS:
        try:
            data = (
                _json(
                    url,
                    {
                        "base64Data": ref,
                        "uploadPath": "tesserae-artwork",
                        "fileName": "reference.png",
                    },
                    key,
                ).get("data")
                or {}
            )
            if data.get("downloadUrl"):
                return str(data["downloadUrl"])
        except KieError as err:
            last = str(err)
    raise KieError(last)


def _download(url: str) -> bytes:
    from app import net_guard

    try:
        raw, _ = net_guard.fetch_bytes(url, timeout=60, max_bytes=30 * 1024 * 1024)
    except Exception as err:
        raise KieError(f"kie.ai picture could not be fetched ({type(err).__name__})") from err
    if not raw:
        raise KieError("kie.ai sent an empty picture")
    return raw


def _write(path: Path, raw: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)


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
) -> bytes:
    """Resume a saved task, or submit exactly once. An ambiguous submission is
    left for account inspection, never automatically paid for a second time.
    Keep completed bytes until the caller has saved its whole artwork job.
    """
    if not key:
        raise KieError("no kie.ai key")
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
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
        )


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
) -> bytes:
    path, picture = folder / f"{slot}.json", folder / f"{slot}.png"
    identity = hashlib.sha256(json.dumps([prompt, aspect, pro, bool(refs)]).encode()).hexdigest()
    if path.exists():
        # Corrupt state must fail closed: it might describe a paid task.
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError) as err:
            raise KieError(
                "kie.ai saved task could not be read; check the account before retrying"
            ) from err
        if not isinstance(record, dict):
            raise KieError("kie.ai saved task is invalid; check the account before retrying")
        if record.get("identity") != identity:
            raise KieError(
                "kie.ai has a saved task for a different prompt; collect it before repainting"
            )
        if picture.exists():
            return picture.read_bytes()
        if record.get("error"):
            raise KieError(record["error"])
        if not record.get("task_id"):
            raise KieError("kie.ai submission status is unknown; check the account before retrying")
    else:
        if stop is not None and stop.is_set():
            raise KieError("stopped")
        cost, model = (
            (9, "nano_banana_pro_kie")
            if pro
            else (2, "nano_banana_kie" if refs else "nano_banana_kie_t2i")
        )
        if credits(key) < cost * 2:
            raise KieError("kie.ai has insufficient credits")
        urls = [_upload(ref, key) for ref in refs]
        inputs: dict[str, Any] = {"prompt": prompt, "aspect_ratio": aspect, "output_format": "png"}
        if pro:
            inputs.update(image_input=urls, resolution="1K")
        elif urls:
            inputs["image_urls"] = urls
        body = {
            "model": "nano-banana-pro"
            if pro
            else "google/nano-banana-edit"
            if urls
            else "google/nano-banana",
            "input": inputs,
        }
        record = {"identity": identity, "state": "submitting"}
        _write(path, json.dumps(record).encode())
        try:
            charge(cost, model)
        except Exception:
            path.unlink()  # No request was submitted when a ceiling refused it.
            raise
        try:
            data = _json(f"{BASE}/jobs/createTask", body, key).get("data") or {}
            tid = data.get("taskId")
            if not isinstance(tid, str) or not tid:
                raise KieError("kie.ai returned no task id; check the account before retrying")
            record.update(task_id=tid, state="pending")
            _write(path, json.dumps(record).encode())
        except (KieError, OSError) as err:
            raise KieError(
                f"kie.ai submission status is unknown ({err}); check the account before retrying"
            ) from err
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            raise KieError("stopped; kie.ai paid task is saved for collection")
        try:
            data = (
                _json(
                    f"{BASE}/jobs/recordInfo?{urllib.parse.urlencode({'taskId': record['task_id']})}",
                    key=key,
                    timeout=max(0.001, min(30, deadline - time.monotonic())),
                ).get("data")
                or {}
            )
            if data.get("state") == "fail":
                record["error"] = "kie.ai: " + str(data.get("failMsg") or "generation failed")[:200]
                _write(path, json.dumps(record).encode())
                raise KieError(record["error"])
            if data.get("state") == "success":
                result = data.get("resultJson") or {}
                if isinstance(result, str):
                    result = json.loads(result)
                urls = result.get("resultUrls") or []
                if not urls:
                    raise KieError("kie.ai finished without a picture URL")
                raw = _download(urls[0])
                _write(picture, raw)
                return raw
        except (KieError, ValueError, OSError) as err:
            if record.get("error"):
                raise
            last = str(err)
        remaining = max(0, deadline - time.monotonic())
        if stop is not None:
            stop.wait(min(POLL_S, remaining))
        else:
            time.sleep(min(POLL_S, remaining))
    raise KieError(
        f"kie.ai paid task {record['task_id']} is saved; collect it on retry"
        + (f" ({last})" if last else "")
    )
