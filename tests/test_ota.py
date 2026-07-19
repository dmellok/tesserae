"""OTA signed-descriptor contract: sign/verify round-trip + fixture verdicts.

The fixtures (``tests/fixtures/ota/``) double as the artifacts the firmware
builds against, so these tests are the executable form of the contract: they
pin the exact reason code each malformed descriptor trips.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ota import (
    OtaVerificationError,
    build_manifest,
    load_private_key,
    load_public_key,
    sign_manifest,
    verify,
)

FIX = Path(__file__).parent / "fixtures" / "ota"
PUBLIC_KEY = load_public_key(bytes.fromhex((FIX / "test_verify_key.hex").read_text().strip()))
SIGNING_SEED = bytes.fromhex((FIX / "test_signing_key.hex").read_text().strip())
IMAGE = (FIX / "sample_image.bin").read_bytes()


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_valid_fixture_verifies_end_to_end() -> None:
    manifest = verify(_load("valid.json"), PUBLIC_KEY, expected_kind="esp32_client", image=IMAGE)
    assert manifest["fw_version"] == "1.4.0"
    assert manifest["device_kind"] == "esp32_client"
    assert manifest["size_bytes"] == len(IMAGE)


def test_wrong_key_is_bad_signature() -> None:
    with pytest.raises(OtaVerificationError) as exc:
        verify(_load("wrong_key.json"), PUBLIC_KEY)
    assert exc.value.reason == "bad_signature"


def test_truncated_is_malformed() -> None:
    with pytest.raises(OtaVerificationError) as exc:
        verify(_load("truncated.json"), PUBLIC_KEY)
    assert exc.value.reason == "malformed_descriptor"


def test_digest_mismatch_passes_signature_but_fails_image() -> None:
    # Signature + manifest shape are fine, so verifying without the image works.
    manifest = verify(_load("digest_mismatch.json"), PUBLIC_KEY, expected_kind="esp32_client")
    assert manifest["sha256"] == "0" * 64
    # The failure only surfaces once the image is checked against the digest.
    with pytest.raises(OtaVerificationError) as exc:
        verify(_load("digest_mismatch.json"), PUBLIC_KEY, image=IMAGE)
    assert exc.value.reason == "digest_mismatch"


def test_kind_mismatch_is_rejected() -> None:
    with pytest.raises(OtaVerificationError) as exc:
        verify(_load("valid.json"), PUBLIC_KEY, expected_kind="pi_png_client")
    assert exc.value.reason == "kind_mismatch"


def test_already_current_is_rejected() -> None:
    with pytest.raises(OtaVerificationError) as exc:
        verify(_load("valid.json"), PUBLIC_KEY, current_fw="1.4.0")
    assert exc.value.reason == "already_current"


def test_size_mismatch_is_rejected() -> None:
    with pytest.raises(OtaVerificationError) as exc:
        verify(_load("valid.json"), PUBLIC_KEY, image=IMAGE + b"x")
    assert exc.value.reason == "size_mismatch"


def test_sign_verify_round_trip() -> None:
    image = b"hello-ota-round-trip"
    manifest = build_manifest(
        key_id="test-ed25519-1",
        device_kind="esp32_client",
        fw_version="2.0.0",
        image_url="https://cdn.example.test/app-2.0.0.bin",
        image=image,
    )
    descriptor = sign_manifest(manifest, load_private_key(SIGNING_SEED))
    out = verify(
        descriptor, PUBLIC_KEY, expected_kind="esp32_client", current_fw="1.4.0", image=image
    )
    assert out["fw_version"] == "2.0.0"
    assert out["sha256"] == manifest["sha256"]


def test_tampered_payload_after_signing_is_caught() -> None:
    descriptor = _load("valid.json")
    # Flip the payload to a different (validly-encoded) manifest; the signature
    # no longer covers these bytes.
    from app.ota._codec import b64u_encode, serialize_manifest

    forged = serialize_manifest({"schema_version": 1, "device_kind": "esp32_client"})
    descriptor["payload"] = b64u_encode(forged)
    with pytest.raises(OtaVerificationError) as exc:
        verify(descriptor, PUBLIC_KEY)
    assert exc.value.reason == "bad_signature"
