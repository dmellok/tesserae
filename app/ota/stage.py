"""Stage a signed OTA descriptor for a device, or clear one.

The descriptor is produced by ``python -m app.ota.sign`` (or any signer that
follows ``docs/ota/contract.md``); this command decodes its payload to record
the target kind, firmware version, and schema, then writes it to the server's
pending-OTA store so the device is offered the update on its next ``/status``.

Stage::

    python -m app.ota.sign --key-id … --device-kind esp32_client \\
        --fw-version 1.4.0 --image app.bin --image-url … --key signing.hex \\
        > descriptor.json
    python -m app.ota.stage --data-root data --device-id hall_esp \\
        --descriptor descriptor.json

Clear::

    python -m app.ota.stage --data-root data --device-id hall_esp --clear

``--data-root`` is the running server's data directory (the store lives at
``<data-root>/core/ota_pending.json``). The server picks the change up on the
next heartbeat; no restart needed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.state.ota_staging import OtaStagingStore

from ._codec import MANIFEST_FIELDS, b64u_decode


def _store(data_root: Path) -> OtaStagingStore:
    return OtaStagingStore(data_root / "core" / "ota_pending.json")


def _manifest_from_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Decode + shape-check the descriptor's manifest (no signature check; the
    device verifies the signature, this is a convenience decode for staging)."""
    if not isinstance(descriptor, dict) or "payload" not in descriptor:
        raise ValueError("descriptor must be an object with a 'payload'")
    manifest = json.loads(b64u_decode(str(descriptor["payload"])))
    if not isinstance(manifest, dict):
        raise ValueError("decoded payload is not a manifest object")
    missing = [k for k in MANIFEST_FIELDS if k not in manifest]
    if missing:
        raise ValueError(f"manifest missing keys: {missing}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ota.stage", description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path, help="server data directory")
    parser.add_argument("--device-id", required=True, help="target device id")
    parser.add_argument("--descriptor", type=Path, help="signed descriptor JSON to stage")
    parser.add_argument("--clear", action="store_true", help="clear the device's pending OTA")
    args = parser.parse_args(argv)

    store = _store(args.data_root)

    if args.clear:
        removed = store.clear(args.device_id)
        print(f"{'cleared' if removed else 'nothing staged for'} {args.device_id}")
        return 0

    if args.descriptor is None:
        print("one of --descriptor or --clear is required", file=sys.stderr)
        return 2

    try:
        descriptor = json.loads(args.descriptor.read_text(encoding="utf-8"))
        manifest = _manifest_from_descriptor(descriptor)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"stage error: {exc}", file=sys.stderr)
        return 2

    entry = store.stage(
        args.device_id,
        descriptor,
        device_kind=str(manifest["device_kind"]),
        fw_version=str(manifest["fw_version"]),
        schema_version=int(manifest["schema_version"]),
    )
    print(
        f"staged OTA for {args.device_id}: kind={entry['device_kind']} "
        f"fw={entry['fw_version']} schema={entry['schema_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
