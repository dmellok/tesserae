"""End-to-end frame sealing for the cloud relay (remote-panel feature).

The relay is a store-and-forward mailbox that never holds the key: it stores
only opaque ciphertext. At pairing the home instance and the panel each
generate an X25519 keypair, exchange *public* keys through the relay, and derive
the same 32-byte frame key by ECDH + HKDF-SHA256. Every rendered frame is then
sealed as::

    nonce (12 bytes) || AES-256-GCM(frame_bytes)

so the relay (which sees both public keys and the read token but never the
derived key) can never decrypt a dashboard.

Wire contract: ``docs/relay/contract.md``. The firmware reproduces
:func:`derive_shared_key` and :func:`unseal`, so the byte layout here *is* the
contract; ``tests/test_relay_crypto.py`` pins it with fixed vectors.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# AES-GCM standard nonce length; AES-256 key length.
NONCE_LEN: int = 12
KEY_LEN: int = 32

# HKDF domain separation. Bump the suffix if the derivation ever changes in a
# way old firmware can't follow (a new value yields a different key, so both
# sides must move together).
_HKDF_INFO: bytes = b"tesserae-relay-frame-key-v1"


def generate_keypair() -> tuple[bytes, bytes]:
    """A fresh X25519 keypair as ``(private_raw, public_raw)``, 32 bytes each."""
    private = X25519PrivateKey.generate()
    return private.private_bytes_raw(), private.public_key().public_bytes_raw()


def public_key_for(private_raw: bytes) -> bytes:
    """The 32-byte X25519 public key for a raw private key."""
    return X25519PrivateKey.from_private_bytes(private_raw).public_key().public_bytes_raw()


def derive_shared_key(our_private: bytes, their_public: bytes) -> bytes:
    """Derive the 32-byte frame key from our X25519 private key and the peer's
    public key.

    HKDF-SHA256 over the raw ECDH output with a fixed info string, so both ends
    agree without exchanging anything secret. Symmetric:
    ``derive(a_priv, b_pub) == derive(b_priv, a_pub)``.
    """
    private = X25519PrivateKey.from_private_bytes(our_private)
    public = X25519PublicKey.from_public_bytes(their_public)
    shared = private.exchange(public)
    hkdf = HKDF(algorithm=hashes.SHA256(), length=KEY_LEN, salt=None, info=_HKDF_INFO)
    return hkdf.derive(shared)


def seal(frame: bytes, key: bytes, *, nonce: bytes | None = None) -> bytes:
    """Seal ``frame`` as ``nonce || AES-256-GCM(frame)``.

    A random 12-byte nonce is generated unless one is supplied (tests pass a
    fixed nonce to pin the wire format). The GCM tag is appended by the cipher,
    so the sealed blob is ``NONCE_LEN + len(frame) + 16`` bytes.
    """
    if len(key) != KEY_LEN:
        raise ValueError(f"frame key must be {KEY_LEN} bytes, got {len(key)}")
    if nonce is None:
        nonce = os.urandom(NONCE_LEN)
    elif len(nonce) != NONCE_LEN:
        raise ValueError(f"nonce must be {NONCE_LEN} bytes, got {len(nonce)}")
    return nonce + AESGCM(key).encrypt(nonce, frame, None)


def unseal(blob: bytes, key: bytes) -> bytes:
    """Inverse of :func:`seal`.

    Raises ``cryptography.exceptions.InvalidTag`` if the blob was tampered with
    or the key is wrong, and ``ValueError`` if it's too short to hold a nonce.
    """
    if len(key) != KEY_LEN:
        raise ValueError(f"frame key must be {KEY_LEN} bytes, got {len(key)}")
    if len(blob) < NONCE_LEN:
        raise ValueError("sealed blob is too short to contain a nonce")
    nonce, ciphertext = blob[:NONCE_LEN], blob[NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)
