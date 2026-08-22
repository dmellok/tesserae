# Self-host the relay

The [remote-panel feature](../install/remote-panel.md) can use the hosted
`relay.tesserae.ink`, or your own relay. Self-hosting keeps the whole path under
your control; the relay is zero-knowledge either way, so it only ever sees
encrypted frames.

There are two ways to run your own, and they run the **same** `src/index.js`:

| | [Cloudflare Worker](#option-a-cloudflare-worker) | [Container](#option-b-container-no-cloudflare) |
|---|---|---|
| Storage | R2 bucket | A volume you mount |
| Public HTTPS | included | you provide it (Caddy, Traefik, nginx) |
| Expiring pairing records | R2 lifecycle rule | swept in-process, hourly |
| Runs on | Cloudflare | anything with Docker, or plain Node 18+ |

The wire contract is identical, so a panel that paired against one relay moves
to the other by changing its base URL. No firmware change is involved.

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

The same Worker module runs on Node with the filesystem standing in for R2.
Nothing about the protocol changes.

```sh
docker run -d --name tesserae-relay \
  -p 8787:8787 \
  -v relay-data:/data \
  ghcr.io/dmellok/tesserae-relay:edge
```

`:edge` tracks `main`, and every build also publishes an immutable
`sha-<commit>` tag to pin to. `:latest` appears with the first tagged relay
release. The relay is versioned separately from Tesserae: the two are coupled
by the wire contract, not by a version number, and the relay changes far more
slowly.

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

`GET /healthz` answers `200 ok` for container health checks. It sits outside
the `/v1` contract so it can never collide with a relay route.

### Put TLS in front of it

The container speaks plain HTTP. Both your Tesserae install and the remote
panel reach the relay over the public internet, so terminate TLS in front of
it. With Caddy that is two lines:

```caddy
relay.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

This is the part Cloudflare was doing for free. A relay has to be publicly
reachable by definition, so plan on a small VPS rather than a machine behind
your home router. What you are exposing is a zero-knowledge mailbox that holds
opaque sealed blobs and never sees a frame key, which is a far smaller surface
than exposing a Tesserae install.

### Run one instance

Storage is a plain directory and several replicas over a shared volume would
race on the read-modify-write paths, token rotation in particular. R2's strong
consistency is doing quiet work in the Worker deployment. One container.

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
wrangler r2 bucket lifecycle add tesserae-relay expire-pairing-codes   "code/" --expire-days 1 -y
wrangler r2 bucket lifecycle add tesserae-relay expire-pairing-records "pair/" --expire-days 1 -y
wrangler r2 bucket lifecycle list tesserae-relay   # confirm
```

(Or the same thing via the Cloudflare dashboard: R2 → tesserae-relay →
Settings → Object lifecycle rules.) Nothing else grows unbounded; frame and
token records are one-per-device.

**On the container** there are no lifecycle rules, so it does this itself: an
hourly in-process sweep deletes `code/` and `pair/` records whose `expires_at`
has passed, and touches nothing else. Frame blobs still self-clean on upload as
above, so a mailbox holds one sealed frame per device either way.

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
