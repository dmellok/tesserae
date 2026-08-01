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

test("unknown code is rejected", async () => {
  const e = env();
  const r = await worker.fetch(req("POST", "/v1/pair", { body: { code: "NOPE99", panel_pubkey: "P" } }), e);
  assert.equal(r.status, 404);
  assert.equal((await r.json()).error.code, "pairing_expired");
});

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
