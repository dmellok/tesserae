"""Verify an OTA ``{payload, signature}`` descriptor against a public key.

Check order (the firmware mirrors this, see ``docs/ota/contract.md``):

1. Ed25519 signature over the *raw decoded* payload bytes.
2. Parse the payload as JSON and confirm the manifest shape + schema version.
3. ``device_kind`` and (optionally) that the firmware version actually advances.
4. Downloaded image: exact byte size, then SHA-256 digest.

Every failure raises :class:`OtaVerificationError` with a stable ``.reason``
code so callers (and tests, and the firmware's log lines) can point at the
exact gate that tripped. Verifying the signature first, before the JSON is even
parsed, means untrusted bytes never reach the parser.
"""

from __future__ import annotations

import binascii
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ._codec import MANIFEST_FIELDS, SCHEMA_VERSION, b64u_decode

# Length of a raw Ed25519 signature; anything else is malformed, not merely a
# mismatch, so we reject it before handing it to the verifier.
_SIG_LEN = 64


class OtaVerificationError(Exception):
    """A descriptor failed verification. ``reason`` is a stable machine code."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def load_public_key(raw: bytes) -> Ed25519PublicKey:
    """Load the verification key from its raw 32-byte Ed25519 public key."""
    return Ed25519PublicKey.from_public_bytes(raw)


def _decode(descriptor: dict[str, Any]) -> tuple[bytes, bytes]:
    try:
        payload = b64u_decode(descriptor["payload"])
        signature = b64u_decode(descriptor["signature"])
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise OtaVerificationError("malformed_descriptor", str(exc)) from exc
    if len(signature) != _SIG_LEN:
        raise OtaVerificationError("malformed_descriptor", "signature is not 64 bytes")
    return payload, signature


def verify_signature(descriptor: dict[str, Any], public_key: Ed25519PublicKey) -> bytes:
    """Return the payload bytes iff the signature over them is valid."""
    payload, signature = _decode(descriptor)
    try:
        public_key.verify(signature, payload)
    except InvalidSignature as exc:
        raise OtaVerificationError("bad_signature") from exc
    return payload


def parse_manifest(payload: bytes) -> dict[str, Any]:
    """Parse verified payload bytes into a manifest, checking its shape."""
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OtaVerificationError("malformed_manifest", str(exc)) from exc
    if not isinstance(manifest, dict):
        raise OtaVerificationError("malformed_manifest", "manifest is not an object")
    missing = [k for k in MANIFEST_FIELDS if k not in manifest]
    if missing:
        raise OtaVerificationError("malformed_manifest", f"missing keys: {missing}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise OtaVerificationError(
            "schema_version", f"unsupported schema_version {manifest['schema_version']!r}"
        )
    return manifest


def check_target(
    manifest: dict[str, Any],
    *,
    expected_kind: str | None = None,
    current_fw: str | None = None,
) -> None:
    """Confirm the manifest is aimed at this device.

    ``current_fw``, when given, rejects a manifest that names the version
    already running. That is a floor, not the ordering: deciding whether a
    version actually *advances* (semver comparison, downgrade policy) is the
    firmware's call; the reference verifier only refuses a no-op.
    """
    if expected_kind is not None and manifest["device_kind"] != expected_kind:
        raise OtaVerificationError(
            "kind_mismatch", f"{manifest['device_kind']!r} != {expected_kind!r}"
        )
    if current_fw is not None and manifest["fw_version"] == current_fw:
        raise OtaVerificationError("already_current", f"already on {current_fw!r}")


def check_image(manifest: dict[str, Any], image: bytes) -> None:
    """Confirm the downloaded image matches the signed size and digest."""
    if len(image) != int(manifest["size_bytes"]):
        raise OtaVerificationError("size_mismatch", f"{len(image)} != {manifest['size_bytes']}")
    digest = hashlib.sha256(image).hexdigest()
    if digest != manifest["sha256"]:
        raise OtaVerificationError("digest_mismatch", f"{digest} != {manifest['sha256']}")


def verify(
    descriptor: dict[str, Any],
    public_key: Ed25519PublicKey,
    *,
    expected_kind: str | None = None,
    current_fw: str | None = None,
    image: bytes | None = None,
) -> dict[str, Any]:
    """Full verification pass; returns the manifest or raises.

    ``image`` is optional so a device can verify the descriptor first (checks
    1-3) and decide to fetch, then re-check the digest (check 4) once the image
    has streamed in, exactly as the firmware does to avoid buffering the image.
    """
    payload = verify_signature(descriptor, public_key)
    manifest = parse_manifest(payload)
    check_target(manifest, expected_kind=expected_kind, current_fw=current_fw)
    if image is not None:
        check_image(manifest, image)
    return manifest
