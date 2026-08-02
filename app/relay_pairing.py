"""Home-side rendezvous pairing for remote relay panels.

The remote panel never reaches the home LAN, so pairing is brokered through the
relay (``docs/relay/contract.md``):

1. The operator adds a remote panel: :func:`mint_remote_panel_code` asks the
   relay for a code and records a local *slot* (which device to create) against
   it.
2. The panel posts ``{code, panel_pubkey}`` to the relay.
3. :class:`RelayPairingPoller` polls the relay, and when a panel public key
   appears for one of our codes it completes the X25519 handshake, creates the
   ``transport="relay"`` instance (with the derived frame key), and hands the
   relay the device token + home public key to forward to the panel.

Install linking (:func:`register_this_install`) mints the install keypair and
registers the install's public key with the relay.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

from app.device_loader import DeviceRegistry
from app.device_service import create_instance
from app.ota._codec import b64u_decode, b64u_encode
from app.relay_client import RelayClient, RelayError, register_install
from app.relay_config import (
    RELAY_SECTION,
    build_client,
    install_privkey,
    relay_config,
)
from app.relay_crypto import derive_shared_key, generate_keypair, public_key_for
from app.renderer_loader import RendererRegistry

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S: float = 30.0

# Kind used for a relay panel when neither the operator nor the panel's
# self-report names one. The standard ESP32 relay client.
DEFAULT_RELAY_KIND: str = "esp32_client"


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def register_this_install(
    settings: Any, *, base: str, label: str = "", allow_local: bool = False
) -> str:
    """Mint the install keypair, register with the relay, and persist the
    identity. Returns the ``install_id``. Raises :class:`RelayError` on failure.

    The private key stays home (used to complete pairings); only the public key
    is registered."""
    priv, pub = generate_keypair()
    install_id, publisher_token = register_install(
        base, b64u_encode(pub), label=label, allow_local=allow_local
    )
    settings.patch_section(
        RELAY_SECTION,
        {
            "enabled": True,
            "base_url": base,
            "allow_local": bool(allow_local),
            "install_id": install_id,
            "publisher_token_secret": publisher_token,
            "install_privkey_secret": b64u_encode(priv),
        },
    )
    return install_id


def mint_remote_panel_code(
    settings: Any,
    *,
    device_id: str,
    kind: str = "",
    name: str = "",
    panel: dict[str, Any] | None = None,
    orientation: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Ask the relay for a pairing code and record the slot it fills. Returns
    ``(code, expires_at)`` to show the operator. Raises :class:`RelayError`
    when the install isn't linked or the relay call fails.

    ``kind`` and ``panel`` are optional: left blank, the panel's self-report at
    pairing (model + geometry) fills them in, so the operator only needs a
    device id."""
    cfg = relay_config(settings)
    client = build_client(cfg)
    if client is None:
        raise RelayError("relay is not linked; register the install first")
    code, expires = client.mint_pair_code()
    pending = dict(cfg.get("pending_pairings") or {})
    pending[code] = {
        "device_id": device_id,
        "kind": kind,
        "name": name,
        "panel": panel or {},
        "orientation": orientation,
        "config": config or {},
    }
    settings.patch_section(RELAY_SECTION, {"pending_pairings": pending})
    return code, expires


class RelayPairingPoller:
    def __init__(
        self,
        *,
        devices: DeviceRegistry,
        renderers: RendererRegistry,
        data_root: Path,
        settings: Any,
        app: Any = None,
        rebuild_transport: Any = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        run_async: bool = True,
    ) -> None:
        self._devices = devices
        self._renderers = renderers
        self._data_root = Path(data_root)
        self._settings = settings
        self._app = app
        self._rebuild_transport = rebuild_transport
        self._interval_s = interval_s
        self._run_async = run_async
        self._stop = threading.Event()
        # De-dupe relayed heartbeats: only ingest a device's status when its
        # relay ``received_at`` advances, so we don't replay the same beat every
        # tick (which would spam battery history + the event log).
        self._last_status_at: dict[str, str] = {}

    def start(self) -> None:
        if not self._run_async:
            return
        threading.Thread(target=self._loop, name="relay-pairing", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_s):
            try:
                self.poll_once()
            except Exception:
                logger.exception("relay pairing poll failed")

    def poll_once(self) -> int:
        """One poll. Returns the number of pairings completed."""
        cfg = relay_config(self._settings)
        client = build_client(cfg)
        if client is None:
            return 0
        # Relayed heartbeats: pull each relay device's telemetry and feed the
        # normal status pipeline. Independent of pairing, so it runs first.
        self._pull_statuses(client)
        privkey_b64 = install_privkey(cfg)
        slots = cfg.get("pending_pairings")
        if not privkey_b64 or not isinstance(slots, dict) or not slots:
            return 0
        try:
            remote = client.pending_pairings()
        except RelayError as exc:
            logger.warning("relay: pending poll failed (%s)", exc)
            return 0
        completed = 0
        for entry in remote:
            code = entry.get("code")
            panel_pub = entry.get("panel_pubkey")
            if not isinstance(code, str) or not isinstance(panel_pub, str):
                continue
            slot = slots.get(code)
            if not isinstance(slot, dict):
                continue
            try:
                self._complete(client, privkey_b64, code, panel_pub, slot, entry)
            except (RelayError, ValueError) as exc:
                logger.warning("relay: completing pairing %s failed (%s)", code, exc)
                continue
            except Exception:
                logger.exception("relay: completing pairing %s failed", code)
                continue
            self._drop_slot(code)
            completed += 1
        return completed

    def _resolve_kind(self, slot_kind: str, reported_model: str) -> str:
        """Pick the device kind: the operator's slot value if set, else the
        panel's self-reported model, else the default relay panel kind. Only a
        value that names a real device *kind* (not an instance) is accepted."""
        for candidate in (slot_kind.strip(), reported_model.strip()):
            if candidate:
                dev = self._devices.get(candidate)
                if dev is not None and dev.kind_of is None:
                    return candidate
        return DEFAULT_RELAY_KIND

    def _resolve_panel(
        self, slot: dict[str, Any], reported: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Panel geometry: the slot's if set, else the panel's self-report."""
        panel = slot.get("panel")
        if isinstance(panel, dict) and panel.get("w") and panel.get("h"):
            return panel
        try:
            pw, ph = int(reported.get("panel_w") or 0), int(reported.get("panel_h") or 0)
        except (TypeError, ValueError):
            return None
        if pw <= 0 or ph <= 0:
            return None
        out: dict[str, Any] = {"w": pw, "h": ph}
        gamut = reported.get("gamut")
        if isinstance(gamut, str) and gamut.strip():
            out["gamut"] = gamut.strip()
        return out

    def _complete(
        self,
        client: RelayClient,
        privkey_b64: str,
        code: str,
        panel_pub_b64: str,
        slot: dict[str, Any],
        reported: dict[str, Any],
    ) -> None:
        frame_key = derive_shared_key(b64u_decode(privkey_b64), b64u_decode(panel_pub_b64))
        device_id = str(slot.get("device_id") or "")
        if not device_id:
            raise ValueError("pairing slot has no device_id")

        device = self._devices.get(device_id)
        if device is None:
            result = create_instance(
                devices=self._devices,
                renderers=self._renderers,
                data_root=self._data_root,
                instance_id=device_id,
                kind_id=self._resolve_kind(
                    str(slot.get("kind") or ""), str(reported.get("model") or "")
                ),
                name=str(slot.get("name") or ""),
                panel_overrides=self._resolve_panel(slot, reported),
                orientation=slot.get("orientation"),
                transport="relay",
                relay_frame_key=b64u_encode(frame_key),
            )
            if result.error or result.device is None:
                raise ValueError(result.error or "create_instance returned no device")
            device = result.device

        device_token = str(device.manifest.get("access_token") or "")
        if not device_token:
            raise ValueError("relay device has no access token")
        home_pub = b64u_encode(public_key_for(b64u_decode(privkey_b64)))
        client.complete_pairing(
            code=code,
            device_id=device_id,
            device_token=device_token,
            device_token_sha256=_sha256_hex(device_token),
            home_pubkey_b64u=home_pub,
            config=slot.get("config") or {},
        )
        if callable(self._rebuild_transport):
            self._rebuild_transport()
        logger.info("relay: paired remote panel %s (code %s)", device_id, code)

    def _pull_statuses(self, client: RelayClient) -> None:
        """Fetch each relay device's relayed telemetry and feed it through the
        shared heartbeat pipeline, so relay devices populate DEVICE_STATUS
        (battery / signal / firmware / last-seen) like any other device."""
        if self._app is None:
            return
        from app.transport_wiring import record_status_heartbeat

        relay_devices = [d for d in self._devices.devices.values() if d.transport == "relay"]
        for device in relay_devices:
            try:
                status = client.get_device_status(device.id)
            except RelayError as exc:
                logger.debug("relay: status pull for %s failed (%s)", device.id, exc)
                continue
            if not status:
                continue
            received = str(status.get("received_at") or "")
            if received and self._last_status_at.get(device.id) == received:
                continue  # already ingested this beat
            body = str(status.get("body") or "")
            try:
                record_status_heartbeat(
                    app=self._app,
                    device=device,
                    payload=body.encode("utf-8"),
                    status_cache=self._app.config["DEVICE_STATUS"],
                    event_log=self._app.config["EVENT_LOG"],
                    event_target=f"relay://{device.id}/status",
                )
            except Exception:
                logger.exception("relay: ingesting status for %s failed", device.id)
                continue
            self._last_status_at[device.id] = received

    def _drop_slot(self, code: str) -> None:
        cfg = relay_config(self._settings)
        pending = dict(cfg.get("pending_pairings") or {})
        if code in pending:
            del pending[code]
            self._settings.patch_section(RELAY_SECTION, {"pending_pairings": pending})
