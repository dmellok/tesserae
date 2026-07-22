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

from app.state.ota_staging import OtaStagingStore

from .keys import default_keys_dir
from .service import manifest_from_descriptor, verify_descriptor
from .verify import OtaVerificationError


def _store(data_root: Path) -> OtaStagingStore:
    return OtaStagingStore(data_root / "core" / "ota_pending.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ota.stage", description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path, help="server data directory")
    parser.add_argument("--device-id", required=True, help="target device id")
    parser.add_argument("--descriptor", type=Path, help="signed descriptor JSON to stage")
    parser.add_argument("--clear", action="store_true", help="clear the device's pending OTA")
    parser.add_argument(
        "--keys-dir",
        type=Path,
        default=None,
        help=f"trusted public keys directory (default: {default_keys_dir()})",
    )
    parser.add_argument(
        "--insecure-skip-verify",
        action="store_true",
        help="stage without verifying the descriptor's signature (not recommended)",
    )
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
    except (OSError, json.JSONDecodeError) as exc:
        print(f"stage error: {exc}", file=sys.stderr)
        return 2

    # Verify the signature against a trusted key before staging, so a corrupt or
    # mis-signed descriptor is refused here rather than shipped to a device. The
    # same verify path backs the Firmware rollout UI (app.ota.service).
    try:
        if args.insecure_skip_verify:
            manifest = manifest_from_descriptor(descriptor)
        else:
            manifest = verify_descriptor(descriptor, keys_dir=args.keys_dir)
    except OtaVerificationError as exc:
        if exc.reason == "unknown_key":
            where = args.keys_dir or default_keys_dir()
            print(
                f"stage error: {exc} in {where}; publish its .pub or pass --insecure-skip-verify",
                file=sys.stderr,
            )
        else:
            print(f"stage error: descriptor failed verification ({exc.reason})", file=sys.stderr)
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
