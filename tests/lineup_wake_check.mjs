// static/lineup_wake.js in node: the lineup editor's words for a display that
// sleeps. Run by tests/test_lineup_wake.py; exits non-zero on a failure.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const src = readFileSync(fileURLToPath(new URL("../static/lineup_wake.js", import.meta.url)), "utf8");
const window = {};
vm.runInNewContext(src, { window });
const W = window.TesseraeLineupWake;
// Objects made in the script's own realm: compare them as plain data.
const plain = (v) => JSON.parse(JSON.stringify(v));

// Wake interval against cadence: 60 min wakes, a send every 5 min.
assert.deepEqual(plain(W.waste(5, 60)), { per: 12, perExact: true, hour: 11, day: 264 });
assert.equal(W.waste(60, 60), null, "one send a wake is all seen");
assert.equal(W.waste(120, 60), null, "sends slower than wakes are all seen");
assert.equal(W.waste(0, 60), null);
assert.equal(W.waste(5, 0), null, "an unknown wake warns of nothing");

let w = W.cadenceWarning({ name: "Test Display", wake: 60, every: 5 });
assert.equal(w.text, "The Test Display wakes every 60 min, so only the last of every 12 sends is ever shown.");
assert.equal(w.paid, "", "no cost line without a paid widget");

// A widget that pays per render: the unseen sends, counted.
w = W.cadenceWarning({ name: "Test Display", wake: 60, every: 5, paid: true });
assert.equal(w.paid, "Each send may make a new AI picture: up to 11 an hour nobody sees.");
w = W.cadenceWarning({ name: "Test Display", wake: 60, every: 45, paid: true });
assert.equal(w.text, "The Test Display wakes every 60 min, so only the last send before each wake is ever shown.");
assert.equal(w.paid, "Each send may make a new AI picture: up to 8 a day nobody sees.");
w = W.cadenceWarning({ name: "Test Display", wake: 120, every: 30, paid: true });
assert.match(w.text, /every 2 h, so only the last of every 4 sends/);
assert.equal(w.paid, "Each send may make a new AI picture: up to 1 an hour nobody sees.");

// Updates while shown: the same numbers, its own noun.
w = W.cadenceWarning({ name: "the hall frame", wake: 30, every: 5, noun: "updates" });
assert.equal(w.text, "the hall frame wakes every 30 min, so only the last of every 6 updates is ever shown.");

// A rotation: a dashboard shown for less than a wake can be skipped.
assert.equal(W.skipWarning({ name: "Test Display", wake: 60, dwells: [60, 90] }), null);
const s = W.skipWarning({ name: "Test Display", wake: 60, dwells: [30, 60] });
assert.equal(s.text, "The Test Display wakes every 60 min, so a dashboard shown for less than that can be skipped.");
assert.equal(s.fix, "Show each for 60 min");

// The header and the freshness line.
assert.equal(W.header({ name: "Test Display", wake: 60 }), "Test Display sleeps and wakes every 60 min. Changes show at its next wake.");
assert.equal(W.header({ name: "Test Display" }), "Test Display sleeps between wakes. Changes show at its next wake.");
let f = W.freshLine({ kind: "interval", wake: 60, every: 5, next: "~1:25 PM", learned: true });
assert.deepEqual(plain(f), { line: "Redrawn just before each wake (every 60 min) · next ~1:25 PM", until: "" });
f = W.freshLine({ kind: "interval", wake: 60, every: 120, next: "", learned: true });
assert.equal(f.line, "Redrawn just before a wake, at most every 2 h");
f = W.freshLine({ kind: "cycle", wake: 60, every: 30, next: "~2:00 PM", learned: false });
assert.equal(f.line, "Each change is sent just before a wake (every 60 min) · next ~2:00 PM");
assert.equal(f.until, "Until its wake times are learned, sent every 30 min.");

console.log("lineup_wake: ok");
