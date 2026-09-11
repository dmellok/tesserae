// Plain-node self-check (no test framework in this repo).
// Run: node tests/clamp_check.mjs
//
// dayLabel: index 0/1 are relative-day words that must come from
// ctx.t() (the locales contract, docs/widgets.md#locales-strings);
// index 2+ used to be server.py's hardcoded English weekday
// abbreviation and is now Intl-driven off ctx.locale instead, so a
// wrong/missing locale would silently show the English weekday name
// in a French render with nothing to notice.
import assert from "node:assert/strict";
import { dayLabel } from "../client.js";

const stubT = (key, fallback) => `t(${key})`;

assert.equal(
  dayLabel({}, 0, "en", stubT),
  "t(day_today)",
  "index 0 always asks ctx.t() for day_today, regardless of locale"
);
assert.equal(
  dayLabel({}, 1, "fr", stubT),
  "t(day_tomorrow)",
  "index 1 always asks ctx.t() for day_tomorrow, regardless of locale"
);

// A Wednesday, so weekday-index confusion (off-by-one from a Monday-
// vs-Sunday week start) would show up as a mismatch here.
const wednesday = { date: "2026-09-16" };
assert.equal(
  dayLabel(wednesday, 2, "en", stubT),
  "Wed",
  "index 2+ in English reads Intl's short weekday name, not ctx.t()"
);
assert.equal(
  dayLabel(wednesday, 4, "fr", stubT),
  "mer.",
  "index 2+ in French asks Intl for the locale's own short weekday name"
);

// Malformed/missing ``date`` falls back to an empty label instead of
// throwing (Number.isFinite guards on unparsable y/m/dd) so a
// half-populated upstream payload doesn't crash the whole render.
assert.equal(dayLabel({ date: "" }, 3, "en", stubT), "", "missing date -> blank label, not a throw");
assert.equal(
  dayLabel({ date: "not-a-date" }, 3, "en", stubT),
  "",
  "unparsable date -> blank label, not a throw"
);

console.log("weather_forecast dayLabel: all assertions passed");
