# Palette calibration

Tesserae's Calibration tab (Settings → Devices → any instance →
**Calibration**) collects everything that lets a user match a panel's
rendered output to what their eyes see, without a colorimeter. The
core primitive is the **palette profile**: a JSON blob describing the
six or seven RGB values the panel actually reproduces, plus tone-
mapping / dither / edge knobs that live around the palette itself.

## Where profiles live

- **Bundled** presets ship in the source tree at
  [`app/palette_profiles/bundled.py`](https://github.com/dmellok/tesserae/blob/main/app/palette_profiles/bundled.py).
  These are read-only; every entry carries `based_on` +
  `attribution` fields so the Calibration tab can render an
  "attribution" line under the picker.
- **User** profiles live at `data/palette_profiles/<slug>.json`.
  Anything a user saves via "Save as new profile" or imports via
  "Import profile from JSON" lands here.

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
  "edges": {"preserve_line_art": false, "smoothing_radius": 0}
}
```

Unknown top-level fields are ignored on load, so profiles are
forward-compatible with schema additions. Out-of-range tone values
are clamped rather than rejected. Bad hex codes fall through to
`#000000` so a corrupt profile can't crash a render.

## What v0.67.0 (phase 1) wires

- **Palette override** flows through the app-side push manager into
  each `.bin` renderer (`esp32_bin`, `pi_bin`, `pico_bin`) as an
  optional kwarg on
  [`pack_to_panel_bin`](https://github.com/dmellok/tesserae/blob/main/app/quantizer.py).
  When a device has an active profile AND the renderer clone has
  `calibrated=True`, the profile's palette wins over the built-in
  `_CALIBRATED_PALETTES` lookup. `trmnl_png_color` also honours the
  override server-side.
- **Contrast + saturation** move to the Calibration tab (they were
  under Rendering → Picture quality). Storage is unchanged; both
  fields still write to `settings.renderers.<clone_id>.contrast` and
  `.saturation`.

## What v0.67.1 (phase 2) adds

- **`exposure` (-100..+100)** — linear brightness shift, applied
  before palette quantisation. `_apply_exposure` in
  `app/quantizer.py` short-circuits on the neutral value so
  no-profile devices pay no cost.
- **`s_curve` (-100..+100)** — sigmoid mid-tone punch. Positive
  values push mid-tones away from grey (more contrast), negative
  values flatten them.
- **`serpentine` (bool)** — reverses every other row's scan
  direction on error-diffusion dithers so diagonal "worming"
  artefacts disappear from gradient regions.
- **`diffusion_strength` (0..200)** — scales propagated error
  amount on error-diffusion dithers. 100 = default, <100 = softer
  / flatter, 0 = nearest-neighbour quantise, >100 = exaggerated
  (rarely useful).
- **UI**: sliders + toggle on the Calibration tab card writing to
  the active profile via
  `POST /settings/devices/<id>/palette/update-tone`. Editing a
  bundled preset forks it into an editable copy on first tweak;
  user profiles are edited in place.

## What v0.67.2 (phase 3) adds

Experimental edge handling, wired for `esp32_bin` / `pi_bin` /
`pico_bin`:

- **`smoothing_radius` (0-3 px)** — Gaussian blur applied to the
  source before tone mapping. Softens antialiased edges before
  error-diffusion has a chance to build a noisy tail along them.
  Zero-cost fast path at `radius=0`.
- **`preserve_line_art` (bool)** — post-dither pass. Detects sharp
  edges in the tone-mapped source via `ImageFilter.FIND_EDGES` on
  the luminance channel, thresholds at 40/255 and dilates 3x3.
  Detected pixels get replaced with plain nearest-neighbour
  quantise output; the surrounding photographic regions keep their
  full error-diffusion dither. Cost is one extra
  `Image.quantize(dither=NONE)` pass plus a mask copy; near-zero on
  all-photo sources.

## What still isn't wired (later phases)

- **LAB dynamic-range compression** (`lab_compress_min` /
  `lab_compress_max` on the profile) — stored but ignored; the
  existing linear `_compress_to_calibrated_range` still runs when
  `calibrated=True`.
- **Colour-match modes** (RGB / LAB / chroma-aware) — stored but
  ignored; RGB nearest is the only path.
- **`pi_png`** — palette override would need a firmware-side
  change on `tesserae-device-pi-png`.
- **`trmnl_png`** — mono; palette calibration doesn't apply.

## What phase 1 doesn't cover

- `pi_png` (Pi client, uses the `inky` library for gamut projection)
  — palette override would need a firmware-side change on
  `tesserae-device-pi-png`.
- `trmnl_png` (mono) — no palette calibration meaningful.
- The advanced tone knobs (see above).

## Adding a new bundled preset

1. Grab the palette hexes from your source (epdoptimize's
   `default-palettes.json` is the current wellspring).
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
