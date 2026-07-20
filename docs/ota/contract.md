# OTA update contract

The wire contract for over-the-air firmware updates: how the server describes a
pending update, how a device verifies it, and where the description travels.
This is the shared boundary between the Tesserae server (which signs) and the
device firmware (which verifies and applies). Discussion: #121.

Status: Phase 1. The signer, verifier, published test key, and signed fixtures
exist (`app/ota/`, `tests/fixtures/ota/`), and the `/status` capability handshake
and descriptor delivery are wired (`app/rest_api.py`, `app/state/ota_staging.py`,
staged via `python -m app.ota.stage`). Still to come: the production key, image
hosting on R2, OTA state reporting, and staged rollout controls.

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

## Capability handshake

A device advertises OTA support by including an `ota` capability object in its
`/register` and `/status` request body, naming the descriptor schema version it
speaks:

```json
{ "...normal heartbeat fields...": "...", "ota": { "schema": 1 } }
```

The server only ever hands back a descriptor to a device that advertised a
schema at least as new as the descriptor's, so OTA is opt-in per device kind and
older firmware is never handed an envelope it can't read.

## Delivery

The descriptor rides on the always-200 `/status` response, not on `/frame`:

```json
{ "...normal status fields...": "...", "ota": { "payload": "...", "signature": "..." } }
```

`ota` is present only when a descriptor is staged for the device, the device
advertised a compatible schema (above), and the descriptor targets the device's
kind; it is absent otherwise. `/frame` keeps its exact 200 / 304 / 204 behaviour,
so the image channel stays byte-clean for every device kind. See #121 for why
`/frame` is the wrong place (non-ESP32 clients poll it for image bytes and would
try to decode a JSON envelope as an image).

Re-offering an update the device already applied is harmless: the firmware's
`already_current` check skips a descriptor naming the running version, so the
server keeps offering a staged descriptor until it is cleared.

## Staging

An operator stages a signed descriptor for a device with the signer + stager:

```bash
python -m app.ota.sign --key-id … --device-kind esp32_client \
    --fw-version 1.4.0 --image app.bin --image-url … --key signing.hex > d.json
python -m app.ota.stage --data-root data --device-id hall_esp --descriptor d.json
# clear:  python -m app.ota.stage --data-root data --device-id hall_esp --clear
```

The store is `<data-root>/core/ota_pending.json`; the server reads it on the next
heartbeat, no restart needed. An admin UI for this is a later slice.

## Keys and signing

- Signing: Ed25519, keyed by `key_id` so keys can rotate. Two signers produce
  byte-identical output: `app/ota/sign.py` (the `python -m app.ota.sign` CLI,
  for local/test signing) and the Cloudflare signing Worker
  (`packages/ota-signer/`, the automated production path, which also hosts the
  image on R2).
- Production keys are minted in the Worker
  (`packages/ota-signer/scripts/mint-key.mjs`): the private key stays in the
  Worker's `OTA_SIGNING_KEY` secret and never leaves it; only the public key is
  published.
- Published **public** keys live in `ota/keys/<key_id>.pub` (hex). `python -m
  app.ota.stage` verifies a descriptor against the key matching its `key_id`
  before staging, so a mis-signed descriptor is refused at staging time. This is
  a staging-side gate; the firmware embeds its own key set and is the real trust
  anchor.
- `tests/fixtures/ota/` carries a **test-only** keypair (published seeds, key_id
  `test-ed25519-1`) and four signed fixtures (`valid`, `wrong_key`, `truncated`,
  `digest_mismatch`) so firmware can self-test the verifier. See that
  directory's `README.md` for the per-fixture expected verdicts.

### Rotation

Every manifest carries `key_id`, and firmware trusts a keyed set
`{key_id: pubkey}` rather than a single hardcoded key. To rotate: mint a new key
with a new id, point the Worker at it, publish its `.pub`, and ship firmware that
trusts both ids. Old and new keys coexist, so a key can be retired once no device
depends on it without a re-flash in between.

## Rollback

The device marks the new app valid on **local** startup checks (correct running
partition, NVS readable, Wi-Fi associates) and reports `completed` on the next
heartbeat as telemetry. A server heartbeat is deliberately **not** part of the
acceptance gate: a briefly-offline self-hosted server must not roll back healthy
firmware. If server reachability is ever added to the gate, it comes with a
bounded retry policy.
