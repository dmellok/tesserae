/**
 * Tesserae OTA firmware signing Worker.
 *
 * Two jobs, co-located with the R2 bucket the images live in:
 *
 *   POST /sign         Authenticated. Body is the raw firmware image; query
 *                      params device_kind, fw_version, key_id. Uploads the
 *                      image to R2, builds a manifest binding its size + SHA-256,
 *                      Ed25519-signs it, and returns the {payload, signature}
 *                      descriptor to stage on the Tesserae server.
 *   GET /firmware/...  Public. Serves the stored image so devices can download
 *                      it (content-addressed path -> immutable + cacheable).
 *
 * The signing key never leaves the Worker: it lives in the OTA_SIGNING_KEY
 * secret as an Ed25519 private JWK (minted with scripts/mint-key.mjs). The
 * signed bytes are byte-identical to the Python signer's (app/ota/sign.py),
 * so a descriptor from either side verifies the same way. Contract:
 * docs/ota/contract.md.
 */

const encoder = new TextEncoder();

function b64uEncode(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function toHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function b64uToBytes(text) {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function sha256Hex(bytes) {
  return toHex(await crypto.subtle.digest("SHA-256", bytes));
}

/**
 * Sorted keys + compact separators, byte-identical to the Python signer's
 * serialize_manifest (json.dumps(sort_keys=True, separators=(",", ":"))).
 * Keys below are already in sorted order; JSON.stringify preserves it.
 */
function serializeManifest(m) {
  const ordered = {
    device_kind: m.device_kind,
    fw_version: m.fw_version,
    image_url: m.image_url,
    key_id: m.key_id,
    schema_version: m.schema_version,
    sha256: m.sha256,
    size_bytes: m.size_bytes,
  };
  return encoder.encode(JSON.stringify(ordered));
}

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function handleSign(request, env) {
  const auth = request.headers.get("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!env.OTA_SIGN_TOKEN || !timingSafeEqual(token, env.OTA_SIGN_TOKEN)) {
    return json({ error: "unauthorized" }, 401);
  }
  if (!env.OTA_SIGNING_KEY) {
    return json({ error: "OTA_SIGNING_KEY secret is not set" }, 500);
  }

  const url = new URL(request.url);
  const deviceKind = url.searchParams.get("device_kind");
  const fwVersion = url.searchParams.get("fw_version");
  const keyId = url.searchParams.get("key_id") || env.KEY_ID;
  if (!deviceKind || !fwVersion || !keyId) {
    return json({ error: "device_kind, fw_version, and key_id (or KEY_ID var) are required" }, 400);
  }

  const image = new Uint8Array(await request.arrayBuffer());
  if (image.length === 0) return json({ error: "empty image body" }, 400);

  const sha = await sha256Hex(image);
  const objectKey = `firmware/${deviceKind}/${fwVersion}/${sha}.bin`;
  await env.OTA_BUCKET.put(objectKey, image, {
    httpMetadata: {
      contentType: "application/octet-stream",
      cacheControl: "public, max-age=31536000, immutable",
    },
  });

  const base = (env.IMAGE_BASE_URL || url.origin).replace(/\/+$/, "");
  const imageUrl = `${base}/${objectKey}`;
  const manifest = {
    schema_version: 1,
    key_id: keyId,
    device_kind: deviceKind,
    fw_version: fwVersion,
    image_url: imageUrl,
    size_bytes: image.length,
    sha256: sha,
  };

  const payloadBytes = serializeManifest(manifest);
  const key = await crypto.subtle.importKey(
    "jwk",
    JSON.parse(env.OTA_SIGNING_KEY),
    { name: "Ed25519" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign({ name: "Ed25519" }, key, payloadBytes);

  return json({
    descriptor: { payload: b64uEncode(payloadBytes), signature: b64uEncode(signature) },
    manifest,
    image_url: imageUrl,
  });
}

async function handleFirmware(env, path, headOnly) {
  // HEAD uses R2's metadata-only head() so a client can probe size/etag without
  // downloading; GET streams the body.
  const object = headOnly ? await env.OTA_BUCKET.head(path) : await env.OTA_BUCKET.get(path);
  if (object === null) return new Response("not found", { status: 404 });
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("etag", object.httpEtag);
  headers.set("cache-control", "public, max-age=31536000, immutable");
  headers.set("content-length", String(object.size));
  return new Response(headOnly ? null : object.body, { headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/sign") {
      return handleSign(request, env);
    }
    if ((request.method === "GET" || request.method === "HEAD") && url.pathname.startsWith("/firmware/")) {
      return handleFirmware(env, url.pathname.slice(1), request.method === "HEAD");
    }
    if (request.method === "GET" && url.pathname === "/pubkey") {
      if (!env.OTA_SIGNING_KEY) return json({ error: "OTA_SIGNING_KEY not set" }, 500);
      const jwk = JSON.parse(env.OTA_SIGNING_KEY);
      // ``x`` is the public half of the Ed25519 keypair, safe to publish.
      return json({ key_id: env.KEY_ID, public_key_hex: toHex(b64uToBytes(jwk.x)) });
    }
    if (request.method === "GET" && url.pathname === "/") {
      return json({ ok: true, service: "tesserae-ota-signer" });
    }
    return new Response("not found", { status: 404 });
  },
};
