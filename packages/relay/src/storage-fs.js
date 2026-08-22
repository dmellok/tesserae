// Filesystem storage backend, shaped like the subset of the R2 API the Worker
// uses: get / put / delete / list({prefix}).
//
// The Worker never touches Cloudflare beyond that binding, so swapping this in
// runs the identical src/index.js outside Workers with no protocol change. The
// wire contract (docs/relay/contract.md) is untouched, which is the point: a
// panel already paired against the hosted relay talks to a self-hosted one by
// changing its base URL and nothing else.
//
// Keys map to nested paths ("frame/inst_x/dev_y/blob" -> frame/inst_x/dev_y/blob).
// Every key here is server-generated from ids the Worker mints, but a key is
// still validated before it reaches path.join: a storage layer that can be
// talked out of its own directory is worth more to an attacker than anything
// it stores.

import fs from "node:fs/promises";
import path from "node:path";

let tmpCounter = 0;

// Suffix for the write-then-rename dance. Listed keys exclude these, so a
// concurrent put is never visible as a half-written object.
const TMP_MARK = ".__tmp-";

function segments(key) {
  const parts = String(key).split("/");
  for (const part of parts) {
    if (!part || part === "." || part === "..") {
      throw new Error(`unsafe storage key: ${key}`);
    }
  }
  return parts;
}

export function fileSystemBucket(rootDir) {
  const root = path.resolve(rootDir);

  function pathFor(key) {
    const full = path.join(root, ...segments(key));
    if (full !== root && !full.startsWith(root + path.sep)) {
      throw new Error(`storage key escapes root: ${key}`);
    }
    return full;
  }

  async function walk(absDir, keyDir, out) {
    let entries;
    try {
      entries = await fs.readdir(absDir, { withFileTypes: true });
    } catch (err) {
      if (err.code === "ENOENT" || err.code === "ENOTDIR") return;
      throw err;
    }
    for (const entry of entries) {
      const key = keyDir + entry.name;
      if (entry.isDirectory()) {
        await walk(path.join(absDir, entry.name), key + "/", out);
      } else if (!entry.name.includes(TMP_MARK)) {
        out.push({ key });
      }
    }
  }

  return {
    async get(key) {
      let buf;
      try {
        buf = await fs.readFile(pathFor(key));
      } catch (err) {
        if (err.code === "ENOENT" || err.code === "EISDIR") return null;
        throw err;
      }
      return {
        async text() {
          return buf.toString("utf8");
        },
        // Handed straight to `new Response(blob.body)`, which accepts a Buffer.
        get body() {
          return buf;
        },
        async arrayBuffer() {
          return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
        },
      };
    },

    // ``options`` carries R2's httpMetadata, which the Worker sets on JSON
    // records. Content types are re-declared on every response it builds, so
    // nothing downstream reads it back and it is accepted and ignored.
    async put(key, value, _options) {
      const file = pathFor(key);
      await fs.mkdir(path.dirname(file), { recursive: true });
      const data =
        typeof value === "string"
          ? Buffer.from(value, "utf8")
          : value instanceof ArrayBuffer
            ? Buffer.from(new Uint8Array(value))
            : Buffer.from(value);
      // Write then rename: a device fetching a frame while it is being
      // replaced must never receive a truncated blob.
      const tmp = `${file}${TMP_MARK}${process.pid}-${tmpCounter++}`;
      await fs.writeFile(tmp, data);
      await fs.rename(tmp, file);
    },

    async delete(key) {
      try {
        await fs.unlink(pathFor(key));
      } catch (err) {
        if (err.code !== "ENOENT") throw err;
      }
    },

    async list({ prefix = "" } = {}) {
      // Every prefix the Worker uses ends at a "/" boundary, but a partial
      // final segment still works: walk the deepest whole directory, then
      // filter on the raw prefix.
      const cut = prefix.lastIndexOf("/");
      const keyDir = cut === -1 ? "" : prefix.slice(0, cut + 1);
      const absDir = keyDir ? path.join(root, ...segments(keyDir.slice(0, -1))) : root;
      const out = [];
      await walk(absDir, keyDir, out);
      return { objects: out.filter((o) => o.key.startsWith(prefix)) };
    },
  };
}
