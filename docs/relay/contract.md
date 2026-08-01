# Cloud relay contract

The wire contract for running a panel at a remote location without exposing the
home instance to the internet. A small cloud **relay** is a per-device mailbox
that both ends reach *outbound*: the home instance seals each rendered frame and
`PUT`s it; the remote panel polls and decrypts it. This is the shared boundary
between the Tesserae server, the relay Worker, and the device firmware.

Status: `app/relay_crypto.py` (frame sealing + key derivation) and its
fixed-vector tests exist. The relay Worker (`packages/relay/`), the home-side
transport (`app/relay_publisher.py`, `app/relay_pairing.py`,
`app/relay_client.py`), and the Settings UI are the rest of the feature. This
document is authoritative for all three sides; the golden vectors below are
load-bearing.

## Roles and trust model

- **Home instance** renders locally, holds the per-device frame key, seals each
  frame, and uploads it to the relay. Outbound HTTPS only, never accepts an
  inbound connection.
- **Relay** (Cloudflare Worker + a Durable Object per device + R2) stores the
  latest **sealed** frame per device and brokers pairing. It authenticates both
  ends but is **zero-knowledge**: it holds ciphertext, both public keys, and the
  read token, never the frame key.
- **Panel firmware** derives the frame key at pairing, polls its mailbox, and
  decrypts. Holds the relay URL, its device token, and its frame key in NVS.

The frame key is an X25519 + HKDF shared secret that is **never transmitted**.
The relay therefore cannot decrypt a dashboard even though it can validate a
poll.

## Base URL and versioning

All routes are under `/v1`. The hosted relay is `https://relay.tesserae.ink`;
a self-hosted Worker uses its own origin (`packages/relay/`). The relay base URL
is per-install configuration on the home side, so the two are interchangeable.

## Authentication

Bearer tokens in `Authorization: Bearer <token>`, compared timing-safe. The
relay stores only `sha256(token)`.

- **Publisher token** — issued to a home install at registration; authorizes
  everything an install does (pairing broker, frame upload, revoke) under its
  own `install_id`.
- **Device token** — minted by the home instance per remote panel, forwarded to
  the panel once through the relay at pairing; authorizes exactly one mailbox
  (`GET .../frame`).

Unknown / revoked token → `401`. Token valid but for the wrong install or device
→ `403`.

## Errors

```json
{ "error": { "code": "not_found", "message": "…" } }
```

Closed `code` set: `invalid_request`, `unauthorized`, `forbidden`, `not_found`,
`pairing_expired`, `conflict`.

## Install registration

```
POST /v1/install/register
Body: { "install_pubkey": "<base64url X25519, 32 bytes>", "label": "<optional>" }
→ 201 { "install_id": "<opaque>", "publisher_token": "<token, shown once>" }
```

An **entitlement hook** runs first (default: allow). A gated relay can require a
Sponsors/paid check here without any contract change; a rejection is `403`.

## Pairing (remote rendezvous)

The panel never reaches the home LAN. Pairing is brokered through the relay:

```
  Home                         Relay                          Panel
   │  POST pair/codes  ───────▶ │                               │
   │  ◀── { code }              │                               │
   │  (show code + relay URL)   │                               │
   │                            │ ◀── POST /v1/pair             │
   │                            │     { code, panel_pubkey }    │
   │                            │ ── 202 (pending) ──▶          │
   │  GET pair/pending ───────▶ │                               │
   │  ◀── [{ code, panel_pubkey }]                              │
   │  (ECDH → frame key, mint token)                            │
   │  POST pair/<code>/complete ▶│                              │
   │     { device_id, device_token, device_token_sha256,        │
   │       home_pubkey, config } │                              │
   │                            │ ◀── GET /v1/pair/<code>       │
   │                            │ ── { home_pubkey, device_token,│
   │                            │      device_id, config } ──▶  │
   │                            │ (relay drops plaintext token,  │
   │                            │  keeps only the hash)          │
```

### Endpoints

```
POST /v1/i/<install>/pair/codes            (publisher)
→ 201 { "code": "<6+ chars>", "expires_at": "<iso8601>" }

POST /v1/pair                              (unauthenticated)
Body: { "code": "<code>", "panel_pubkey": "<base64url X25519>" }
→ 202 { "status": "pending" }   (unknown/expired code → 404 pairing_expired)

GET  /v1/i/<install>/pair/pending          (publisher)
→ 200 { "pending": [ { "code": "…", "panel_pubkey": "…" } ] }

POST /v1/i/<install>/pair/<code>/complete  (publisher)
Body: { "device_id": "<id>", "device_token": "<token>",
        "device_token_sha256": "<hex>", "home_pubkey": "<base64url X25519>",
        "config": { … } }
→ 200 {}

GET  /v1/pair/<code>                        (unauthenticated, poll)
→ 200 { "status": "ready", "home_pubkey": "…", "device_token": "…",
        "device_id": "…", "config": { … } }
   or 200 { "status": "pending" }
```

The plaintext `device_token` transits the relay exactly once (in `complete`,
returned once from `GET /v1/pair/<code>`) so it can reach the panel; the relay
then retains only `device_token_sha256` for future poll validation. The frame
key is **not** part of pairing: both sides derive it from the exchanged public
keys.

Codes are single-use and expire (default 10 minutes). Pairing state is
short-lived; mailbox state is not.

## Frames

```
PUT /v1/i/<install>/d/<device>/frame       (publisher)
Headers: ETag: "<render digest>"
         X-Tesserae-Panel-W, X-Tesserae-Panel-H, X-Tesserae-Format,
         X-Tesserae-Renderer, X-Tesserae-Meta (base64url JSON of the render
         payload: rotate/scale/bg/saturation/palette_signature/…)
Body: the sealed frame (application/octet-stream)
→ 200 {}   (same ETag already stored → 200, treated as idempotent)

GET /v1/i/<install>/d/<device>/frame        (device token)
Headers: If-None-Match: "<last etag>"
→ 304                        (unchanged)
   204                        (no frame yet)
   200 + sealed body, with ETag + the same X-Tesserae-* metadata headers
```

The frame **body** is opaque to the relay. The metadata headers are
**plaintext** (panel dimensions and render hints are not sensitive); only the
image is sealed. A firmware that wants everything sealed can ignore the headers
and read the dimensions from the decrypted payload instead.

### Sealed-frame format

```
sealed = nonce (12 bytes) || AES-256-GCM(frame_bytes, key, nonce, aad="")
```

The 16-byte GCM tag is appended by the cipher, so `len(sealed) == 12 + len(frame)
+ 16`. The nonce is random per seal.

## Key derivation

X25519 ECDH, then HKDF-SHA256 to the 32-byte AES key:

```
shared = X25519(our_private, their_public)          # 32 bytes
frame_key = HKDF-SHA256(ikm=shared, salt=<none>, info="tesserae-relay-frame-key-v1", L=32)
```

Both sides derive the same key: `derive(home_priv, panel_pub) ==
derive(panel_priv, home_pub)`. Public/private keys are raw 32-byte X25519,
base64url (no padding) on the wire.

## Golden vectors

Firmware and any reimplementation must reproduce these exactly. Generated from
fixed private scalars (`home_priv = 0x01·32`, `panel_priv = 0x02·32`); see
`tests/test_relay_crypto.py`.

```
home_pubkey   = a4e09292b651c278b9772c569f5fa9bb13d906b46ab68c9df9dc2b4409f8a209
panel_pubkey  = ce8d3ad1ccb633ec7b70c17814a5c76ecd029685050d344745ba05870e587d59
frame_key     = 613376ae6bc97931b6d33c17aaf561fb2ff2e2f12937249705e1e5b75dd98e83

nonce         = 00112233445566778899aabb
frame (hex)   = 74657373657261652d6672616d652d0001027061796c6f6164
sealed (hex)  = 00112233445566778899aabb4fa3210211222b8aa47f010d2a30fc71c
                70e6de8d4345adf90c057a2b39262b5ab8cee9507d7839ce8
```

(`sealed` is the two lines concatenated.)

## Firmware responsibilities

1. Accept a relay base URL + pairing code (captive portal / companion app).
2. Generate an X25519 keypair; `POST /v1/pair { code, panel_pubkey }`; poll
   `GET /v1/pair/<code>` until `ready`.
3. Derive `frame_key` from `home_pubkey`; store `relay_url`, `device_token`,
   `frame_key` in NVS.
4. On the normal wake cadence: `GET .../frame` with `If-None-Match`; on `200`,
   `unseal` the body with `frame_key` and paint; on `304`/`204`, keep the
   current image. No long-poll in v1.

The panel talks only to the relay in steady state; it never needs the home
instance's address.
