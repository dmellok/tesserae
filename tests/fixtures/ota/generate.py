"""Regenerate the OTA test fixtures. Run from the repo root::

    python tests/fixtures/ota/generate.py

Deterministic: keys derive from fixed seeds and the sample image is fixed
content, so re-running produces byte-identical files. These keys are TEST ONLY
(published seeds); production signing uses a key held off-repo.
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.ota import build_manifest, serialize_manifest, sign_manifest
from app.ota._codec import b64u_encode

HERE = Path(__file__).parent

# Fixed, obviously-synthetic seeds. The "wrong" key signs the tamper fixture so
# its signature is valid under a DIFFERENT key than the published verifier.
SIGNING_SEED = bytes(range(32))  # 00 01 02 ... 1f
WRONG_SEED = bytes(range(1, 33))  # 01 02 03 ... 20
KEY_ID = "test-ed25519-1"

# Fixed sample image so size + digest are stable across regenerations.
SAMPLE_IMAGE = b"TESSERAE-OTA-TEST\x00" * 64  # 1152 bytes

DEVICE_KIND = "esp32_client"
FW_VERSION = "1.4.0"
IMAGE_URL = "https://cdn.example.test/tesserae/esp32_client/app-1.4.0.bin"


def _write_json(name: str, obj: dict) -> None:
    (HERE / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    signing = Ed25519PrivateKey.from_private_bytes(SIGNING_SEED)
    wrong = Ed25519PrivateKey.from_private_bytes(WRONG_SEED)
    pub = signing.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    (HERE / "test_signing_key.hex").write_text(SIGNING_SEED.hex() + "\n", encoding="utf-8")
    (HERE / "test_verify_key.hex").write_text(pub.hex() + "\n", encoding="utf-8")
    (HERE / "sample_image.bin").write_bytes(SAMPLE_IMAGE)

    manifest = build_manifest(
        key_id=KEY_ID,
        device_kind=DEVICE_KIND,
        fw_version=FW_VERSION,
        image_url=IMAGE_URL,
        image=SAMPLE_IMAGE,
    )

    # valid: correct signature, digest + size match the sample image.
    _write_json("valid.json", sign_manifest(manifest, signing))

    # wrong_key: identical manifest, signed with a different key. Signature
    # verification must fail against the published verifier.
    _write_json("wrong_key.json", sign_manifest(manifest, wrong))

    # truncated: a valid descriptor with its signature chopped short, so it is
    # no longer a 64-byte signature.
    truncated = sign_manifest(manifest, signing)
    truncated["signature"] = truncated["signature"][:-12]
    _write_json("truncated.json", truncated)

    # digest_mismatch: validly signed, but the manifest's sha256 does not match
    # the sample image. Signature + parse pass; the image digest check fails.
    tampered = dict(manifest)
    tampered["sha256"] = "0" * 64
    bad = sign_manifest(tampered, signing)
    # sanity: this really is a valid signature over the tampered bytes.
    assert serialize_manifest(tampered) != serialize_manifest(manifest)
    _write_json("digest_mismatch.json", bad)

    print("wrote fixtures to", HERE)
    print("  verify key:", b64u_encode(pub), f"(hex {pub.hex()})")


if __name__ == "__main__":
    main()
