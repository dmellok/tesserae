# Self-host the relay

Use the [remote-panel feature](../install/remote-panel.md) with
`relay.tesserae.ink` or your own relay. Self-hosting puts the whole path under
your control. Either relay sees only encrypted frames.

Both options run the same `src/index.js`:

| | [Cloudflare Worker](#option-a-cloudflare-worker) | [Container](#option-b-container-no-cloudflare) |
|---|---|---|
| Storage | R2 bucket | A volume you mount |
| Public HTTPS | included | you provide it (Caddy, Traefik, nginx) |
| Expiring pairing records | R2 lifecycle rule | swept in-process, hourly |
| Runs on | Cloudflare | anything with Docker, or plain Node 18+ |

Both options use the same protocol. Change the panel's base URL to switch
relays; no firmware change is needed.

## Option A: Cloudflare Worker

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

## Option B: Container (no Cloudflare)

The Worker code runs on Node and stores files on disk instead of R2.

```sh
docker run -d --name tesserae-relay \
  -p 8787:8787 \
  -v relay-data:/data \
  ghcr.io/dmellok/tesserae-relay:latest
```

- `:latest` follows the most recent tagged release.
- `:0.1` pins a minor version; `:0.1.0` pins an exact release.
- `:edge` tracks `main`, including unreleased changes.
- Every build has an immutable `sha-<commit>` tag.

Prefer a release tag for a panel you rely on; `:edge` can change.
Relay versions are separate from Tesserae versions and change far more slowly.
They only need to agree on the protocol.

To build it yourself instead:

```sh
docker build -t tesserae-relay packages/relay
```

Or without Docker, on any machine with Node 18 or newer:

```sh
cd packages/relay
RELAY_DATA_DIR=/var/lib/tesserae-relay PORT=8787 npm start
```

| Variable | Default | Meaning |
|---|---|---|
| `RELAY_DATA_DIR` | `/data` | Where objects are stored. Back this up, or accept re-pairing. |
| `PORT` | `8787` | Listen port |
| `HOST` | `0.0.0.0` | Bind address |
| `RELAY_MAX_BODY` | `8388608` | Request body cap in bytes; over it returns `413` |
| `FRAME_DAILY_LIMIT` | unset (unlimited) | Frame uploads per panel per UTC day; over it `PUT .../frame` returns `429 rate_limited` |

`GET /healthz` answers `200 ok` for container health checks. It sits outside
the `/v1` contract so it can never collide with a relay route.

### Put TLS in front of it

The container speaks plain HTTP. Both Tesserae and the remote panel connect
over the public internet, so put an HTTPS proxy in front. With Caddy:

```caddy
relay.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

Cloudflare provides TLS for free. Your own relay needs a public address, so
plan on a small VPS instead of a machine behind your home router.

The mailbox holds encrypted data and never sees a frame key.
This exposes far less than a Tesserae install.

### Run one instance

Run one container. Multiple containers sharing a directory can overwrite each
other's changes, particularly during token rotation. The Worker relies on R2's
strong consistency.

## Point Tesserae at it

In **Settings → Cloud relay**, enter your Worker URL or your container's public
hostname. Click **Register this install**. Add and pair remote panels the same
way as with the hosted relay.

## Cost

Worker: one R2 bucket holds encrypted frames and small install, token and
pairing records. A panel polling every 15–60 minutes uses a few requests per
day and a few KB per frame, within Cloudflare's free tier.

No paid Workers plan is required. Scheduled polling and R2's strong consistency
mean v1 needs no Durable Objects.

Container: the same traffic uses a directory on disk. Each mailbox holds one
encrypted frame, so storage grows with panel count, not time.

## Storage cleanup

Each upload replaces the device's previous frame. Expired pairing records
are checked on read but remain in storage. Add an R2 lifecycle rule to delete
records under `code/` and `pair/` after a day:

```sh
wrangler r2 bucket lifecycle add tesserae-relay expire-pairing-codes   "code/" --expire-days 1 -y
wrangler r2 bucket lifecycle add tesserae-relay expire-pairing-records "pair/" --expire-days 1 -y
wrangler r2 bucket lifecycle list tesserae-relay   # confirm
```

Or use the Cloudflare dashboard: R2 → tesserae-relay → Settings → Object
lifecycle rules. Frame and token records are one-per-device; nothing else
grows without a limit.

The container deletes expired `code/` and `pair/` records hourly, using
`expires_at`. It touches nothing else and needs no lifecycle rules.

## Analytics (optional)

The Worker counts frames pushed and mailboxes created or removed in Workers
Analytics Engine. It sends these counts without waiting for a reply.
Frame content stays encrypted, and recorded ids are opaque.

Analytics is off in the container. To disable it in the Worker, remove the
`analytics_engine_datasets` binding from `wrangler.jsonc`.
Query counts through the Analytics Engine SQL API; examples are in
`packages/relay/README.md`.

## Restricting who can register (hosted operators)

If you run a relay for other people, override `checkEntitlement(env, request)`
in `packages/relay/src/index.js` to add a Sponsors or billing check before an
install may register. It defaults to allow; a rejection is returned as `403`.

See `packages/relay/README.md` for the same steps alongside the source.
