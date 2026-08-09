// Plain-node self-check (no test framework in this repo).
// Run: node tests/clamp_check.mjs
import assert from "node:assert/strict";
import { clampScale, computeRange, styleShortLabel } from "../client.js";

assert.equal(clampScale(undefined, 1.0, 0.7, 1.5), 1.0, "missing falls back to default");
assert.equal(clampScale(null, 1.0, 0.7, 1.5), 1.0, "null falls back to default");
assert.equal(clampScale("not-a-number", 1.0, 0.7, 1.5), 1.0, "unparsable falls back to default");
assert.equal(clampScale(1.3, 1.0, 0.7, 1.5), 1.3, "in-range value passes through");
assert.equal(clampScale(9, 1.0, 0.7, 1.5), 1.5, "above max clamps down");
assert.equal(clampScale(0.01, 1.0, 0.7, 1.5), 0.7, "below min clamps up");

// computeRange: day_start_hour/day_end_hour overrides (-1 = auto-fit).
assert.deepEqual(computeRange([]), { start: 8, end: 18 }, "no events falls back to 8-18");
assert.deepEqual(
  computeRange([{ events: [{ start: "2026-01-01T10:00:00", end: "2026-01-01T11:00:00" }] }]),
  { start: 9, end: 12 },
  "auto-fits with 1h padding around events"
);
assert.deepEqual(computeRange([], 0, 24), { start: 0, end: 24 }, "explicit 0/24 shows the whole day");
assert.deepEqual(computeRange([], 20, 20), { start: 20, end: 21 }, "equal start/end widened by 1h to stay renderable");

// date_label_style: short (current) / minimal (1-2 chars) / full (whole word).
const DOW_MINIMAL = { TUE: "TU", THU: "TH" };
const DOW_FULL = { TUE: "Tuesday" };
assert.equal(styleShortLabel("TUE", "short", DOW_MINIMAL), "TUE", "short passes the existing label through unchanged");
assert.equal(styleShortLabel("TUE", "minimal", DOW_MINIMAL), "TU", "minimal uses the disambiguation map");
assert.equal(styleShortLabel("ZZZ", "minimal", {}), "ZZ", "unmapped label falls back to first 2 chars");
assert.equal(styleShortLabel("TUE", "full", DOW_MINIMAL, DOW_FULL), "Tuesday", "full looks up the whole word");
assert.equal(styleShortLabel("ZZZ", "full", {}, {}), "ZZZ", "unmapped full falls back to the short label as-is");

console.log("clamp_check.mjs: all assertions passed");
