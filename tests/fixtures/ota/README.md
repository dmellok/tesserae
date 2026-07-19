# OTA test fixtures

Signed-descriptor fixtures for the OTA update contract (`docs/ota/contract.md`).
The firmware can build and self-test against these before the live signing
pipeline exists. Regenerate with `python tests/fixtures/ota/generate.py`
(byte-reproducible).

## Keys (TEST ONLY, published seeds, not production)

- `test_signing_key.hex` — 32-byte Ed25519 seed, `00 01 02 … 1f`.
- `test_verify_key.hex` — matching raw public key:
  `03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8`
  (base64url `A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg`).

Production firmware embeds a **different** public key, published separately;
these seeds are in the repo only so the fixtures are reproducible and anyone can
verify them.

## Image

- `sample_image.bin` — 1152 bytes of fixed content; the `valid` manifest binds
  its size and SHA-256.

## Descriptors and expected verdicts

| File | Verifies? | Failing gate (`OtaVerificationError.reason`) |
| --- | --- | --- |
| `valid.json` | yes | — (manifest returned; digest matches `sample_image.bin`) |
| `wrong_key.json` | no | `bad_signature` (signed with a different key) |
| `truncated.json` | no | `malformed_descriptor` (signature not 64 bytes) |
| `digest_mismatch.json` | no | `digest_mismatch` (signature valid, `sha256` ≠ image) |

`digest_mismatch.json` passes the signature and manifest-shape checks; it only
trips once the image is supplied, which is the point: the signature binds the
metadata, and the digest binds the image to that metadata.
