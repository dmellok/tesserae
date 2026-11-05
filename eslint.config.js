// Flat ESLint config for the hand-rolled static/ frontend.
//
// The bar is js.configs.recommended. static/ is clean of it, and CI's
// frontend-lint job fails on any new error.
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
        // Set on window by a classic <script> the page loads first: the
        // vendored Chart.js and CodeMirror, and our own shared modules.
        Chart: "readonly",
        CodeMirror: "readonly",
        TouchInteraction: "readonly",
        PanelsDecorate: "readonly",
        PanelsAgentRail: "readonly",
      },
    },
  },
  {
    // Frontend modules that use import/export.
    files: [
      "static/composer.js",
      "static/spectra-chart.js",
      "static/pages/json-highlight.js",
      "static/widget-i18n.js",
    ],
    languageOptions: {
      sourceType: "module",
    },
  },
];
