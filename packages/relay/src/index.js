/**
 * Tesserae cloud relay Worker.
 *
 * A zero-knowledge, store-and-forward mailbox for remote e-ink panels. A home
 * instance seals each rendered frame (it holds the key) and PUTs the ciphertext
 * here; the remote panel polls and decrypts. The relay only ever handles opaque
 * bytes plus the small records needed to authenticate and to broker rendezvous
 * pairing. It never sees a frame key.
 *
 * Storage is R2 only (strongly consistent; the free tier covers a household).
 * With scheduled-poll-only delivery there is no long-poll to coordinate, so no
 * Durable Object is needed. Wire contract: docs/relay/contract.md (in the
 * Tesserae repo).
 *
 * Keys in the RELAY_BUCKET:
 *   install/<install_id>.json          { publisher_token_sha256, install_pubkey }
 *   code/<code>.json                   { install_id, expires_at }
 *   pair/<install_id>/<code>.json      { code, expires_at, panel_pubkey?, completion? }
 *   token/<device_token_sha256>.json   { install_id, device_id }
 *   frame/<install_id>/<device_id>/latest.json   { etag, blob_key, meta }
 *   frame/<install_id>/<device_id>/<digest>.bin  sealed frame bytes
 */

const CODE_TTL_MS = 10 * 60 * 1000;
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // no ambiguous 0/O/1/I
const ERROR_CODES = new Set([
  "invalid_request",
  "unauthorized",
  "forbidden",
  "not_found",
  "pairing_expired",
  "conflict",
]);

// --- helpers ---------------------------------------------------------------

function toHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(text) {
  return toHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function randomToken(prefix) {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  const b64u = btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return prefix + b64u;
}

function randomCode() {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += CODE_ALPHABET[b % CODE_ALPHABET.length];
  return out;
}

function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function fail(code, message, status) {
  const c = ERROR_CODES.has(code) ? code : "invalid_request";
  return json({ error: { code: c, message: message || c } }, status);
}

function bearer(request) {
  const auth = request.headers.get("authorization") || "";
  return auth.startsWith("Bearer ") ? auth.slice(7) : "";
}

async function getJson(env, key) {
  const obj = await env.RELAY_BUCKET.get(key);
  if (!obj) return null;
  try {
    return JSON.parse(await obj.text());
  } catch {
    return null;
  }
}

async function putJson(env, key, value) {
  await env.RELAY_BUCKET.put(key, JSON.stringify(value), {
    httpMetadata: { contentType: "application/json" },
  });
}

// Aggregate operational metrics for a dashboard (frames pushed, mailboxes).
// Fire-and-forget to Workers Analytics Engine; a no-op when the optional
// ANALYTICS binding isn't configured, so self-hosters can drop it. The ids are
// opaque random values (not PII) and frame content stays sealed, so this is
// operational counting, not content. Query aggregates via the external
// Analytics Engine SQL API (see packages/relay/README.md); the Worker only writes.
function track(env, event, installId, deviceId) {
  if (!env.ANALYTICS) return;
  env.ANALYTICS.writeDataPoint({
    blobs: [event, installId || "", deviceId || ""],
    doubles: [1],
    indexes: [installId || ""],
  });
}

// Default entitlement gate: allow. A gated relay overrides this (Sponsors /
// paid check) without any contract change; a rejection becomes 403.
async function checkEntitlement(_env, _request) {
  return true;
}

async function requirePublisher(env, request, installId) {
  const record = await getJson(env, `install/${installId}.json`);
  if (!record) return null;
  const presented = await sha256Hex(bearer(request));
  return timingSafeEqual(presented, record.publisher_token_sha256 || "") ? record : null;
}

// --- handlers --------------------------------------------------------------

async function registerInstall(env, request) {
  if (!(await checkEntitlement(env, request))) return fail("forbidden", "not entitled", 403);
  let body;
  try {
    body = await request.json();
  } catch {
    return fail("invalid_request", "body must be JSON", 400);
  }
  if (typeof body?.install_pubkey !== "string" || !body.install_pubkey) {
    return fail("invalid_request", "install_pubkey required", 400);
  }
  const installId = "inst_" + toHex(crypto.getRandomValues(new Uint8Array(16)));
  const publisherToken = randomToken("tr_pub_");
  await putJson(env, `install/${installId}.json`, {
    publisher_token_sha256: await sha256Hex(publisherToken),
    install_pubkey: body.install_pubkey,
    label: typeof body.label === "string" ? body.label.slice(0, 128) : "",
    created_at: new Date().toISOString(),
  });
  return json({ install_id: installId, publisher_token: publisherToken }, 201);
}

async function mintCode(env, request, installId) {
  if (!(await requirePublisher(env, request, installId))) return fail("unauthorized", "", 401);
  const code = randomCode();
  const expiresAt = new Date(Date.now() + CODE_TTL_MS).toISOString();
  await putJson(env, `code/${code}.json`, { install_id: installId, expires_at: expiresAt });
  await putJson(env, `pair/${installId}/${code}.json`, { code, expires_at: expiresAt });
  return json({ code, expires_at: expiresAt }, 201);
}

async function panelPair(env, request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return fail("invalid_request", "body must be JSON", 400);
  }
  const code = typeof body?.code === "string" ? body.code : "";
  const panelPubkey = typeof body?.panel_pubkey === "string" ? body.panel_pubkey : "";
  if (!code || !panelPubkey) return fail("invalid_request", "code + panel_pubkey required", 400);
  const ref = await getJson(env, `code/${code}.json`);
  if (!ref || Date.parse(ref.expires_at) < Date.now()) {
    return fail("pairing_expired", "unknown or expired code", 404);
  }
  const key = `pair/${ref.install_id}/${code}.json`;
  const pending = await getJson(env, key);
  if (!pending) return fail("pairing_expired", "unknown or expired code", 404);
  pending.panel_pubkey = panelPubkey;
  await putJson(env, key, pending);
  return json({ status: "pending" }, 202);
}

async function pendingPairings(env, request, installId) {
  if (!(await requirePublisher(env, request, installId))) return fail("unauthorized", "", 401);
  const listed = await env.RELAY_BUCKET.list({ prefix: `pair/${installId}/` });
  const pending = [];
  for (const item of listed.objects) {
    const rec = await getJson(env, item.key);
    if (rec && rec.panel_pubkey && !rec.completion) {
      pending.push({ code: rec.code, panel_pubkey: rec.panel_pubkey });
    }
  }
  return json({ pending });
}

async function completePairing(env, request, installId, code) {
  if (!(await requirePublisher(env, request, installId))) return fail("unauthorized", "", 401);
  let body;
  try {
    body = await request.json();
  } catch {
    return fail("invalid_request", "body must be JSON", 400);
  }
  const { device_id, device_token, device_token_sha256, home_pubkey, config } = body || {};
  if (!device_id || !device_token || !device_token_sha256 || !home_pubkey) {
    return fail("invalid_request", "missing pairing completion fields", 400);
  }
  const key = `pair/${installId}/${code}.json`;
  const pending = await getJson(env, key);
  if (!pending) return fail("not_found", "no such pairing", 404);
  pending.completion = { home_pubkey, device_token, device_id, config: config || {} };
  await putJson(env, key, pending);
  // Store the token hash the device will authenticate future polls with.
  await putJson(env, `token/${device_token_sha256}.json`, {
    install_id: installId,
    device_id,
  });
  track(env, "mailbox_created", installId, device_id);
  return json({});
}

async function pairStatus(env, code) {
  const ref = await getJson(env, `code/${code}.json`);
  if (!ref) return fail("pairing_expired", "unknown or expired code", 404);
  const pending = await getJson(env, `pair/${ref.install_id}/${code}.json`);
  if (!pending) return fail("pairing_expired", "unknown or expired code", 404);
  if (!pending.completion) return json({ status: "pending" });
  const c = pending.completion;
  return json({
    status: "ready",
    home_pubkey: c.home_pubkey,
    device_token: c.device_token,
    device_id: c.device_id,
    config: c.config || {},
  });
}

const META_HEADERS = [
  ["X-Tesserae-Panel-W", "panel_w"],
  ["X-Tesserae-Panel-H", "panel_h"],
  ["X-Tesserae-Format", "format"],
  ["X-Tesserae-Renderer", "renderer"],
  ["X-Tesserae-Meta", "meta"],
];

async function putFrame(env, request, installId, deviceId) {
  if (!(await requirePublisher(env, request, installId))) return fail("unauthorized", "", 401);
  const etag = (request.headers.get("etag") || "").replace(/"/g, "");
  if (!etag) return fail("invalid_request", "ETag header required", 400);
  const meta = {};
  for (const [header, field] of META_HEADERS) meta[field] = request.headers.get(header) || "";
  const pointerKey = `frame/${installId}/${deviceId}/latest.json`;
  const blobKey = `frame/${installId}/${deviceId}/${etag}.bin`;
  const previous = await getJson(env, pointerKey);
  const body = await request.arrayBuffer();
  await env.RELAY_BUCKET.put(blobKey, body);
  await putJson(env, pointerKey, { etag, blob_key: blobKey, meta });
  // Delete the frame this one supersedes so the mailbox holds only the latest
  // sealed blob (each render is a new digest; without this, blobs accumulate
  // forever). A repeated ETag points at the same blob, so never delete that.
  if (previous?.blob_key && previous.blob_key !== blobKey) {
    await env.RELAY_BUCKET.delete(previous.blob_key);
  }
  track(env, "frame_push", installId, deviceId);
  return json({});
}

async function getFrame(env, request, installId, deviceId) {
  const tokenSha = await sha256Hex(bearer(request));
  const tokenRec = await getJson(env, `token/${tokenSha}.json`);
  if (!tokenRec) return fail("unauthorized", "", 401);
  if (tokenRec.install_id !== installId || tokenRec.device_id !== deviceId) {
    return fail("forbidden", "token is for another device", 403);
  }
  const pointer = await getJson(env, `frame/${installId}/${deviceId}/latest.json`);
  if (!pointer) return new Response(null, { status: 204 });
  const ifNoneMatch = (request.headers.get("if-none-match") || "").replace(/"/g, "");
  const headers = { ETag: `"${pointer.etag}"`, "Cache-Control": "no-store" };
  for (const [header, field] of META_HEADERS) {
    if (pointer.meta?.[field]) headers[header] = pointer.meta[field];
  }
  if (ifNoneMatch && ifNoneMatch === pointer.etag) {
    return new Response(null, { status: 304, headers });
  }
  const blob = await env.RELAY_BUCKET.get(pointer.blob_key);
  if (!blob) return new Response(null, { status: 204 });
  headers["content-type"] = "application/octet-stream";
  return new Response(blob.body, { status: 200, headers });
}

async function revokeDevice(env, request, installId, deviceId) {
  if (!(await requirePublisher(env, request, installId))) return fail("unauthorized", "", 401);
  const listed = await env.RELAY_BUCKET.list({ prefix: `frame/${installId}/${deviceId}/` });
  await Promise.all(listed.objects.map((o) => env.RELAY_BUCKET.delete(o.key)));
  track(env, "mailbox_removed", installId, deviceId);
  return json({});
}

// --- router ----------------------------------------------------------------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const seg = url.pathname.split("/").filter(Boolean); // e.g. ["v1","i","<id>","d","<dev>","frame"]
    const m = request.method;

    if (seg.length === 0) return json({ product: "tesserae-relay", api: { version: 1 } });
    if (seg[0] !== "v1") return fail("not_found", "", 404);

    try {
      if (m === "POST" && seg[1] === "install" && seg[2] === "register") {
        return await registerInstall(env, request);
      }
      if (m === "POST" && seg[1] === "pair" && seg.length === 2) {
        return await panelPair(env, request);
      }
      if (m === "GET" && seg[1] === "pair" && seg.length === 3) {
        return await pairStatus(env, seg[2]);
      }
      if (seg[1] === "i" && seg[2]) {
        const installId = seg[2];
        if (m === "POST" && seg[3] === "pair" && seg[4] === "codes") {
          return await mintCode(env, request, installId);
        }
        if (m === "GET" && seg[3] === "pair" && seg[4] === "pending") {
          return await pendingPairings(env, request, installId);
        }
        if (m === "POST" && seg[3] === "pair" && seg[5] === "complete") {
          return await completePairing(env, request, installId, seg[4]);
        }
        if (seg[3] === "d" && seg[4]) {
          const deviceId = seg[4];
          if (m === "PUT" && seg[5] === "frame") return await putFrame(env, request, installId, deviceId);
          if (m === "GET" && seg[5] === "frame") return await getFrame(env, request, installId, deviceId);
          if (m === "DELETE" && seg.length === 5) return await revokeDevice(env, request, installId, deviceId);
        }
      }
    } catch (err) {
      return fail("invalid_request", String(err && err.message ? err.message : err), 400);
    }
    return fail("not_found", "", 404);
  },
};
