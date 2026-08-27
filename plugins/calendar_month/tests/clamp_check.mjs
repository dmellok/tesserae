// Plain-node self-check (no test framework in this repo).
// Run: node tests/clamp_check.mjs
import assert from "node:assert/strict";
import { clampScale, localizedFull, minimalMapFor, styleLabel } from "../client.js";

assert.equal(clampScale(undefined, 1.0, 0.1, 5.0), 1.0, "missing falls back to default");
assert.equal(clampScale(null, 1.0, 0.1, 5.0), 1.0, "null falls back to default");
assert.equal(clampScale("not-a-number", 1.0, 0.1, 5.0), 1.0, "unparsable falls back to default");
assert.equal(clampScale(2.5, 1.0, 0.1, 5.0), 2.5, "in-range value passes through");
assert.equal(clampScale(9, 1.0, 0.1, 5.0), 5.0, "above max clamps down");
assert.equal(clampScale(0.0, 1.0, 0.1, 5.0), 0.1, "below min clamps up");

// date_label_style: full (default) / short (3-letter) / minimal (1-2
// chars, disambiguated). Base label is always the full name; display
// casing is CSS's job (--label-transform), not styleLabel's.
const DOW_MINIMAL = { Tuesday: "Tu", Thursday: "Th" };
assert.equal(styleLabel("Monday", "full", {}), "Monday", "full keeps the whole word, natural casing");
assert.equal(styleLabel("Tuesday", "short", {}), "Tue", "short is a 3-letter abbreviation");
assert.equal(styleLabel("Tuesday", "minimal", DOW_MINIMAL), "Tu", "minimal uses the disambiguation map");
assert.equal(styleLabel("Thursday", "minimal", DOW_MINIMAL), "Th", "Tue/Thu don't collide at 1 char");
assert.equal(styleLabel("Zzyzx", "minimal", {}), "Zz", "unmapped label falls back to first 2 chars");

// locales contract (docs/widgets.md#locales-strings): localizedFull() /
// minimalMapFor() are what feed a locale-aware name into styleLabel above.
// A Monday, so weekday indices are unambiguous either way.
const monday = new Date(2026, 0, 5);
assert.equal(localizedFull(monday, "weekday", "en"), "Monday", "English reads the hardcoded array, not Intl");
assert.equal(localizedFull(monday, "month", "en-US"), "January", "English variants (en-US) still count as English");
assert.equal(localizedFull(monday, "weekday", "fr"), "lundi", "non-English asks Intl for the real localised name");
assert.equal(localizedFull(monday, "month", "fr"), "janvier", "same for months");
assert.equal(localizedFull(monday, "weekday", ""), "Monday", "no locale at all defaults to English");

assert.equal(minimalMapFor("weekday", "en").Tuesday, "Tu", "English gets the real disambiguation map");
assert.deepEqual(minimalMapFor("weekday", "fr"), {}, "non-English gets {} -- styleLabel's generic slice, not English abbreviations");
assert.deepEqual(minimalMapFor("month", "de"), {}, "same for months");

console.log("clamp_check.mjs: all assertions passed");
