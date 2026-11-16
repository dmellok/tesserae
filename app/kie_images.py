"""kie.ai Nano Banana tasks, persisted before waiting for a paid picture.

API shapes: https://docs.kie.ai/market/google/nano-banana-edit
Prices: https://kie.ai/nano-banana (November 2026), 2 cents, Pro 9 cents.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

BASE = "https://api.kie.ai/api/v1"
UPLOAD_URLS = (
    "https://kieai.redpandaai.co/api/file-base64-upload",
    "https://api.kie.ai/api/file-base64-upload",
)
POLL_S = 4.0
# A submission whose outcome is unknown has no task id to collect, so after
# this long it is given up on and the picture may be asked for again.
UNKNOWN_MAX_AGE_S = 3600
# A task kie.ai has not finished by now never will.
PENDING_MAX_AGE_S = 24 * 3600
logger = logging.getLogger(__name__)
FIELDS = [
    {"name": "kie_api_key", "secret": True},
    {"name": "image_service", "type": "string", "default": "auto"},
]
SERVICES = ("auto", "fal", "gemini", "openai", "kie")
_BOUND_STORE: Any = None
_TASK_ROOT: Path | None = None


class KieError(Exception):
    """A provider error suitable for the picture's error card."""


class KieRefused(KieError):
    """kie.ai definitely did not take the request: it answered with a
    refusal, or the request never left. Nothing was bought."""


def bind_store(store: Any, task_root: Path | None = None) -> None:
    global _BOUND_STORE, _TASK_ROOT
    _BOUND_STORE, _TASK_ROOT = store, task_root


def configuration() -> tuple[str, str]:
    store = _BOUND_STORE
    with contextlib.suppress(Exception):
        from flask import current_app

        store = current_app.config.get("SETTINGS_STORE") or store
    vals: dict[str, Any] = {}
    if store is not None:
        # A key that can't be decrypted must not take the artwork pages down.
        try:
            vals = store.get_for_runtime("app", "images", FIELDS)
        except Exception:
            logger.exception("kie.ai: the image settings could not be read")
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
            raise KieRefused("kie.ai refused the key") from err
        # A 4xx is an answer: the request was turned down (429 included).
        # A 5xx or a 408 says nothing about whether it was acted on.
        if 400 <= err.code < 500 and err.code != 408:
            raise KieRefused(f"kie.ai answered HTTP {err.code}") from err
        raise KieError(f"kie.ai answered HTTP {err.code}") from err
    except urllib.error.URLError as err:
        # No address, or nothing listening: the request never left.
        if isinstance(err.reason, (socket.gaierror, ConnectionRefusedError)):
            raise KieRefused(f"kie.ai could not be reached ({type(err.reason).__name__})") from err
        raise KieError(f"kie.ai could not be reached ({type(err).__name__})") from err
    except (OSError, ValueError) as err:
        raise KieError(f"kie.ai could not be reached ({type(err).__name__})") from err
    if not isinstance(answer, dict):
        raise KieError("kie.ai sent an invalid answer")
    if answer.get("code", 200) != 200:
        raise KieRefused(
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


@contextlib.contextmanager
def _locked(folder: Path) -> Iterator[None]:
    """Hold the folder's lock, so one caller at a time submits or collects.
    ``fcntl`` does not exist on Windows, and importing it there stopped the
    whole server from starting, so Windows locks a byte of the file instead.
    """
    with (folder / ".lock").open("ab") as lock:
        if sys.platform == "win32":
            import errno
            import msvcrt

            fd = lock.fileno()
            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    # Gives up after about ten seconds; a picture takes longer.
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError as err:
                    if err.errno not in (errno.EACCES, errno.EDEADLK):
                        raise
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


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
    kie.ai definitely refused is forgotten and its charge handed back through
    ``refund``. Keep completed bytes until the caller has saved its whole
    artwork job.
    """
    if not key:
        raise KieError("no kie.ai key")
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


def _forget(path: Path, picture: Path) -> None:
    for f in (path, picture):
        with contextlib.suppress(OSError):
            f.unlink()


def clear_unfinished(folder: Path) -> int:
    """Forget saved tasks that have nothing to collect (no task id): an
    explicit Repaint is the owner saying to start again. Tasks kie.ai is
    still working on are kept. How many were forgotten."""
    n = 0
    with contextlib.suppress(OSError):
        for path in folder.glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                record = None
            if not isinstance(record, dict) or not record.get("task_id"):
                _forget(path, path.with_suffix(".png"))
                n += 1
    return n


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
            raise KieError("kie.ai saved task could not be read; check the account before retrying")
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
            raise KieError("kie.ai submission status is unknown; check the account before retrying")
        _forget(path, picture)
        return None
    if age > PENDING_MAX_AGE_S:
        _forget(path, picture)
        return None
    return record


class _Failed(KieError):
    """kie.ai finished the task without a picture."""


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
        except KieRefused as err:
            # Turned down, so nothing was bought: forget the attempt and
            # hand the charge back, and the next attempt submits again.
            _forget(path, picture)
            if refund is not None:
                with contextlib.suppress(Exception):
                    refund(cost, model)
            raise KieError(str(err)) from err
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
                # Nothing to collect. The record goes, so a Repaint starts
                # a new task; the caller's error card stops automatic retries.
                _forget(path, picture)
                raise _Failed("kie.ai: " + str(data.get("failMsg") or "generation failed")[:200])
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
        except _Failed:
            raise
        except (KieError, ValueError, OSError) as err:
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
