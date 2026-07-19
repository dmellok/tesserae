"""Sign an OTA manifest into a ``{payload, signature}`` descriptor.

Library use::

    from app.ota import build_manifest, sign_manifest, load_private_key

    manifest = build_manifest(
        key_id="prod-ed25519-1",
        device_kind="esp32_client",
        fw_version="1.4.0",
        image_url="https://cdn.example/app-1.4.0.bin",
        image=Path("app.bin").read_bytes(),
    )
    descriptor = sign_manifest(manifest, load_private_key(seed_bytes))

CLI (the seed of the signing pipeline)::

    python -m app.ota.sign \\
        --key-id prod-ed25519-1 --device-kind esp32_client \\
        --fw-version 1.4.0 --image app.bin \\
        --image-url https://cdn.example/app-1.4.0.bin \\
        --key signing.seed.hex > descriptor.json

The ``--key`` file holds the 32-byte Ed25519 seed as hex. The private key never
leaves the signing host; only the descriptor and the published *public* key
travel to devices.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ._codec import SCHEMA_VERSION, b64u_encode, serialize_manifest


def build_manifest(
    *,
    key_id: str,
    device_kind: str,
    fw_version: str,
    image_url: str,
    image: bytes,
) -> dict[str, Any]:
    """Assemble a manifest, binding the image's exact size and SHA-256 so the
    signature covers both the metadata and the image identity."""
    return {
        "schema_version": SCHEMA_VERSION,
        "key_id": key_id,
        "device_kind": device_kind,
        "fw_version": fw_version,
        "image_url": image_url,
        "size_bytes": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
    }


def load_private_key(seed: bytes) -> Ed25519PrivateKey:
    """Load a signing key from its raw 32-byte Ed25519 seed."""
    return Ed25519PrivateKey.from_private_bytes(seed)


def sign_manifest(manifest: dict[str, Any], private_key: Ed25519PrivateKey) -> dict[str, str]:
    """Serialize the manifest, sign the bytes, return the wire descriptor."""
    payload = serialize_manifest(manifest)
    signature = private_key.sign(payload)
    return {"payload": b64u_encode(payload), "signature": b64u_encode(signature)}


def _read_seed(path: Path) -> bytes:
    seed = bytes.fromhex(path.read_text(encoding="utf-8").strip())
    if len(seed) != 32:
        raise ValueError(f"signing key must be a 32-byte hex seed, got {len(seed)} bytes")
    return seed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ota.sign", description=__doc__)
    parser.add_argument("--key-id", required=True, help="identifier of the signing key")
    parser.add_argument("--device-kind", required=True, help="target device kind")
    parser.add_argument("--fw-version", required=True, help="firmware version this image installs")
    parser.add_argument("--image", required=True, type=Path, help="path to the app image")
    parser.add_argument("--image-url", required=True, help="URL devices fetch the image from")
    parser.add_argument("--key", required=True, type=Path, help="hex Ed25519 seed file")
    args = parser.parse_args(argv)

    try:
        image = args.image.read_bytes()
        private_key = load_private_key(_read_seed(args.key))
    except (OSError, ValueError) as exc:
        print(f"sign error: {exc}", file=sys.stderr)
        return 2

    manifest = build_manifest(
        key_id=args.key_id,
        device_kind=args.device_kind,
        fw_version=args.fw_version,
        image_url=args.image_url,
        image=image,
    )
    json.dump(sign_manifest(manifest, private_key), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
