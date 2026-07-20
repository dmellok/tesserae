"""Trusted OTA signing public keys, resolved by ``key_id``.

The server verifies a descriptor's signature against these before staging it,
so a corrupt or mis-signed descriptor is refused at staging time rather than
handed to a device. This is a staging-side sanity gate; the firmware embeds its
own key set and is the real trust anchor.

Keys live in ``ota/keys/<key_id>.pub`` as a hex-encoded raw Ed25519 public key.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .verify import load_public_key


def default_keys_dir() -> Path:
    """``ota/keys/`` at the repo root (this file is ``app/ota/keys.py``)."""
    return Path(__file__).resolve().parents[2] / "ota" / "keys"


def load_trusted_keys(keys_dir: Path | None = None) -> dict[str, Ed25519PublicKey]:
    """Map ``key_id`` to public key for every readable ``*.pub`` in the keys
    directory. Unreadable / malformed files are skipped, so one bad key file
    doesn't take out the whole set."""
    directory = keys_dir or default_keys_dir()
    out: dict[str, Ed25519PublicKey] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.pub")):
        try:
            raw = bytes.fromhex(path.read_text(encoding="utf-8").strip())
            out[path.stem] = load_public_key(raw)
        except (OSError, ValueError):
            continue
    return out
