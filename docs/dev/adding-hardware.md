# Add hardware support

Tesserae's device model has two tiers:

1. **Protocols** in `devices/<id>/`. Python plugin folders that implement how
   Tesserae talks to firmware: `parse_status`, optional `validate_config`, the
   BYOS HTTP endpoints, the MQTT topic layout, the binary packing. Each
   protocol covers one wire format.
2. **Hardware catalog** in `hardware/<vendor>/<model>.json`. Data-only JSON
   files that declare a specific SKU's panel dimensions, gamut, vendor
   metadata, and any protocol-specific defaults. Each catalog entry
   references an existing protocol by id.

Adding a new e-paper SKU that speaks an existing protocol is a single JSON
file. No Python, no tests at the SKU level (the protocol's tests already
cover the wire format).

## When to use the catalog

Use a catalog entry when:

- Your hardware speaks an existing protocol (TRMNL BYOS, MQTT 4-bpp binary,
  MQTT PNG, REST pull).
- The only things that differ from existing SKUs are panel dimensions,
  gamut, vendor metadata, refresh cadence, or protocol-specific config.
- You want the SKU to ship with Tesserae without forking a Python module.

Write a new protocol folder under `devices/<id>/` when:

- Your hardware speaks a wire format Tesserae doesn't yet implement.
- You need custom `parse_status` / `validate_config` logic.
- You need new MQTT topics or new BYOS endpoint shapes.

If in doubt, lean toward the catalog. Most hardware is a panel plus an
existing protocol.

## The schema

The full schema lives at
[`schema/hardware.schema.json`](https://github.com/dmellok/tesserae/blob/main/schema/hardware.schema.json).
Key fields:

| Field | Required | Description |
|---|---|---|
| `tesserae_compat` | yes | Host major-version compat string, e.g. `"1.x"`. |
| `id` | yes | Unique kind id, matches `^[a-z][a-z0-9_]*$`. |
| `name` | yes | Human-readable name, shown in device pickers. |
| `vendor` | yes | Vendor name. |
| `protocol` | yes | Id of the protocol folder under `devices/` to inherit from. |
| `panel` | yes | Panel block: `{w, h, orientation?, gamut?, native_w?, native_h?, underscan?}`. |
| `url` | no | Product page URL. |
| `icon` | no | Phosphor icon slug (no `ph-` prefix). |
| `description` | no | One-paragraph description. |
| `protocol_config` | no | Free-form protocol-specific defaults. Each protocol owns its own validation. |
| `renderers` | no | Override for the protocol's default renderer list. Absent means inherit the protocol's renderers. |
| `touch` | no | `true` when the panel has a digitizer, so touch dispatch and the editors' Interaction UI apply. Absent means not tappable. |
| `config_schema_extends` | no | Additive merge over the protocol's `config_schema`. Use sparingly. |
| `refresh_floor_s` | no | The repaint floor the profile lists for this glass, in seconds. A declaration, not a guarantee: it appears in the "?" help on the device card's cadence fields and in the Companion API, and nothing gates on it. Not a poll cadence, since a poll is a conditional GET and a 304 never reaches the glass. |
| `buttons` | no | `false` when the SKU has no physical buttons (or its protocol forwards none): its device page shows no Controls button map and Home Assistant gets no Input event entity for it, unless it also has `touch`. Absent means it may have buttons. |
| `battery` | no | `false` for a mains-powered display (a TV): its device page shows no Power section. Absent means it may run on a battery. |
| `renderer_settings` | no | `false` when the kind sends the composition itself rather than its renderer's output (a push publisher that uploads the page image): its device page leaves out the renderer's client rotation, scale and letterbox settings. |
| `config_schema` field `group` | no | Name of a device page group for the field (e.g. `"Power"`); fields sharing a name sit together in a group of that name after Rendering. Absent keeps today's place (Identity, Rendering or Timing by type). |
| `config_schema` field `show_if` | no | Show the field only while other fields of the kind hold given values, e.g. `{"power_control": "switch"}`; hidden live as they change, still saved. |
| `image_format` | no | Wire format hint: `png` / `bin` / `webp`. |
| `notes_md` | no | Maintainer notes shown on the docs hardware page. |
| `deprecated_aliases` | no | Older ids this SKU also registers under, for back-compat after a rename. |

## A worked example

The bundled
[`hardware/trmnl/x.json`](https://github.com/dmellok/tesserae/blob/main/hardware/trmnl/x.json)
is the reference (`notes_md` shortened here):

```json
{
  "tesserae_compat": "1.x",
  "id": "trmnl_x",
  "name": "TRMNL X",
  "vendor": "TRMNL",
  "url": "https://shop.trmnl.com/products/trmnl-x",
  "icon": "device-tablet-speaker",
  "description": "TRMNL X, 10.3 inch ePaper at 1872x1404 with 16 grey levels. Stock firmware speaks TRMNL BYOS and reports its model as `x`; Tesserae sends it a 4-bit greyscale PNG (trmnl_png_gray16), with the 1-bit trmnl_png still selectable on the device card.",
  "protocol": "trmnl_client",
  "renderers": [
    "trmnl_png_gray16",
    "trmnl_png"
  ],
  "panel": {
    "w": 1872,
    "h": 1404,
    "orientation": "landscape",
    "name": "10.3 inch ePaper, 16-level greyscale",
    "gamut": "gray_16",
    "native_w": 1872,
    "native_h": 1404
  },
  "protocol_config": {
    "model_header": "x",
    "png_bit_depth": 4
  },
  "refresh_floor_s": 60,
  "image_format": "png",
  "notes_md": "Confirmed on real hardware running the stock TRMNL firmware. It auto-provisions on first poll; Tesserae provisions it on this kind and picks the native 1872x1404 panel from the Model header. ..."
}
```

## How loading works

1. At boot, `device_loader.discover()` walks `devices/` and registers each
   folder-based kind. `trmnl_client` registers here as a protocol.
2. The loader then walks `hardware/` recursively. The TRMNL X file passes
   schema validation, and the loader looks up `trmnl_client` in the
   registry.
3. The catalog builds a derived `Device`: shares the protocol's
   `parse_status` Python module, carries the SKU's own manifest (the
   panel block, name, vendor, `protocol_config`, etc.).
4. The derived Device registers under `trmnl_x`. The
   Settings UI's kind picker now offers it alongside folder-defined kinds.

The flow is identical to a folder-defined kind because the catalog-derived
Device is indistinguishable from a folder kind at the registry level.

## How a real device pairs

For a TRMNL-BYOS-compatible SKU (like the TRMNL X), once the catalog entry
is registered, pairing follows the standard TRMNL flow:

1. Point the device's TRMNL firmware at your Tesserae base URL (via the
   firmware's captive portal).
2. On first boot the device calls `GET /api/setup` with its MAC in the
   `Id` header. Tesserae creates the device instance, on the catalog SKU
   whose `protocol_config.model_header` matches the `Model` header (else
   the bare `trmnl_client` kind), and hands back a freshly minted access
   token. No admin click is needed.
3. The device polls `GET /api/display`. Tesserae's BYOS endpoint responds
   with the rendered frame URL.

For MQTT-based SKUs, the device subscribes to the topics the protocol
declares (with the SKU's instance id substituted). For REST-pull SKUs, the
device polls `/api/v1/device/<id>/frame`. See
[Install a client](../install/clients.md) for the per-client setup.

## Backwards compatibility

The loader follows three rules so existing installs see no behaviour
change when a new SKU lands:

- **Folder kinds always win on id conflict.** If a hardware JSON declares
  `id: trmnl_client` and a `devices/trmnl_client/` folder exists, the
  folder kind keeps its slot and the JSON entry surfaces as
  `"id already in use"` in the loader-error panel.
- **`deprecated_aliases` register the canonical kind under both names.**
  When you rename a SKU (say you shipped `acme_panel_v1` and want to
  standardise on `acme_panel`), add
  `"deprecated_aliases": ["acme_panel_v1"]` and existing device-instance
  files keyed on the old id keep resolving.
- **Orphan device instances surface as errors, not 500s.** If a user has
  an instance whose `kind_of` no longer resolves to anything, the Settings
  UI flags it as "missing kind" rather than crashing the page.

## Contributing a new SKU

1. **Pick a protocol.** Check `devices/` for an existing protocol that
   matches your hardware's wire format. If none fit, you'll need a new
   protocol folder (see [Architecture](architecture.md)).
2. **Write the JSON file.** Drop it under `hardware/<vendor>/<model>.json`.
   The vendor folder is just an organisation hint, not load-bearing for
   the loader.
3. **Test locally.**

    ```sh
    .venv/bin/pytest tests/test_hardware_catalog.py -q
    .venv/bin/python -m app.main --dev
    ```

    `pytest` confirms your schema validation passes. The dev server lets
    you confirm the kind appears in **Settings → Devices → Add device**
    under the right vendor.

4. **Verify on real hardware** if you have it. Pair a device against the
   new SKU, push a render, confirm the panel paints.
5. **Open a PR** with the JSON file. The bundled
   `trmnl/x.json` is the shape to mirror. Update
   [`docs/compatibility.md`](../compatibility.md) too if your SKU
   introduces a new resolution / gamut combination the matrix doesn't
   already cover.

## Per-protocol notes

What each protocol currently reads from `protocol_config`:

### `trmnl_client`

- `model_header` (string): the value the firmware sends in the `Model`
  HTTP header. Read by `app/trmnl_api.py` to drive auto-provision and
  panel-dim fallback for native TRMNL devices.
- `png_bit_depth` (1 / 2 / 4 / 8): fixed PNG bit depth this SKU asks its
  renderer for (the TRMNL X uses `4`). Read by `app/renderer_loader.py`.
- `renderer_min_firmware` (object, renderer id -> version): the lowest
  firmware that can paint each renderer's output, so a device moves onto
  that renderer once it reports a new enough build. Read by
  `app/device_service.py`.

### `pi_bin_client` / `pi_png_client` / `esp32_client` / `esp32_bw_client` / `pico_bin_client`

No protocol-specific `protocol_config` fields are read today. The SKU's
`panel` block carries everything the renderers need.

As protocols add new fields, they'll be documented here. The schema's
`additionalProperties: true` on `protocol_config` means you can stash
extra keys for a protocol you're prototyping without a schema PR.

## Catalog vs. community marketplace

The catalog under `hardware/` ships bundled in the repo. A community
distribution channel (similar to the
[`tesserae-widgets`](https://github.com/dmellok/tesserae-widgets) catalog
for widgets) is a future task. For now, hardware support contributions go
via PRs to the main repo.
