"""Shared OTA descriptor helpers used by both the CLI (`app.ota.stage`) and the
Firmware rollout admin UI.

Centralises the "decode + shape-check + verify a descriptor against the trusted
key registry" path so the UI never reimplements it, and a small display summary
of a manifest for the rollout page. Every verification failure surfaces as an
:class:`OtaVerificationError` with a stable ``.reason`` string (the same
vocabulary the contract documents), so callers can show the reason verbatim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._codec import MANIFEST_FIELDS, b64u_decode
from .keys import load_trusted_keys
from .verify import OtaVerificationError, verify


def manifest_from_descriptor(descriptor: Any) -> dict[str, Any]:
    """Decode + shape-check a descriptor's manifest (no signature check).

    Raises :class:`OtaVerificationError` (``malformed_descriptor`` /
    ``malformed_manifest``) so the caller handles one exception type."""
    if (
        not isinstance(descriptor, dict)
        or "payload" not in descriptor
        or "signature" not in descriptor
    ):
        raise OtaVerificationError(
            "malformed_descriptor", "descriptor must be an object with payload + signature"
        )
    try:
        manifest = json.loads(b64u_decode(str(descriptor["payload"])))
    except (ValueError, TypeError) as exc:
        raise OtaVerificationError("malformed_descriptor", str(exc)) from exc
    if not isinstance(manifest, dict):
        raise OtaVerificationError("malformed_manifest", "decoded payload is not an object")
    missing = [k for k in MANIFEST_FIELDS if k not in manifest]
    if missing:
        raise OtaVerificationError("malformed_manifest", f"missing keys: {missing}")
    return manifest


def verify_descriptor(descriptor: Any, *, keys_dir: Path | None = None) -> dict[str, Any]:
    """Verify a descriptor against the trusted key registry, returning its
    manifest. Raises :class:`OtaVerificationError` with a stable ``.reason``:
    ``malformed_descriptor`` / ``malformed_manifest`` / ``unknown_key`` /
    ``bad_signature`` / ``schema_version`` (+ the target reasons ``verify``
    raises). ``unknown_key`` means the descriptor's ``key_id`` has no published
    public key, so the server can't establish trust."""
    manifest = manifest_from_descriptor(descriptor)
    keys = load_trusted_keys(keys_dir)
    key_id = str(manifest["key_id"])
    public_key = keys.get(key_id)
    if public_key is None:
        raise OtaVerificationError("unknown_key", f"no trusted key for key_id {key_id!r}")
    verify(descriptor, public_key)
    return manifest


def _human_size(n: int) -> str:
    if n <= 0:
        return "0 B"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """Display-shaped view of a verified manifest for the rollout page."""
    sha = str(manifest.get("sha256") or "")
    size = int(manifest.get("size_bytes") or 0)
    image_url = str(manifest.get("image_url") or "")
    return {
        "device_kind": str(manifest.get("device_kind") or ""),
        "fw_version": str(manifest.get("fw_version") or ""),
        "key_id": str(manifest.get("key_id") or ""),
        "sha256": sha,
        "sha256_short": sha[:12],
        "size_bytes": size,
        "size_human": _human_size(size),
        "image_url": image_url,
        "image_host": urlparse(image_url).netloc or image_url,
    }
