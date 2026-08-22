// Self-hosted relay: the filesystem storage backend and the node:http bridge.
//
// The point of these is that src/index.js is NOT forked. The same Worker
// module runs against a different bucket, so the wire contract in
// docs/relay/contract.md holds for both deployments and a paired panel needs
// no firmware change to move between them.
//
// Plain node --test, no wrangler or Miniflare, same as relay.test.mjs.

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import worker from "../src/index.js";
import { createServer, sweepExpired } from "../src/server.js";
import { fileSystemBucket } from "../src/storage-fs.js";

async function tmpDir() {
  return await fs.mkdtemp(path.join(os.tmpdir(), "relay-fs-"));
}

// -- storage backend ------------------------------------------------------

test("bucket round-trips text and binary, and reports a miss as null", async () => {
  const bucket = fileSystemBucket(await tmpDir());

  assert.equal(await bucket.get("nothing/here.json"), null);

  await bucket.put("device/inst_a/dev_b.json", JSON.stringify({ ok: true }));
  assert.deepEqual(JSON.parse(await (await bucket.get("device/inst_a/dev_b.json")).text()), {
    ok: true,
  });

  const bytes = new Uint8Array([0, 1, 2, 253, 254, 255]).buffer;
  await bucket.put("frame/inst_a/dev_b/blob", bytes);
  const blob = await bucket.get("frame/inst_a/dev_b/blob");
  assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), new Uint8Array([0, 1, 2, 253, 254, 255]));
});

test("list returns keys under a prefix and nothing outside it", async () => {
  const bucket = fileSystemBucket(await tmpDir());
  await bucket.put("pair/inst_a/one.json", "{}");
  await bucket.put("pair/inst_a/two.json", "{}");
  await bucket.put("pair/inst_b/three.json", "{}");
  await bucket.put("frame/inst_a/dev/blob", "x");

  const listed = await bucket.list({ prefix: "pair/inst_a/" });
  assert.deepEqual(
    listed.objects.map((o) => o.key).sort(),
    ["pair/inst_a/one.json", "pair/inst_a/two.json"],
  );

  assert.deepEqual((await bucket.list({ prefix: "nothing/" })).objects, []);
});

test("delete is idempotent", async () => {
  const bucket = fileSystemBucket(await tmpDir());
  await bucket.put("token/abc.json", "{}");
  await bucket.delete("token/abc.json");
  await bucket.delete("token/abc.json");
  assert.equal(await bucket.get("token/abc.json"), null);
});

test("a key cannot escape the storage root", async () => {
  const root = await tmpDir();
  const bucket = fileSystemBucket(root);
  await assert.rejects(() => bucket.put("../escaped.json", "{}"), /unsafe storage key/);
  await assert.rejects(() => bucket.get("frame/../../escaped"), /unsafe storage key/);
  assert.deepEqual(await fs.readdir(path.dirname(root)).then((e) => e.includes("escaped.json")), false);
});

test("a partial write is never listed", async () => {
  // put() writes to a temp name and renames, so a reader mid-write sees the
  // old object or none, never a truncated frame.
  const root = await tmpDir();
  const bucket = fileSystemBucket(root);
  await fs.mkdir(path.join(root, "frame"), { recursive: true });
  await fs.writeFile(path.join(root, "frame", "blob.__tmp-999-0"), "half");
  assert.deepEqual((await bucket.list({ prefix: "frame/" })).objects, []);
});

// -- the Worker, unmodified, on filesystem storage ------------------------

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

test("the full rendezvous and frame round-trip runs on the filesystem backend", async () => {
  const env = { RELAY_BUCKET: fileSystemBucket(await tmpDir()) };
  const req = (method, url, { body, headers } = {}) =>
    new Request("https://relay.test" + url, {
      method,
      headers: headers || {},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  const auth = (token) => ({ authorization: "Bearer " + token });

  let r = await worker.fetch(req("POST", "/v1/install/register", { body: { install_pubkey: "PUB" } }), env);
  assert.equal(r.status, 201);
  const { install_id, publisher_token } = await r.json();

  r = await worker.fetch(req("POST", `/v1/i/${install_id}/pair/codes`, { headers: auth(publisher_token) }), env);
  assert.equal(r.status, 201);
  const { code } = await r.json();

  r = await worker.fetch(req("POST", "/v1/pair", { body: { code, panel_pubkey: "PANELPUB" } }), env);
  assert.equal(r.status, 202, await r.text());

  r = await worker.fetch(req("GET", `/v1/i/${install_id}/pair/pending`, { headers: auth(publisher_token) }), env);
  assert.deepEqual((await r.json()).pending, [{ code, panel_pubkey: "PANELPUB" }]);

  const deviceToken = "dev_token_fs";
  r = await worker.fetch(
    req("POST", `/v1/i/${install_id}/pair/${code}/complete`, {
      headers: auth(publisher_token),
      body: {
        device_id: "panel1",
        device_token: deviceToken,
        device_token_sha256: await sha256Hex(deviceToken),
        home_pubkey: "HOMEPUB",
        config: { sleep: 900 },
      },
    }),
    env,
  );
  assert.equal(r.status, 200, await r.text());

  r = await worker.fetch(req("GET", `/v1/pair/${code}`), env);
  const status = await r.json();
  assert.equal(status.status, "ready");
  assert.equal(status.device_token, deviceToken);

  const sealed = new Uint8Array([1, 2, 3, 4, 5]);
  r = await worker.fetch(
    new Request(`https://relay.test/v1/i/${install_id}/d/panel1/frame`, {
      method: "PUT",
      headers: { ...auth(publisher_token), ETag: '"cafe1234"', "X-Tesserae-Format": "bin" },
      body: sealed,
    }),
    env,
  );
  assert.equal(r.status, 200, await r.text());

  r = await worker.fetch(
    new Request(`https://relay.test/v1/i/${install_id}/d/panel1/frame`, { headers: auth(deviceToken) }),
    env,
  );
  // No eager .text() in the assertion message here: it would consume the body
  // before the byte comparison below.
  assert.equal(r.status, 200);
  assert.deepEqual(new Uint8Array(await r.arrayBuffer()), sealed);
});

test("stored objects survive a fresh bucket handle on the same directory", async () => {
  // What a container restart looks like: the process goes, the volume stays.
  const dir = await tmpDir();
  const first = fileSystemBucket(dir);
  await first.put("device/inst_a/panel1.json", JSON.stringify({ device_id: "panel1" }));
  await first.put("frame/inst_a/panel1/cafe.bin", new Uint8Array([7, 7, 7]).buffer);

  const second = fileSystemBucket(dir);
  assert.deepEqual(JSON.parse(await (await second.get("device/inst_a/panel1.json")).text()), {
    device_id: "panel1",
  });
  assert.deepEqual(
    new Uint8Array(await (await second.get("frame/inst_a/panel1/cafe.bin")).arrayBuffer()),
    new Uint8Array([7, 7, 7]),
  );
});

// -- the node:http bridge -------------------------------------------------

test("the http bridge preserves method, status, headers and a binary body", async () => {
  const server = createServer({ dataDir: await tmpDir() });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    let res = await fetch(`${base}/healthz`);
    assert.equal(res.status, 200);
    assert.equal((await res.text()).trim(), "ok");

    res = await fetch(`${base}/v1/install/register`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ install_pubkey: "PUB" }),
    });
    assert.equal(res.status, 201);
    assert.match(res.headers.get("content-type") || "", /json/);
    const { install_id, publisher_token } = await res.json();

    // Binary in, binary out, over a real socket.
    res = await fetch(`${base}/v1/i/${install_id}/pair/codes`, {
      method: "POST",
      headers: { authorization: `Bearer ${publisher_token}` },
    });
    const { code } = await res.json();

    res = await fetch(`${base}/v1/pair`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code, panel_pubkey: "PANELPUB" }),
    });
    assert.equal(res.status, 202);

    const device_token = "dev_token_http";
    res = await fetch(`${base}/v1/i/${install_id}/pair/${code}/complete`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${publisher_token}` },
      body: JSON.stringify({
        device_id: "panel1",
        device_token,
        device_token_sha256: await sha256Hex(device_token),
        home_pubkey: "HOMEPUB",
      }),
    });
    assert.equal(res.status, 200, await res.text());

    const sealed = new Uint8Array([9, 8, 7, 0, 255]);
    res = await fetch(`${base}/v1/i/${install_id}/d/panel1/frame`, {
      method: "PUT",
      headers: {
        authorization: `Bearer ${publisher_token}`,
        ETag: '"cafe5678"',
        "X-Tesserae-Panel-W": "800",
        "X-Tesserae-Format": "bin",
      },
      body: sealed,
    });
    assert.equal(res.status, 200, await res.text());

    res = await fetch(`${base}/v1/i/${install_id}/d/panel1/frame`, {
      headers: { authorization: `Bearer ${device_token}` },
    });
    assert.equal(res.status, 200);
    assert.deepEqual(new Uint8Array(await res.arrayBuffer()), sealed);

    // An unknown route still answers through the bridge rather than hanging.
    res = await fetch(`${base}/v1/nope`);
    assert.equal(res.status, 404);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("an oversized body is refused rather than buffered", async () => {
  const server = createServer({ dataDir: await tmpDir(), maxBody: 1024 });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const res = await fetch(`${base}/v1/install/register`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: Buffer.alloc(4096, 0x41),
    });
    assert.equal(res.status, 413);
  } catch (err) {
    // Destroying the request mid-upload can surface as a socket error on the
    // client side; either outcome is a refusal, which is what matters.
    assert.match(String(err), /fetch failed|socket|terminated|ECONNRESET/i);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

// -- expired pairing-record sweep ----------------------------------------

test("the sweep removes expired pairing records and keeps live ones", async () => {
  // On Cloudflare an R2 lifecycle rule expires the code/ and pair/ prefixes.
  // A container has no lifecycle rules, so without this sweep abandoned
  // pairing codes are the one thing that grows forever.
  const bucket = fileSystemBucket(await tmpDir());
  const past = new Date(Date.now() - 60_000).toISOString();
  const future = new Date(Date.now() + 3_600_000).toISOString();

  await bucket.put("code/OLD1.json", JSON.stringify({ install_id: "inst_a", expires_at: past }));
  await bucket.put("pair/inst_a/OLD1.json", JSON.stringify({ code: "OLD1", expires_at: past }));
  await bucket.put("code/NEW1.json", JSON.stringify({ install_id: "inst_a", expires_at: future }));
  await bucket.put("pair/inst_a/NEW1.json", JSON.stringify({ code: "NEW1", expires_at: future }));
  // Nothing outside those two prefixes may be touched.
  await bucket.put("device/inst_a/panel1.json", JSON.stringify({ device_id: "panel1" }));
  await bucket.put("frame/inst_a/panel1/cafe.bin", "sealed");

  const removed = await sweepExpired(bucket);
  assert.equal(removed, 2);

  assert.equal(await bucket.get("code/OLD1.json"), null);
  assert.equal(await bucket.get("pair/inst_a/OLD1.json"), null);
  assert.ok(await bucket.get("code/NEW1.json"));
  assert.ok(await bucket.get("pair/inst_a/NEW1.json"));
  assert.ok(await bucket.get("device/inst_a/panel1.json"));
  assert.ok(await bucket.get("frame/inst_a/panel1/cafe.bin"));
});

test("the sweep tolerates an unreadable record", async () => {
  const bucket = fileSystemBucket(await tmpDir());
  await bucket.put("code/BROKEN.json", "not json at all");
  await bucket.put(
    "code/OLD2.json",
    JSON.stringify({ install_id: "i", expires_at: new Date(Date.now() - 1000).toISOString() }),
  );
  assert.equal(await sweepExpired(bucket), 1);
  assert.ok(await bucket.get("code/BROKEN.json"), "an unparsable record is left alone, not deleted");
});
