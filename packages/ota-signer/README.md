# tesserae-ota-signer

A Cloudflare Worker that signs Tesserae OTA firmware descriptors and serves the
images from R2. It co-locates signing with storage: the Ed25519 signing key
never leaves the Worker, and the image it signs is the image it hosts.

Contract: [`docs/ota/contract.md`](../../docs/ota/contract.md) in the main repo.

## What it does

- `POST /sign` (authenticated): body is the raw firmware image; query params
  `device_kind`, `fw_version`, and `key_id` (defaults to the `KEY_ID` var).
  Uploads the image to R2, builds a manifest binding its exact size and
  SHA-256, Ed25519-signs it, and returns `{ descriptor, manifest, image_url }`.
- `GET /firmware/<kind>/<fw>/<sha>.bin` (public): serves the stored image so a
  device can download it. Content-addressed, so it is immutable and cacheable.

The signed bytes match the Python signer (`app/ota/sign.py`), so a descriptor
from either path verifies identically.

## One-time setup

```bash
npm install                                   # installs wrangler
wrangler r2 bucket create tesserae-ota        # or reuse an existing bucket
```

Mint the signing key (private key stays local, only the public key is printed):

```bash
node scripts/mint-key.mjs prod-1
wrangler secret put OTA_SIGNING_KEY < prod-1.key.jwk   # load the private JWK
rm prod-1.key.jwk                                       # then delete it
```

Set the request-signing token and deploy:

```bash
wrangler secret put OTA_SIGN_TOKEN            # a long random string you keep
wrangler deploy
```

Publish the printed public key to the main repo so the server can verify what it
stages, and so firmware can embed it:

```bash
echo <public-key-hex> > ota/keys/prod-1.pub
```

Optionally bind a custom domain (e.g. `fw.tesserae.ink`) to the Worker and set
`IMAGE_BASE_URL` in `wrangler.jsonc` to it, so `image_url` is stable regardless
of route.

## Cut an update

```bash
# Sign + host, and capture the descriptor.
curl -sS -X POST "https://<worker>/sign?device_kind=esp32_client&fw_version=1.4.0" \
  -H "Authorization: Bearer $OTA_SIGN_TOKEN" \
  --data-binary @app.bin | jq .descriptor > d.json

# Stage it on the Tesserae server for a device.
python -m app.ota.stage --data-root data --device-id hall_esp --descriptor d.json
```

The device is offered the update on its next `/status`, verifies the signature
against the embedded `prod-1` key, downloads from `image_url`, checks the digest,
and applies it.

## Key rotation

Mint a new key with a new id (`prod-2`), set `OTA_SIGNING_KEY` to it and bump
`KEY_ID`, publish `ota/keys/prod-2.pub`, and ship firmware that trusts both ids.
Because every manifest carries `key_id`, old and new keys coexist and you can
retire `prod-1` once no device depends on it, without a re-flash in between.
