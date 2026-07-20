# OTA trusted signing keys

Published Ed25519 **public** keys, one file per key, named `<key_id>.pub` and
holding the raw 32-byte public key as hex. `python -m app.ota.stage` verifies a
descriptor's signature against the key matching its `key_id` before staging it,
so a corrupt or mis-signed descriptor is refused at staging time.

This is a **staging-side** sanity gate. The real trust anchor is the firmware,
which embeds its own key set; a device rejects anything it can't verify against
the keys it ships with, regardless of what the server staged.

Private keys never live here. Production signing keys are minted and held in the
signing Worker (`packages/ota-signer/`); only their public half is published.

- `test-ed25519-1.pub` — the published test key (its seed is in
  `tests/fixtures/ota/`, non-production, for fixtures and dev staging).
- `prod-1.pub` — add after minting the production key
  (`packages/ota-signer/scripts/mint-key.mjs`).
