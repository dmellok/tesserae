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
