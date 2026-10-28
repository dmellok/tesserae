// Plain-node self-check (no test framework in this repo), run by
// tests/test_widget_clamp_checks.py. Run: node tests/clamp_check.mjs
//
// The layout maths behind the comics page: which rows fit, and the gap.
import assert from "node:assert/strict";
import { farSideColumns, gapsFor, noteText, planRows, scaleFor, shrinkFor, MIN_ZOOM } from "../client.js";

const gaps = { gap: 32, min: 6, max: 48 };

// Everything fits at the standard gap; spare height widens it to the max.
assert.deepEqual(planRows([500, 300, 300], 2000, gaps), { keep: [0, 1, 2], gap: 48 });
// Spare height between standard and max is shared out evenly.
assert.deepEqual(planRows([500, 300, 300], 1180, gaps), { keep: [0, 1, 2], gap: 40 });
// Too tight for the standard gap: squeeze toward the smallest.
assert.deepEqual(planRows([500, 300, 300], 1120, gaps), { keep: [0, 1, 2], gap: 10 });
// Too tight even at the smallest gap: the last row goes.
assert.deepEqual(planRows([500, 300, 300], 1105, gaps), { keep: [0, 1], gap: 48 });
// A row taller than the whole box is passed over (the Far Side row in a
// short landscape cell) so the strips still show.
assert.deepEqual(planRows([900, 200, 200], 600, gaps), { keep: [1, 2], gap: 48 });
// One row: no gaps to spread.
assert.deepEqual(planRows([400], 1000, gaps), { keep: [0], gap: 32 });
// Nothing fits.
assert.deepEqual(planRows([700, 800], 600, gaps), { keep: [], gap: 32 });

// Gaps scale with width and stay ordered whatever the options say.
assert.equal(scaleFor(1408), 1);
assert.equal(scaleFor(2816), 1, "never enlarged past the reference panel");
assert.equal(scaleFor(704), 0.5);
assert.equal(scaleFor(0), 1);
assert.deepEqual(gapsFor({}, 1), { gap: 32, min: 6, max: 48 });
assert.deepEqual(gapsFor({ gap: 20, gap_min: 30, gap_max: 10 }, 0.5), { gap: 10, min: 10, max: 10 });
assert.deepEqual(gapsFor({ gap: "x", gap_min: -3, gap_max: "" }, 1), { gap: 32, min: 6, max: 48 });

// Far Side panels drop as the row narrows.
assert.equal(farSideColumns(1408, 4), 4);
assert.equal(farSideColumns(1408, 2), 2);
assert.equal(farSideColumns(640, 4), 2);
assert.equal(farSideColumns(380, 4), 1);
assert.equal(farSideColumns(380, 0), 0);
assert.equal(farSideColumns(0, 3), 3, "unknown width keeps the asked-for count");

// The status line.
const t = (key, fallback) => fallback;
assert.equal(noteText([], t, "en"), "");
assert.equal(
  noteText(
    [
      { label: "Garfield", state: "stale", since: "2026-10-24" },
      { label: "Dilbert", state: "missing" },
      { label: "xyz!", state: "invalid" },
    ],
    t,
    "en",
  ),
  "Not updated today: Garfield (Oct 24), Dilbert, xyz! (not a strip)",
);

console.log("comics_daily clamp_check: ok");

// A row taller than its space is drawn smaller to fit, never cut.
assert.equal(shrinkFor(900, 600), 0.666);
assert.equal(shrinkFor(500, 600), 1);
assert.equal(shrinkFor(0, 600), 1);

// An 800 x 50 sliver: the strip would need about a twentieth of its size,
// below what can be made out, so the cell says it is too small instead.
assert.ok(shrinkFor(253, 34) < MIN_ZOOM);
assert.ok(shrinkFor(253, 200) > MIN_ZOOM);
