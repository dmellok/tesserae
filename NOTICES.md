# Third-party attribution

Tesserae bundles or ports numeric data and reference algorithms from
the following projects. Code-level dependencies are tracked in
[pyproject.toml](pyproject.toml); this file lists upstream
contributions that aren't reflected in a package install.

## paperlesspaper/epdoptimize

Tesserae's calibrated Spectra 6 and ACeP palettes (in
[app/quantizer.py](app/quantizer.py)) are ported from
[paperlesspaper/epdoptimize](https://github.com/paperlesspaper/epdoptimize),
specifically the `spectra6` and `acep` profiles in
`src/dither/data/default-palettes.json`.

The upstream project is licensed under the Apache License 2.0. The
calibration measurements characterise how Spectra 6 and ACeP panels
reproduce the nominal sRGB primaries under normal viewing light;
Tesserae uses them as the target colour set during dithering so the
panel's actual reproduced output matches the source image more
faithfully than nominal-palette quantization would. The on-the-wire
nibble values are unchanged.

* Upstream: https://github.com/paperlesspaper/epdoptimize
* License: Apache License 2.0
* Files used: `src/dither/data/default-palettes.json` (calibration
  values only; the dithering algorithms remain Tesserae's own
  Python/NumPy implementations).
