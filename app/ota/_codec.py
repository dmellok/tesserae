"""Shared helpers for the OTA signed-descriptor format.

Wire contract: ``docs/ota/contract.md``. A *descriptor* is a JSON object::

    {"payload": "<base64url>", "signature": "<base64url>"}

where ``payload`` is the base64url (no padding) of the exact manifest JSON
bytes, and ``signature`` is the base64url of the Ed25519 signature over those
same raw bytes. Verification happens over the decoded payload bytes *before*
the JSON is parsed, so the two sides never have to reproduce a canonical JSON
encoding: whatever bytes the signer emitted are the bytes that get verified.
"""

from __future__ import annotations

import base64
import json
from typing import Any

# Bump when the manifest shape changes in a way older firmware can't read. The
# verifier rejects a manifest whose ``schema_version`` it doesn't recognise.
SCHEMA_VERSION = 1

# Manifest keys in the order the contract lists them. Every one is required;
# the verifier rejects a manifest missing any of them.
MANIFEST_FIELDS = (
    "schema_version",
    "key_id",
    "device_kind",
    "fw_version",
    "image_url",
    "size_bytes",
    "sha256",
)


def b64u_encode(raw: bytes) -> str:
    """base64url without padding (the wire form)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    """Inverse of :func:`b64u_encode`; tolerant of missing padding."""
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def serialize_manifest(manifest: dict[str, Any]) -> bytes:
    """The exact bytes that get signed.

    Sorted keys and tight separators make the output deterministic so fixtures
    are byte-reproducible. The *format* does not require this canonical form,
    the firmware verifies over the raw decoded bytes regardless, it is only a
    convenience so signing the same manifest twice yields the same bytes.
    """
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
