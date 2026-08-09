// Plain-node self-check (no test framework in this repo).
// Run: node tests/clamp_check.mjs
import assert from "node:assert/strict";
import { clampScale, styleShortLabel } from "../client.js";

assert.equal(clampScale(undefined, 1.0, 0.1, 5.0), 1.0, "missing falls back to default");
assert.equal(clampScale(null, 1.0, 0.1, 5.0), 1.0, "null falls back to default");
assert.equal(clampScale("not-a-number", 1.0, 0.1, 5.0), 1.0, "unparsable falls back to default");
assert.equal(clampScale(2.5, 1.0, 0.1, 5.0), 2.5, "in-range value passes through");
assert.equal(clampScale(9, 1.0, 0.1, 5.0), 5.0, "above max clamps down");
assert.equal(clampScale(0.0, 1.0, 0.1, 5.0), 0.1, "below min clamps up");

// date_label_style: short (current) / minimal (1-2 chars) / full (whole word).
const DOW_MINIMAL = { Tue: "Tu", Thu: "Th" };
const DOW_FULL = { Tue: "Tuesday" };
assert.equal(styleShortLabel("Tue", "short", DOW_MINIMAL), "Tue", "short passes the existing label through unchanged");
assert.equal(styleShortLabel("Tue", "minimal", DOW_MINIMAL), "Tu", "minimal uses the disambiguation map");
assert.equal(styleShortLabel("Zzz", "minimal", {}), "Zz", "unmapped label falls back to first 2 chars");
assert.equal(styleShortLabel("Tue", "full", DOW_MINIMAL, DOW_FULL), "Tuesday", "full looks up the whole word");
assert.equal(styleShortLabel("Zzz", "full", {}, {}), "Zzz", "unmapped full falls back to the short label as-is");

console.log("clamp_check.mjs: all assertions passed");
