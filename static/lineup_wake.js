/* What a sleeping display means for a lineup's timing, in words.
 *
 * A display that sleeps (a battery frame) takes the newest frame the server
 * has at each wake, then sleeps again. A lineup that sends more often than
 * it wakes makes frames nobody sees, and a widget that pays per render (an
 * AI picture, manifest render.paid) pays for every one of them. The lineup
 * editor (deck_editor.js) draws its Timing step from these: the header,
 * the freshness line, and the warning with its numbers.
 *
 * Pure functions of minutes and names, so tests/lineup_wake_check.mjs runs
 * them in node (with a stand-in window). A classic script: window.TesseraeLineupWake.
 */
(function (root) {
  "use strict";

  const mins = (n) => {
    n = Math.round(n);
    if (n % 1440 === 0) return n === 1440 ? "1 day" : `${n / 1440} days`;
    if (n >= 60) return `${Math.floor(n / 60)} h${n % 60 ? ` ${n % 60} min` : ""}`;
    return `${n} min`;
  };
  // A wake interval: "60 min" reads better than "1 h" up to an hour and a half.
  const wakeMins = (n) => (n <= 90 ? `${Math.round(n)} min` : mins(n));
  // "The Sample Display", but not "The The hall frame".
  const theName = (name) => (/^the\s/i.test(name || "") ? name : `The ${name || "display"}`);

  // How many sends every `every` minutes go unseen by a display that wakes
  // every `wake` minutes: {per, perExact, hour, day} or null when each send
  // can be seen. `per` sends land between two wakes and only the last shows.
  function waste(every, wake) {
    if (!(every > 0) || !(wake > 0) || every >= wake) return null;
    const ratio = wake / every;
    const lost = 1 / every - 1 / wake; // unseen sends a minute
    return {
      per: Math.floor(ratio),
      perExact: Number.isInteger(ratio),
      hour: Math.floor(lost * 60 + 1e-9),
      day: Math.floor(lost * 1440 + 1e-9),
    };
  }

  // The warning for a cadence shorter than the wake interval: {text, paid}
  // or null. `noun` is "sends" (the lineup's timer) or "updates" (a
  // dashboard's Updates while shown); `paid` asks for the cost line.
  function cadenceWarning({ name, wake, every, noun = "sends", paid = false }) {
    const w = waste(every, wake);
    if (!w) return null;
    const one = noun === "updates" ? "update" : "send";
    const head = `${theName(name)} wakes every ${wakeMins(wake)}, so `;
    const text =
      w.perExact && w.per > 1
        ? `${head}only the last of every ${w.per} ${noun} is ever shown.`
        : `${head}only the last ${one} before each wake is ever shown.`;
    let cost = "";
    if (paid) {
      const each = `Each ${one} may make a new AI picture`;
      if (w.hour >= 1) cost = `${each}: up to ${w.hour} an hour nobody sees.`;
      else if (w.day >= 1) cost = `${each}: up to ${w.day} a day nobody sees.`;
      else cost = `${each} that nobody sees.`;
    }
    return { text, paid: cost };
  }

  // A rotation whose dashboards show for less than the wake interval: some
  // are never on the glass when it wakes. `dwells` are minutes per dashboard.
  function skipWarning({ name, wake, dwells }) {
    if (!(wake > 0) || !dwells.some((d) => d > 0 && d < wake)) return null;
    return {
      text: `${theName(name)} wakes every ${wakeMins(wake)}, so a dashboard shown for less than that can be skipped.`,
      fix: `Show each for ${wakeMins(wake)}`,
    };
  }

  // The Timing step's header for a sleeping display.
  function header({ name, wake }) {
    const facts = wake > 0 ? `sleeps and wakes every ${wakeMins(wake)}` : "sleeps between wakes";
    return `${name || "The display"} ${facts}. Changes show at its next wake.`;
  }

  // How fresh a lineup that sends just before each wake is: one line.
  // `kind` is "interval" (one dashboard sent again) or "cycle" (in turn);
  // `every` the lineup's own timer, a floor on how often it sends; `next`
  // the next wake ("~1:25 PM"); `learned` false while the server still
  // sends on the timer because it doesn't trust the wake times yet.
  function freshLine({ kind, wake, every, next, learned }) {
    let line;
    if (!(wake > 0)) line = "Redrawn just before each wake";
    else if (kind === "cycle")
      line = `Each change is sent just before a wake (every ${wakeMins(wake)})`;
    else if (every > wake) line = `Redrawn just before a wake, at most every ${mins(every)}`;
    else line = `Redrawn just before each wake (every ${wakeMins(wake)})`;
    if (next) line += ` · next ${next}`;
    const until =
      learned || !(every > 0)
        ? ""
        : ` Until its wake times are learned, sent every ${mins(every)}.`;
    return { line, until: until.trim() };
  }

  root.TesseraeLineupWake = {
    mins,
    wakeMins,
    theName,
    waste,
    cadenceWarning,
    skipWarning,
    header,
    freshLine,
  };
})(window);
