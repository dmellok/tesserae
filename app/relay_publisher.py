"""Out-of-band frame publisher for ``transport="relay"`` devices.

Registered as a ``PushManager`` listener (like ``OpenDisplayHaPublisher``): on
each render it seals the packed artifact for every relay-bound device whose frame
changed and uploads it to the device's relay mailbox. The home instance only
ever makes outbound calls; the remote panel polls the relay.

Sealing uses the device's per-panel ``relay_frame_key`` (set at pairing) so the
relay stores ciphertext only. See ``docs/relay/contract.md``.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Flask

from app.device_loader import DeviceRegistry
from app.ota._codec import b64u_decode, b64u_encode
from app.relay_client import RelayClient, RelayError
from app.relay_config import build_client, relay_config
from app.relay_crypto import seal

logger = logging.getLogger(__name__)


class RelayPublisher:
    def __init__(
        self,
        *,
        app: Flask,
        devices: DeviceRegistry,
        settings: Any,
        renders_dir: Path,
        latest_render_fn: Callable[[str], dict[str, Any] | None],
        run_async: bool = True,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._renders_dir = Path(renders_dir)
        self._latest = latest_render_fn
        self._last_sent: dict[str, str] = {}
        self._lock = threading.Lock()
        # Uploads are network-bound; push listeners run in the push thread, so
        # offload to a single serial worker and coalesce via a dirty flag so a
        # push never stalls the pipeline. Tests use run_async=False.
        self._run_async = run_async
        self._dirty = False
        self._worker_running = False

    # -- push reaction ----------------------------------------------------

    def on_push(self, _result: Any = None) -> None:
        """Listener entry point (runs in the push thread). Never raises."""
        if not self._run_async:
            self._process_once()
            return
        if not self._relay_devices():
            return
        with self._lock:
            self._dirty = True
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(target=self._worker, name="relay-publisher", daemon=True).start()

    def _worker(self) -> None:
        while True:
            with self._lock:
                if not self._dirty:
                    self._worker_running = False
                    return
                self._dirty = False
            self._process_once()

    def _process_once(self) -> None:
        client = build_client(relay_config(self._settings))
        if client is None:
            return  # install not linked to a relay; nothing to publish
        for device in self._relay_devices():
            try:
                self._maybe_send(client, device)
            except RelayError as exc:
                # Leave _last_sent unset so the next push retries this frame.
                logger.warning("relay %s: upload failed (%s)", device.id, exc)
            except Exception:
                logger.exception("relay: send failed for %s", device.id)

    def _relay_devices(self) -> list[Any]:
        return [d for d in self._devices.devices.values() if d.transport == "relay"]

    def _maybe_send(self, client: RelayClient, device: Any) -> None:
        latest = self._latest(device.id)
        if not latest:
            return
        digest = str(latest.get("digest") or "")
        if not digest:
            return
        with self._lock:
            if self._last_sent.get(device.id) == digest:
                return
        key_b64 = device.manifest.get("relay_frame_key")
        if not isinstance(key_b64, str) or not key_b64:
            logger.warning("relay %s: no frame key (not paired?); skipping", device.id)
            return
        filename = str(latest.get("filename") or "")
        src = self._renders_dir / filename
        if not filename or not src.exists():
            logger.warning("relay %s: render %s missing on disk", device.id, filename)
            return

        sealed = seal(src.read_bytes(), b64u_decode(key_b64))
        panel = device.panel or {}
        meta = {
            "render_id": digest,
            "renderer_id": latest.get("renderer_id"),
            "page_id": latest.get("page_id"),
        }
        meta_b64u = b64u_encode(
            json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        client.put_frame(
            device_id=device.id,
            etag=digest,
            sealed=sealed,
            panel_w=int(panel.get("w") or 0),
            panel_h=int(panel.get("h") or 0),
            fmt=str(latest.get("ext") or ""),
            renderer_id=str(latest.get("renderer_id") or ""),
            meta_b64u=meta_b64u,
        )
        with self._lock:
            self._last_sent[device.id] = digest
        logger.info("relay %s: uploaded frame %s", device.id, digest)
