"""Per-device log collection state and uploaded log batches.

Two things live here, both under ``data/core/``:

* ``device_log_collect.json``: per device, the number of wakes the operator
  asked logs to be collected for (decremented once per status response that
  asks for an upload) and the last ``diag`` failure report seen, whose ``id``
  dedups a report the firmware re-sends until one status POST returns 2xx.
  Deliberately NOT in settings ``devices.<id>``: that section is served
  verbatim as the device's ``config`` and persisted to NVS by the firmware.
* ``device_logs/<device_id>/<UTC timestamp>.log``: the uploaded batches,
  UTF-8 text, the newest 20 kept up to 1 MB per device.
* ``device_logs/<device_id>/ota-<UTC timestamp>.log``: OTA logs, the log a
  client sends on its own around a firmware update (``X-Tesserae-Log-Kind:
  ota``). Kept apart, the newest 10 up to 512 KB, so a run of routine
  uploads never evicts the log of the update that went wrong.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_UPLOAD_BYTES = 64 * 1024
KEEP_UPLOADS = 20
KEEP_BYTES = 1024 * 1024
# Upper bound for an operator-set collection window, so a hand-edited
# request can't leave a panel uploading on every wake indefinitely.
MAX_COLLECT_WAKES = 100

# Log kinds. "log" is the routine batch; "ota" an update's own log.
KIND_LOG = "log"
KIND_OTA = "ota"
KINDS = (KIND_LOG, KIND_OTA)
KEEP_OTA_UPLOADS = 10
KEEP_OTA_BYTES = 512 * 1024
_KEEP = {KIND_LOG: (KEEP_UPLOADS, KEEP_BYTES), KIND_OTA: (KEEP_OTA_UPLOADS, KEEP_OTA_BYTES)}

_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_NAME_RE = re.compile(r"^(?:(ota)-)?(\d{8}T\d{6}Z)(?:-(\d{1,4}))?\.log$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def count_lines(data: bytes) -> int:
    """Lines in a batch: newline count, plus one for an unterminated tail."""
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _sort_key(name: str) -> tuple[str, int]:
    m = _NAME_RE.match(name)
    if m is None:
        return (name, 0)
    return (m.group(2), int(m.group(3) or 0))


def _kind_of(m: re.Match[str]) -> str:
    return KIND_OTA if m.group(1) else KIND_LOG


@dataclass(frozen=True)
class UploadInfo:
    name: str
    bytes: int
    lines: int
    received_at: float
    kind: str = KIND_LOG


class DeviceLogStore:
    """Thread-safe store for log collection state and uploaded batches."""

    def __init__(self, core_dir: Path) -> None:
        self._state_path = core_dir / "device_log_collect.json"
        self._logs_root = core_dir / "device_logs"
        self._lock = threading.Lock()

    # -- collection state ------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._state_path)

    def _entry(self, data: dict[str, Any], device_id: str) -> dict[str, Any]:
        raw = data.get(device_id)
        return dict(raw) if isinstance(raw, dict) else {}

    def _put(self, data: dict[str, Any], device_id: str, entry: dict[str, Any]) -> None:
        if entry:
            data[device_id] = entry
        else:
            data.pop(device_id, None)
        self._save(data)

    def remaining(self, device_id: str) -> int:
        """Wakes left in the operator's collection window (0 when off)."""
        value = self._entry(self._load(), device_id).get("remaining")
        return value if isinstance(value, int) and value > 0 else 0

    def set_collect(self, device_id: str, wakes: int) -> None:
        """Collect logs on the next ``wakes`` wakes; 0 stops collection."""
        wakes = max(0, min(int(wakes), MAX_COLLECT_WAKES))
        with self._lock:
            data = self._load()
            entry = self._entry(data, device_id)
            if wakes:
                entry["remaining"] = wakes
            else:
                entry.pop("remaining", None)
            self._put(data, device_id, entry)

    def take_wake(self, device_id: str) -> bool:
        """Spend one wake of the collection window. True when one was left,
        i.e. this status response should ask for an upload."""
        with self._lock:
            data = self._load()
            entry = self._entry(data, device_id)
            left = entry.get("remaining")
            if not isinstance(left, int) or left <= 0:
                return False
            if left > 1:
                entry["remaining"] = left - 1
            else:
                entry.pop("remaining", None)
            self._put(data, device_id, entry)
            return True

    def note_diag(self, device_id: str, diag: dict[str, Any], received_at: float) -> bool:
        """Remember a ``diag`` report. True when its ``id`` differs from the
        last one seen for this device (a new failure), False for a re-send."""
        with self._lock:
            data = self._load()
            entry = self._entry(data, device_id)
            last = entry.get("last_diag")
            if isinstance(last, dict) and last.get("id") == diag.get("id"):
                return False
            entry["last_diag"] = {**diag, "received_at": received_at}
            self._put(data, device_id, entry)
            return True

    def last_diag(self, device_id: str) -> dict[str, Any] | None:
        last = self._entry(self._load(), device_id).get("last_diag")
        return last if isinstance(last, dict) else None

    # -- uploads -----------------------------------------------------------

    def _device_dir(self, device_id: str) -> Path | None:
        if not _SAFE_ID_RE.match(device_id or ""):
            return None
        return self._logs_root / device_id

    def save_upload(
        self,
        device_id: str,
        data: bytes,
        *,
        kind: str = KIND_LOG,
        now: float | None = None,
    ) -> UploadInfo:
        """Store one batch as ``<UTC timestamp>.log`` (``ota-<stamp>.log``
        for an OTA log) and prune the oldest of that kind past its caps.
        Invalid UTF-8 is replaced, not refused: a garbled line is still
        worth reading."""
        target_dir = self._device_dir(device_id)
        if target_dir is None:
            raise ValueError(f"unsafe device id {device_id!r}")
        if kind not in KINDS:
            raise ValueError(f"unknown log kind {kind!r}")
        prefix = "ota-" if kind == KIND_OTA else ""
        received_at = time.time() if now is None else now
        stamp = datetime.fromtimestamp(received_at, UTC).strftime(_STAMP_FORMAT)
        text = data.decode("utf-8", errors="replace")
        with self._lock:
            target_dir.mkdir(parents=True, exist_ok=True)
            name = f"{prefix}{stamp}.log"
            n = 1
            while (target_dir / name).exists():
                n += 1
                name = f"{prefix}{stamp}-{n}.log"
            (target_dir / name).write_text(text, encoding="utf-8", newline="")
            self._prune(target_dir, kind)
        return UploadInfo(
            name=name,
            bytes=len(data),
            lines=count_lines(data),
            received_at=received_at,
            kind=kind,
        )

    def _prune(self, target_dir: Path, kind: str = KIND_LOG) -> None:
        keep_uploads, keep_bytes = _KEEP[kind]
        names = []
        for p in target_dir.iterdir():
            m = _NAME_RE.match(p.name)
            if m is not None and _kind_of(m) == kind:
                names.append(p.name)
        names.sort(key=_sort_key, reverse=True)
        total = 0
        for index, name in enumerate(names):
            path = target_dir / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total += size
            # The newest batch always stays, even on its own over the cap.
            if index > 0 and (index >= keep_uploads or total > keep_bytes):
                path.unlink(missing_ok=True)

    def list_uploads(self, device_id: str, kind: str | None = None) -> list[UploadInfo]:
        """Stored batches, newest first: every kind, or just ``kind``."""
        target_dir = self._device_dir(device_id)
        if target_dir is None or not target_dir.is_dir():
            return []
        out = []
        for path in target_dir.iterdir():
            m = _NAME_RE.match(path.name)
            if m is None or (kind is not None and _kind_of(m) != kind):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            stamp = datetime.strptime(m.group(2), _STAMP_FORMAT).replace(tzinfo=UTC)
            out.append(
                UploadInfo(
                    name=path.name,
                    bytes=len(data),
                    lines=count_lines(data),
                    received_at=stamp.timestamp(),
                    kind=_kind_of(m),
                )
            )
        out.sort(key=lambda u: _sort_key(u.name), reverse=True)
        return out

    def read_upload(self, device_id: str, name: str) -> str | None:
        """One stored batch's text, or None for an unknown or unsafe name."""
        target_dir = self._device_dir(device_id)
        if target_dir is None or not _NAME_RE.match(name or ""):
            return None
        path = target_dir / name
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def forget(self, device_id: str) -> None:
        """Drop a device's uploads and collection state, e.g. on delete, so
        a future device reusing the id starts clean."""
        target_dir = self._device_dir(device_id)
        with self._lock:
            if target_dir is not None and target_dir.is_dir():
                shutil.rmtree(target_dir, ignore_errors=True)
            data = self._load()
            if device_id in data:
                del data[device_id]
                self._save(data)
