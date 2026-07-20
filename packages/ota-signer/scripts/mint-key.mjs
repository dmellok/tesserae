#!/usr/bin/env node
/**
 * Mint a production Ed25519 OTA signing key.
 *
 * Run locally. The PRIVATE key is written to a gitignored file for
 * `wrangler secret put` and is never printed; only the PUBLIC key goes to
 * stdout. Never commit the .jwk file.
 *
 *   node scripts/mint-key.mjs [key_id]      # key_id defaults to "prod-1"
 *
 * Then:
 *   wrangler secret put OTA_SIGNING_KEY < <key_id>.key.jwk
 *   rm <key_id>.key.jwk
 * and give the printed public key (hex) to the Tesserae repo as
 *   ota/keys/<key_id>.pub
 */
import { generateKeyPairSync } from "node:crypto";
import { writeFileSync } from "node:fs";

const keyId = process.argv[2] || "prod-1";
const { publicKey, privateKey } = generateKeyPairSync("ed25519");
const jwkPrivate = privateKey.export({ format: "jwk" });
const jwkPublic = publicKey.export({ format: "jwk" });
const publicHex = Buffer.from(jwkPublic.x, "base64url").toString("hex");

const outFile = `${keyId}.key.jwk`;
writeFileSync(outFile, JSON.stringify(jwkPrivate), { mode: 0o600 });

// Public material only on stdout.
console.log(`key_id:            ${keyId}`);
console.log(`public key (hex):  ${publicHex}`);
console.log(`public key (b64u): ${jwkPublic.x}`);

// Operator instructions on stderr so `... | tee pub.txt` keeps stdout clean.
console.error(`\nWrote ${outFile} (PRIVATE key, gitignored). Do not commit it.`);
console.error(`Load it into the Worker, then delete it:`);
console.error(`  wrangler secret put OTA_SIGNING_KEY < ${outFile}`);
console.error(`  rm ${outFile}`);
console.error(`\nPublish the public key to the Tesserae repo:`);
console.error(`  echo ${publicHex} > ota/keys/${keyId}.pub`);
