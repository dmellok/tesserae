# Third-party attribution

Tesserae bundles or ports numeric data and reference algorithms from
the following projects. Code-level dependencies are tracked in
[pyproject.toml](pyproject.toml); this file lists upstream contributions
that aren't reflected in a package install.

## paperlesspaper/epdoptimize

Tesserae's optional calibrated Spectra 6 and ACeP palettes (in
[app/quantizer.py](app/quantizer.py) as
`WAVESHARE_E6_CALIBRATED_PALETTE` and `INKY_7COLOUR_CALIBRATED_PALETTE`)
are ported from
[paperlesspaper/epdoptimize](https://github.com/paperlesspaper/epdoptimize),
specifically the `spectra6` and `acep` profiles in
`src/dither/data/default-palettes.json`.

The upstream project is licensed under the Apache License 2.0. The
calibration measurements characterise how Spectra 6 and ACeP panels
reproduce the nominal sRGB primaries under normal viewing light.
Tesserae uses them, gated by the per-device `calibrated` toggle in
the `esp32_bin` and `pi_bin` renderer settings, as the target colour
set during dithering, paired with a linear tone-mapping pre-pass that
squeezes the source range into the calibrated black/white band so
Floyd-Steinberg has somewhere to spread its error. The on-the-wire
nibble values are unchanged.

The tone-mapping pre-pass (`_compress_to_calibrated_range` in
`app/quantizer.py`) is a pragmatic linear approximation of
epdoptimize's LAB dynamic-range compression; the dithering algorithms
themselves remain Tesserae's own Python/NumPy implementations.

* Upstream: https://github.com/paperlesspaper/epdoptimize
* License: Apache License 2.0
* Files used: `src/dither/data/default-palettes.json` (calibration
  values only).

**v0.67+ — Palette recalibration presets.** The v0.67 Calibration tab
adds a "Palette recalibration" section that ships every measured
palette profile from
[`default-palettes.json`](https://github.com/paperlesspaper/epdoptimize/blob/main/src/dither/data/default-palettes.json)
that Tesserae's gamuts have room for: paperlesspaper's `spectra6`,
`spectra6legacy`, `spectra6-boeber`, `aitjcize-spectra6`, and the
seven-colour `acep` profile. Each bundled preset carries `based_on`
and `attribution` fields that surface as a "via paperlesspaper /
epdoptimize" chip on the picker card so end users see the source of
the calibration data. The presets live in
[`app/palette_profiles/bundled.py`](app/palette_profiles/bundled.py);
user-authored profiles are saved to
`data/palette_profiles/<slug>.json` and follow the same schema.

## varanu5 — PicPak 4.2" BWRY calibration

The 4-colour BWRY calibrated palette (in
[app/quantizer.py](app/quantizer.py) as `BWRY_4_CALIBRATED_PALETTE`, and
shipped as the `picpak-bwry-calibrated` preset in
[`app/palette_profiles/bundled.py`](app/palette_profiles/bundled.py)) was
measured against a physical PicPak 4.2" black/white/red/yellow panel by
[varanu5](https://github.com/varanu5), author of the PicPak community
firmware. epdoptimize carries no BWRY profile, so these values are an
independent contribution rather than a port.

The preset carries `based_on` and `attribution` fields that surface as a
"via varanu5" chip on the picker card, the same way the
paperlesspaper-derived presets do.

* Upstream: https://github.com/varanu5
* PicPak firmware: https://github.com/varanu5/picpak-tesserae-client
