// Flat ESLint config for the hand-rolled static/ frontend.
//
// The bar is intentionally low for now: this runs as an advisory CI job
// (continue-on-error) so findings surface without blocking main while the
// legacy files are cleaned up. Ratchet rules up and drop the advisory flag
// once the existing warnings are worked down.
import js from "@eslint/js";
import globals from "globals";

export default [
  {
    // Vendored libraries, generated docs output, phosphor icon sprites, and
    // node deps are not ours to lint.
    ignores: [
      "static/vendor/**",
      "static/icons/**",
      "site/**",
      "node_modules/**",
    ],
  },
  js.configs.recommended,
  {
    files: ["static/**/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      // Most files are IIFE-wrapped classic scripts, not ES modules.
      sourceType: "script",
      globals: {
        ...globals.browser,
      },
    },
  },
  {
    // The two frontend entry points that genuinely use import/export.
    files: ["static/spectra-chart.js", "static/pages/json-highlight.js"],
    languageOptions: {
      sourceType: "module",
    },
  },
];
