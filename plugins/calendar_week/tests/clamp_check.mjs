// Plain-node self-check (no test framework in this repo).
// Run: node tests/clamp_check.mjs
import assert from "node:assert/strict";
import { clampScale, clampToDay, computeRange, localizedFull, minimalMapFor, pctSpan, styleLabel } from "../client.js";

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

// clampToDay: the server repeats a multi-day timed event's original
// start/end on every covered day's bucket, so each day-copy must clamp
// to *that* day, not redraw at the trip's original start hour.
const trip = { start: "2026-08-09T16:00:00", end: "2026-08-14T10:00:00" };
assert.deepEqual(clampToDay(trip, "2026-08-09"), { s: 16, e: 24 }, "start day keeps the real start hour, runs to midnight");
assert.deepEqual(clampToDay(trip, "2026-08-11"), { s: 0, e: 24 }, "a pass-through day runs the full 24h, not the start hour");
assert.deepEqual(clampToDay(trip, "2026-08-14"), { s: 0, e: 10 }, "end day starts at midnight, keeps the real end hour");
assert.deepEqual(
  clampToDay({ start: "2026-08-09T16:00:00", end: "2026-08-09T17:00:00" }, "2026-08-09"),
  { s: 16, e: 17 },
  "single-day event is unaffected"
);

// pctSpan: a block's [s,e) hour span must clamp to the visible lane
// without ever exceeding 100% height, even when day_start_hour/
// day_end_hour narrows the range well below a pass-through day's full
// 0-24h span (regression: height used to be derived from the *unclamped*
// span, so it could exceed 100% and spill past the lane's bottom edge).
assert.deepEqual(pctSpan(9, 17, 8, 10), { top: 10, height: 80 }, "fully inside the range renders normally");
assert.deepEqual(pctSpan(0, 24, 8, 4), { top: 0, height: 100 }, "a full 0-24h pass-through day clamps to exactly the lane, not beyond");
assert.deepEqual(pctSpan(0, 24, 8, 10), { top: 0, height: 100 }, "still clamps to 100% with a wider (but still partial) range");
assert.deepEqual(pctSpan(20, 24, 8, 4), { top: 100, height: 2 }, "a span entirely after the visible range clamps to the bottom edge, not off it");

// date_label_style: full (default) / short (3-letter) / minimal (1-2
// chars, disambiguated). Base label is always the full name.
const DOW_MINIMAL = { Tuesday: "Tu", Thursday: "Th" };
assert.equal(styleLabel("Monday", "full", {}), "MONDAY", "full keeps the whole word, upper-cased");
assert.equal(styleLabel("Tuesday", "short", {}), "TUE", "short is a 3-letter abbreviation");
assert.equal(styleLabel("Tuesday", "minimal", DOW_MINIMAL), "TU", "minimal uses the disambiguation map");
assert.equal(styleLabel("Thursday", "minimal", DOW_MINIMAL), "TH", "Tue/Thu don't collide at 1 char");
assert.equal(styleLabel("Zzyzx", "minimal", {}), "ZZ", "unmapped label falls back to first 2 chars");

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
