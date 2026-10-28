// Plain-node self-check, run by tests/test_widget_clamp_checks.py.
// How much summary each screen gets.
import assert from "node:assert/strict";
import { summaryLines } from "../client.js";

const title = 88; // characters
// The 32-inch portrait panel: the picture on top, a full summary below.
assert.ok(summaryLines(1440, 2560, false, true, title) >= 4);
// A 13-inch 1600x1200: side by side, a long summary.
const big = summaryLines(1600, 1200, true, true, title);
// A 7.5-inch 800x480: side by side, a shorter summary that still fits.
const small = summaryLines(800, 480, true, true, title);
assert.ok(small >= 2 && small < big, `${small} < ${big}`);
// ... and fits: headline (4 lines at most), source line and summary within
// the height.
const titleFont = Math.max(13, Math.min(64, 0.06 * 480));
const used = 4 * titleFont * 1.12 + small * Math.max(11, 0.034 * 480) * 1.45 + 0.17 * 480;
assert.ok(used <= 480, `${used} > 480`);
// A small tile: a few lines at most.
assert.ok(summaryLines(300, 200, true, true, title) <= 5);
console.log("news_illustrated clamp_check: ok");
