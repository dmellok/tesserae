"""OTA signed-descriptor contract: sign and verify firmware update metadata.

This is the server/pipeline half of the over-the-air update flow. The manifest
(target kind, firmware version, image URL, size, SHA-256) is signed with an
Ed25519 key into a compact ``{payload, signature}`` descriptor; devices that
advertise OTA support receive it on ``/status`` and verify it against the
published public key before fetching and applying the image.

See ``docs/ota/contract.md`` for the wire format and the on-device check order.
"""

from __future__ import annotations

from ._codec import (
    MANIFEST_FIELDS,
    SCHEMA_VERSION,
    b64u_decode,
    b64u_encode,
    serialize_manifest,
)
from .sign import build_manifest, load_private_key, sign_manifest
from .verify import (
    OtaVerificationError,
    check_image,
    check_target,
    load_public_key,
    parse_manifest,
    verify,
    verify_signature,
)

__all__ = [
    "MANIFEST_FIELDS",
    "SCHEMA_VERSION",
    "OtaVerificationError",
    "b64u_decode",
    "b64u_encode",
    "build_manifest",
    "check_image",
    "check_target",
    "load_private_key",
    "load_public_key",
    "parse_manifest",
    "serialize_manifest",
    "sign_manifest",
    "verify",
    "verify_signature",
]
