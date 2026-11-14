# Add hardware support

Add a hardware model through a protocol or a catalog entry:

1. Protocols in `devices/<id>/` are Python plugins, one per wire format.
   They define `parse_status`, optional `validate_config`, BYOS HTTP endpoints,
   MQTT topics and binary packing.
2. Hardware catalog entries in `hardware/<vendor>/<model>.json` describe a model (SKU).
   They contain panel dimensions, colour gamut, vendor metadata and protocol defaults.
   Each JSON file references an existing protocol by id.

For hardware using an existing protocol, add one JSON file.
No Python or SKU-level tests are needed; protocol tests cover the wire format.

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

Most hardware uses an existing protocol. Use the catalog if unsure.

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
| `panel` | yes | Panel block: `{w, h, orientation?, name?, gamut?, native_w?, native_h?, col_offset?, underscan?}`. |
| `url` | no | Product page URL. |
| `icon` | no | Phosphor icon slug (no `ph-` prefix). |
| `description` | no | One-paragraph description. |
| `protocol_config` | no | Free-form protocol-specific defaults. Each protocol owns its own validation. |
| `renderers` | no | Override for the protocol's default renderer list. Absent means inherit the protocol's renderers. |
| `colour_depths` | no | Colour depths the board can paint, as panel gamut -> renderer id (each a renderer the kind offers). The first renderer's depth is the baseline; another depth is used only once the device advertises that gamut in the `gamuts` array of its register / status body. Drives the device card's Colour depth control. |
| `colour_depth_default` | no | The `colour_depths` key a device that advertises it starts on when no depth has been picked. |
| `auto_select` | no | `false` stops a device's self-report resolving to this SKU automatically (relay pairing infers the kind from the reported protocol + gamut). For a variant that differs from a sibling SKU only in which firmware image was flashed. Default `true`. |
| `touch` | no | `true` when the panel has a digitizer, so touch dispatch and the editors' Interaction UI apply. Absent means not tappable. |
| `config_schema_extends` | no | Additive merge over the protocol's `config_schema`. Use sparingly. |
| `refresh_floor_s` | no | The repaint floor the profile lists for this glass, in seconds. A declaration, not a guarantee: it appears in the "?" help on the device card's cadence fields and in the Companion API, and nothing gates on it. Not a poll cadence, since a poll is a conditional GET and a 304 never reaches the glass. |
| `buttons` | no | `false` when the SKU has no physical buttons (or its protocol forwards none): its device page shows no Controls button map and Home Assistant gets no Input event entity for it, unless it also has `touch`. Absent means it may have buttons. |
| `battery` | no | `false` for a mains-powered display (a TV): its device page shows no Power section. Absent means it may run on a battery. |
| `renderer_settings` | no | `false` when the kind sends the composition itself rather than its renderer's output (a push publisher that uploads the page image): its device page leaves out the renderer's client rotation, scale and letterbox settings. |
| `palettes` | no | Panel gamuts the operator may pick for a device of this kind, for hardware sold in several colour sets that cannot report which it has (an OpenDisplay tag). Its device page shows a Colour palette choice in Panel, stored as `panel.gamut`. Absent means the kind's `panel.gamut` is fixed. |
| `config_schema` field `retired` | no | `true` for a setting that newer behaviour replaced. The device page shows it only while a device still holds a non-default value, so it can be cleared; new devices never see it. The stored value is still honoured. |
| `config_schema` field `group` | no | Device page group for the field (e.g. `"Power"`). Fields with the same group name sit together in that group, after Rendering. Without it, the field goes in Identity, Rendering or Timing by type. |
| `config_schema` field `show_if` | no | Show the field only while other fields hold given values, e.g. `{"power_control": "switch"}`. It hides and shows live, and its value is still saved. |
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

Catalog-derived and folder-defined kinds behave identically in the registry.

## How a real device pairs

Once registered, a TRMNL BYOS model such as the TRMNL X pairs as follows:

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

New SKUs do not change existing installs. The loader follows these rules:

- Folder kinds always win on id conflict. If a hardware JSON declares
  `id: trmnl_client` and a `devices/trmnl_client/` folder exists, the
  folder kind keeps its slot and the JSON entry surfaces as
  `"id already in use"` in the loader-error panel.
- `deprecated_aliases` register the canonical kind under both names.
  When you rename a SKU (say you shipped `acme_panel_v1` and want to
  standardise on `acme_panel`), add
  `"deprecated_aliases": ["acme_panel_v1"]` and existing device-instance
  files keyed on the old id keep resolving.
- Orphan device instances surface as errors, not 500s. If a user has
  an instance whose `kind_of` no longer resolves to anything, the Settings
  UI flags it as "missing kind" rather than crashing the page.

## Contributing a new SKU

1. Pick a protocol. Check `devices/` for an existing protocol that
   matches your hardware's wire format. If none fit, you'll need a new
   protocol folder (see [Architecture](architecture.md)).
2. Write the JSON file. Drop it under `hardware/<vendor>/<model>.json`.
   The vendor folder is for organisation; the loader does not depend on it.
3. Test locally.

    ```sh
    .venv/bin/pytest tests/test_hardware_catalog.py -q
    .venv/bin/python -m app.main --dev
    ```

    `pytest` confirms your schema validation passes. The dev server lets
    you confirm the kind appears in **Settings → Devices → Add device**
    under the right vendor.

4. Verify on real hardware if you have it. Pair a device against the
   new SKU, push a render, confirm the panel paints.
5. Open a PR with the JSON file. The bundled
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

`additionalProperties: true` on `protocol_config` lets you add prototype keys
without a schema PR. New protocol fields will be documented here.

## Catalog vs. community marketplace

The `hardware/` catalog ships with Tesserae. A community catalog like
[`tesserae-widgets`](https://github.com/dmellok/tesserae-widgets) is planned.
Contribute hardware support through PRs to the main repo.
