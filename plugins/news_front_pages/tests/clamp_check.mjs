// Plain-node self-check, run by tests/test_widget_clamp_checks.py.
// How a front page sits in cells of real screen sizes.
import assert from "node:assert/strict";
import { READABLE_PX, pageFit } from "../client.js";

const page = 2632 / 1440; // a page drawn 1440 wide

// The 32-inch portrait panel: full width from the top, as before.
assert.equal(pageFit(1440, 2560, page, "top"), "full");
// A 13-inch 1600x1200: wide enough to read; the top of the page shows.
assert.equal(pageFit(1600, 1200, page, "top"), "full");
// A 7.5-inch 800x480: still at least READABLE_PX wide, so full width, top.
assert.equal(pageFit(800, 480, page, "top"), "full");
// A small tile: drawn READABLE_PX wide and centred, so headlines stay readable.
assert.equal(pageFit(400, 300, page, "top"), "zoom");
assert.ok(READABLE_PX >= 700);
// "The whole page, smaller" wherever the page is taller than the cell.
assert.equal(pageFit(800, 480, page, "whole"), "whole");
assert.equal(pageFit(1440, 2632, page, "whole"), "full");
// No size known: full width, as before.
assert.equal(pageFit(0, 0, page, "top"), "full");
console.log("news_front_pages clamp_check: ok");
