# OTA update contract

The wire contract for over-the-air firmware updates: how the server describes a
pending update, how a device verifies it, and where the description travels.
This is the shared boundary between the Tesserae server (which signs) and the
device firmware (which verifies and applies). Discussion: #121.

Status: Phase 1. The signer, verifier, published test key, and signed fixtures
exist (`app/ota/`, `tests/fixtures/ota/`). The `/status` delivery and capability
handshake land in a following slice; this document is the frozen part both sides
build against.

## Roles

- **Server / pipeline** holds the private signing key, builds the manifest,
  signs it, hosts the image, and hands the descriptor to a device on `/status`.
- **Device firmware** holds the *public* verification key, verifies the
  descriptor, streams the image, checks it against the signed digest, applies
  it to the inactive slot, and reboots.

The private key never leaves the signing host. Only the descriptor and the
published public key reach devices.

## Descriptor

A descriptor is a JSON object:

```json
{
  "payload": "<base64url, no padding>",
  "signature": "<base64url, no padding>"
}
```

- `payload` is the base64url of the **exact manifest JSON bytes**.
- `signature` is the base64url of the **Ed25519 signature over those same raw
  bytes** (64-byte signature).

The signature covers the raw payload bytes, so neither side reproduces a
canonical JSON encoding. The device verifies the signature over the decoded
bytes **before** parsing them, so untrusted input never reaches the JSON parser.

## Manifest

The decoded `payload` is a JSON object with every one of these keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `schema_version` | int | Contract version. Current: `1`. A device rejects a version it doesn't know. |
| `key_id` | string | Which signing key produced this. Lets keys rotate. |
| `device_kind` | string | Target kind, e.g. `esp32_client`. A device rejects a kind that isn't its own. |
| `fw_version` | string | Version this image installs. |
| `image_url` | string | Where the device fetches the image. |
| `size_bytes` | int | Exact image length. |
| `sha256` | string | Lowercase hex SHA-256 of the image. |

`size_bytes` and `sha256` are inside the signed payload, so the signature binds
the metadata **and** the image identity: a device can stream the image straight
to flash while hashing, and a single digest comparison at the end proves the
bytes match what was signed, without buffering the image in RAM.

## Verification order

The firmware performs, and the reference verifier (`app/ota/verify.py`) mirrors,
these checks in order. Each stop has a stable reason code:

1. **Signature** over the raw decoded payload bytes. Fail → `bad_signature`
   (or `malformed_descriptor` if the fields don't base64url-decode / the
   signature isn't 64 bytes).
2. **Parse + shape**: JSON object, all manifest keys present, `schema_version`
   recognised. Fail → `malformed_manifest` / `schema_version`.
3. **Target**: `device_kind` matches this device; `fw_version` is not the
   version already running. Fail → `kind_mismatch` / `already_current`.
   Whether a version actually *advances* (semver ordering, downgrade policy) is
   the firmware's decision; the contract only refuses a no-op.
4. **Image**, after download: exact `size_bytes`, then `sha256`. Fail →
   `size_mismatch` / `digest_mismatch`.

A device verifies checks 1-3, decides to fetch, then re-checks 4 as the image
streams in. `app/ota/verify.py:verify()` takes an optional `image=` to match
that split.

## Delivery (following slice)

The descriptor rides on the always-200 `/status` response, not on `/frame`:

```json
{ "...normal status fields...": "...", "ota": { "payload": "...", "signature": "..." } }
```

`ota` is absent when no update is pending. `/frame` keeps its exact 200 / 304 /
204 behaviour, so the image channel stays byte-clean for every device kind. See
#121 for why `/frame` is the wrong place (non-ESP32 clients poll it for image
bytes and would try to decode a JSON envelope as an image).

Delivery is gated on a capability the firmware advertises at register/status, so
the server only ever emits `ota` to a device that has declared it can apply one.
This makes OTA opt-in per device kind. Exact capability field: TBD in that slice.

## Keys and signing

- Signing: Ed25519. `app/ota/sign.py` builds and signs a manifest; the CLI
  (`python -m app.ota.sign …`) is the seed of the pipeline.
- The production public key is published separately and embedded in firmware.
- `tests/fixtures/ota/` carries a **test-only** keypair (published seeds) and
  four signed fixtures (`valid`, `wrong_key`, `truncated`, `digest_mismatch`)
  so firmware can self-test the verifier before the live pipeline exists. See
  that directory's `README.md` for the per-fixture expected verdicts.

## Rollback

The device marks the new app valid on **local** startup checks (correct running
partition, NVS readable, Wi-Fi associates) and reports `completed` on the next
heartbeat as telemetry. A server heartbeat is deliberately **not** part of the
acceptance gate: a briefly-offline self-hosted server must not roll back healthy
firmware. If server reachability is ever added to the gate, it comes with a
bounded retry policy.
