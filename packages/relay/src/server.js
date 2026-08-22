// Node entry point for the relay: a node:http server that hands each request
// to the same Worker module Cloudflare runs, backed by the filesystem instead
// of R2.
//
// Nothing about the protocol changes. src/index.js is imported as-is, so a
// panel paired against the hosted relay reaches a self-hosted one by pointing
// at a different base URL; no firmware change, no contract change.
//
// Deliberately dependency-free: node:http plus the Web Request/Response that
// Node has had globally since 18. A relay is a small public-facing service and
// the shortest supply chain is the point.
//
//   RELAY_DATA_DIR   where objects live          (default /data)
//   PORT             listen port                 (default 8787)
//   HOST             bind address                (default 0.0.0.0)
//   RELAY_MAX_BODY   request body cap, bytes     (default 8 MiB)
//
// Run TLS in front of it (Caddy, Traefik, nginx). This speaks plain HTTP.

import http from "node:http";

import worker from "./index.js";
import { fileSystemBucket } from "./storage-fs.js";

const DEFAULT_MAX_BODY = 8 * 1024 * 1024;
const DEFAULT_SWEEP_MS = 60 * 60 * 1000;

// Frame blobs are self-cleaning (each upload deletes the one it supersedes),
// but pairing records are not: the Worker checks `expires_at` on read and
// leaves the object, because on Cloudflare an R2 lifecycle rule expires the
// code/ and pair/ prefixes. A container has no such rule, so without this the
// one thing that grows without bound here is abandoned pairing codes. Same
// expiry field, swept in-process.
export async function sweepExpired(bucket, now = Date.now()) {
  let removed = 0;
  for (const prefix of ["code/", "pair/"]) {
    const listed = await bucket.list({ prefix });
    for (const { key } of listed.objects) {
      let record;
      try {
        const obj = await bucket.get(key);
        if (!obj) continue;
        record = JSON.parse(await obj.text());
      } catch {
        continue; // Unreadable or mid-write; the next sweep gets it.
      }
      const expiresAt = Date.parse(record?.expires_at ?? "");
      if (Number.isFinite(expiresAt) && expiresAt < now) {
        await bucket.delete(key);
        removed++;
      }
    }
  }
  return removed;
}

export function createServer({ dataDir, maxBody = DEFAULT_MAX_BODY, sweepMs = DEFAULT_SWEEP_MS } = {}) {
  const bucket = fileSystemBucket(dataDir);
  const env = { RELAY_BUCKET: bucket };

  if (sweepMs > 0) {
    const timer = setInterval(() => {
      sweepExpired(bucket).catch((err) => console.error("relay: sweep failed", err));
    }, sweepMs);
    // Never hold the process open for a cleanup pass.
    timer.unref?.();
  }

  return http.createServer((req, res) => {
    handle(req, res, env, maxBody).catch((err) => {
      // Never leak an internal message to a public endpoint.
      console.error("relay: unhandled error", err);
      if (!res.headersSent) res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "internal error" }));
    });
  });
}

async function handle(req, res, env, maxBody) {
  // Liveness for container orchestration. Outside the /v1 contract, so it
  // cannot collide with a relay route.
  if (req.url === "/healthz") {
    res.writeHead(200, { "content-type": "text/plain" });
    res.end("ok\n");
    return;
  }

  let body = null;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await readBody(req, maxBody);
    if (body === null) {
      res.writeHead(413, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "request body too large" }));
      return;
    }
  }

  const host = req.headers.host || "relay.local";
  const request = new Request(new URL(req.url, `http://${host}`), {
    method: req.method,
    headers: toHeaders(req.headers),
    body,
  });

  const response = await worker.fetch(request, env);
  const payload = Buffer.from(await response.arrayBuffer());
  const headers = {};
  response.headers.forEach((value, key) => {
    headers[key] = value;
  });
  res.writeHead(response.status, headers);
  res.end(payload);
}

function toHeaders(nodeHeaders) {
  const headers = new Headers();
  for (const [key, value] of Object.entries(nodeHeaders)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) value.forEach((v) => headers.append(key, v));
    else headers.set(key, value);
  }
  return headers;
}

// Buffered rather than streamed: frames are small, and buffering keeps the
// bridge free of the duplex/half-stream handling a streaming body would need.
// Returns null when the cap is exceeded.
function readBody(req, maxBody) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let aborted = false;
    req.on("data", (chunk) => {
      if (aborted) return;
      size += chunk.length;
      if (size > maxBody) {
        aborted = true;
        req.destroy();
        resolve(null);
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (!aborted) resolve(Buffer.concat(chunks));
    });
    req.on("error", (err) => {
      if (!aborted) reject(err);
    });
  });
}

// Started directly (not imported by a test).
if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.env.PORT || 8787);
  const host = process.env.HOST || "0.0.0.0";
  const dataDir = process.env.RELAY_DATA_DIR || "/data";
  const maxBody = Number(process.env.RELAY_MAX_BODY || DEFAULT_MAX_BODY);

  createServer({ dataDir, maxBody }).listen(port, host, () => {
    console.log(`relay listening on ${host}:${port}, storage ${dataDir}`);
  });
}
