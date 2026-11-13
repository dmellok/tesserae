# OTA update contract

This contract covers pending firmware updates, verification and delivery.
The server signs updates; device firmware verifies and applies them. Discussion: #121.

The update system is live:

- Signing, verification, a published test key and signed fixtures are in
  `app/ota/` and `tests/fixtures/ota/`.
- `/status` handles capability checks and descriptor delivery through
  `app/rest_api.py` and `app/state/ota_staging.py`. Stage updates with `python -m app.ota.stage`.
- The production signing Worker and R2 image hosting are live.
  `python -m app.ota.release` controls releases per device kind.
- Device reports appear as a Devices-card chip; each lifecycle change goes to the event log.
  See `app/ota/report.py` and `app/transport_wiring.py`.
- Settings → Firmware lists devices and uses the same release state as the CLI
  (`app/settings/firmware_routes.py`). Check for updates, then queue or withdraw
  each device. The queue is the release store's per-device offer set, one-shot per release version.
- Each device's "Auto" switch opts into every new release (`app/ota/auto.py`, checked on heartbeat).
  A paused release is never auto-resumed.
- Canary, promote and pause controls are in the collapsed Advanced section and the `python -m app.ota.release` CLI.

## Roles

- Server / pipeline holds the private signing key, builds the manifest,
  signs it, hosts the image, and hands the descriptor to a device on `/status`.
- Device firmware holds the *public* verification key, verifies the
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

The signed `size_bytes` and `sha256` identify the image as well as its metadata.
A device can stream to flash while hashing, then compare the digest once.
It needs no image buffer in RAM.

## Verification order

The firmware performs, and the reference verifier (`app/ota/verify.py`) mirrors,
these checks in order. Each stop has a stable reason code:

1. Signature over the raw decoded payload bytes. Fail → `bad_signature`
   (or `malformed_descriptor` if the fields don't base64url-decode / the
   signature isn't 64 bytes).
2. Parse + shape: JSON object, all manifest keys present, `schema_version`
   recognised. Fail → `malformed_manifest` / `schema_version`.
3. Target: `device_kind` matches this device; `fw_version` is not the
   version already running. Fail → `kind_mismatch` / `already_current`.
   Whether a version actually *advances* (semver ordering, downgrade policy) is
   the firmware's decision; the contract only refuses a no-op.
4. Image, after download: exact `size_bytes`, then `sha256`. Fail →
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

## State reporting

A device reports the outcome of its OTA lifecycle back to the server by adding
report fields to the same `ota` object it already sends for the capability
handshake:

```json
{ "...normal heartbeat fields...": "...",
  "ota": {
    "schema": 1,
    "phase": "confirmed",
    "reason": "ok",
    "target_fw": "1.6.0",
    "attempt_id": "a3f19c",
    "detail": ""
  }
}
```

`schema` is unchanged and stays required (it is the capability advertisement the
server gates delivery on). The report fields are all optional, so existing
firmware that sends only `{"schema": 1}` keeps working and simply reports no
state.

| Field | Type | Meaning |
| --- | --- | --- |
| `phase` | string | Current lifecycle phase (enum below). Absent → treated as `idle`. |
| `reason` | string | Stable outcome code for a terminal phase; `ok` on success. Ignored for in-progress phases. |
| `target_fw` | string | The `fw_version` of the descriptor this report is about. Lets the server correlate the report to what it offered (kind + `target_fw`). Absent when `idle`. |
| `attempt_id` | string | Opaque, device-chosen id, stable across the multi-wake life of one attempt (a counter, or the descriptor `sha256` prefix). Lets the server dedup progress beats and tie a `rolled_back` back to the `downloading` that preceded it. Optional. |
| `detail` | string | Short human context for logs (e.g. `http 500 on image fetch`). Never parsed for logic; capped at 200 bytes server-side. |

### Phases

Coarse enough that a device need only heartbeat at phase boundaries, not
mid-transfer. A device that sleeps through the whole apply in one wake reports
only the terminal phase.

| `phase` | Meaning | Terminal |
| --- | --- | --- |
| `idle` | Running confirmed firmware, nothing in progress. | – |
| `downloading` | Streaming the image to the inactive slot. | no |
| `validating` | Post-download size + `sha256` + image-validity checks. | no |
| `pending_confirm` | New image written and booted, awaiting first-boot confirmation. | no |
| `confirmed` | First boot succeeded, new slot marked valid. Success. | yes |
| `rejected` | Descriptor refused before any download. | yes |
| `failed` | Attempt failed during or after download. | yes |
| `rolled_back` | Booted the new image, the first-boot gate failed, reverted to the previous slot. | yes |

Verification (signature, shape, target) is fast and pre-download, so its
failures surface as `rejected` with the reason rather than a phase of their own.

### Reason codes

`reason` is meaningful only on a terminal phase. All lowercase snake_case;
the pre-download codes reuse the verifier's reasons (see **Verification order**).

- `confirmed` → `ok`.
- `rejected` → `bad_signature`, `malformed_descriptor`, `malformed_manifest`,
  `schema_version`, `kind_mismatch`, `already_current`, `battery_low`,
  `battery_unknown`.
- `failed` → `download_error`, `size_mismatch`, `digest_mismatch`,
  `image_invalid`, `flash_error`.
- `rolled_back` → `boot_failed`.

`battery_low` / `battery_unknown` are the apply-time gate (a device refuses to
flash below its safe threshold, or when it can't read the level); the rest of
the apply-time codes name where the write or boot broke.

### Server handling

The report is advisory: the server records it and surfaces it, but a device's
own local checks remain the acceptance gate (see **Rollback**). On each
`/status` the server:

- stores the latest report on the device's live status so the Devices card can
  show a chip (`OTA: confirmed 1.6.0`, `OTA: rolled_back (digest_mismatch)`);
- appends an event-log entry when the report *changes* (`phase`, `reason`,
  `target_fw`, or `attempt_id` differs from the last stored one), so a device
  re-sending the same terminal report every heartbeat logs once, not every wake;
- (later slice) uses a canary's terminal outcome to inform rollout: a
  `confirmed` from every canary is the signal to `promote`; a `rolled_back` or
  `failed` pauses the release. This stays a manual decision until the reporting
  path has real fleet data behind it.

## OTA logs

A client that installs updates sends its own log of each attempt, without
waiting for the server to ask, as a log batch on the existing upload route:

```
POST /api/v1/device/<id>/log
Content-Type: text/plain; charset=utf-8
X-Tesserae-Log-Kind: ota
X-Tesserae-Ota-Phase: failed            (optional: the report phase)
X-Tesserae-Ota-Target: 1.63.1           (optional: target_fw)
X-Tesserae-Ota-Attempt: 98980c          (optional: attempt_id)
```

The body is the same redacted UTF-8 log text as a routine upload, capped at
64 KB. The server stores `ota-<UTC stamp>.log` separately from routine batches
and shows it under **OTA logs** on the device page.

It keeps the newest 10 OTA logs, up to 512 KB per device.
An unknown `X-Tesserae-Log-Kind` is stored as a routine batch.
A client that never advertised `logs.schema` still gets its OTA logs stored and shown.

When to send one:

1. The attempt ends in the same wake: any `rejected` from the apply-time
   gate (`battery_low`, `battery_unknown`) or any `failed`. Send the log of
   that wake, while the network is still up.
2. Before the reboot into a new image: the download, the checks and the
   slot switch. A client that reboots before it could send keeps the log and
   sends it on the next boot.
3. The first boot after the switch resolves: `confirmed` or
   `rolled_back`. Send that boot's log, up to the point it reported, plus
   whatever survived from the boot that failed.

Send at most one OTA log per change of (`attempt_id`, `phase`, `reason`), so
a panel that refuses `battery_low` on every wake uploads once, not every
wake. Pre-download `rejected` reasons other than the battery gate
(`already_current`, `kind_mismatch`, a bad signature) are not an attempt and
send nothing. A failed upload is retried on a later wake until one succeeds
or the next change replaces it. An OTA log is best-effort and never blocks or
delays the install, the reboot or the report.

## Staging

An operator stages a signed descriptor for a device with the signer + stager:

```bash
python -m app.ota.sign --key-id … --device-kind esp32_client \
    --fw-version 1.4.0 --image app.bin --image-url … --key signing.hex > d.json
python -m app.ota.stage --data-root data --device-id hall_esp --descriptor d.json
# clear:  python -m app.ota.stage --data-root data --device-id hall_esp --clear
```

The store is `<data-root>/core/ota_pending.json`; the server reads it on the next
heartbeat without a restart. This per-device store has no admin UI; Settings →
Firmware writes the release store below.

## Rollout (per kind)

Staging targets one device; a **release** targets a device kind, with a manual
promote + canary flow so a bad build can't hit the whole fleet at once:

```bash
python -m app.ota.release set --data-root data --descriptor d.json --canary hall_esp
python -m app.ota.release promote --data-root data --kind seeed_reterminal_e1001
# pause / clear / list also available
```

`set` reads the target kind and firmware version from the descriptor (verified
first) and offers it only to the listed canary devices; `promote` extends it to
every device of the kind; `pause` stops offering it. On `/status` a device is
offered its kind's release when it is eligible (canary or promoted) and the
release firmware is strictly newer than the version it reports (`fw_version`),
so an already-updated device isn't re-offered. A per-device staged descriptor
(above) takes precedence over the kind release. Store:
`<data-root>/core/ota_releases.json`.

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
  a staging-side gate; the firmware embeds its own trusted key set and is the real trust anchor.
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
partition, NVS readable, Wi-Fi associates) and reports the `confirmed` phase on
the next heartbeat as telemetry (see **State reporting**). A server heartbeat is
deliberately **not** part of the acceptance gate: a briefly-offline self-hosted
server must not roll back healthy firmware. If server reachability is ever added
to the gate, it comes with a bounded retry policy.
