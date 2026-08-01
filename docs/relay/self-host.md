# Self-host the relay

The [remote-panel feature](../install/remote-panel.md) can use the hosted
`relay.tesserae.ink`, or your own relay running in your own Cloudflare account.
Self-hosting keeps the whole path under your control; the relay is
zero-knowledge either way, so it only ever sees encrypted frames.

## Deploy

The Worker source lives in `packages/relay/` in the Tesserae repo. You need a
Cloudflare account and [`wrangler`](https://developers.cloudflare.com/workers/wrangler/).

```sh
cd packages/relay
npm install
wrangler r2 bucket create tesserae-relay   # one-time
wrangler deploy
```

`wrangler deploy` prints your Worker URL (e.g.
`https://tesserae-relay.<you>.workers.dev`). Bind a custom domain in the
Cloudflare dashboard for a stable hostname if you like.

## Point Tesserae at it

**Settings → Cloud relay**, set the relay URL to your Worker's origin, and
**Register this install**. Everything else (adding a remote panel, pairing) is
the same as with the hosted relay.

## Cost

One R2 bucket holds the sealed frames and the small install/token/pairing
records. A panel polling every 15–60 minutes is a few requests per day and a few
KB per frame, well inside Cloudflare's free tier. There are no Durable Objects
(v1 is scheduled-poll-only and R2 is strongly consistent), so no paid Workers
plan is required.

## Storage cleanup

Frame blobs are self-cleaning: each upload deletes the frame it supersedes, so a
device's mailbox holds only its latest sealed frame. The short-lived pairing
records (the `code/` and `pair/` prefixes) are checked for expiry on read but not
deleted, so add an R2 **lifecycle rule** to expire those prefixes after a day:

```sh
# Cloudflare dashboard: R2 → tesserae-relay → Settings → Object lifecycle rules
#   delete objects under prefix "code/" older than 1 day
#   delete objects under prefix "pair/" older than 1 day
```

Nothing else grows unbounded; frame and token records are one-per-device.

## Analytics (optional)

The Worker writes aggregate counts (frames pushed, mailboxes created / removed)
to a Workers Analytics Engine dataset for a dashboard. It's fire-and-forget and
a no-op if you remove the `analytics_engine_datasets` binding from
`wrangler.jsonc`. No frame content is involved (it's sealed); the recorded ids
are opaque. Query it via the Analytics Engine SQL API, sample queries are in
`packages/relay/README.md`.

## Restricting who can register (hosted operators)

If you run a relay for other people, override `checkEntitlement(env, request)`
in `packages/relay/src/index.js` to add a Sponsors or billing check before an
install may register. It defaults to allow; a rejection is returned as `403`.

See `packages/relay/README.md` for the same steps alongside the source.
