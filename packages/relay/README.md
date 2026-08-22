# Tesserae cloud relay (self-host)

A tiny store-and-forward mailbox that lets a Tesserae panel run at a remote
location (a parent's house, a cabin) and display your home instance's dashboards
**without exposing your home network** — no port-forward, no dynamic DNS, no VPN.

It runs either as a Cloudflare Worker or as a container. Both deployments run
the same `src/index.js`; the only Cloudflare-specific piece is the R2 binding,
used as a plain blob store, so the container swaps the backend rather than
forking the Worker. The wire contract is identical, which means a panel paired
against one relay reaches the other by changing its base URL, with no firmware
change.

Both ends connect *outbound* to this relay: your home instance seals each frame
and uploads it; the remote panel polls and decrypts it. The relay is
**zero-knowledge** — it stores only encrypted frames and the small records
needed to authenticate and to broker pairing. It never holds a frame key, so it
can't read your dashboards.

You can point Tesserae at the hosted `relay.tesserae.ink`, or run your own copy
with the steps below. Wire contract: `docs/relay/contract.md` in the Tesserae
repo.

## Deploy your own: container

```sh
docker run -d --name tesserae-relay \
  -p 8787:8787 \
  -v relay-data:/data \
  ghcr.io/dmellok/tesserae-relay:edge
```

Multi-arch (amd64 + arm64). `:edge` tracks `main`, every build also publishes an
immutable `sha-<commit>` tag, and `:latest` follows a tagged `relay-v*` release.
Without Docker, on Node 18 or newer:

```sh
cd packages/relay
RELAY_DATA_DIR=/var/lib/tesserae-relay PORT=8787 npm start
```

| Variable | Default | Meaning |
|---|---|---|
| `RELAY_DATA_DIR` | `/data` | Where objects are stored |
| `PORT` | `8787` | Listen port |
| `HOST` | `0.0.0.0` | Bind address |
| `RELAY_MAX_BODY` | `8388608` | Request body cap in bytes; over it returns `413` |

`GET /healthz` answers `200 ok` for container health checks; it sits outside the
`/v1` contract so it cannot collide with a relay route.

It speaks plain HTTP: terminate TLS in front of it, and run one instance
(storage is a directory, and replicas would race on token rotation). Both ends
reach the relay over the public internet, so this wants a small VPS rather than
a machine behind your home router. See `docs/relay/self-host.md` for the
reasoning and a Caddy example.

## Deploy your own: Cloudflare Worker

Prerequisites: a Cloudflare account and [`wrangler`](https://developers.cloudflare.com/workers/wrangler/).

```sh
cd packages/relay
npm install
wrangler r2 bucket create tesserae-relay   # one-time
wrangler deploy
```

That prints your Worker URL (e.g. `https://tesserae-relay.<you>.workers.dev`).
Bind a custom domain in the Cloudflare dashboard if you want a stable hostname.

## Tests

```sh
npm test
```

Plain `node --test`, no wrangler or Miniflare, so it runs anywhere. Covers the
Worker flows against an in-memory bucket, the filesystem backend, the HTTP
bridge, and the expiry sweep.

## Point Tesserae at it

In Tesserae: **Settings → Cloud relay**, set the relay URL to your Worker's
origin, and **Register this install**. Then **Add a remote panel** to get a
pairing code, and enter that code + the relay URL on the panel.

## Cost

**Worker:** everything lives in one R2 bucket. A panel polling every 15–60 min
is a handful of requests per day and a few KB per frame, comfortably inside
Cloudflare's free tier. There are no Durable Objects (v1 is scheduled-poll-only,
and R2 is strongly consistent), so no paid Workers plan is required.

**Container:** the same traffic against a directory. A mailbox holds one sealed
frame per device, so storage is bounded by device count rather than by uptime.

## Analytics (optional)

The Worker writes aggregate operational metrics to a Workers Analytics Engine
dataset (`relay_events`, configured in `wrangler.jsonc`) — one data point per
frame push and per mailbox created / removed. It's fire-and-forget and a no-op
if you remove the `analytics_engine_datasets` binding. The data points carry
`blobs: [event, install_id, device_id]`, `doubles: [1]`, `index: install_id`.
The ids are opaque random values, not personal data, and frame content is never
seen (it's sealed).

Query aggregates from **outside** the Worker via the Analytics Engine SQL API
(the Worker only writes):

```bash
SQL="SELECT SUM(double1) AS frames FROM relay_events
     WHERE blob1='frame_push' AND timestamp >= NOW() - INTERVAL '7' DAY"
curl -s "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/analytics_engine/sql" \
  -H "Authorization: Bearer $CF_API_TOKEN" --data "$SQL"
```

Useful queries:

```sql
-- Frames pushed per day
SELECT toStartOfDay(timestamp) AS day, SUM(double1) AS frames
FROM relay_events WHERE blob1='frame_push'
GROUP BY day ORDER BY day;

-- Net active mailboxes (created minus removed, all time)
SELECT SUM(multiIf(blob1='mailbox_created', 1, blob1='mailbox_removed', -1, 0)) AS mailboxes
FROM relay_events;

-- Distinct devices that pushed in the last 30 days
SELECT COUNT(DISTINCT blob3) AS active_mailboxes
FROM relay_events
WHERE blob1='frame_push' AND timestamp >= NOW() - INTERVAL '30' DAY;
```

Point your dashboard at those queries. For pure aggregate with no ids at all,
drop the `install_id` / `device_id` blobs in `track()` and rely on the
created-minus-removed count.

## Gating registration (optional)

Hosted operators can restrict who may register an install by overriding
`checkEntitlement(env, request)` in `src/index.js` (a Sponsors or billing check).
It defaults to allow. Rejections are returned as `403`.
