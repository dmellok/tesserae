# Frame-cache collections (contract)

A producer-neutral, content-addressed on-device frame cache. It generalizes the
existing [deck cache](client-protocol.md#deck-cache-sync-on-device-frame-cache)
so more than one feature can sync pre-rendered frames to a storage-capable
display and let the device play them back locally (wake, read card, paint, radio
off). The first new producer is **offline photo albums** (discussion #177);
Decks become another producer over time.

This is the shared boundary between the Tesserae server (which renders frames and
emits the manifest) and device firmware (which caches and plays back). It is the
concrete form of the "state bundles" direction sketched in
[protocol-v2-touch.md §B3](../protocol-v2-touch.md). Everything here is optional:
a client that never advertises the capability sees no change on any endpoint.

Status: **slice 1 implemented server-side.** The album producer, the
`/collection` manifest + frame endpoints, the `frame_cache` capability, and the
`/status` `collection` envelope are built; authoring is the Gallery folder's
"Use as offline album" action. Firmware (the cache + playback side) is the
remaining half. Discussion: #177.

## Design split

- The **shared cache** owns: capability, the collection manifest (frame id,
  digest, byte size, cache eligibility, order), content-addressed frame fetch,
  the capacity budget, and version/orphan handling. It understands *frames*, not
  navigation or shuffle.
- Each **producer** owns an opaque `producer` object in the manifest (a Deck's
  link graph, an album's playback settings). The cache never parses it; firmware
  routes it to the matching producer module.

This keeps producer semantics (graph distance, shuffle bags) out of the generic
sync path.

## Relationship to the deck cache (v1 compatibility)

The deck cache (`deck_cache` capability, `GET /deck`, `GET /deck/frame/<digest>`)
is unchanged and keeps working. In the first slice a capable client advertises
**both** capabilities; the server offers the new collection endpoint only when
`frame_cache` is present, and keeps using `deck_cache` / `/deck` for Decks. Decks
can migrate to be a `collection` producer later (`kind: "deck"`, links in the
`producer` object) without a capability change.

## Capability

Advertised in the `/register` and every `/status` body, **only while the storage
is present and usable** (current-state per heartbeat, not sticky, same rule as
`deck_cache`):

```json
{
  "frame_cache": { "schema": 1, "capacity_bytes": 67108864, "max_frames": 32 },
  "deck_cache":  { "schema": 1, "capacity_bytes": 67108864 }
}
```

- `capacity_bytes` is the storage budget the server plans against.
- `max_frames` is the firmware's hard cap on cached frames for this slice. The
  server never puts more than this many `cache: true` frames in a page. Making
  the limit explicit avoids the server inferring it from bytes.

## Heartbeat: active collection

When a collection is bound to the device and `frame_cache` was advertised on that
beat, the `/status` response carries:

```json
{ "collection": { "id": "album:kitchen", "kind": "album", "version": "a1b2c3d4e5f60718" } }
```

Absent means no collection is active (drop local playback state). This coexists
with the existing `deck` envelope; a device may run a deck (old path) or a
collection (new path), and slice 1 is **one active producer per device**.

## Collection manifest

```
GET /api/v1/device/<device_id>/collection      (Bearer device token)
→ 200 manifest   ·   204 no collection bound
```

```json
{
  "schema": 1,
  "collection_id": "album:kitchen",
  "kind": "album",
  "version": "a1b2c3d4e5f60718",
  "total_frames": 127,
  "cursor": null,
  "next_cursor": "opaque-version-bound-cursor",
  "frames": [
    {
      "frame_id": "photo:7f8c",
      "position": 0,
      "digest": "0f1e2d3c4b5a6978",
      "bytes": 960000,
      "ttl_s": 0,
      "cache": true,
      "url": "/api/v1/device/<device_id>/collection/frame/0f1e2d3c4b5a6978"
    }
  ],
  "producer": {
    "album": { "playback": { "mode": "shuffle", "interval_s": 1800, "repeat": "reshuffle" } }
  }
}
```

Per-frame fields (the shared primitive):

- `frame_id`: stable producer id for the frame (survives re-render). Distinct
  from `digest`, which changes when the rendered bytes change.
- `position`: stable total-order index (0-based) across the whole collection,
  not just this page. It is also the **capacity priority**: the server keeps the
  lowest-`position` frames that fit the budget. Producers assign it meaningfully
  (an album by playback order; a Deck by ring distance from home). The shared
  cache never computes it.
- `digest`: `sha256(frame bytes)[:16]`, content address; also the fetch path
  and `ETag`.
- `bytes`: on-disk artifact size, for the budget.
- `ttl_s`: 0 = no expiry (photos); >0 = re-fetch after this age (live content).
- `cache`: `false` when the frame overflowed the capacity budget; firmware
  fetches it on demand instead of caching it.
- `url`: content-addressed fetch path (below).

### Paging

`total_frames` is the full count. Slice 1 firmware consumes a single page and
enforces `max_frames == 32`; the wire shape carries paging from day one so later
firmware can walk without a contract change:

- `cursor` is the page you requested (`null` for the first page); pass
  `?cursor=<next_cursor>` to continue.
- `next_cursor` is opaque and **bound to `version`**. If it no longer resolves
  (the collection changed mid-walk), the server returns the current manifest with
  a fresh `version`; firmware discards the partial walk and restarts at page one.

## Frame fetch

```
GET /api/v1/device/<device_id>/collection/frame/<digest>   (Bearer device token)
→ 200 raw bytes   ·   404 unknown/stale digest → re-sync the manifest
```

`application/octet-stream`, `ETag: "<digest>"`, `Cache-Control: immutable,
max-age=31536000`. Same content-addressed semantics as `/deck/frame/<digest>`; a
`404` always means the manifest is stale, re-fetch `/collection`.

## Version, sync, and orphans

- `version` = `sha256` (truncated to 16 hex) of the manifest **excluding**
  `version`, `cursor`, and `next_cursor`. Per-device (renders are per-device). It
  bumps on any change to frame membership, order, digests, `ttl_s`, or the
  `producer` block, so it is the single change signal.
- Sync: compare the `/status` `collection.version` against your cached one; on
  mismatch `GET /collection`, then fetch only frames whose `digest` you don't
  already hold.
- Orphan cleanup is **client-side by contract**: delete cached files the current
  manifest no longer references. The server does not track device-side files.

## Producer extensions (opaque to the cache)

The `producer` object is keyed by `kind`. Firmware hands it to the matching
module; the shared cache ignores it.

### Album (`kind: "album"`)

```json
{ "album": { "playback": {
  "mode": "sequential | shuffle",
  "interval_s": 1800,
  "repeat": "loop | reshuffle | once"
} } }
```

- `interval_s` is a request; firmware **clamps** it to board/power bounds.
- Shuffle state is firmware-local. For the 32-frame slice a shuffle bag can be a
  bitset; it **resets when `version` changes** (new membership → new bag).
- `repeat`: `loop` replays in order, `reshuffle` draws a new bag, `once` holds
  the last frame.

### Deck (`kind: "deck"`, future)

The Deck link graph (button/zone/swipe → target) moves into
`producer.deck.links` when Decks migrate to collections. Until then Decks stay on
the `deck_cache` path unchanged.

## Reporting and truthful state

Once playback is local the server is **not** the authority on the current frame,
and must not claim to be. The device reports (in the `/status` body and/or as
query params on `/frame` polls, mirroring `deck_page_id`):

```json
{ "collection": {
  "id": "album:kitchen",
  "version": "a1b2c3d4e5f60718",
  "cached": 32,
  "total": 127,
  "state": "playing | paused | syncing | error"
} }
```

An exact frame digest, if reported at all, is a **last-reported observation**,
not a live "current screen" contract. The Companion / Devices UI shows collection
id, version, cached/total, and state, not a guaranteed current image.

## Interruption semantics

While a collection is playing:

- A **one-off Image or Dashboard push** to the display *interrupts* playback,
  shows the pushed frame, and playback **auto-resumes at the next interval**.
- **Reassigning the display's content** (binding a different rotation, deck, or
  album, or unbinding) *stops* the collection; the device drops playback state on
  the next `/status` (the `collection` envelope disappears or changes `id`).

## First slice (scope)

- Single active producer per device; the album producer on the new path, Decks
  still on `deck_cache`.
- Single manifest page, `max_frames = 32`, `next_cursor` present but the server
  may return one page.
- Album playback: `sequential` / `shuffle`, clamped `interval_s`, `loop` /
  `reshuffle` / `once`.
- Authoring: a Gallery folder gains a "Use as offline album" action that renders
  the folder's images to panel frames (fit/fill, order) and emits this manifest.

Later, without a contract change: manifest paging past 32, Decks as a
`collection` producer, and multiple concurrent producers per device.
