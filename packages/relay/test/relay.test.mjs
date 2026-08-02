// Worker tests: drive the full relay flow against an in-memory R2 mock.
// Plain Node (node --test) — no wrangler / Miniflare needed, so it runs in CI.
import assert from "node:assert/strict";
import { test } from "node:test";

import worker from "../src/index.js";

// Minimal R2 bucket: get/put/list/delete over a Map. Matches the subset of the
// R2 API the Worker uses (text(), body, arrayBuffer(), list({prefix}).objects).
function makeBucket() {
  const store = new Map();
  return {
    async get(key) {
      if (!store.has(key)) return null;
      const value = store.get(key);
      return {
        async text() {
          return typeof value === "string" ? value : new TextDecoder().decode(value);
        },
        get body() {
          return value;
        },
      };
    },
    async put(key, value) {
      store.set(key, value instanceof ArrayBuffer ? new Uint8Array(value) : value);
    },
    async delete(key) {
      store.delete(key);
    },
    async list({ prefix }) {
      return {
        objects: [...store.keys()].filter((k) => k.startsWith(prefix)).map((key) => ({ key })),
      };
    },
  };
}

const env = () => ({ RELAY_BUCKET: makeBucket() });
const req = (method, path, { body, headers } = {}) =>
  new Request("https://relay.test" + path, {
    method,
    headers: headers || {},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const authHdr = (token) => ({ authorization: "Bearer " + token });

test("full rendezvous + frame round-trip", async () => {
  const e = env();

  // 1. Register install.
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "PUB" } }), e);
  assert.equal(r.status, 201);
  const { install_id, publisher_token } = await r.json();
  assert.match(install_id, /^inst_/);
  assert.match(publisher_token, /^tr_pub_/);

  // 2. Mint a pairing code (publisher-authed).
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/codes`, { headers: authHdr(publisher_token) }),
    e,
  );
  assert.equal(r.status, 201);
  const { code } = await r.json();
  assert.ok(code.length >= 6);

  // 3. Panel posts its pubkey.
  r = await worker.fetch(req("POST", "/v1/pair", { body: { code, panel_pubkey: "PANELPUB" } }), e);
  assert.equal(r.status, 202);

  // 4. Home sees it pending.
  r = await worker.fetch(
    req("GET", `/v1/i/${install_id}/pair/pending`, { headers: authHdr(publisher_token) }),
    e,
  );
  const { pending } = await r.json();
  assert.deepEqual(pending, [{ code, panel_pubkey: "PANELPUB" }]);

  // 5. Home completes: hands over token (hash) + home pubkey.
  const deviceToken = "dev_token_123";
  const tokenSha = await sha256Hex(deviceToken);
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/${code}/complete`, {
      headers: authHdr(publisher_token),
      body: {
        device_id: "panel1",
        device_token: deviceToken,
        device_token_sha256: tokenSha,
        home_pubkey: "HOMEPUB",
        config: { sleep: 900 },
      },
    }),
    e,
  );
  assert.equal(r.status, 200);

  // 6. Panel polls pair status → ready, receives token + home pubkey.
  r = await worker.fetch(req("GET", `/v1/pair/${code}`), e);
  const status = await r.json();
  assert.equal(status.status, "ready");
  assert.equal(status.install_id, install_id); // panel needs this to build its mailbox URL
  assert.equal(status.device_id, "panel1");
  assert.equal(status.device_token, deviceToken);
  assert.equal(status.home_pubkey, "HOMEPUB");
  assert.deepEqual(status.config, { sleep: 900 });

  // 7. Home uploads a sealed frame.
  const sealed = new Uint8Array([1, 2, 3, 4, 5]);
  r = await worker.fetch(
    new Request(`https://relay.test/v1/i/${install_id}/d/panel1/frame`, {
      method: "PUT",
      headers: {
        authorization: "Bearer " + publisher_token,
        ETag: '"deadbeef"',
        "X-Tesserae-Panel-W": "800",
        "X-Tesserae-Format": "bin",
      },
      body: sealed,
    }),
    e,
  );
  assert.equal(r.status, 200);

  // 8. Panel fetches the frame with its device token.
  r = await worker.fetch(req("GET", `/v1/i/${install_id}/d/panel1/frame`, { headers: authHdr(deviceToken) }), e);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("etag"), '"deadbeef"');
  assert.equal(r.headers.get("x-tesserae-panel-w"), "800");
  assert.deepEqual(new Uint8Array(await r.arrayBuffer()), sealed);

  // 9. Conditional GET with the same ETag → 304.
  r = await worker.fetch(
    req("GET", `/v1/i/${install_id}/d/panel1/frame`, {
      headers: { ...authHdr(deviceToken), "If-None-Match": '"deadbeef"' },
    }),
    e,
  );
  assert.equal(r.status, 304);
});

test("a new frame supersedes and deletes the previous blob", async () => {
  const e = env();
  const bucket = e.RELAY_BUCKET;
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "PUB" } }), e);
  const { install_id, publisher_token } = await r.json();

  const put = async (etag, bytes) =>
    worker.fetch(
      new Request(`https://relay.test/v1/i/${install_id}/d/panel1/frame`, {
        method: "PUT",
        headers: { authorization: "Bearer " + publisher_token, ETag: `"${etag}"` },
        body: new Uint8Array(bytes),
      }),
      e,
    );

  await put("aaa", [1]);
  const listAfterFirst = await bucket.list({ prefix: `frame/${install_id}/panel1/` });
  assert.ok(listAfterFirst.objects.some((o) => o.key.endsWith("aaa.bin")));

  // Second render → old blob gone, only the new one + the pointer remain.
  await put("bbb", [2, 3]);
  const keys = (await bucket.list({ prefix: `frame/${install_id}/panel1/` })).objects.map((o) => o.key);
  assert.ok(keys.some((k) => k.endsWith("bbb.bin")));
  assert.ok(!keys.some((k) => k.endsWith("aaa.bin")), "superseded blob should be deleted");

  // Re-PUT the same ETag → the current blob must survive (not delete itself).
  await put("bbb", [2, 3]);
  const still = await bucket.get(`frame/${install_id}/panel1/bbb.bin`);
  assert.ok(still, "idempotent re-PUT must keep the current blob");
});

test("auth is enforced", async () => {
  const e = env();
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "PUB" } }), e);
  const { install_id, publisher_token } = await r.json();

  // Wrong publisher token → 401.
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/codes`, { headers: authHdr("wrong") }),
    e,
  );
  assert.equal(r.status, 401);

  // Unknown device token on frame GET → 401.
  r = await worker.fetch(req("GET", `/v1/i/${install_id}/d/panel1/frame`, { headers: authHdr("nope") }), e);
  assert.equal(r.status, 401);

  // A device token bound to another device → 403. Mint + complete a real
  // pairing for device "other" so its token record exists, then try to use
  // that token against "panel1".
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/codes`, { headers: authHdr(publisher_token) }),
    e,
  );
  const { code } = await r.json();
  const tokenSha = await sha256Hex("tok");
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/${code}/complete`, {
      headers: authHdr(publisher_token),
      body: { device_id: "other", device_token: "tok", device_token_sha256: tokenSha, home_pubkey: "H" },
    }),
    e,
  );
  assert.equal(r.status, 200);
  r = await worker.fetch(req("GET", `/v1/i/${install_id}/d/panel1/frame`, { headers: authHdr("tok") }), e);
  assert.equal(r.status, 403);
});

test("panel self-report (geometry + model) rides pairing to the pending list", async () => {
  const e = env();
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "P" } }), e);
  const { install_id, publisher_token } = await r.json();
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/codes`, { headers: authHdr(publisher_token) }),
    e,
  );
  const { code } = await r.json();

  r = await worker.fetch(
    req("POST", "/v1/pair", {
      body: {
        code,
        panel_pubkey: "PP",
        panel_w: 800,
        panel_h: 480,
        model: "esp32_client",
        gamut: "waveshare_e6",
      },
    }),
    e,
  );
  assert.equal(r.status, 202);

  r = await worker.fetch(
    req("GET", `/v1/i/${install_id}/pair/pending`, { headers: authHdr(publisher_token) }),
    e,
  );
  const { pending } = await r.json();
  assert.deepEqual(pending, [
    {
      code,
      panel_pubkey: "PP",
      panel_w: 800,
      panel_h: 480,
      model: "esp32_client",
      gamut: "waveshare_e6",
    },
  ]);
});

test("device status: panel posts, home pulls", async () => {
  const e = env();
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "P" } }), e);
  const { install_id, publisher_token } = await r.json();
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/codes`, { headers: authHdr(publisher_token) }),
    e,
  );
  const { code } = await r.json();
  const dtok = "statustok";
  await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/${code}/complete`, {
      headers: authHdr(publisher_token),
      body: { device_id: "panel1", device_token: dtok, device_token_sha256: await sha256Hex(dtok), home_pubkey: "H" },
    }),
    e,
  );

  // 204 before any status posted.
  r = await worker.fetch(req("GET", `/v1/i/${install_id}/d/panel1/status`, { headers: authHdr(publisher_token) }), e);
  assert.equal(r.status, 204);

  // Panel posts telemetry with its device token.
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/d/panel1/status`, {
      headers: authHdr(dtok),
      body: { battery: 87, rssi: -60, fw_version: "1.0.0" },
    }),
    e,
  );
  assert.equal(r.status, 200);

  // Home pulls it (publisher auth) and gets the verbatim body + received_at.
  r = await worker.fetch(req("GET", `/v1/i/${install_id}/d/panel1/status`, { headers: authHdr(publisher_token) }), e);
  assert.equal(r.status, 200);
  const rec = await r.json();
  assert.equal(JSON.parse(rec.body).battery, 87);
  assert.ok(rec.received_at);

  // A device token for another device can't post here (403); the publisher
  // token can't post as a device (401, no token record).
  r = await worker.fetch(req("POST", `/v1/i/${install_id}/d/panel1/status`, { headers: authHdr(publisher_token), body: {} }), e);
  assert.equal(r.status, 401);
});

test("emits analytics data points for frame push and mailbox lifecycle", async () => {
  const points = [];
  const e = { ...env(), ANALYTICS: { writeDataPoint: (p) => points.push(p) } };
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "PUB" } }), e);
  const { install_id, publisher_token } = await r.json();
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/codes`, { headers: authHdr(publisher_token) }),
    e,
  );
  const { code } = await r.json();
  await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/${code}/complete`, {
      headers: authHdr(publisher_token),
      body: { device_id: "panel1", device_token: "t", device_token_sha256: await sha256Hex("t"), home_pubkey: "H" },
    }),
    e,
  );
  await worker.fetch(
    new Request(`https://relay.test/v1/i/${install_id}/d/panel1/frame`, {
      method: "PUT",
      headers: { authorization: "Bearer " + publisher_token, ETag: '"aaa"' },
      body: new Uint8Array([1]),
    }),
    e,
  );
  await worker.fetch(
    req("DELETE", `/v1/i/${install_id}/d/panel1`, { headers: authHdr(publisher_token) }),
    e,
  );

  const events = points.map((p) => p.blobs[0]);
  assert.ok(events.includes("mailbox_created"));
  assert.ok(events.includes("frame_push"));
  assert.ok(events.includes("mailbox_removed"));
  // Every point carries a count double + the install as the index.
  for (const p of points) {
    assert.deepEqual(p.doubles, [1]);
    assert.equal(p.indexes[0], install_id);
  }
});

test("works without the analytics binding", async () => {
  // No ANALYTICS in env → track() is a no-op, nothing throws.
  const e = env();
  const r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "P" } }), e);
  assert.equal(r.status, 201);
});

test("pair-code TTL is honoured and clamped", async () => {
  const e = env();
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "P" } }), e);
  const { install_id, publisher_token } = await r.json();
  const mint = async (body) => {
    const resp = await worker.fetch(
      req("POST", `/v1/i/${install_id}/pair/codes`, { headers: authHdr(publisher_token), body }),
      e,
    );
    assert.equal(resp.status, 201);
    return resp.json();
  };
  const ttlMinutes = (json) => (Date.parse(json.expires_at) - Date.now()) / 60000;

  // Default (no body): ~10 minutes.
  let minted = await mint(undefined);
  assert.ok(Math.abs(ttlMinutes(minted) - 10) < 1, minted.expires_at);
  // Requested 2 hours: honoured.
  minted = await mint({ ttl_seconds: 7200 });
  assert.ok(Math.abs(ttlMinutes(minted) - 120) < 1, minted.expires_at);
  // Over the 24h cap and under the 5min floor: clamped.
  minted = await mint({ ttl_seconds: 999999 });
  assert.ok(Math.abs(ttlMinutes(minted) - 1440) < 1, minted.expires_at);
  minted = await mint({ ttl_seconds: 10 });
  assert.ok(Math.abs(ttlMinutes(minted) - 5) < 1, minted.expires_at);
  // Garbage keeps the default.
  minted = await mint({ ttl_seconds: "soon" });
  assert.ok(Math.abs(ttlMinutes(minted) - 10) < 1, minted.expires_at);
});

test("unknown code is rejected", async () => {
  const e = env();
  const r = await worker.fetch(req("POST", "/v1/pair", { body: { code: "NOPE99", panel_pubkey: "P" } }), e);
  assert.equal(r.status, 404);
  assert.equal((await r.json()).error.code, "pairing_expired");
});

test("config mailbox: push, conditional fetch, status piggyback, revoke cleanup", async () => {
  const e = env();
  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "P" } }), e);
  const { install_id, publisher_token } = await r.json();
  const deviceToken = "cfg_dev_token";
  // Register the device token directly (pairing is covered elsewhere).
  await e.RELAY_BUCKET.put(
    `token/${await sha256Hex(deviceToken)}.json`,
    JSON.stringify({ install_id, device_id: "panel1" }),
  );

  // No config yet: device GET is 204, status response has no etag.
  r = await worker.fetch(req("GET", `/v1/i/${install_id}/d/panel1/config`, { headers: authHdr(deviceToken) }), e);
  assert.equal(r.status, 204);
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/d/panel1/status`, { headers: authHdr(deviceToken), body: { battery: 88 } }),
    e,
  );
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), {});

  // Home pushes a sealed config doc; ETag is required and publisher-only.
  const sealed = new Uint8Array([9, 8, 7, 6]);
  const putConfig = (headers) =>
    worker.fetch(
      new Request(`https://relay.test/v1/i/${install_id}/d/panel1/config`, {
        method: "PUT",
        headers,
        body: sealed,
      }),
      e,
    );
  r = await putConfig({ authorization: "Bearer " + publisher_token });
  assert.equal(r.status, 400); // missing ETag
  r = await putConfig({ authorization: "Bearer " + deviceToken, ETag: '"cfg1"' });
  assert.equal(r.status, 401); // device token can't publish
  r = await putConfig({ authorization: "Bearer " + publisher_token, ETag: '"cfg1"' });
  assert.equal(r.status, 200);

  // Panel fetch: 200 with body + ETag, then 304 on the same etag.
  r = await worker.fetch(req("GET", `/v1/i/${install_id}/d/panel1/config`, { headers: authHdr(deviceToken) }), e);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("etag"), '"cfg1"');
  assert.deepEqual(new Uint8Array(await r.arrayBuffer()), sealed);
  r = await worker.fetch(
    req("GET", `/v1/i/${install_id}/d/panel1/config`, {
      headers: { ...authHdr(deviceToken), "If-None-Match": '"cfg1"' },
    }),
    e,
  );
  assert.equal(r.status, 304);

  // The status response now piggybacks the current config etag.
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/d/panel1/status`, { headers: authHdr(deviceToken), body: { battery: 87 } }),
    e,
  );
  assert.deepEqual(await r.json(), { config_etag: "cfg1" });

  // Revoke drops the config mailbox with the rest.
  r = await worker.fetch(
    new Request(`https://relay.test/v1/i/${install_id}/d/panel1`, {
      method: "DELETE",
      headers: authHdr(publisher_token),
    }),
    e,
  );
  assert.equal(r.status, 200);
  assert.equal(await e.RELAY_BUCKET.get(`config/${install_id}/panel1.bin`), null);
  assert.equal(await e.RELAY_BUCKET.get(`config/${install_id}/panel1.json`), null);
});

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
