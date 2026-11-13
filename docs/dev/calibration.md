# Palette calibration

Open Settings → Devices → any instance → **Calibration** to adjust the panel's
colours without a colorimeter. A palette profile stores its six or seven RGB
colours in JSON, with tone, dither and edge settings.

## Where profiles live

- Bundled presets ship in
  [`app/palette_profiles/bundled.py`](https://github.com/dmellok/tesserae/blob/main/app/palette_profiles/bundled.py).
  They are read-only. Their `based_on` + `attribution` fields supply the
  attribution line under the picker.
- User profiles live at `data/palette_profiles/<slug>.json`.
  "Save as new profile" and "Import profile from JSON" both write here.

A device's active profile lives in the settings store at
`settings.devices.<instance_id>.palette_profile_slug`. Empty string
means no override (the built-in `_CALIBRATED_PALETTES` in
`app/quantizer.py` wins).

## Profile schema

```json
{
  "slug": "my-warm-study",
  "name": "My warm study",
  "family": "spectra6",
  "based_on": "paperlesspaper-spectra6",
  "attribution": "https://github.com/paperlesspaper/epdoptimize",
  "notes": "Evening lamp light, glass frame",
  "saved_at": "2026-07-04T10:30:00+00:00",
  "palette": {
    "black": "#1F2226", "white": "#B9C7C9",
    "yellow": "#C1BB1E", "red": "#62201E",
    "blue": "#233F8E", "green": "#35563A"
  },
  "tone": {
    "exposure": 0, "contrast": 1.0, "saturation": 1.0,
    "s_curve": 0, "lab_compress_min": 0, "lab_compress_max": 100
  },
  "dither": {
    "algorithm": "floyd-steinberg", "serpentine": true,
    "color_match": "rgb", "diffusion_strength": 100
  },
  "edges": {
    "preserve_line_art": false, "smoothing_radius": 0,
    "protect_native_colours": 0
  }
}
```

Unknown top-level fields are ignored, so newer profiles can load with extra
fields. Tone values outside the allowed range are clamped. Bad hex codes use
`#000000` so a corrupt profile can't crash a render.

## What v0.67.0 (phase 1) wires

- The push manager passes the palette override to each `.bin` renderer
  (`esp32_bin`, `pi_bin`, `pico_bin`) as an optional keyword argument on
  [`pack_to_panel_bin`](https://github.com/dmellok/tesserae/blob/main/app/quantizer.py).
  When a device has an active profile AND the renderer clone has
  `calibrated=True`, the profile's palette wins over the built-in
  `_CALIBRATED_PALETTES` lookup. `trmnl_png_color` also honours the
  override server-side.
- Contrast and saturation are in the Calibration tab.
  They write to `settings.renderers.<clone_id>.contrast` and
  `.saturation`.

## What v0.67.1 (phase 2) adds

- `exposure` (-100..+100): shifts brightness linearly before reducing colours
  to the palette. `_apply_exposure` in `app/quantizer.py` skips the neutral
  value; devices without a profile pay no cost.
- `s_curve` (-100..+100): adjusts mid-tone contrast with a sigmoid curve.
  Positive values push mid-tones away from grey; negative values flatten them.
- `serpentine` (bool): reverses alternate scan rows during error-diffusion
  dithering. This removes diagonal "worming" patterns from gradients.
- `diffusion_strength` (0..200): scales the error passed to neighbouring pixels.
  100 is the default; <100 is softer, 0 uses the nearest palette colour,
  and >100 exaggerates the effect (rarely useful).
- Calibration sliders and the toggle update the active profile through
  `POST /settings/devices/<id>/palette/update-tone`. Editing a
  bundled preset forks it into an editable copy on first tweak;
  user profiles are edited in place.

## What v0.67.2 (phase 3) adds

Edge handling, wired for `esp32_bin` / `pi_bin` /
`pico_bin`:

- `smoothing_radius` (0-3 px): Gaussian blur before tone mapping.
  It softens antialiased edges to prevent noisy dither trails.
  `radius=0` skips the work at no cost.
- `preserve_line_art` (bool): protects sharp edges after dithering.
  It runs `ImageFilter.FIND_EDGES` on tone-mapped luminance, thresholds at
  40/255 and dilates 3x3. Edge pixels use the nearest palette colour;
  photographic regions keep full error-diffusion dithering.

  This costs one extra `Image.quantize(dither=NONE)` pass and a mask copy.
  The cost is near-zero for all-photo sources.

## What v0.306.0 adds

- `protect_native_colours` (0-48): keeps pixels close to the panel's own
  colours, measured as Euclidean distance in sRGB. Without protection,
  neighbouring pixels can spread dither error into a matching colour and
  cause speckles, even though that colour produces no error itself.

  Above 0, matching pixels use the nearest-colour override from
  `preserve_line_art`. With error-diffusion dithering, they also stop passing
  error onward. This prevents error spreading through a protected region.

  The mask uses the source after exposure, the S-curve, LAB compression and
  the calibrated pre-pass. An earlier mask would miss colours those steps
  changed. Around 16-24 catches bundled light themes' warm paper backgrounds;
  0 (off) keeps the previous byte-for-byte output.

  Supported by `esp32_bin` / `pi_bin` / `pico_bin` and `esp32_bw_bin`.
  Black-and-white backgrounds gain little from diffusion. Greyscale packers
  exclude this control: almost every pixel is near a colour in the 16-level
  ramp, so protection would flatten photographs.

## What v0.67.4 adds

- LAB dynamic-range compression (`lab_compress_min` /
  `lab_compress_max` on the profile, 0-100): `_compress_lab_range`
  rescales L* only, leaving hue alone. Overrides the linear
  `_compress_to_calibrated_range` when set; the default `(0, 100)` is
  a no-op.
- Colour-match modes (`rgb` / `lab` / `chroma-aware`) on the
  error-diffusion dithers. `chroma-aware` weights a*/b* 2x to keep hue
  over lightness.

## What still isn't wired (later phases)

- `pi_png` uses the `inky` library to match colours to the panel.
  Palette override needs a client-side change on `tesserae-device-pi`.
- `trmnl_png` is mono; palette calibration doesn't apply.

## What phase 1 doesn't cover

Phase 1 has the same client exclusions above. It also excludes the advanced
tone controls.

## Adding a new bundled preset

1. Grab the palette hexes from your source (epdoptimize's
   `default-palettes.json` is the current source).
2. Add a `_profile(...)` call to `BUNDLED_PROFILES` in
   [`app/palette_profiles/bundled.py`](https://github.com/dmellok/tesserae/blob/main/app/palette_profiles/bundled.py)
   with a stable slug, human-facing name, `family`, and
   `based_on` / `attribution` if the palette isn't your own
   measurement.
3. If you introduce a new gamut, update `_palette_family_for` in
   `app/settings/index_routes.py` so the family picker knows which
   gamut string maps to which profile family.
4. Bump the module docstring + a line in
   [`NOTICES.md`](https://github.com/dmellok/tesserae/blob/main/NOTICES.md)
   if the palette data comes from a licensed upstream.

## Sharing profiles

The Calibration tab has **Export JSON** and **Import profile from
JSON** actions. Exported files follow the schema above verbatim; drop
one into `data/palette_profiles/` on another Tesserae install to make
it selectable there.
