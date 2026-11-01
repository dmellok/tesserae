# Display-accurate previews

`app/display_color.py:display_color_mode` reads `Device.panel`, which the
loader resolves from the catalog, instance settings and the selected renderer
(`_renderer_gamut`). Mono maps to `mono` / 2 levels only when the dither is
`none`; any other dither (or none resolved) maps to `mono-dither` / 2 levels,
`gray_N` to `greyN` / N levels (including 4, 8 and 16), and color palettes or
unknown panels to `color` / 0 grey levels. Visionect Place & Play 32 is
`gray_16`.
The helper also resolves the selected clone's dither setting, including fixed
renderer overrides. Dither `none` does not change its level count.

The request-cached Jinja `preview_color` resolver and Lineups view model use
this helper. `data-gamut` and `data-grey-levels` sit on preview media or its
immediate wrapper. Global `static/style/preview-color.css` filters grey media with
`grayscale(1)`, `mono-dither` media likewise (error diffusion and ordered
dithering average to grey at thumbnail size, so a dithered 1-bit frame reads as
a smooth grey photo) and `mono` media (dither `none`) through the shell's single
`tess-mono` SVG threshold filter. Color stays unchanged. Grey ramps
deliberately remain smooth at thumbnail size: these are color-range approximations, not spatial
dither or calibrated-palette simulations. No images are regenerated.

Unbound dashboards keep color. Multiple targets use their common mode and
level count when they agree, otherwise color; unknown/removed targets also
keep color. Lineups resolve each display separately, even for a shared lineup.
The JSON additions preserve schema version 1.

## Thumbnail audit

- Devices list quick panels and device page: shared `_device_parts.html`
  latest-render thumbnail. `/preview/<id>.png` serves the **composition**, so
  this needs filtering even when delivery uses a quantised renderer. Legacy
  entries that have no composition digest fall back to the real artifact and
  are explicitly excluded with `data-device-quantised`. For a pull device with
  a newer image waiting, the big thumbnail is the frame's last fetched image
  (`/renders/<composition>.png`) and a small "Queued" one (`/preview`) sits
  under it; both carry the same color attributes.
- Dashboards list: row image, opened preview and hover popover, including live
  iframe fallbacks (`pages_list.html`). Detached hover copies the row metadata.
- Lineups: display header, step tiles and hover enlargement (`_lineups.html`
  and `lineups_render.js`), detached gallery popover, timeline step and carried
  hold thumbnails. All use the owning display. Older lineup editor filmstrip
  (`deck_editor.js`) follows its display chooser too.
- History: composition thumbnails and every Open frame link; Events: push
  composition thumbnails. Shared lightbox copies the originating color mode.
  History uses the event's original target IDs, not today's page bindings.
- Send: device chooser updates image, blurred image background, note and web
  iframe previews. The upload/file chip remains the original source image.
- Dashboard composer: bound HTML preview groups use the group's display modes.
  The explicit quantised Panel view is marked `data-device-quantised` and
  skipped, preserving its actual palette/dither. Device calibration pattern
  previews already use native palette output and are left untouched.
- Smart sync cards contain status text, not separate image previews; shared
  device thumbnails use the Devices path above.
- Artwork, photo pickers/viewers, widget/template catalogs, theme swatches,
  layout diagrams, freeform canvas assets and QR/brand images show original
  assets or generic content without a display target and remain unchanged.
- Raw render/preview URLs, Companion API images and `/mirror` are delivery/API
  surfaces, not admin thumbnails; their bytes and behaviour are unchanged.
