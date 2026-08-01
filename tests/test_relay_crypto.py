"""Frame-sealing crypto for the cloud relay.

The byte layout here is the firmware contract (docs/relay/contract.md), so the
golden-vector tests below are load-bearing: if they change, every paired panel's
decrypt breaks. The vectors were generated from the fixed private scalars below;
regenerate deliberately, never to "make the test pass."
"""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from app.relay_crypto import (
    KEY_LEN,
    NONCE_LEN,
    derive_shared_key,
    generate_keypair,
    public_key_for,
    seal,
    unseal,
)

# Fixed scalars → reproducible public keys and derived key.
_HOME_PRIV = bytes.fromhex("01" * 32)
_PANEL_PRIV = bytes.fromhex("02" * 32)
_HOME_PUB = bytes.fromhex("a4e09292b651c278b9772c569f5fa9bb13d906b46ab68c9df9dc2b4409f8a209")
_PANEL_PUB = bytes.fromhex("ce8d3ad1ccb633ec7b70c17814a5c76ecd029685050d344745ba05870e587d59")
_SHARED_KEY = bytes.fromhex("613376ae6bc97931b6d33c17aaf561fb2ff2e2f12937249705e1e5b75dd98e83")
_NONCE = bytes.fromhex("00112233445566778899aabb")
_FRAME = b"tesserae-frame-\x00\x01\x02payload"
_SEALED = bytes.fromhex(
    "00112233445566778899aabb4fa3210211222b8aa47f010d2a30fc71c70e6de8d4345adf90c057a2b39262b5ab8cee9507d7839ce8"
)


def test_public_key_derivation_is_pinned() -> None:
    assert public_key_for(_HOME_PRIV) == _HOME_PUB
    assert public_key_for(_PANEL_PRIV) == _PANEL_PUB


def test_ecdh_both_sides_derive_the_same_key() -> None:
    home = derive_shared_key(_HOME_PRIV, _PANEL_PUB)
    panel = derive_shared_key(_PANEL_PRIV, _HOME_PUB)
    assert home == panel == _SHARED_KEY
    assert len(home) == KEY_LEN


def test_seal_matches_golden_vector() -> None:
    # Fixed nonce → byte-identical to the pinned blob (the firmware contract).
    assert seal(_FRAME, _SHARED_KEY, nonce=_NONCE) == _SEALED
    assert len(_SEALED) == NONCE_LEN + len(_FRAME) + 16


def test_unseal_round_trips_the_golden_vector() -> None:
    assert unseal(_SEALED, _SHARED_KEY) == _FRAME


def test_seal_uses_a_random_nonce_by_default() -> None:
    a = seal(_FRAME, _SHARED_KEY)
    b = seal(_FRAME, _SHARED_KEY)
    assert a[:NONCE_LEN] != b[:NONCE_LEN]  # random nonce each call
    assert unseal(a, _SHARED_KEY) == unseal(b, _SHARED_KEY) == _FRAME


def test_unseal_rejects_a_tampered_blob() -> None:
    tampered = bytearray(_SEALED)
    tampered[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        unseal(bytes(tampered), _SHARED_KEY)


def test_unseal_rejects_the_wrong_key() -> None:
    wrong = bytes.fromhex("ff" * 32)
    with pytest.raises(InvalidTag):
        unseal(_SEALED, wrong)


def test_generated_keypairs_interoperate() -> None:
    a_priv, a_pub = generate_keypair()
    b_priv, b_pub = generate_keypair()
    assert len(a_priv) == 32 and len(a_pub) == 32
    assert derive_shared_key(a_priv, b_pub) == derive_shared_key(b_priv, a_pub)


@pytest.mark.parametrize("bad", [b"", b"short", b"x" * (KEY_LEN - 1), b"x" * (KEY_LEN + 1)])
def test_seal_rejects_wrong_key_length(bad: bytes) -> None:
    with pytest.raises(ValueError):
        seal(_FRAME, bad)
