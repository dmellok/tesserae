# Tesserae cloud relay (self-host)

A tiny Cloudflare Worker that lets a Tesserae panel run at a remote location
(a parent's house, a cabin) and display your home instance's dashboards
**without exposing your home network** — no port-forward, no dynamic DNS, no VPN.

Both ends connect *outbound* to this relay: your home instance seals each frame
and uploads it; the remote panel polls and decrypts it. The relay is
**zero-knowledge** — it stores only encrypted frames and the small records
needed to authenticate and to broker pairing. It never holds a frame key, so it
can't read your dashboards.

You can point Tesserae at the hosted `relay.tesserae.ink`, or run your own copy
with the steps below. Wire contract: `docs/relay/contract.md` in the Tesserae
repo.

## Deploy your own

Prerequisites: a Cloudflare account and [`wrangler`](https://developers.cloudflare.com/workers/wrangler/).

```sh
cd packages/relay
npm install
wrangler r2 bucket create tesserae-relay   # one-time
wrangler deploy
```

That prints your Worker URL (e.g. `https://tesserae-relay.<you>.workers.dev`).
Bind a custom domain in the Cloudflare dashboard if you want a stable hostname.

## Point Tesserae at it

In Tesserae: **Settings → Cloud relay**, set the relay URL to your Worker's
origin, and **Register this install**. Then **Add a remote panel** to get a
pairing code, and enter that code + the relay URL on the panel.

## Cost

Everything lives in one R2 bucket. A panel polling every 15–60 min is a handful
of requests per day and a few KB per frame, comfortably inside Cloudflare's free
tier. There are no Durable Objects (v1 is scheduled-poll-only, and R2 is
strongly consistent), so no paid Workers plan is required.

## Gating registration (optional)

Hosted operators can restrict who may register an install by overriding
`checkEntitlement(env, request)` in `src/index.js` (a Sponsors or billing check).
It defaults to allow. Rejections are returned as `403`.
