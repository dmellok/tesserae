/* The lineup editor (templates/deck_editor.html).
 *
 * One state, one draw: the server's JSON island (app.deck_routes._editor_state)
 * becomes `st`, every control edits `st`, and draw() repaints the sections from
 * it. The lineup waits for Save lineup, which posts the whole lineup to
 * /decks/editor-save (respond=json) and reloads; the new-lineup flow posts to
 * /decks/new/lineup and opens the editor of what it made. Push now (a row, or
 * the footer), Draw another and Duplicate dashboard act at once.
 *
 * The day bar, day tiles and thumbnails are the Lineups page's own
 * (window.TesseraeLineupsRender): same markup, same dx-lineups.css.
 */
(function () {
  "use strict";

  const root = document.getElementById("deck-editor");
  if (!root || !window.TesseraeLineupsRender) return;
  const R = window.TesseraeLineupsRender;
  const S = JSON.parse(document.getElementById("dxe-data").textContent);
  const form = document.getElementById("deck-form");
  const $ = (id) => document.getElementById(id);
  const esc = R.esc;
  const ico = (name, cls) => R.ico(name, cls);
  R.setTimeCycle(S.timeCycle);

  const DAY_MIN = 1440;
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const GRID_PAGE = 18;
  const STRIP = 8; // items shown up front, then "+N more" opens the whole list
  const SHOW_FOR = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240];
  const UPDATES = [0, 1, 5, 10, 15, 30, 60, 120, 360, 720, 1440];
  const KEEP_READY = [0, 5, 15, 30, 60, 120, 360];
  const RETURN_HOME = [0, 5, 15, 30, 60];

  /* ------------------------------------------------------------ helpers */
  const T = (v) => {
    const m = /^(\d{1,2}):(\d{2})/.exec(v || "");
    return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
  };
  const clockOf = (v) => (v === "24:00" ? R.clock(DAY_MIN, true) : R.clock(T(v)));
  const mins = (n) => {
    n = Math.round(n);
    if (n % 1440 === 0) return n === 1440 ? "1 day" : `${n / 1440} days`;
    if (n >= 60) return `${Math.floor(n / 60)} h${n % 60 ? ` ${n % 60} min` : ""}`;
    return `${n} min`;
  };
  const plural = (n, word) => `${n} ${n === 1 ? word.replace(/s$/, "") : word}`;
  const pageOf = (id) =>
    S.pages[id] || { id, name: id, subtitle: "", items: { mode: "none" }, missing: true };
  const deviceOf = (id) => S.devices.find((d) => d.id === id) || null;
  // A lineup bound to several displays: the editor edits the first and keeps
  // the rest (they are posted back as they are).
  const otherDisplays = () => (S.displays || []).slice(1).filter((d) => d && d !== st.display);
  const displayNames = () =>
    [st.display, ...otherDisplays()]
      .filter(Boolean)
      .map((id) => (deviceOf(id) || { name: id }).name)
      .join(" + ");
  const viewOf = (id) => (id && S.views ? S.views[id] : null) || null;
  const capsOf = (id) => (deviceOf(id) || {}).caps || {};
  const live = (text) => {
    const n = $("dxe-live");
    if (n) n.textContent = "";
    window.setTimeout(() => n && (n.textContent = text), 30);
  };
  const daysText = (days) => {
    const on = days.map((v, i) => (v ? i : -1)).filter((i) => i >= 0);
    if (on.length === 7) return "every day";
    if (on.join() === "0,1,2,3,4") return "weekdays";
    if (on.join() === "5,6") return "weekends";
    if (!on.length) return "no days";
    return on.map((i) => DAYS[i]).join(", ");
  };
  // A failed request as one plain line: the server's own words when it gave
  // some, else what went wrong in general terms.
  const NET = "Can't reach Tesserae; changes kept, try again.";
  function failure(r, data) {
    if (data && data.error) return data.error;
    if (r && r.status >= 500) return "Server error; try again.";
    return r ? `Refused (error ${r.status}).` : NET;
  }
  async function request(url, init) {
    let r;
    try {
      r = await fetch(url, Object.assign({ credentials: "same-origin" }, init));
    } catch {
      throw Object.assign(new Error(NET), { net: true });
    }
    const data = await r.json().catch(() => null);
    if (!r.ok || !data || data.ok === false)
      throw Object.assign(new Error(failure(r, data)), {
        status: r.status,
        reload: !!(data && data.reload),
      });
    return data;
  }
  const nonce = () =>
    Array.from(crypto.getRandomValues(new Uint8Array(6)), (b) =>
      b.toString(16).padStart(2, "0")
    ).join("");

  /* ------------------------------------------------------------ state */
  function initial() {
    const days = [0, 1, 2, 3, 4, 5, 6].map((i) => S.when.days.includes(i));
    return {
      name: S.name,
      enabled: S.enabled,
      display: S.display,
      when: { mode: S.when.mode, start: S.when.start, end: S.when.end, days },
      whenChanged: false,
      steps: S.steps.map((s) => ({
        id: s.id,
        dwell: s.dwell,
        override: s.override,
        conditions:
          s.conditions && s.conditions.length ? JSON.stringify(s.conditions, null, 2) : "",
        refresh: pageOf(s.id).refresh || 0,
        pin: undefined,
      })),
      home: S.home,
      timeout: S.timeout,
      taps: S.advance === "both",
      smartSync: S.smartSync,
      lead: S.lead,
      interval: S.interval,
      priority: S.priority,
      mode: S.mode,
      minHold: S.minHold,
      cadence: S.cadence,
    };
  }
  let st = initial();
  const ui = {
    open: new Set(),
    editing: new Set(),
    rawOpen: new Set(), // step ids whose conditions' raw JSON is open
    condOpen: new Set(), // step ids whose Conditions editor was opened with "Add a condition"
    grid: {}, // page id -> {q, offset, data, busy, open}
    strip: {}, // page id -> {data} (the first few items)
    // page id -> the item chosen with a tile's arrows, as on the Lineups page:
    // {mode: "pick", idx, key, label, state, thumb, large, note} or
    // {mode: "draw", hist: [{key, state, thumb, large, note, sig}], pos, n, note}
    choice: {},
    picker: false, // the add-dashboard picker is open
    step: S.isNew ? (S.display ? 2 : 1) : 0, // the new flow's open step
    saving: false,
    duplicating: null,
    pushing: false,
    reload: false,
    msg: "",
    msgKind: "",
    justDay: null,
    consent: {}, // page id -> the bind prompt's answer ({mode, modes, keep}), for the display chosen
    consentDisplay: S.display,
  };
  const snapshot = () => JSON.stringify(Object.assign({}, st, { whenChanged: false }));
  const stepHadConditions = new Set(
    S.steps.filter((s) => s.conditions && s.conditions.length).map((s) => s.id)
  );
  // Keep the condition editor open after its last condition is removed.
  const hasConditions = (s) => stepHadConditions.has(s.id) || !!(s.conditions || "").trim();
  const showConditions = (s) => hasConditions(s) || ui.condOpen.has(s.id);
  let saved = snapshot();
  const dirty = () => snapshot() !== saved;

  /* ------------------------------------------------------------ the lineup's shape */
  // What the lineup is as edited: "manual", "daily" (one send), "cycle" (a window taken
  // in turn) or "interval" (one dashboard repeated through a window); the same rule as
  // the server's _apply_when and _reshape_window, which apply it on every save.
  function kind() {
    if (st.when.mode === "manual") return "manual";
    if (st.when.mode === "at") return "daily";
    return st.steps.length === 1 ? "interval" : "cycle";
  }
  // An interval has one cadence, "Sent again every"; a dashboard's own dwell only counts in a cycle.
  const dwellOf = (s) => (s.dwell && kind() !== "interval" ? s.dwell : st.interval);
  const loopMinutes = () => st.steps.reduce((t, s) => t + dwellOf(s), 0);
  const window_ = () => {
    const s = T(st.when.start);
    const e = st.when.mode === "between" ? (st.when.end === "24:00" ? DAY_MIN : T(st.when.end)) : s;
    return { s, e };
  };
  const pause = () => {
    const v = viewOf(st.display);
    return v && v.pause ? v.pause : null;
  };
  // Quiet hours on weekday `day` as [start, end) day-minute spans: the
  // server's rule (lineups_view._pause_parts), each weekday on its own
  // clock, with its weekday and all-day masks.
  function pauseParts(day) {
    const p = pause();
    if (!p) return [];
    if (p.all_day && p.all_day[day]) return [[0, DAY_MIN]];
    if (p.days && !p.days[day]) return [];
    const a = p.start_min % DAY_MIN;
    const b = p.end_min % DAY_MIN;
    return a <= b
      ? [[a, b]]
      : [
          [0, b],
          [a, DAY_MIN],
        ];
  }
  // The days it runs (every day for a new lineup with none picked yet).
  const runDays = () => {
    const on = [0, 1, 2, 3, 4, 5, 6].filter((i) => st.when.days[i]);
    return on.length ? on : [0, 1, 2, 3, 4, 5, 6];
  };
  // The run days on which minute `m` is quiet.
  const quietDaysAt = (m) =>
    Number.isFinite(m)
      ? runDays().filter((d) => pauseParts(d).some(([a, b]) => m >= a && m < b))
      : [];
  // Minute `m` is quiet on every day it runs.
  const inPause = (m) => {
    const q = quietDaysAt(m);
    return q.length > 0 && q.length === runDays().length;
  };
  // The whole of s..e (minutes, e up to 1440) is quiet on every day it runs.
  function allInPause(s, e) {
    if (!pause() || !Number.isFinite(s) || !Number.isFinite(e) || e <= s) return false;
    return runDays().every((d) => pauseParts(d).some(([a, b]) => s >= a && e <= b));
  }
  // "Sat, Sun" when only some of the run days are named.
  const someDays = (days) =>
    days.length === runDays().length ? "" : daysText(DAYS.map((_, i) => days.includes(i)));
  // "6–8 AM": the clock range, the first AM/PM left off when both share it.
  function shortRange(a, b) {
    const x = R.clock(a);
    const y = R.clock(b, true);
    const m = x.match(/\s?[AP]M$/);
    return m && y.endsWith(m[0].trim())
      ? `${x.slice(0, -m[0].length)}\u2013${y}`
      : `${x}\u2013${y}`;
  }
  const pauseWhere = () => {
    const dev = deviceOf(st.display);
    return `${dev ? `${dev.name}'s` : "the display's"} quiet hours`;
  };
  // What quiet hours do to the window s..e, in words (mirrors
  // lineups_view.pause_note): { text, none } or null when they don't touch it.
  function windowPauseNote(s, e) {
    if (!pause() || !Number.isFinite(s) || !Number.isFinite(e) || e <= s) return null;
    const where = pauseWhere();
    let whole = false;
    for (const d of runDays()) {
      const hits = pauseParts(d)
        .map(([a, b]) => [Math.max(a, s), Math.min(b, e)])
        .filter(([a, b]) => a < b);
      if (!hits.length) continue;
      const [a, b] = hits.sort((x, y) => x[0] - y[0])[0];
      if (hits.reduce((n, [x, y]) => n + y - x, 0) >= e - s) {
        whole = true;
        continue;
      }
      if (a <= s) return { text: `Starts at ${R.clock(b)}: ${shortRange(a, b)} is in ${where}.` };
      if (b >= e) return { text: `Stops at ${R.clock(a)}: ${shortRange(a, b)} is in ${where}.` };
      return { text: `Pauses ${shortRange(a, b)} for ${where}.` };
    }
    return whole ? { text: `Sends nothing: all of it is in ${where}.`, none: true } : null;
  }
  // The first quarter hour after quiet hours end: the scheduler counts the
  // end minute as quiet, so a push exactly at the end is still held.
  function slotAfterPause(m) {
    let t = Math.ceil((m + 1) / 15) * 15;
    for (let i = 0; i < 96 && inPause(t % DAY_MIN); i++) t += 15;
    return t % DAY_MIN;
  }
  // A window's end that comes before its start: refused, except on a saved
  // one-dashboard interval, which may run past midnight.
  function endBeforeStart() {
    if (st.when.mode !== "between") return false;
    const w = window_();
    if (!Number.isFinite(w.s) || !Number.isFinite(w.e)) return false;
    if (w.e === w.s) return true;
    return w.e < w.s && !(S.when.kind === "interval" && st.steps.length === 1);
  }
  const takesInput = () => !!capsOf(st.display).inputs;
  const sleeps = () => !!capsOf(st.display).sleeps;
  // A display that sleeps takes the newest frame at each wake and nothing in
  // between (static/lineup_wake.js has the words). Its wake: {name, min,
  // next, url, learned}, min 0 when its interval isn't known; null when it
  // doesn't sleep.
  const W = window.TesseraeLineupWake;
  function wakeOf() {
    if (!sleeps() || !W) return null;
    const c = capsOf(st.display);
    const dev = deviceOf(st.display);
    return {
      name: dev ? dev.name : "",
      min: Number(c.wake_minutes) || 0,
      next: c.next_wake || "",
      url: c.wake_url || "",
      learned: !!c.wake_learned,
    };
  }
  // The lineup's sends wait for a wake (smart sync on a repeat or rotation).
  const sendsAtWake = () => showSmart() && st.smartSync;
  // A dashboard in it has a widget that may pay on each render (an AI picture).
  const paidIn = (list) => list.some((s) => (pageOf(s.id).paid || []).length > 0);
  // What flips it by hand: buttons always do; touch does too.
  function flipWord() {
    const c = capsOf(st.display);
    if (c.buttons && !c.touch) return "buttons";
    if (c.touch && !c.buttons) return "taps";
    return "taps and buttons";
  }
  // The other lineups on display `x.display` that run at the same time as
  // lineup state `x` on a shared day. [{l, days}]
  function overlapsOf(x) {
    const v = viewOf(x.display);
    const mode = x.when.mode;
    if (!v || mode === "manual" || !x.enabled) return [];
    const s = T(x.when.start);
    const e = mode === "between" ? (x.when.end === "24:00" ? DAY_MIN : T(x.when.end)) : s;
    if (!Number.isFinite(s) || !Number.isFinite(e)) return [];
    const spans = (a, b, point) =>
      point
        ? [[a, a + 1]]
        : b > a
          ? [[a, b]]
          : [
              [a, DAY_MIN],
              [0, b],
            ];
    const mine = spans(s, e, mode === "at");
    return (v.lineups || [])
      .filter((l) => l.id !== S.deckId && l.enabled && l.window)
      .map((l) => {
        const days = [0, 1, 2, 3, 4, 5, 6].filter((i) => x.when.days[i] && l.days[i]);
        const theirs = spans(l.window.start_min, l.window.end_min, l.window.is_point);
        const hit = days.length && mine.some(([a, b]) => theirs.some(([c, d]) => a < d && c < b));
        return hit ? { l, days } : null;
      })
      .filter(Boolean);
  }
  // Overlaps it already had are kept, and Priority decides between them;
  // a save that makes a new one is refused (app.deck_routes), so the edit
  // says so instead. {known: [{l, days}], fresh: [{l, days}]}
  const savedOverlaps = new Set();
  function overlaps() {
    if (S.isNew) return { known: [], fresh: [] };
    if (!savedOverlaps.size)
      overlapsOf(initial()).forEach(({ l, days }) =>
        days.forEach((d) => savedOverlaps.add(`${l.id}:${d}`))
      );
    const known = [];
    const fresh = [];
    overlapsOf(st).forEach(({ l, days }) => {
      const old = days.filter((d) => savedOverlaps.has(`${l.id}:${d}`));
      const added = days.filter((d) => !savedOverlaps.has(`${l.id}:${d}`));
      if (old.length) known.push({ l, days: old });
      if (added.length) fresh.push({ l, days: added });
    });
    return { known, fresh };
  }
  // Timing as saved vs as edited: NOW / NEXT and the header's next line are
  // the saved plan's, so they wait for Save once the timing changes.
  const timingOf = (x) =>
    JSON.stringify([
      x.enabled,
      x.display,
      x.when.mode,
      x.when.start,
      x.when.end,
      x.when.days,
      x.interval,
      x.mode,
      x.steps.map((s) => [s.id, s.dwell]),
    ]);
  const timingDirty = () => !S.isNew && timingOf(st) !== timingOf(initial());

  /* ------------------------------------------------------------ binding (consent) */
  const strandedFor = (id, dev) => {
    const p = S.pages[id];
    return !!(dev && p && !p.missing && (p.devices || []).length && !p.devices.includes(dev));
  };
  const devInfo = (id) => {
    const d = deviceOf(id) || { id, name: id, w: 0, h: 0 };
    return { id: d.id, name: d.name, width: d.w || 0, height: d.h || 0 };
  };
  function needsConsent(ids, dev) {
    if (ui.consentDisplay !== dev) {
      ui.consent = {};
      ui.consentDisplay = dev;
    }
    return ids.filter((id) => strandedFor(id, dev) && !ui.consent[id]);
  }
  async function confirmBind(ids, dev) {
    const need = needsConsent(ids, dev);
    if (!need.length) return true;
    if (!window.TesseraeBindPrompt) return false;
    const mode = await window.TesseraeBindPrompt.ask({
      display: devInfo(dev),
      pages: need.map((id) => ({
        id,
        name: S.pages[id].name,
        bound_to: S.pages[id].devices.map((d) => ({
          ...devInfo(d),
          shared: (S.pages[id].shared_displays || []).includes(d),
        })),
      })),
    });
    if (!mode) return false;
    need.forEach((id) => (ui.consent[id] = mode));
    return true;
  }
  // The Move dialog: pick the display, see what it changes in one line.
  // Resolves to the display id, or null when cancelled.
  function askMove() {
    return new Promise((resolve) => {
      if (typeof HTMLDialogElement === "undefined") return resolve(null);
      const opener = root.querySelector("[data-loc-more]") || document.activeElement;
      const choices = S.devices.filter((d) => d.id !== st.display);
      const dlg = document.createElement("dialog");
      dlg.className = "wizard-dialog bind-dialog dxe-move-dialog";
      dlg.setAttribute("aria-labelledby", "dxe-move-title");
      dlg.setAttribute("aria-describedby", "dxe-move-lines");
      const more = otherDisplays().map(nameOf);
      dlg.innerHTML =
        `<div class="wizard-head"><span class="wizard-head-icon" aria-hidden="true"><i class="ph-bold ph-arrows-left-right"></i></span>` +
        `<div class="wizard-head-text"><h2 id="dxe-move-title">Move this lineup?</h2></div></div>` +
        `<div class="wizard-body bind-dialog-body">` +
        `<div class="wizard-field"><label class="wizard-label" for="dxe-move-to">Move to</label>` +
        `<select id="dxe-move-to">${choices.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")}</select></div>` +
        `<div id="dxe-move-lines" class="dxe-move-lines" aria-live="polite"></div></div>` +
        `<div class="wizard-footer"><button type="button" class="ghost" data-cancel>Cancel</button>` +
        `<span class="wizard-footer-spacer"></span><button type="button" class="primary dxe-caution" data-ok>Move</button></div>`;
      document.body.append(dlg);
      const sel = dlg.querySelector("select");
      const lines = dlg.querySelector("#dxe-move-lines");
      const ok = dlg.querySelector("[data-ok]");
      const paint = () => {
        const to = sel.value;
        const from = S.display;
        const rows = [];
        if (to === from) rows.push(`Stays on ${nameOf(to)}; the move is dropped.`);
        else
          rows.push(
            from ? `Stops on ${nameOf(from)}; runs on ${nameOf(to)}.` : `Runs on ${nameOf(to)}.`
          );
        if (more.length) rows.push(`Only ${nameOf(S.display)} moves; ${more.join(" + ")} stays.`);
        const n = needsConsent(
          st.steps.map((x) => x.id),
          to
        ).length;
        if (n) rows.push(`You'll be asked to bind ${plural(n, "dashboards")} to ${nameOf(to)}.`);
        lines.innerHTML = rows.map((r) => `<p>${esc(r)}</p>`).join("");
        ok.textContent = `Move to ${nameOf(to)}`;
      };
      sel.addEventListener("change", paint);
      paint();
      let answer = null;
      dlg.querySelector("[data-cancel]").addEventListener("click", () => dlg.close());
      ok.addEventListener("click", () => {
        answer = sel.value;
        dlg.close();
      });
      dlg.addEventListener("close", () => {
        dlg.remove();
        if (opener && typeof opener.focus === "function" && document.contains(opener))
          opener.focus({ preventScroll: true });
        resolve(answer);
      });
      dlg.addEventListener("click", (e) => {
        if (e.target === dlg) dlg.close();
      });
      dlg.showModal();
      sel.focus();
    });
  }
  // The bind answers were for the display it was moving to.
  function dropMoveConsent() {
    if (ui.consentDisplay === S.display) return;
    ui.consent = {};
    ui.consentDisplay = S.display;
  }
  // Confirm stages the move; it applies on Save like the other edits.
  async function moveDisplay() {
    if (ui.saving || S.isNew) return;
    const to = await askMove();
    if (!to || to === st.display) return;
    if (
      !(await confirmBind(
        st.steps.map((s) => s.id),
        to
      ))
    )
      return;
    if (to === S.display) dropMoveConsent();
    change(() => (st.display = to));
    live(to === S.display ? "Move dropped." : `Moving to ${nameOf(to)}. Save to apply.`);
    root.querySelector("[data-loc-more]")?.focus();
  }
  function consentFields(fd) {
    st.steps.forEach((s) => {
      if (!strandedFor(s.id, st.display) || !ui.consent[s.id]) return;
      window.TesseraeBindPrompt.fields(ui.consent[s.id], s.id).forEach(([k, v]) => fd.append(k, v));
    });
  }

  /* ------------------------------------------------------------ thumbnails */
  function thumbOf(p, cls, extra) {
    const v = viewOf(st.display) || {};
    const w = p.w || v.width || 16;
    const h = p.h || v.height || 10;
    return R.thumb(
      p.name,
      p.missing ? "" : p.thumb,
      `${w}/${h}`,
      h > w ? "p" : "l",
      [p.missing ? "missing" : "", cls || ""].filter(Boolean).join(" "),
      null,
      false,
      v,
      extra || ""
    );
  }
  // A row's thumbnail sits in a fixed box the display's shape (shapeVars), the
  // picture fitted inside it; several items stack sheets behind it and put
  // the count on its corner.
  function stackedThumb(p) {
    const it = p.items || {};
    const many =
      (it.mode === "pick" && it.count > 1) ||
      it.mode === "draw" ||
      (!!it.empty && !!(p.recent && p.recent.length));
    if (!many) return `<span class="dxe-th">${thumbOf(p)}</span>`;
    const badge =
      it.mode !== "pick" && it.mode !== "draw"
        ? ""
        : it.mode === "draw" && !(it.count > 1)
          ? `<span class="dxe-badge" title="A random photo each time it's sent">random</span>`
          : `<span class="dxe-badge" title="${esc(plural(it.count, it.noun || "items"))}">×${esc(it.count)}</span>`;
    return `<span class="dxe-th is-stack">${thumbOf(p)}${badge}</span>`;
  }
  // The display's shape, for the thumbnail boxes (dx-decks.css --dxe-tw).
  function shapeVars() {
    const v = viewOf(st.display) || {};
    const w = Number(v.width) || 16;
    const h = Number(v.height) || 10;
    return ` style="--dxe-arw:${w};--dxe-arh:${h}"`;
  }

  /* ------------------------------------------------------------ header */
  function drawHeader() {
    const here = $("dxe-crumb-here");
    const dev = deviceOf(st.display);
    if (here && !S.isNew) here.textContent = dev ? displayNames() : "No display";
    const d = viewOf(st.display);
    const model = $("dxe-model");
    if (model) {
      const bits = [];
      if (dev && dev.model) bits.push(dev.model);
      if (d && d.link && d.link.label) bits.push(d.link.label.toLowerCase());
      if (d && d.pause) {
        const qd = d.pause.days && d.pause.days.some((x) => !x) ? daysText(d.pause.days) : "";
        bits.push(`quiet ${d.pause.label}${qd ? ` ${qd}` : ""}`);
      }
      model.textContent = S.isNew ? "A display, a time, then dashboards." : bits.join(" · ");
    }
    const now = $("dxe-now");
    if (now) {
      now.hidden = S.isNew || !d;
      if (d) {
        const op = d.on_screen;
        const fr = op ? null : d.frame;
        const url = op ? op.thumb_url : fr ? fr.thumb_url : "";
        const name = op ? op.name : fr ? fr.label : "Nothing sent yet";
        const since =
          op && op.since ? `, since ${op.since}` : fr && fr.since ? `, since ${fr.since}` : "";
        now.innerHTML =
          R.thumb(
            name,
            url,
            `${d.width || 16}/${d.height || 10}`,
            d.orientation,
            "",
            name,
            true,
            d
          ) +
          `<figcaption><span class="dxe-k">On screen now</span><span><b>${esc(name)}</b>${esc(since)}</span></figcaption>`;
      }
    }
    const lines = $("dxe-status-lines");
    if (lines) lines.innerHTML = S.isNew ? "" : statusLines(d);
    drawLoc();
    drawDayBar();
  }

  // Top right, by the On switch: where the lineup runs, and the way to move it.
  // A staged move shows here until it is saved, undone or discarded.
  const nameOf = (id) => (deviceOf(id) || { name: id }).name;
  function drawLoc() {
    const box = $("dxe-loc");
    if (!box) return;
    const pending = st.display !== S.display;
    const text = pending
      ? `<span class="dxe-loc-txt is-pending" title="${esc(S.display ? `Now on ${nameOf(S.display)}` : "Not on a display yet")}">Moving to <b>${esc(nameOf(st.display))}</b></span>`
      : `<span class="dxe-loc-txt">${st.display ? `On <b>${esc(displayNames())}</b>` : "<b>No display</b>"}</span>`;
    const undo = pending
      ? `<button type="button" class="dxe-link quiet" data-move-undo data-fk="move-undo">Undo</button>`
      : "";
    const can = S.devices.length > 1 && !ui.saving;
    const wasOpen = !!box.querySelector("details.lu-more[open]");
    const home = st.home || (st.steps[0] || {}).id || "";
    const canPushNow = !S.isNew && S.display && home && isSavedStep(home) && !ui.pushing;
    box.innerHTML =
      text +
      undo +
      `<button type="button" class="lu-btn-ghost lu-btn-line dxe-act dxe-push-now" data-push-lineup data-fk="f-push"${canPushNow ? "" : " disabled"} title="Send ${esc(pageOf(home).name || "it")} now">${ico("send")}<span>Push now</span></button>` +
      `<details class="lu-more dxe-more-menu"${wasOpen ? " open" : ""}><summary title="More" aria-label="More actions" data-loc-more data-fk="loc-more">${ico("dots")}</summary><div class="lu-more-menu">` +
      `<button type="button" data-move data-fk="move" aria-haspopup="dialog"${can ? "" : " disabled"}>Move to another display…</button>` +
      `<hr class="lu-more-sep"><button type="button" class="danger" data-delete data-fk="f-delete">Delete lineup</button></div></details>`;
  }

  function statusLines(d) {
    const l = S.lineup;
    const line = (text, cls) =>
      `<p class="dxe-live-line${cls ? ` ${cls}` : ""}"><i class="dxe-dot" aria-hidden="true"></i>${text}</p>`;
    if (!st.enabled) return line("Off, nothing runs.", "off");
    if (!l || !d) return "";
    if (S.when.mode === "manual" && l.status !== "live")
      return line(`<b>By hand: ${esc(flipWord())} flip it.</b>`);
    // A daily push in quiet hours is skipped, not held: the scheduler counts
    // it as done for the day (as the When section says).
    if (
      S.when.kind === "daily" &&
      l.status !== "live" &&
      S.display === st.display &&
      inPause(T(S.when.start))
    )
      return line(`<b>Skipped: ${esc(clockOf(S.when.start))} is in ${esc(pauseWhere())}.</b>`);
    if (l.status === "live") {
      // What is on the glass first (the Lineups page's Now), then the step due.
      const cur = l.steps.find((s) => s.on_screen) || l.steps.find((s) => s.current);
      const queued = l.steps.find((s) => s.queued);
      const nx = queued ? null : l.steps.find((s) => s.next);
      const nl = d.next_line || {};
      const pos = !cur
        ? ""
        : l.priority_mode
          ? `${cur.name}, ${cur.fallback ? "the fallback" : cur.position}`
          : `dashboard ${cur.position.replace("/", " of ")}, ${cur.name}`;
      const first = cur
        ? `Live: ${pos}`
        : `Live: ${l.live_now && l.live_now.on_screen_name ? l.live_now.on_screen_name : "on screen"}`;
      const nextAt =
        nx && nx.pill && /\d/.test(nx.pill.label) ? nx.pill.label.replace(/^Next\s*/, "") : "";
      const second = timingDirty()
        ? "New times start when you save."
        : nx
          ? `Next: ${nx.name}${nextAt ? ` at ${nextAt}` : ""}.`
          : nl.before
            ? `${nl.before}${nl.at ? ` ${nl.at}` : ""}${nl.after ? ` ${nl.after}` : ""}.`
            : "";
      return (
        `<p class="dxe-live-line is-live"><i class="dxe-dot" aria-hidden="true"></i><b>${esc(first)}</b></p>` +
        (second ? `<p class="dxe-next-line">${esc(second)}</p>` : "")
      );
    }
    const nr = l.next_run;
    const first = nr ? `Next run ${nr.text.replace(/^next\s+/, "")}` : "Not scheduled";
    const second = nr && nr.held && nr.note ? nr.note : "";
    return (
      `<p class="dxe-live-line"><i class="dxe-dot" aria-hidden="true"></i><b>${esc(first)}</b></p>` +
      (second ? `<p class="dxe-next-line">${esc(second)}</p>` : "")
    );
  }

  // This lineup's slice of the display's day: the Lineups page's bar, its blocks
  // recomputed from the edits (R.withPending), the other lineups greyed.
  // Phones and narrow bars are read-only, as on the Lineups page.
  const phoneMQ = window.matchMedia(
    "(pointer: coarse) and (max-width: 600px), (pointer: coarse) and (max-height: 500px)"
  );
  const barReadOnly = () => {
    const bar = $("dxe-daybar");
    return phoneMQ.matches || !bar || bar.clientWidth <= 602;
  };
  const WEEK_KEY = "tesserae.lineupEditor.week";
  const DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const store = {
    get: (k) => {
      try {
        return window.localStorage.getItem(k);
      } catch {
        return null;
      }
    },
    set: (k, v) => {
      try {
        window.localStorage.setItem(k, v);
      } catch {
        /* private mode: the fold just isn't remembered */
      }
    },
  };
  let weekOn = store.get(WEEK_KEY) === "1"; // read before the first draw: no flash
  let shownView = null; // the display as edited: what a drag measures against
  let barRO = null;
  function drawDayBar() {
    const bar = $("dxe-daybar");
    const d = viewOf(st.display);
    shownView = null;
    if (!bar) return;
    if (!d || !d.week || !S.now || S.isNew) {
      bar.hidden = true;
      bar.innerHTML = "";
      return;
    }
    const id = S.deckId || "__new__";
    let shown = d;
    const timed =
      kind() !== "manual" && st.enabled && st.steps.length && Number.isFinite(T(st.when.start));
    if (dirty() || S.isNew) {
      const copy = JSON.parse(JSON.stringify(d));
      copy.lineups = copy.lineups.filter((x) => x.id !== id);
      if (!timed) {
        // Off, by hand or empty: nothing of this lineup runs, so none of its blocks show.
        shown = R.rebuildWeek(copy, d, S.now.weekday, S.now.minute);
      } else {
        const w = window_();
        const k = kind();
        const mine = {
          id,
          name: st.name || "This lineup",
          kind: k,
          enabled: true,
          days: st.when.days.slice(),
          window: {
            start_min: w.s,
            end_min: k === "daily" ? w.s : w.e,
            is_point: k === "daily",
            start: st.when.start,
            end: k === "daily" ? null : st.when.end,
            label: "",
          },
          steps: st.steps.map((s) => ({
            dashboard_id: s.id,
            name: pageOf(s.id).name,
            dwell_minutes: dwellOf(s),
          })),
          step_minutes: st.interval,
        };
        copy.lineups.push(mine);
        const pend = {};
        pend[id] = {
          start_min: mine.window.start_min,
          end_min: mine.window.end_min,
          days: mine.days,
        };
        shown = R.withPending(copy, pend, S.now.weekday, S.now.minute);
      }
    }
    const today = S.now.weekday;
    let day = today;
    if (!st.when.days[today]) {
      for (let i = 1; i < 7; i++)
        if (st.when.days[(today + i) % 7]) {
          day = (today + i) % 7;
          break;
        }
    }
    const dd = shown.week[day];
    const label = day === today ? "Today" : DAY_NAMES[day];
    bar.hidden = false;
    shownView = shown;
    // Only this lineup's block takes a drag or the arrow keys; it stages the
    // edit into When it runs, nothing is saved from here.
    barRO = barReadOnly();
    const editable = timed && !barRO ? [id] : [];
    const toggle = `<button type="button" class="dxe-link quiet dxe-week-toggle" data-week-toggle data-fk="bar-week" aria-expanded="${weekOn}">${weekOn ? "Show today" : "Show all"}</button>`;
    const head = (text) =>
      `<div class="dxe-bar-head"><p class="dxe-bar-day">${esc(text)}</p>${toggle}</div>`;
    if (weekOn) {
      // The Lineups page's day bars, one per day; this lineup's block edits the
      // same start and end on every day it runs.
      const rows = shown.week
        .map(
          (w) =>
            `<div class="dxe-wk-row${w.weekday === today ? " is-today" : ""}" data-day="${w.weekday}"><span class="dxe-wk-lab" title="${esc(DAY_NAMES[w.weekday])}${w.weekday === today ? " (today)" : ""}">${DAY_ABBR[w.weekday]}</span><div class="lu-today-bar">${R.dayTrack(shown, w, editable, true, "dxe-trk")}</div></div>`
        )
        .join("");
      bar.innerHTML =
        head("This week") +
        `<div class="dxe-wk">${rows}<div class="dxe-wk-row dxe-wk-scale"><span class="dxe-wk-lab"></span><div class="lu-today-bar">${R.dayScale({ segments: [] })}</div></div></div>`;
    } else {
      bar.innerHTML =
        head(label) +
        `<div class="lu-today-bar">${R.dayTrack(shown, dd, editable, true, "dxe-trk")}${R.dayScale(dd)}</div>`;
    }
    const editUrlOf = (b) => {
      const l = shown.lineups.find((x) => x.id === b.dataset.lineup);
      if (l && l.name) b.dataset.name = l.name;
      return (l && l.actions && l.actions.edit_url) || "";
    };
    bar.querySelectorAll(".lu-blk").forEach((b) => {
      if (b.dataset.lineup === id) b.classList.add("is-mine");
      if (!b.hasAttribute("data-edit")) {
        b.removeAttribute("tabindex");
        // Another lineup's block opens its own editor; holds stay inert.
        const url = b.classList.contains("hold") || b.dataset.lineup === id ? "" : editUrlOf(b);
        if (url) {
          const name = b.dataset.name || "this lineup";
          b.classList.add("is-link");
          b.dataset.href = url;
          b.tabIndex = 0;
          b.setAttribute("role", "link");
          b.setAttribute("aria-label", `Edit ${name}. ${b.getAttribute("title") || ""}`.trim());
          b.setAttribute("title", `Edit ${name}`);
        }
      } else {
        b.setAttribute("data-fk", "bar-mine");
        b.setAttribute(
          "aria-label",
          (b.getAttribute("aria-label") || "").replace(
            / Click to open\.$/,
            " Enter edits the time."
          )
        );
      }
    });
    // The "Update in" flag is this lineup's next advance, as saved: another
    // lineup's, or one the edits have moved, isn't said here.
    bar.querySelectorAll(".lu-trk").forEach((trk) => {
      const flag = trk.querySelector(".lu-nflag");
      if (!flag) return;
      const mine = trk.querySelector(`.lu-blk[data-lineup="${CSS.escape(id)}"] .lu-tick.next`);
      if (!mine || timingDirty()) {
        flag.remove();
        trk.classList.remove("flagged");
      }
    });
    bar.querySelectorAll(".lu-blk-side").forEach((b) => {
      if (b.title && b.title.startsWith(st.name || "This lineup")) b.classList.add("is-mine");
    });
  }

  // A drag, resize or arrow key on this lineup's block stages the time, as if
  // typed in the fields; Save applies it and Discard drops it.
  const barEdit = R.barEdit({
    host: root,
    readOnly: () => ui.saving || S.isNew || barReadOnly(),
    find: (blk) => {
      const id = S.deckId;
      if (blk.dataset.lineup !== id || !shownView) return null;
      const l = shownView.lineups.find((x) => x.id === id);
      return l && l.window ? { d: shownView, ed: shownView, l } : null;
    },
    commit: (ctx, s, e) => {
      change(() => {
        st.when.start = R.hhmm(s);
        if (st.when.mode === "between") st.when.end = e >= DAY_MIN ? "24:00" : R.hhmm(e);
        st.whenChanged = true;
      });
      root.querySelector(".lu-blk[data-edit]")?.focus();
      live(
        st.when.mode === "between"
          ? `Runs ${R.clockRange(s, e)}. Save to apply.`
          : `Sends at ${R.clock(s)}. Save to apply.`
      );
    },
  });
  window.addEventListener("resize", () => {
    if (!barEdit.active() && barReadOnly() !== barRO) drawDayBar();
  });

  /* ------------------------------------------------------------ 1 When it runs */
  function whenLead() {
    const dev = deviceOf(st.display);
    const on = dev ? ` on ${dev.name}` : "";
    if (st.when.mode === "manual") return `No set time${on}; ${flipWord()} flip it.`;
    const days = daysText(st.when.days);
    if (st.when.mode === "at") return `Sends at ${clockOf(st.when.start)}${on}, ${days}.`;
    const w = window_();
    const range = Number.isFinite(w.s) && Number.isFinite(w.e) ? R.clockRange(w.s, w.e) : "…";
    return `Runs ${range}${on}, ${days}.`;
  }

  function dayTilesHtml() {
    const changed = S.isNew
      ? []
      : [0, 1, 2, 3, 4, 5, 6].filter((i) => st.when.days[i] !== S.when.days.includes(i));
    const tiles = DAYS.map((n, i) => {
      const on = st.when.days[i];
      const just = ui.justDay === i ? ` just-${on ? "on" : "off"}` : "";
      const edited = changed.includes(i) ? " is-edited" : "";
      const today = S.now && S.now.weekday === i ? " is-today" : "";
      return `<button type="button" class="lu-dt${on ? " on" : ""}${today}${edited}${just}" data-day="${i}" data-fk="day-${i}" aria-pressed="${on}" title="${DAY_NAMES[i]}: ${on ? "runs, click to remove" : "off, click to add"}">${n}</button>`;
    }).join("");
    return `<span class="lu-days dxe-days" role="group" aria-label="Days it runs">${tiles}</span>`;
  }

  function timeHtml() {
    const m = st.when.mode;
    const seg =
      `<div class="lu-seg dxe-seg" role="group" aria-label="When">` +
      `<button type="button" data-when="at" data-fk="when-at" aria-pressed="${m === "at"}">At a time</button>` +
      `<button type="button" data-when="between" data-fk="when-between" aria-pressed="${m === "between"}">Between times</button>` +
      (S.when.mode === "manual" || takesInput()
        ? `<button type="button" data-when="manual" data-fk="when-manual" aria-pressed="${m === "manual"}">By hand</button>`
        : "") +
      `</div>`;
    if (m === "manual") return `<div class="dxe-time">${seg}</div>`;
    const endVal = st.when.end === "24:00" ? "23:59" : st.when.end;
    const fields =
      `<input type="time" step="900" class="dxe-in dxe-time-in" data-fk="start" data-time="start" value="${esc(st.when.start)}" aria-label="Start">` +
      (m === "between"
        ? `<span class="dxe-to">to</span><input type="time" step="900" class="dxe-in dxe-time-in${endBeforeStart() ? " is-bad" : ""}" data-fk="end" data-time="end" value="${esc(endVal)}" aria-label="End"${endBeforeStart() ? ' aria-invalid="true" aria-describedby="dxe-end-err"' : ""}>` +
          `<button type="button" class="lu-win-end is-rm" data-end="rm" title="Remove end time" aria-label="Remove end time">${ico("x")}</button>`
        : `<button type="button" class="lu-win-end" data-end="add" title="Add end time" aria-label="Add end time">${ico("plus")}<span>End time</span></button>`);
    return `<div class="dxe-time">${seg}<span class="dxe-time-fields">${fields}</span></div>`;
  }

  function whenWarnings() {
    const out = [];
    if (st.when.mode === "manual") return "";
    const w = window_();
    if (endBeforeStart())
      out.push(
        `<p class="dxe-warn is-err" id="dxe-end-err">${ico("info")}<span>End must be after start.</span></p>`
      );
    if (st.when.mode === "at" && st.steps.length > 1)
      out.push(
        `<p class="dxe-warn">${ico("info")}<span>Only the first dashboard is sent.</span><button type="button" class="dxe-link" data-end="add">Add end time</button></p>`
      );
    // A new overlap is refused on Save: say so here, with what it meets.
    overlaps()
      .fresh.slice(0, 1)
      .forEach(({ l, days }) => {
        const on = days.length === 7 ? "" : ` on ${daysText(DAYS.map((_, i) => days.includes(i)))}`;
        const at = l.window.is_point
          ? `at ${R.clock(l.window.start_min)}`
          : R.clockRange(l.window.start_min, l.window.end_min);
        out.push(
          `<p class="dxe-warn is-err">${ico("layers")}<span>Clashes with ${esc(l.name)} ${esc(at)}${esc(on)}; move it to save.</span></p>`
        );
      });
    const p = pause();
    const quiet = p ? quietDaysAt(w.s) : [];
    const on = someDays(quiet);
    if (p && st.when.mode === "at" && quiet.length) {
      const after = slotAfterPause(p.end_min);
      out.push(
        `<p class="dxe-warn">${ico("moon")}<span>Skipped: ${esc(clockOf(st.when.start))} is in ${esc(pauseWhere())}${on ? ` on ${esc(on)}` : ""}.</span><button type="button" class="dxe-link" data-start="${esc(R.hhmm(after))}" data-fk="start-fix">Start at ${esc(R.clock(after))}</button></p>`
      );
    } else if (p && st.when.mode === "between" && !endBeforeStart()) {
      const note = windowPauseNote(w.s, w.e);
      if (note)
        out.push(
          `<p class="${note.none ? "dxe-warn" : "dxe-note"}">${ico("moon")}<span>${esc(note.text)}</span></p>`
        );
    }
    return out.join("");
  }

  function whenCard() {
    // By hand has no time, so no days either.
    const days =
      st.when.mode === "manual"
        ? ""
        : `<div class="dxe-field"><span class="dxe-label" id="dxe-days-l">Days</span><div class="dxe-ctl">${dayTilesHtml()}</div></div>`;
    return (
      `<div class="dxe-card">` +
      days +
      `<div class="dxe-field"><span class="dxe-label">Time</span><div class="dxe-ctl">${timeHtml()}${whenWarnings()}</div></div>` +
      priorityHtml() +
      `</div>`
    );
  }

  // Priority only matters where another lineup runs at the same time.
  function priorityHtml() {
    const over = overlaps().known;
    if (!over.length) return "";
    const named = over.slice(0, 2).map(({ l, days }) => {
      const p = (S.priorities || {})[l.id];
      const on = days.length === 7 ? "" : daysText(DAYS.map((_, i) => days.includes(i)));
      const when = l.window.is_point
        ? `at ${R.clock(l.window.start_min)}`
        : R.clockRange(l.window.start_min, l.window.end_min);
      return `${l.name} ${on ? `${on} ` : ""}${when}${p !== undefined ? ` (priority ${p})` : ""}`;
    });
    const more = over.length > 2 ? ` and ${over.length - 2} more` : "";
    return (
      `<div class="dxe-field"><label class="dxe-label" for="dxe-prio">Priority</label><div class="dxe-ctl">` +
      `<input type="number" class="dxe-in dxe-num" id="dxe-prio" data-fk="prio" data-priority min="-1000" max="1000" value="${esc(st.priority)}" aria-describedby="dxe-prio-why">` +
      `<p class="dxe-note" id="dxe-prio-why">${ico("layers")}<span>Overlaps ${esc(named.join(" and "))}${esc(more)}. Higher number wins.</span></p></div></div>`
    );
  }

  function secHead(n, title, lead, id) {
    return (
      `<div class="dxe-sec-head"><h2 class="dxe-h2" id="${id}"><span class="dxe-n" aria-hidden="true">${n}</span>${esc(title)}</h2>` +
      (lead ? `<p class="dxe-lead">${esc(lead)}</p>` : "") +
      `</div>`
    );
  }

  /* ------------------------------------------------------------ 2 Dashboards */
  function stepsLead() {
    const n = st.steps.length;
    if (!n) return "No dashboards yet.";
    const k = kind();
    if (k === "manual") {
      const w = flipWord();
      return `${w.charAt(0).toUpperCase()}${w.slice(1)} step through them.`;
    }
    if (k === "daily") return n > 1 ? "Only the first dashboard is sent." : "Sent once, then held.";
    if (k === "interval")
      return sendsAtWake()
        ? "Redrawn just before each wake."
        : `Sent again every ${mins(st.interval)}.`;
    if (ranked()) {
      // Priority mode never takes them in turn: the first whose conditions pass shows.
      const last = st.steps[n - 1];
      return hasConditions(last)
        ? "Shows the first one whose conditions pass. When none do, the screen stays as it is."
        : `Shows the first one whose conditions pass, else ${pageOf(last.id).name}.`;
    }
    const same = st.steps.every((s) => dwellOf(s) === st.interval);
    // Rules can skip a dashboard, so the loop is only the plan.
    const loop = st.steps.some(hasConditions) ? "planned loop" : "full loop";
    return same
      ? `${mins(st.interval)} each, ${loop} ${mins(loopMinutes())}.`
      : `${loop.charAt(0).toUpperCase()}${loop.slice(1)} ${mins(loopMinutes())}.`;
  }

  // NOW / NEXT from the Lineups view, only while the steps are as saved.
  function liveFlags(id) {
    const l = S.lineup;
    if (!l || dirtySteps()) return {};
    const s = l.steps.find((x) => x.dashboard_id === id);
    if (!s) return {};
    // Now is what is on the glass; the step due but not landed yet is queued.
    const now = !!s.on_screen || (!!s.current && !s.queued && !l.steps.some((x) => x.on_screen));
    if (timingDirty()) return { now };
    return {
      now,
      queued: s.queued && s.pill ? s.pill : null,
      next: !!s.next,
      nextLabel: s.pill && s.next ? s.pill.label : "",
    };
  }
  const savedIds = S.steps.map((s) => s.id).join(",");
  const dirtySteps = () => st.steps.map((s) => s.id).join(",") !== savedIds;
  const isSavedStep = (id) =>
    !S.isNew && S.steps.some((s) => s.id === id) && st.display === S.display;

  // The tags a dashboard carries in its caption: what differs from the rest.
  function stepTags(s, i) {
    const p = pageOf(s.id);
    const n = st.steps.length;
    const k = kind();
    const home = st.home === s.id || (!st.home && i === 0);
    let tags = "";
    if (k === "daily" && i > 0) tags += `<span class="dxe-tag mute">Not sent</span>`;
    if (strandedFor(s.id, st.display))
      tags += `<span class="dxe-tag warn" title="Bound to another display; saving asks to bind it here">Not on this display</span>`;
    if (s.dwell && s.dwell !== st.interval && k === "cycle")
      tags += `<span class="dxe-tag time" title="Shows ${esc(mins(s.dwell))}, not the lineup's ${esc(mins(st.interval))}">${ico("clock")}${esc(mins(s.dwell))}</span>`;
    // Only an unusual Updates is said: a dashboard that redraws while shown.
    if (s.refresh)
      tags += `<span class="dxe-tag time" title="Updates every ${esc(mins(s.refresh))} while shown">${ico("refresh")}${esc(mins(s.refresh))}</span>`;
    if (home && (takesInput() || k === "manual") && n > 1)
      tags += `<span class="dxe-tag home" title="Home dashboard">★ home</span>`;
    if (p.missing) tags += `<span class="dxe-tag warn">Deleted</span>`;
    return tags;
  }
  const canPush = (id) => isSavedStep(id) && !pageOf(id).missing && !strandedFor(id, st.display);
  // Its items can be stepped through (the Lineups arrows): a saved step on this display.
  function canPick(id) {
    const it = pageOf(id).items || {};
    return (
      canPush(id) &&
      !!S.urls.stepItem &&
      ((it.mode === "pick" && it.count > 1) || it.mode === "draw")
    );
  }

  /* --- the strip: the Lineups page's own step tiles (lineups_render.js stepHtml),
     bigger, in order; a tile opens its details under the strip. */
  function displayModel() {
    const v = viewOf(st.display) || {};
    const w = Number(v.width) || 16;
    const h = Number(v.height) || 10;
    const dev = deviceOf(st.display) || {};
    return {
      id: st.display || "",
      name: dev.name || "",
      orientation: h > w ? "p" : "l",
      gamut: v.gamut,
      grey_levels: v.grey_levels,
      width: w,
      height: h,
      aspect: `${w}/${h}`,
    };
  }
  // Priority mode on a rotation: a ranked list of choices, not a loop.
  const ranked = () => st.mode === "priority" && kind() === "cycle" && st.steps.length > 1;
  const ordinal = (k) =>
    k + (k % 100 >= 10 && k % 100 <= 20 ? "th" : { 1: "st", 2: "nd", 3: "rd" }[k % 10] || "th");
  function condsOf(s) {
    try {
      const v = JSON.parse(s.conditions || "[]");
      return Array.isArray(v) ? v : [];
    } catch {
      return [];
    }
  }
  function stepModel(s, i, d) {
    const p = pageOf(s.id);
    const it = p.items || {};
    const n = st.steps.length;
    const f = liveFlags(s.id);
    const rank = ranked();
    const chip = R.condChip(condsOf(s), S.entityNames || {});
    // Whether its conditions pass now: the Lineups view's answer, while they are as saved.
    const saved = S.lineup && !dirtySteps() && S.lineup.steps.find((x) => x.dashboard_id === s.id);
    const condOk = saved && saved.cond_label === chip.label ? saved.cond_ok : null;
    const pick = it.mode === "pick" && it.count > 1;
    return {
      index: i,
      dashboard_id: s.id,
      name: p.name,
      thumb_url: p.missing ? "" : p.thumb,
      aspect: d.aspect,
      item_count: pick ? Number(it.count) || 0 : 1,
      draw_count: it.mode === "draw" ? Number(it.count) || 0 : 0,
      stacked: !!it.empty && !!(p.recent && p.recent.length),
      pick_items: pick,
      draw_items: it.mode === "draw",
      items: { length: Number(it.count) || 0 },
      item_noun: String(it.noun || "items").replace(/s$/, ""),
      draw_label: "Draw another",
      position: rank ? `${ordinal(i + 1)} choice` : `${i + 1}/${n}`,
      separator: i < n - 1 ? (rank ? "else" : "arrow") : "",
      cond_label: chip.label,
      cond_title: chip.title,
      cond_ok: condOk === undefined ? null : condOk,
      fallback: rank && i === n - 1 && !hasConditions(s),
      pill: f.now
        ? { kind: "now", label: "Now", title: "On screen now" }
        : f.queued
          ? f.queued
          : f.next
            ? { kind: "next", label: "Next", title: f.nextLabel || "Next" }
            : null,
      on_screen: !!f.now,
      current: !!f.now,
      next: !!f.next,
      missing: !!p.missing,
      off_display: false,
      composition_digest: "",
      push_title: `Push ${p.name} now`,
      conditional: hasConditions(s),
    };
  }
  function stripHtml() {
    const d = displayModel();
    const n = st.steps.length;
    const vms = st.steps.map((s, i) => stepModel(s, i, d));
    const lineup = (id) => ({
      id: S.deckId || "new",
      name: st.name,
      steps: vms,
      actions: {
        push_step_url: canPick(id) ? S.urls.pushStep : "",
        step_item_url: S.urls.stepItem,
      },
    });
    const hooks = {
      pushOverlay: false,
      zoomBeside: true,
      itemChoice: (_d, _l, vm) => choiceView(vm.dashboard_id),
      stepKey: (vm) => vm.dashboard_id,
      stepAttrs: () => ` draggable="true"`,
      stepClass: (vm) =>
        [
          "dxe-step",
          ui.open.has(vm.dashboard_id) ? "is-open" : "",
          kind() === "daily" && vm.index > 0 ? "is-muted" : "",
        ]
          .filter(Boolean)
          .join(" "),
      tileExtra: (vm) => {
        const open = ui.open.has(vm.dashboard_id);
        const duplicate = vm.missing
          ? ""
          : `<button type="button" class="lu-zoom dxe-duplicate" data-duplicate="${esc(vm.dashboard_id)}" data-fk="dup-${esc(vm.dashboard_id)}" title="Duplicate ${esc(vm.name)}" aria-label="Duplicate ${esc(vm.name)}"${ui.duplicating ? " disabled" : ""}>${ico("copy")}</button>`;
        return `<button type="button" class="dxe-sel" data-expand="${esc(vm.dashboard_id)}" data-grip data-fk="sel-${esc(vm.dashboard_id)}" aria-expanded="${open}" aria-controls="dxe-detail" aria-label="${esc(vm.name)}, ${vm.index + 1} of ${n}. ${open ? "Hide" : "Show"} its details; Alt+arrows move it"></button>${duplicate}`;
      },
      captionExtra: (vm) => {
        const tags = stepTags(st.steps[vm.index], vm.index);
        return tags ? `<span class="dxe-step-tags">${tags}</span>` : "";
      },
    };
    // Every tile's picture loads at once: a strip that scrolls sideways is short.
    const tiles = st.steps
      .map((s, i) => R.stepHtml(d, lineup(s.id), vms[i], hooks))
      .join("")
      .replaceAll(' loading="lazy"', ' loading="eager"');
    const addTile = st.display
      ? `<li class="lu-step dxe-add-step"><figure><span class="lu-slot"></span><button type="button" class="dxe-add-tile" data-picker data-fk="picker" aria-haspopup="dialog">${ico("plus")}<span>Add dashboard</span></button></figure></li>`
      : "";
    const now = vms.some((x) => x.pill) ? " has-now" : "";
    return (
      `<div class="dxe-tiles-scroll"><ol class="lu-strip dxe-tiles ${d.orientation}${now}" id="dxe-steps" aria-label="Dashboards in order">${tiles}${addTile}</ol>` +
      (n > 1 ? `<p class="dxe-soft dxe-hint">Drag or Alt+←→ to reorder.</p>` : "") +
      `</div>` +
      detailHtml()
    );
  }

  // The open dashboard's details, under the strip: what it is (its widget's icon and
  // one line), what it shows one of, then its own settings and actions.
  function detailHtml() {
    const id = [...ui.open][0];
    const i = st.steps.findIndex((s) => s.id === id);
    if (i < 0) return "";
    const s = st.steps[i];
    const p = pageOf(id);
    const c = choiceView(id);
    const ready = !c || c.state === "ready";
    const label = c && c.label ? `Push ${c.label}` : "Push now";
    const push = S.isNew
      ? ""
      : `<button type="button" class="lu-btn-ghost lu-btn-line dxe-act dxe-push" data-push="${esc(id)}" data-fk="push-${esc(id)}"${canPush(id) && !ui.pushing && ready ? "" : " disabled"} title="${!canPush(id) ? "Save first to push it" : ready ? `Push ${esc(p.name)} to the display now` : "Wait for this picture to finish"}">${ico("send")}<span>${esc(label)}</span></button>`;
    // Draw another sits with Push now: both act on this dashboard at once.
    const it = p.items || {};
    let drawBtn = "";
    if (!S.isNew && it.mode === "draw") {
      const c0 = ui.choice[id];
      const hist = c0 && c0.mode === "draw" ? c0.hist : [];
      const ok = canPick(id);
      const busy = hist.length && hist[hist.length - 1].state === "loading";
      drawBtn = `<button type="button" class="lu-btn-ghost lu-btn-line dxe-act dxe-draw" data-draw="${esc(id)}" data-fk="draw-${esc(id)}"${ok && !busy ? "" : ` disabled title="${ok ? "Drawing…" : "Save first to draw"}"`}>${ico("refresh")}<span>Draw another</span></button>`;
    }
    const sub = p.subtitle && p.subtitle !== p.name ? p.subtitle : "";
    const body = S.isNew ? newStepBody(s, p) : `${childrenHtml(s, p)}${settingsHtml(s, p, i)}`;
    return (
      `<section class="dxe-detail" id="dxe-detail" data-panel="${esc(id)}" aria-label="${esc(p.name)}">` +
      `<div class="dxe-detail-head"><span class="dxe-plug" aria-hidden="true"><i class="ph ${esc(p.icon || "ph-squares-four")}"></i></span>` +
      `<span class="dxe-detail-t"><span class="dxe-name-row"><b>${esc(p.name)}</b>${editLink(p)}</span>${sub ? `<span class="dxe-soft">${esc(sub)}</span>` : ""}</span>` +
      `<span class="dxe-detail-acts">${drawBtn}${push}</span><button type="button" class="lu-ibtn dxe-close" data-expand="${esc(id)}" data-fk="close-${esc(id)}" aria-label="Close ${esc(p.name)} details" title="Close (Esc)">${ico("x")}</button></div>` +
      body +
      `</section>`
    );
  }

  // A new lineup's dashboard: what it does, an optional condition (closed
  // until asked for) and Remove.
  function newStepBody(s, p) {
    const id = esc(s.id);
    const open = showConditions(s);
    const rm = `<button type="button" class="dxe-link quiet" data-remove="${id}" data-fk="rm-${id}" aria-label="Remove ${esc(p.name)}">Remove</button>`;
    if (!open)
      return (
        `<div class="dxe-sum"><span class="dxe-soft dxe-sum-t">Shows whenever the lineup runs.</span>` +
        `<span class="dxe-sum-r"><button type="button" class="dxe-link" data-add-cond="${id}" data-fk="addcond-${id}" aria-label="Add a condition for ${esc(p.name)}">Add a condition</button>${rm}</span></div>`
      );
    return (
      `<div class="dxe-sum"><span class="dxe-sum-t"><b id="dxe-cond-t-${id}">Only show while…</b> <span class="dxe-soft">optional</span></span>` +
      `<span class="dxe-sum-r"><button type="button" class="dxe-link quiet" data-clear-cond="${id}" data-fk="clearcond-${id}" aria-label="Clear the condition for ${esc(p.name)}">Clear condition</button>${rm}</span></div>` +
      `<div class="dxe-cond dxe-cond-new" role="group" aria-labelledby="dxe-cond-t-${id}">` +
      `<small class="dxe-soft">Skipped while a condition fails. If none pass, the display keeps its last picture.</small>` +
      `<div class="condition-picker" data-condition-picker data-empty-message="No condition yet."><textarea rows="2" spellcheck="false" data-cond="${id}" data-fk="cond-${id}" placeholder="[]" aria-label="Conditions for ${esc(p.name)}">${esc(s.conditions)}</textarea></div></div>`
    );
  }

  /* The gear beside a dashboard's name opens it in the dashboard editor. */
  function editLink(p) {
    if (p.missing || !S.urls.editPage) return "";
    const href = S.urls.editPage.replace("__ID__", encodeURIComponent(p.id));
    return `<a class="lu-gear dxe-gear" href="${esc(href)}" data-edit-dashboard="${esc(p.id)}" title="Edit dashboard" aria-label="Edit dashboard ${esc(p.name)}">${ico("gear")}</a>`;
  }

  /* --- children: what a dashboard shows one of */
  function pinnedKey(s, p) {
    return s.pin !== undefined ? s.pin : (p.items && p.items.pinned) || "";
  }
  function itemTile(p, it, mark, pinMode) {
    const label = it.label || `#${it.index + 1}`;
    const pic = it.thumb_url
      ? `<img src="${esc(it.thumb_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
      : `<span class="dxe-it-word">${esc(label)}</span>`;
    const tag = mark
      ? `<span class="dxe-tag ${mark === "Chosen" ? "now" : "mute"}">${esc(mark)}</span>`
      : "";
    const inner = `<span class="dxe-it-pic">${pic}</span><span class="dxe-it-l">${esc(`#${it.index + 1} ${it.thumb_url ? label : ""}`.trim())}</span>${tag}`;
    return pinMode
      ? `<button type="button" class="dxe-it${mark ? " is-marked" : ""}" data-pin-item="${esc(p.id)}" data-key="${esc(it.key)}" data-fk="pin-${esc(p.id)}-${esc(it.key)}" aria-pressed="${mark === "Chosen"}" title="Show ${esc(label)}">${inner}</button>`
      : `<span class="dxe-it${mark ? " is-marked" : ""}" title="${esc(label)}">${inner}</span>`;
  }

  function childrenHtml(s, p) {
    const it = p.items || {};
    if (p.missing) return `<p class="dxe-one">${esc(p.subtitle)}</p>`;
    if (it.mode === "pick") {
      const pinned = pinnedKey(s, p);
      const pinMode = !!it.pinnable && !!pinned;
      const head =
        `<div class="dxe-kids-head"><span class="dxe-mono">${esc(plural(it.count, it.noun || "items"))}</span>` +
        `<span class="dxe-soft">${pinned ? `Shows the chosen one.` : it.order === "random" ? `A random one each visit.` : `Rotates through them.`}</span>` +
        (it.pinnable
          ? `<span class="lu-seg dxe-seg sm" role="group" aria-label="${esc(p.name)}: rotate items or show chosen"><button type="button" data-pin-mode="${esc(p.id)}" data-mode="walk" data-fk="pinmode-${esc(p.id)}-walk" aria-pressed="${!pinned}">Rotate items</button><button type="button" data-pin-mode="${esc(p.id)}" data-mode="pin" data-fk="pinmode-${esc(p.id)}-pin" aria-pressed="${!!pinned}">Show chosen</button></span>`
          : "") +
        // A pin is the dashboard's own setting, so it moves every lineup that shows it.
        (it.pinnable && p.shared
          ? `<small class="dxe-soft dxe-shared">Applies to this dashboard in every lineup.</small>`
          : "") +
        `</div>`;
      const strip = ui.strip[p.id];
      let tiles = "";
      if (!strip || strip.busy)
        tiles = Array.from(
          { length: Math.min(STRIP, it.count || STRIP) },
          () =>
            `<span class="dxe-it is-ghost" aria-hidden="true"><span class="dxe-it-pic"></span></span>`
        ).join("");
      else if (strip.err) tiles = `<span class="dxe-soft">${esc(strip.err)}</span>`;
      else {
        const gridOpen = !!(ui.grid[p.id] && ui.grid[p.id].open);
        tiles = strip.data.items
          .slice(0, gridOpen ? 0 : STRIP)
          .map((x) => itemTile(p, x, pinned && x.key === pinned ? "Chosen" : "", pinMode))
          .join("");
        const more = it.count - Math.min(STRIP, strip.data.items.length);
        const g = ui.grid[p.id];
        if (more > 0)
          tiles += `<button type="button" class="dxe-more" data-grid="${esc(p.id)}" data-fk="grid-${esc(p.id)}" aria-expanded="${!!(g && g.open)}">${ico("layers")}<span><b>${g && g.open ? "Hide" : `+${more} more`}</b><small>${g && g.open ? "" : "Browse all"}</small></span></button>`;
      }
      return `<div class="dxe-kids${it.fit ? " is-fit" : ""}"${shapeVars()} aria-busy="${!strip || !!strip.busy}">${head}<div class="dxe-strip">${tiles}</div>${gridHtml(s, p)}</div>`;
    }
    if (it.empty) {
      // A list widget that listed nothing: what it last sent, to look at only (those may
      // be filtered out now or the wrong shape), and a line saying why there is no list.
      const sent = (Array.isArray(p.recent) ? p.recent : []).slice(0, STRIP);
      const lastLabel = liveFlags(s.id).now ? "On screen" : "Last sent";
      const tiles = sent
        .map(
          (r, k) =>
            `<span class="dxe-it is-render" title="${k === 0 ? lastLabel : "Sent earlier"}"><span class="dxe-it-pic"><img src="${esc(r.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer"></span><span class="dxe-it-l">${k === 0 ? lastLabel : "Sent earlier"}</span></span>`
        )
        .join("");
      return (
        `<div class="dxe-kids dxe-recent is-empty"${shapeVars()}><div class="dxe-kids-head">${sent.length ? `<span class="dxe-mono">Recent photos</span>` : ""}` +
        `<span class="dxe-soft">No matches to list right now.</span></div>` +
        (tiles ? `<div class="dxe-strip">${tiles}</div>` : "") +
        `</div>`
      );
    }
    if (it.mode === "draw") {
      const c = ui.choice[p.id];
      const hist = c && c.mode === "draw" ? c.hist : [];
      const pos = c && c.mode === "draw" ? c.pos : -1;
      const ok = canPick(s.id);
      const tile = (k, pic, label, on) =>
        `<button type="button" class="dxe-it is-render${on ? " is-marked" : ""}" data-draw-pos="${esc(p.id)}" data-pos="${k}" data-fk="dpos-${esc(p.id)}-${k}" aria-pressed="${on}"${ok ? "" : " disabled"} title="${esc(label)}"><span class="dxe-it-pic">${pic}</span><span class="dxe-it-l">${esc(label)}</span></button>`;
      // Newest first: pictures drawn here and not sent yet, then what this dashboard
      // actually sent (the push log), the latest marked. Up to STRIP in all.
      const sent = Array.isArray(p.recent) ? p.recent : [];
      const lastLabel = liveFlags(s.id).now ? "On screen" : "Last sent";
      const drawn = hist
        .map((e, k) =>
          tile(
            k,
            e.thumb
              ? `<img src="${esc(e.thumb)}" alt="">`
              : `<span class="dxe-it-word">${e.state === "error" ? "!" : "…"}</span>`,
            e.state === "error" ? e.note : e.state === "ready" ? `Draw ${e.n}` : "Drawing…",
            k === pos
          )
        )
        .reverse();
      const sentTiles = (sent.length ? sent : p.thumb ? [{ thumb: p.thumb }] : []).map((r, k) => {
        const pic = `<img src="${esc(r.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
        return k === 0
          ? tile(-1, pic, lastLabel, pos < 0)
          : `<span class="dxe-it is-render" title="Sent earlier"><span class="dxe-it-pic">${pic}</span><span class="dxe-it-l">Sent earlier</span></span>`;
      });
      const strip = [...drawn, ...sentTiles].slice(0, STRIP).join("");
      return (
        `<div class="dxe-kids dxe-recent"${shapeVars()}><div class="dxe-kids-head"><span class="dxe-mono">Recent photos</span>` +
        `<span class="dxe-soft">${c && c.note ? esc(c.note) : "A random photo each time it's sent."}</span></div>` +
        `<div class="dxe-strip">${strip}</div></div>`
      );
    }
    return "";
  }

  function gridHtml(s, p) {
    const g = ui.grid[p.id];
    if (!g || !g.open) return "";
    const it = p.items || {};
    const pinMode = !!it.pinnable && !!pinnedKey(s, p);
    const pinned = pinnedKey(s, p);
    const data = g.data;
    const pages = data ? Math.max(1, Math.ceil(data.matched / GRID_PAGE)) : 1;
    const cur = Math.floor(g.offset / GRID_PAGE) + 1;
    let pager = "";
    if (pages > 1) {
      const nums = [];
      for (let n = 1; n <= pages; n++)
        if (n === 1 || n === pages || Math.abs(n - cur) <= 1) nums.push(n);
        else if (nums[nums.length - 1] !== "…") nums.push("…");
      pager =
        `<nav class="dxe-pager" aria-label="Pages">` +
        `<button type="button" class="lu-ibtn sm" data-gpage="${cur - 1}" data-fk="gprev-${esc(p.id)}"${cur <= 1 ? " disabled" : ""} aria-label="Previous page">${ico("chevl")}</button>` +
        nums
          .map((n) =>
            n === "…"
              ? `<span class="dxe-soft">…</span>`
              : `<button type="button" class="dxe-pg" data-gpage="${n}" data-fk="gpage-${esc(p.id)}-${n}"${n === cur ? ' aria-current="page"' : ""}>${n}</button>`
          )
          .join("") +
        `<button type="button" class="lu-ibtn sm" data-gpage="${cur + 1}" data-fk="gnext-${esc(p.id)}"${cur >= pages ? " disabled" : ""} aria-label="Next page">${ico("chev")}</button></nav>`;
    }
    const range = data
      ? data.matched
        ? `${g.offset + 1}–${Math.min(data.matched, g.offset + GRID_PAGE)} of ${data.matched}`
        : `None match`
      : "";
    const body = !data
      ? `<p class="dxe-soft dxe-loading">Loading…</p>`
      : `<div class="dxe-grid-items${g.busy ? " is-busy" : ""}"${g.busy ? ' aria-busy="true"' : ""}>${data.items.map((x) => itemTile(p, x, pinned && x.key === pinned ? "Chosen" : "", pinMode)).join("")}</div>`;
    return (
      `<div class="dxe-grid" data-grid-for="${esc(p.id)}">` +
      `<div class="dxe-grid-tb"><label class="lu-tb-search dxe-search">${ico("search")}<input type="search" data-fk="q-${esc(p.id)}" data-grid-q="${esc(p.id)}" value="${esc(g.q)}" placeholder="Search ${esc(plural(it.count, it.noun || "items"))}" aria-label="Search ${esc(p.name)}" autocomplete="off" spellcheck="false"></label>` +
      `<span class="dxe-mono dxe-range" aria-live="polite">${esc(range)}</span></div>` +
      body +
      pager +
      `</div>`
    );
  }

  /* --- per-dashboard settings: what differs, in one line, and its actions */
  function settingsHtml(s, p, i) {
    const k = kind();
    const window = k === "cycle";
    const bits = [];
    if (window)
      bits.push(
        s.dwell && s.dwell !== st.interval
          ? `shows ${mins(s.dwell)}`
          : `shows ${mins(st.interval)}, the default`
      );
    if (s.refresh) bits.push(`updates every ${mins(s.refresh)} while shown`);
    if (showKeepReady() && s.override !== null && s.override !== undefined)
      bits.push(s.override ? `drawn ahead every ${mins(s.override)}` : "not drawn ahead");
    let nCond = 0;
    try {
      nCond = s.conditions.trim() ? JSON.parse(s.conditions).length : 0;
    } catch {
      nCond = 1;
    }
    if (nCond) bits.push(plural(nCond, "conditions"));
    const sum = bits.join(" · ");
    const editing = ui.editing.has(s.id);
    const home = st.home === s.id || (!st.home && i === 0);
    const homeBtn =
      (takesInput() || k === "manual") && st.steps.length > 1
        ? `<button type="button" class="dxe-link quiet" data-home="${esc(s.id)}" data-fk="home-${esc(s.id)}"${home ? ' aria-disabled="true"' : ""} aria-label="${home ? `${esc(p.name)} is home` : `Make ${esc(p.name)} home`}">${home ? "★ Home" : "Make home ★"}</button>`
        : "";
    let edit = "";
    if (editing) {
      const showFor = window
        ? `<label class="dxe-mini"><span>Shows for</span><select class="dxe-in dxe-select" data-fk="dwell-${esc(s.id)}" data-dwell="${esc(s.id)}">` +
          `<option value="">Lineup default (${esc(mins(st.interval))})</option>` +
          (s.dwell && !SHOW_FOR.includes(s.dwell)
            ? `<option value="${s.dwell}" selected>${esc(mins(s.dwell))}</option>`
            : "") +
          SHOW_FOR.map(
            (v) =>
              `<option value="${v}"${s.dwell === v ? " selected" : ""}>${esc(mins(v))}</option>`
          ).join("") +
          `</select></label>`
        : "";
      // A repeat on a sleeping display is redrawn on each send already:
      // Updates while shown only shows here once it is set.
      const upd =
        kind() === "interval" && wakeOf() && !s.refresh
          ? ""
          : `<label class="dxe-mini"><span>Updates while shown</span><select class="dxe-in dxe-select" data-fk="upd-${esc(s.id)}" data-updates="${esc(s.id)}">` +
            (UPDATES.includes(s.refresh)
              ? ""
              : `<option value="${s.refresh}" selected>every ${esc(mins(s.refresh))}</option>`) +
            UPDATES.map(
              (v) =>
                `<option value="${v}"${s.refresh === v ? " selected" : ""}>${v ? `every ${esc(mins(v))}` : "no, only when sent"}</option>`
            ).join("") +
            `</select><small class="dxe-soft">Changes this dashboard everywhere.</small></label>`;
      // Ready for presses, for this dashboard: where the display flips by press.
      let keep = "";
      if (showKeepReady()) {
        const cur = s.override === null || s.override === undefined ? "" : String(s.override);
        const opts = [
          ["", `Lineup's (${st.cadence ? `every ${mins(st.cadence)}` : "off"})`],
          ...KEEP_READY.map((v) => [String(v), v ? `every ${mins(v)}` : "off"]),
        ];
        if (cur && !opts.some(([v]) => v === cur)) opts.push([cur, `every ${mins(Number(cur))}`]);
        keep = `<label class="dxe-mini"><span>${esc(readyLabel())}</span><select class="dxe-in dxe-select" data-fk="ov-${esc(s.id)}" data-override="${esc(s.id)}">${opts.map(([v, l]) => `<option value="${v}"${v === cur ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
      }
      const cond = showConditions(s)
        ? `<div class="dxe-mini dxe-cond"><span>Conditions</span><div class="condition-picker" data-condition-picker><textarea rows="2" spellcheck="false" data-cond="${esc(s.id)}" data-fk="cond-${esc(s.id)}" placeholder="[]" aria-label="Conditions for ${esc(p.name)}">${esc(s.conditions)}</textarea></div><small class="dxe-soft">Skipped while they fail.</small></div>`
        : `<div class="dxe-mini dxe-cond"><span>Conditions</span><button type="button" class="dxe-link" data-add-cond="${esc(s.id)}" data-fk="addcond-${esc(s.id)}" aria-label="Add a condition for ${esc(p.name)}">Add a condition</button><small class="dxe-soft">Skips this dashboard while a condition fails.</small></div>`;
      edit = `<div class="dxe-edit">${showFor}${upd}${keep}${cond}</div>${updatesWarning(s, "edit")}`;
    }
    return (
      `<div class="dxe-sum"><span class="dxe-soft dxe-sum-t">${esc(sum.charAt(0).toUpperCase() + sum.slice(1))}</span>` +
      `<span class="dxe-sum-r"><button type="button" class="dxe-link" data-edit="${esc(s.id)}" data-fk="edit-${esc(s.id)}" aria-expanded="${editing}" aria-label="${editing ? "Done with" : "Edit"} ${esc(p.name)}">${editing ? "Done" : "Edit"}</button>` +
      `${homeBtn}<button type="button" class="dxe-link quiet" data-remove="${esc(s.id)}" data-fk="rm-${esc(s.id)}" aria-label="Remove ${esc(p.name)}">Remove</button></span></div>` +
      edit
    );
  }

  /* --- the add-dashboard picker: a dialog over the page. Pick one, Add puts it in
     the lineup at once (asking to bind it first when it is on another display);
     Cancel, Esc or a click outside add nothing. */
  // Every dashboard, those on this display (or on none) first; the ones already in
  // the lineup stay listed, greyed, after the rest of their group.
  function candidates() {
    const inList = new Set(st.steps.map((s) => s.id));
    const all = Object.values(S.pages).filter((p) => !p.missing);
    const last = (a, b) => Number(inList.has(a.id)) - Number(inList.has(b.id));
    const here = all.filter((p) => !strandedFor(p.id, st.display)).sort(last);
    const other = all.filter((p) => strandedFor(p.id, st.display)).sort(last);
    return { here, other, inList };
  }
  const PICK_SEARCH_FROM = 7; // a search field once the list is longer than a glance
  function pickOption(p, inList, elsewhere) {
    const taken = inList.has(p.id);
    const where = elsewhere
      ? `<span class="dxe-pick-note">${ico("screen")}<span>Not on this display (on ${esc(p.devices.map((d) => (deviceOf(d) || { name: d }).name).join(", "))})</span></span>`
      : "";
    const tag = taken ? `<span class="dxe-pick-tag">Already in this lineup</span>` : "";
    const hay = `${p.name} ${p.subtitle || ""}`.toLowerCase();
    return (
      `<div class="dxe-pick-opt${taken ? " is-taken" : ""}" role="option" id="dxe-opt-${esc(p.id)}" tabindex="-1" data-add="${esc(p.id)}"${elsewhere ? " data-elsewhere" : ""} data-hay="${esc(hay)}" aria-selected="false"${taken ? ' aria-disabled="true"' : ""}>` +
      `<span class="dxe-pick-th">${stackedThumb(p)}</span>` +
      `<span class="dxe-pick-text"><b>${esc(p.name)}</b>${p.subtitle && p.subtitle !== p.name ? `<span class="dxe-pick-sub">${esc(p.subtitle)}</span>` : ""}${where}${tag}</span>` +
      `<span class="dxe-pick-mark" aria-hidden="true">${ico("check")}</span></div>`
    );
  }
  function pickerDialogHtml() {
    const { here, other, inList } = candidates();
    const dev = deviceOf(st.display) || { name: st.display };
    const total = here.length + other.length;
    const free = total - inList.size;
    const group = (id, title, list, elsewhere) =>
      list.length
        ? `<div class="dxe-pick-g" role="group" aria-labelledby="${id}"><p class="dxe-pick-gh" id="${id}">${esc(title)}</p>${list.map((p) => pickOption(p, inList, elsewhere)).join("")}</div>`
        : "";
    const search =
      total >= PICK_SEARCH_FROM
        ? `<div class="dxe-pick-find"><label class="lu-tb-search dxe-search">${ico("search")}<input type="search" data-pick-q placeholder="Search dashboards" aria-label="Search dashboards" aria-controls="dxe-pick-list" autocomplete="off" spellcheck="false"></label></div>`
        : "";
    const empty = !total
      ? `<p class="dxe-empty">${ico("layers")}<span>No dashboards yet.</span></p>`
      : !free
        ? `<p class="dxe-soft dxe-pick-allin">Every dashboard is in this lineup already.</p>`
        : "";
    const make = S.urls.newDashboard
      ? `<a class="dxe-pick-make" href="${esc(S.urls.newDashboard)}">Make a new dashboard</a>`
      : "";
    return (
      `<div class="wizard-head"><div class="wizard-head-text"><h2 id="dxe-pick-title">Add a dashboard</h2><p class="wizard-progress">To ${esc(dev.name)}</p></div>` +
      `<button type="button" class="wizard-close" data-add-cancel aria-label="Close">${ico("x")}</button></div>` +
      search +
      `<div class="wizard-body dxe-pick-body">${empty}` +
      (total
        ? `<div class="dxe-pick-list" id="dxe-pick-list" role="listbox" aria-labelledby="dxe-pick-title">` +
          group("dxe-pg-here", `On ${dev.name}`, here, false) +
          group("dxe-pg-other", "On other displays", other, true) +
          `</div><p class="dxe-soft dxe-pick-none" data-pick-none hidden>No dashboards match.</p>`
        : "") +
      `</div>` +
      `<details class="dxe-pick-cond"><summary>Only show while… <span class="dxe-soft">optional</span></summary>` +
      `<div class="condition-picker" data-condition-picker data-empty-message="No conditions: it can show whenever the lineup runs."><textarea id="dxe-add-conditions" rows="2" aria-label="Conditions for the new dashboard" spellcheck="false"></textarea></div>` +
      `<p class="dxe-soft">Skipped while a condition fails; if none pass, the display keeps its last image.</p></details>` +
      `<div class="wizard-footer">${make}<span class="wizard-footer-spacer"></span>` +
      `<button type="button" class="lu-btn-ghost" data-add-cancel>Cancel</button>` +
      `<button type="button" class="lu-btn-primary" data-add-confirm disabled>Add</button></div>`
    );
  }
  // Opens the picker; resolves when it closes (added or not).
  function openPicker() {
    if (!st.display) return;
    const old = document.getElementById("dxe-picker");
    if (old && old.hasAttribute("open")) return;
    if (old) old.remove(); // closed, its close event still to come
    const opener = document.activeElement;
    const nativeDialog =
      typeof HTMLDialogElement !== "undefined" &&
      typeof HTMLDialogElement.prototype.showModal === "function";
    const dlg = document.createElement(nativeDialog ? "dialog" : "div");
    dlg.id = "dxe-picker";
    dlg.className = "wizard-dialog dxe-pick-dlg";
    dlg.setAttribute("aria-labelledby", "dxe-pick-title");
    if (!nativeDialog) {
      dlg.classList.add("is-inline");
      dlg.setAttribute("role", "region");
      dlg.setAttribute("open", "");
    }
    const v = viewOf(st.display) || {};
    dlg.style.cssText = `--dxe-arw:${Number(v.width) || 16};--dxe-arh:${Number(v.height) || 10}`;
    dlg.innerHTML = pickerDialogHtml();
    ui.picker = true;
    const close = () => {
      if (nativeDialog) dlg.close();
      else dlg.dispatchEvent(new Event("close"));
    };
    let sel = null;
    let busy = false;
    let added = false;
    const q = dlg.querySelector("[data-pick-q]");
    const ok = dlg.querySelector("[data-add-confirm]");
    const opts = () => [...dlg.querySelectorAll(".dxe-pick-opt:not([hidden])")];
    const usable = () => opts().filter((o) => o.getAttribute("aria-disabled") !== "true");
    const choose = (o, focus) => {
      if (!o) return;
      dlg.querySelectorAll(".dxe-pick-opt").forEach((x) => {
        x.setAttribute("aria-selected", String(x === o));
        x.tabIndex = x === o ? 0 : -1;
      });
      if (o.getAttribute("aria-disabled") === "true") {
        sel = null;
        ok.disabled = true;
        ok.textContent = "Add";
      } else {
        sel = o.dataset.add;
        ok.disabled = false;
        ok.textContent = `Add ${pageOf(sel).name}`;
      }
      if (focus) {
        o.focus();
        o.scrollIntoView({ block: "nearest" });
      }
    };
    const filter = () => {
      const words = (q ? q.value : "").trim().toLowerCase().split(/\s+/).filter(Boolean);
      dlg.querySelectorAll(".dxe-pick-opt").forEach((o) => {
        o.hidden = !words.every((w) => o.dataset.hay.includes(w));
      });
      dlg.querySelectorAll(".dxe-pick-g").forEach((g) => {
        g.hidden = !g.querySelector(".dxe-pick-opt:not([hidden])");
      });
      const none = dlg.querySelector("[data-pick-none]");
      if (none) none.hidden = !!opts().length;
      // A choice the search hid is no choice.
      const cur = sel && dlg.querySelector(`[data-add="${CSS.escape(sel)}"]`);
      if (cur && cur.hidden) {
        sel = null;
        cur.setAttribute("aria-selected", "false");
        ok.disabled = true;
        ok.textContent = "Add";
      }
      const first = usable()[0] || opts()[0];
      if (first && !opts().some((o) => o.tabIndex === 0)) first.tabIndex = 0;
    };
    const confirm = async () => {
      if (!sel || busy) return;
      busy = true;
      const id = sel;
      // Bound to another display: the bind prompt comes over the picker; its Cancel
      // goes back to the picker with the choice kept.
      const yes = await confirmBind([id], st.display);
      busy = false;
      if (!yes) {
        dlg.querySelector(`[data-add="${CSS.escape(id)}"]`)?.focus();
        return;
      }
      const ta = dlg.querySelector("#dxe-add-conditions");
      added = true;
      shut();
      addStep(id, ta ? ta.value.trim() : "");
    };
    // The page's own handlers never see what happens in here.
    ["click", "keydown", "input", "change", "dblclick"].forEach((type) =>
      dlg.addEventListener(type, (e) => e.stopPropagation())
    );
    // Closed is gone: nothing half-open is left in the page.
    const shut = () => {
      close();
      dlg.remove();
    };
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault(); // Esc
      shut();
    });
    dlg.addEventListener("click", (e) => {
      if (e.target === dlg) return shut(); // the backdrop
      const t = e.target.closest("[data-add-cancel], [data-add-confirm], .dxe-pick-opt");
      if (!t) return;
      if (t.matches("[data-add-cancel]")) return shut();
      if (t.matches("[data-add-confirm]")) return confirm();
      choose(t, true);
      if (t.getAttribute("aria-disabled") === "true")
        live(`${pageOf(t.dataset.add).name} is already in this lineup.`);
    });
    dlg.addEventListener("dblclick", (e) => {
      const o = e.target.closest(".dxe-pick-opt");
      if (o && o.dataset.add === sel) confirm();
    });
    dlg.addEventListener("keydown", (e) => {
      if (!nativeDialog && e.key === "Escape") {
        e.preventDefault();
        return shut();
      }
      const o = e.target.closest && e.target.closest(".dxe-pick-opt");
      const inQ = e.target === q;
      if (!o && !inQ) return;
      const all = opts();
      const at = o ? all.indexOf(o) : -1;
      const go = (i) => {
        if (!all.length) return;
        e.preventDefault();
        choose(all[Math.max(0, Math.min(all.length - 1, i))], true);
      };
      if (e.key === "ArrowDown") {
        if (inQ) {
          const cur = all.find((x) => x.dataset.add === sel);
          return go(cur ? all.indexOf(cur) : 0);
        }
        return go(at + 1);
      }
      if (e.key === "ArrowUp") {
        if (o && at === 0 && q) {
          e.preventDefault();
          return q.focus();
        }
        return o ? go(at - 1) : undefined;
      }
      if (o && e.key === "Home") return go(0);
      if (o && e.key === "End") return go(all.length - 1);
      if (e.key === "Enter") {
        e.preventDefault();
        if (o) {
          choose(o, false);
          return confirm();
        }
        // In the search: Enter adds the choice when one shows, else picks the first match.
        const cur = all.find((x) => x.dataset.add === sel);
        if (cur) return confirm();
        const first = usable()[0];
        if (first) choose(first, true);
        return;
      }
      if (o && e.key === " ") {
        e.preventDefault();
        return choose(o, true);
      }
      // Typing on the list searches.
      if (o && q && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) q.focus();
    });
    if (q) q.addEventListener("input", filter);
    dlg.addEventListener("close", () => {
      dlg.remove();
      ui.picker = false;
      if (added) return; // the new tile takes the focus
      window.setTimeout(() => {
        const back =
          opener && document.contains(opener) && opener.matches("[data-picker]")
            ? opener
            : root.querySelector("[data-picker]");
        back?.focus({ preventScroll: true });
      }, 0);
    });
    root.append(dlg);
    filter();
    if (nativeDialog) dlg.showModal();
    else dlg.scrollIntoView({ block: "nearest" });
    const display = st.display;
    request(S.urls.pickerPreviews, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_id: display,
        page_ids: Object.values(S.pages)
          .filter((p) => !p.missing)
          .map((p) => p.id),
      }),
    })
      .then((data) => {
        if (!dlg.isConnected || st.display !== display) return;
        Object.entries(data.pages || {}).forEach(([id, shot]) => {
          Object.assign(S.pages[id], shot);
          const thumb = dlg.querySelector(`[data-add="${CSS.escape(id)}"] .dxe-pick-th`);
          if (thumb) thumb.innerHTML = stackedThumb(pageOf(id));
        });
      })
      .catch(() => {
        // The shared pictures remain available when display previews cannot load.
      });
    // Search first on a long list; else the first dashboard that can be added.
    if (q) q.focus();
    else {
      const first = usable()[0];
      if (first) first.focus();
      else ok.closest(".wizard-footer").querySelector("[data-add-cancel]").focus();
    }
  }

  function emptyHtml() {
    return `<div class="dxe-none"><button type="button" class="dxe-add-tile dxe-add-first" data-picker data-fk="picker" aria-haspopup="dialog"${st.display ? "" : " disabled"}>${ico("plus")}<span>Add a dashboard</span></button><p class="dxe-none-t"><b>No dashboards yet</b><span class="dxe-soft">${st.display ? "Add one or more. They show in the order you add them." : "Pick a display first."}</span></p></div>`;
  }
  function stepsCard() {
    const n = st.steps.length;
    return `<div class="dxe-card dxe-steps-card${n ? "" : " is-empty"}"${shapeVars()}>${n ? stripHtml() : emptyHtml()}</div>`;
  }

  /* ------------------------------------------------------------ 3 Timing */
  // The dashboards with their own time, not the lineup's (a cycle only).
  const ownTime = () =>
    kind() === "cycle" ? st.steps.filter((x) => x.dwell && x.dwell !== st.interval) : [];
  function paceLead() {
    const k = kind();
    if (k === "manual") return `No timer; ${flipWord()} flip it.`;
    // A sleeping display: its facts first, as the rest follows from them.
    const w = wakeOf();
    if (w) return W.header({ name: w.name, wake: w.min });
    if (k === "daily") return "Sent once, then held.";
    if (k === "interval") return `Sent again every ${mins(st.interval)}.`;
    const own = ownTime().length;
    if (!own) return `Next dashboard every ${mins(st.interval)}.`;
    if (own === st.steps.length) return "Every dashboard has its own time.";
    return `Dashboard times override the ${mins(st.interval)} default.`;
  }
  function selectHtml(fk, attr, values, cur, label, fmt, cls) {
    const opts = values.includes(cur) ? values : values.concat([cur]).sort((a, b) => a - b);
    return (
      `<select class="dxe-in dxe-select${cls ? ` ${cls}` : ""}" data-fk="${fk}" ${attr} aria-label="${esc(label)}">` +
      opts
        .map((v) => `<option value="${v}"${v === cur ? " selected" : ""}>${esc(fmt(v))}</option>`)
        .join("") +
      `</select>`
    );
  }
  function switchHtml(fk, attr, on, label) {
    return `<span class="switch dxe-switch"><input type="checkbox" data-fk="${fk}" ${attr}${on ? " checked" : ""} aria-label="${esc(label)}"><span class="switch-track"><span class="switch-thumb"></span></span></span>`;
  }

  // What each display option needs (the settings audit, the timing audit):
  // touch flips only a rotation; buttons always work; wake sync only holds a
  // rotation's or repeat's send; Keep ready only helps a display that flips
  // by press, and a push renders live.
  const showTaps = () => !!capsOf(st.display).touch && kind() === "cycle";
  const showHome = () => {
    const c = capsOf(st.display);
    const k = kind();
    return !!c.inputs && (k === "manual" || (k === "cycle" && (st.taps || !!c.buttons)));
  };
  const showSmart = () => sleeps() && (kind() === "cycle" || kind() === "interval");
  const showKeepReady = () => takesInput() && kind() !== "daily";
  // "First whose conditions pass" is a legacy mode: offered only where set.
  const showMode = () => S.mode === "priority" && kind() === "cycle";
  // Min hold only gates a rotation, and only matters when something can cut
  // a dashboard short: a press, a rule, or another lineup.
  function interrupters() {
    const c = capsOf(st.display);
    const out = [];
    const tap = !!c.touch && st.taps;
    if (c.buttons && tap) out.push("a tap or button");
    else if (c.buttons) out.push("a button");
    else if (tap) out.push("a tap");
    if (st.steps.some(hasConditions)) out.push("a rule");
    if (overlaps().known.length) out.push("another lineup");
    return out;
  }
  const showMinHold = () => kind() === "cycle" && interrupters().length > 0;
  const holdWhy = (list) => `Stays at least ${mins(st.minHold)} before ${list} can replace it.`;

  // "Ready for button presses": a warm frame for each flip by hand.
  function readyLabel() {
    const c = capsOf(st.display);
    if (c.buttons && c.touch) return "Ready for presses and taps";
    return c.touch && !c.buttons ? "Ready for taps" : "Ready for button presses";
  }

  // A warning with its fixes: the words (and a cost line for a widget that
  // pays per render), then a link on the left and buttons on the right,
  // the primary last. fixes: [{attr, label, primary}].
  function wakeWarnHtml(warn, fixes, link) {
    const acts =
      (link
        ? `<a class="dxe-link quiet" href="${esc(link.url)}">${esc(link.label)}</a>`
        : "<span></span>") +
      `<span class="dxe-wake-btns">` +
      fixes
        .map(
          (f) =>
            `<button type="button" class="${f.primary ? "lu-btn-primary" : "lu-btn-ghost lu-btn-line"} sm" ${f.attr} data-fk="${esc(f.fk)}">${esc(f.label)}</button>`
        )
        .join("") +
      `</span>`;
    return (
      `<div class="dxe-warn dxe-wake-warn${warn.paid ? " is-paid" : ""}" role="note">${ico("warn")}` +
      `<span>${esc(warn.text)}${warn.paid ? `<b class="dxe-wake-cost">${esc(warn.paid)}</b>` : ""}</span>` +
      `<div class="dxe-wake-acts">${acts}</div></div>`
    );
  }
  const wakeLink = (w) => (w.url ? { url: w.url, label: "Change wake interval" } : null);

  // The Timing card's warning: a sleeping display that gets more sends than
  // it wakes for, or a rotation whose dashboards can fall between two wakes.
  function wakeCallout() {
    const w = wakeOf();
    if (!w || !w.min) return "";
    const k = kind();
    const paid = paidIn(st.steps);
    if (k === "interval" && !st.smartSync) {
      const warn = W.cadenceWarning({ name: w.name, wake: w.min, every: st.interval, paid });
      if (warn)
        return wakeWarnHtml(
          warn,
          [
            {
              attr: 'data-wake-fix="smart"',
              fk: "wake-fix-smart",
              label: "Send just before each wake",
              primary: true,
            },
          ],
          wakeLink(w)
        );
    }
    if (k === "cycle") {
      const dwells = st.steps.map(dwellOf);
      const skip = W.skipWarning({ name: w.name, wake: w.min, dwells });
      if (skip) {
        const cost = st.smartSync
          ? ""
          : (
              W.cadenceWarning({ name: w.name, wake: w.min, every: Math.min(...dwells), paid }) ||
              {}
            ).paid || "";
        return wakeWarnHtml(
          { text: skip.text, paid: cost },
          [{ attr: 'data-wake-fix="dwell"', fk: "wake-fix-dwell", label: skip.fix, primary: true }],
          wakeLink(w)
        );
      }
    }
    return "";
  }

  // A dashboard's Updates while shown, faster than the display wakes: the
  // same warning, under its control and in the Timing card. On a repeat
  // (redrawn on each send) the fix is to stop it; in a rotation, to match
  // the wakes.
  function updatesWarning(s, where) {
    const w = wakeOf();
    if (!w || !w.min || !s.refresh) return "";
    const warn = W.cadenceWarning({
      name: w.name,
      wake: w.min,
      every: s.refresh,
      noun: "updates",
      paid: paidIn([s]),
    });
    if (!warn) return "";
    const stop = kind() === "interval";
    const to = stop ? 0 : UPDATES.filter((v) => v >= w.min)[0] || 0;
    return wakeWarnHtml(
      {
        text: `${pageOf(s.id).name} updates every ${mins(s.refresh)} while shown. ${warn.text}`,
        paid: warn.paid,
      },
      [
        {
          attr: `data-wake-updates="${esc(s.id)}" data-to="${to}"`,
          fk: `wake-upd-${where}-${s.id}`,
          label: to ? `Update every ${W.wakeMins(to)}` : "Only when sent",
          primary: true,
        },
      ],
      wakeLink(w)
    );
  }

  // How fresh a lineup that sends just before each wake is, with the way to
  // the display's wake interval and the way back to a plain timer.
  function freshHtml(w) {
    const f = W.freshLine({
      kind: kind(),
      wake: w.min,
      every: kind() === "cycle" ? Math.min(...st.steps.map(dwellOf)) : st.interval,
      next: w.next,
      learned: w.learned,
    });
    return (
      `<p class="dxe-fresh">${ico("moon")}<span>${esc(f.line)}${f.until ? `<small class="dxe-soft">${esc(f.until)}</small>` : ""}</span></p>` +
      `<p class="dxe-fresh-acts">` +
      (w.url ? `<a class="dxe-link quiet" href="${esc(w.url)}">Change wake interval</a>` : "") +
      `<button type="button" class="dxe-link quiet" data-wake-timer data-fk="wake-timer">Send on a timer anyway</button></p>`
    );
  }

  function optionsHtml() {
    const dev = deviceOf(st.display);
    if (!dev) return "";
    const caps = dev.caps || {};
    const opt = (title, hint, ctl) =>
      `<div class="dxe-opt"><div><b>${title}</b><p class="dxe-soft">${hint}</p></div>${ctl}</div>`;
    const rows = [];
    if (showTaps())
      rows.push(
        opt(
          "Touch also flips",
          caps.buttons ? "Buttons always work." : "A tap moves on; the timer keeps going.",
          switchHtml("taps", "data-taps", st.taps, "Touch also flips")
        )
      );
    if (showHome()) {
      const home = pageOf(st.home || (st.steps[0] || {}).id || "");
      rows.push(
        opt(
          `Back to ★ ${esc(home.name || "home")}`,
          "When it's left alone.",
          selectHtml(
            "timeout",
            "data-timeout",
            RETURN_HOME,
            st.timeout,
            "Return home after",
            (v) => (v ? `after ${v} idle min` : "never")
          )
        )
      );
    }
    // Sending just before it wakes is chosen in the Timing card above; how
    // far ahead is the fine-tuning left here.
    if (sendsAtWake())
      rows.push(
        opt(
          "How far ahead of a wake",
          caps.wake_learned
            ? `${ico("check")}Wake times learned.`
            : "Learns wake times from check-ins.",
          `<span class="dxe-opt-ctl"><label class="dxe-lead-s"><input type="number" class="dxe-in dxe-num" min="0" max="600" data-fk="lead" data-lead value="${esc(st.lead)}" aria-label="Seconds ahead of a wake"><span>s</span></label></span>`
        )
      );
    if (showKeepReady())
      rows.push(
        opt(
          esc(readyLabel()),
          `Draws ahead so a ${caps.buttons ? "press" : "tap"} shows at once. Doesn't wake the display.`,
          selectHtml(
            "cadence",
            "data-cadence",
            KEEP_READY,
            st.cadence,
            `${readyLabel()} every`,
            (v) => (v ? `every ${mins(v)}` : "off")
          )
        )
      );
    if (!rows.length) {
      if (caps.meural_swipe)
        return `<p class="dxe-one dxe-opts-none">${ico("info")}<span>A swipe on the frame shows the next item, or the next dashboard.</span></p>`;
      const why = caps.inputs || caps.sleeps ? "not for this lineup" : "always on, no buttons";
      return `<p class="dxe-one dxe-opts-none">${ico("info")}<span>Nothing to set for ${esc(dev.name)}: ${esc(why)}.</span></p>`;
    }
    return (
      `<details class="dxe-fold" data-fold="options"${ui.optOpen ? " open" : ""}><summary>${ico("caret", "dxe-caret")}<b>Options for ${esc(dev.name)}</b></summary>` +
      `<div class="dxe-fold-body">${rows.join("")}</div></details>`
    );
  }

  // The dashboards that show for their own time, not the lineup's: each name
  // takes you to its tile.
  function overriddenLine() {
    const own = ownTime();
    if (!own.length) return "";
    const links = own
      .map(
        (s) =>
          `<button type="button" class="dxe-link quiet" data-overridden="${esc(s.id)}">${esc(pageOf(s.id).name)} (${esc(mins(s.dwell))})</button>`
      )
      .join(", ");
    return `<p class="dxe-note dxe-overridden">${ico("warn")}<span>Overridden by: ${links}</span></p>`;
  }

  function paceHtml() {
    const k = kind();
    const field = (label, ctl, forId) =>
      `<div class="dxe-field"><${forId ? `label for="${forId}"` : "span"} class="dxe-label">${label}</${forId ? "label" : "span"}><div class="dxe-ctl">${ctl}</div></div>`;
    const rows = [];
    const w = wakeOf();
    const atWake = !!w && sendsAtWake();
    // A repeat that waits for each wake has one line instead of a timer; its
    // timer stays only as a floor that is longer than the wake interval.
    if (k === "interval" && atWake) {
      rows.push(field("Sent", freshHtml(w)));
      // ...or as what it runs on until the wake times are learned.
      const floor = w.min && st.interval > w.min;
      if (floor || !w.learned) {
        const label = floor ? "At most every" : "Until then, every";
        rows.push(
          field(label, selectHtml("interval", "data-interval", SHOW_FOR, st.interval, label, mins))
        );
      }
    } else if (k === "cycle" || k === "interval") {
      const label = k === "interval" ? "Sent again every" : "Each dashboard shows for";
      // Every dashboard has its own time: the default is unused (it is what
      // a dashboard added later starts with).
      const unused = k === "cycle" && st.steps.length > 0 && ownTime().length === st.steps.length;
      rows.push(
        field(
          label,
          selectHtml(
            "interval",
            "data-interval",
            SHOW_FOR,
            st.interval,
            label,
            mins,
            unused ? "is-unused" : ""
          ) + overriddenLine()
        )
      );
      if (k === "cycle" && atWake) rows.push(field("Sent", freshHtml(w)));
      // On a timer to a sleeping display: each send waits for its wake.
      else if (w && showSmart() && !st.smartSync && !wakeCallout())
        rows.push(
          `<p class="dxe-note dxe-pace-note">${ico("moon")}<span>Each send waits for its next wake.</span><button type="button" class="dxe-link" data-wake-fix="smart" data-fk="wake-fix-smart">Send just before each wake</button></p>`
        );
    }
    if (showMinHold()) {
      const who = interrupters();
      const list =
        who.length > 1 ? `${who.slice(0, -1).join(", ")} or ${who[who.length - 1]}` : who[0];
      rows.push(
        field(
          "Min hold",
          `<span class="dxe-unit"><input type="number" class="dxe-in dxe-num" id="dxe-hold" data-fk="hold" data-hold min="0" max="120" value="${esc(st.minHold)}" aria-describedby="dxe-hold-why"><span>min</span></span>` +
            `<p class="dxe-note" id="dxe-hold-why">${ico("info")}<span>${esc(holdWhy(list))}</span></p>`,
          "dxe-hold"
        )
      );
    }
    if (showMode())
      rows.push(
        field(
          "Picks",
          `<select class="dxe-in dxe-select" id="dxe-mode" data-fk="mode" data-mode-sel><option value="scheduled"${st.mode === "scheduled" ? " selected" : ""}>Each in turn</option><option value="priority"${st.mode === "priority" ? " selected" : ""}>First whose conditions pass</option></select>`,
          "dxe-mode"
        )
      );
    if (k === "daily" && st.steps.length > 1)
      rows.push(
        `<p class="dxe-note dxe-pace-note">${ico("info")}<span><button type="button" class="dxe-link" data-end="add">Add end time</button> to show them in turn.</span></p>`
      );
    const warns = wakeCallout() + st.steps.map((s) => updatesWarning(s, "pace")).join("");
    return `<div class="dxe-card dxe-pace">${rows.join("")}${warns ? `<div class="dxe-wake-warns">${warns}</div>` : ""}${optionsHtml()}</div>`;
  }

  /* ------------------------------------------------------------ the new-lineup flow */
  function doneLine(n, title, text, step, locked) {
    const change = locked
      ? ""
      : `<button type="button" class="dxe-link" data-goto="${step}" data-fk="goto-${step}" aria-label="Change ${esc(title.toLowerCase())}">Change</button>`;
    return `<div class="dxe-done"><span class="dxe-n is-done" aria-hidden="true">${ico("check")}</span><b>${esc(title)}</b><span class="dxe-soft">${esc(text)}</span>${change}<span class="lu-sr-only">Step ${n} done.</span></div>`;
  }
  function waitingHead(n, title, why) {
    return `<div class="dxe-sec-head is-wait"><h2 class="dxe-h2"><span class="dxe-n" aria-hidden="true">${n}</span>${esc(title)}</h2><p class="dxe-lead">${esc(why)}</p></div>`;
  }
  function displayCards() {
    if (!S.devices.length)
      return `<p class="dxe-empty">${ico("screen")}<span>No displays yet.</span></p>`;
    return (
      `<div class="dxe-cards" role="radiogroup" aria-labelledby="dxe-h-display">` +
      S.devices
        .map((d) => {
          const v = viewOf(d.id) || {};
          const th = R.thumb(
            d.name,
            d.thumb,
            `${d.w || 16}/${d.h || 10}`,
            d.h > d.w ? "p" : "l",
            "",
            d.name,
            false,
            v
          );
          return (
            `<button type="button" class="dxe-dcard" role="radio" aria-checked="${d.id === st.display}" tabindex="${d.id === (st.display || S.devices[0].id) ? 0 : -1}" data-pick-display="${esc(d.id)}" data-fk="dcard-${esc(d.id)}">` +
            `<span class="dxe-dcard-th">${th}</span><b>${esc(d.name)}</b><span class="dxe-soft">${esc(d.model || "")}</span>` +
            `<span class="dxe-soft">${d.lineups ? esc(plural(d.lineups, "lineups")) : "No lineups yet"}</span></button>`
          );
        })
        .join("") +
      `</div>`
    );
  }
  function whenSummary() {
    if (st.when.mode === "manual") return "By hand";
    if (st.when.mode === "at") return `At ${clockOf(st.when.start)}, ${daysText(st.when.days)}`;
    const w = window_();
    return `${R.clockRange(w.s, w.e)}, ${daysText(st.when.days)}`;
  }
  // Step 2 is done when it has a day, a start, and an end after the start.
  const whenReady = () =>
    st.when.mode === "manual" ||
    (st.when.days.some(Boolean) && Number.isFinite(T(st.when.start)) && !endBeforeStart());
  const newReady = () => !!st.display && whenReady() && st.steps.length > 0;
  // The save bar's line: the step that is open, what is missing, or what Create
  // will make.
  function newStatus() {
    const dev = deviceOf(st.display);
    if (!dev) return "Step 1 of 3: pick a display.";
    if (ui.step < 3) return "Step 2 of 3: choose when it runs.";
    if (!st.steps.length) return "Add at least one dashboard to create it.";
    if (!whenReady()) return "Choose when it runs to create it.";
    const when = whenSummary();
    return `Ready: ${plural(st.steps.length, "dashboards")} on ${dev.name}, ${when.charAt(0).toLowerCase()}${when.slice(1)}.`;
  }
  // Create pressed before it can be: say what is missing and go there.
  function missingStep() {
    ui.msg = newStatus();
    ui.msgKind = "err";
    drawFooter();
    live(ui.msg);
    const to = !st.display
      ? "[data-pick-display]"
      : ui.step < 3
        ? "[data-goto='3']"
        : "[data-picker]";
    root.querySelector(to)?.focus();
  }
  function newFlowHtml() {
    const dev = deviceOf(st.display);
    let html = "";
    // 1 Display
    // A lineup started from a display's + is for that display: no Change.
    if (ui.step > 1 && dev) html += doneLine(1, "Display", dev.name, 1, S.displayLocked);
    else
      html += `<section class="dxe-sec is-current" aria-labelledby="dxe-h-display" aria-current="step">${secHead(1, "Display", "Pick the display it runs on.", "dxe-h-display")}${displayCards()}</section>`;
    // 2 When
    if (ui.step > 2) html += doneLine(2, "When it runs", whenSummary(), 2);
    else if (ui.step === 2)
      html +=
        `<section class="dxe-sec is-current" aria-labelledby="dxe-h-when" aria-current="step">${secHead(2, "When it runs", whenLead(), "dxe-h-when")}${whenCard()}` +
        `<div class="dxe-next-step"><button type="button" class="lu-btn-primary" data-goto="3" data-fk="goto-3"${whenReady() ? "" : ' aria-disabled="true"'}>Continue</button></div></section>`;
    else html += waitingHead(2, "When it runs", "Pick a display first.");
    // 3 Dashboards
    if (ui.step === 3) {
      const n = st.steps.length;
      const lead = n
        ? `${plural(n, "dashboards")}, shown in this order.${n > 1 ? "" : " Add more if you like."}`
        : "Pick what it shows.";
      html +=
        `<section class="dxe-sec is-current" aria-labelledby="dxe-h-dash" aria-current="step">${secHead(3, "Dashboards", lead, "dxe-h-dash")}` +
        stepsCard() +
        `</section>`;
    } else html += waitingHead(3, "Dashboards", "Choose when it runs first.");
    return html;
  }

  /* ------------------------------------------------------------ draw */
  // A redraw replaces the controls: the focused one (by its data-fk) gets
  // focus, and its caret, back.
  function focusMark() {
    const a = document.activeElement;
    const fk = a && a.dataset ? a.dataset.fk : null;
    let sel = null;
    if (fk && "selectionStart" in a) {
      try {
        sel = [a.selectionStart, a.selectionEnd];
      } catch {
        sel = null;
      }
    }
    if (fk) return { fk, sel };
    // A control the condition picker made (it has no data-fk): its place in its picker.
    const box = a && a.closest ? a.closest("[data-condition-picker]") : null;
    const ta = box ? box.querySelector("[data-cond]") : null;
    if (!ta) return null;
    const idx = [...box.querySelectorAll("input, select, textarea, button, summary")].indexOf(a);
    return idx < 0 ? null : { pk: ta.dataset.cond, idx };
  }
  // `later`: the pickers have been built (they start a moment after a draw).
  function refocus(mark, later) {
    if (!mark) return;
    if (mark.pk) {
      if (!later) return;
      const ta = root.querySelector(`[data-cond="${CSS.escape(mark.pk)}"]`);
      const box = ta ? ta.closest("[data-condition-picker]") : null;
      const back = box
        ? [...box.querySelectorAll("input, select, textarea, button, summary")][mark.idx]
        : null;
      if (back && !back.disabled && document.activeElement !== back) back.focus();
      return;
    }
    const a = document.activeElement;
    if (a && a.dataset && a.dataset.fk === mark.fk && root.contains(a)) return;
    const back = root.querySelector(`[data-fk="${CSS.escape(mark.fk)}"]`);
    if (!back || back.disabled) return;
    back.focus();
    if (mark.sel && "setSelectionRange" in back) {
      try {
        back.setSelectionRange(mark.sel[0], mark.sel[1]);
      } catch {
        /* a time or number input has no selection */
      }
    }
  }

  // Which conditions pickers have "Edit raw JSON" open: a redraw builds the
  // pickers anew, closed.
  function noteRawOpen() {
    root.querySelectorAll("[data-cond]").forEach((ta) => {
      const det = ta.closest("details");
      if (det) ui.rawOpen[det.open ? "add" : "delete"](ta.dataset.cond);
    });
  }

  function draw() {
    const mark = focusMark();
    noteRawOpen();
    const sections = $("dxe-sections");
    if (S.isNew) sections.innerHTML = newFlowHtml();
    else
      sections.innerHTML =
        `<section class="dxe-sec" aria-labelledby="dxe-h-when">${secHead(1, "When it runs", whenLead(), "dxe-h-when")}${whenCard()}</section>` +
        `<section class="dxe-sec" aria-labelledby="dxe-h-dash">${secHead(2, "Dashboards", stepsLead(), "dxe-h-dash")}${stepsCard()}</section>` +
        `<section class="dxe-sec dxe-sec-pace" aria-labelledby="dxe-h-timing">${secHead(3, "Timing", paceLead(), "dxe-h-timing")}${paceHtml()}</section>`;
    drawHeader();
    drawFooter();
    refocus(mark);
    // The pickers start in a microtask queued by the redraw above; this runs after it.
    queueMicrotask(() => {
      ui.rawOpen.forEach((id) => {
        const ta = root.querySelector(`[data-cond="${CSS.escape(id)}"]`);
        const det = ta ? ta.closest("details") : null;
        if (det) det.open = true;
        else if (!ta) ui.rawOpen.delete(id);
      });
      refocus(mark, true);
    });
    ui.justDay = null;
    // A drag cut short by a redraw (an item list landing) never ends.
    dragId = null;
  }

  function drawFooter() {
    const mark = focusMark();
    paintFooter();
    if (!S.isNew) drawLoc(); // Push now sits up there and follows the save state
    refocus(mark);
    // Nothing can be edited while a save is on its way: it would be marked
    // saved and lost.
    const sections = $("dxe-sections");
    const hero = $("dxe-hero");
    if (sections) sections.inert = ui.saving || !!ui.duplicating;
    if (hero) hero.inert = ui.saving;
    const on = $("dxe-enabled");
    if (on) on.disabled = ui.saving;
    const mv = root.querySelector("[data-move]");
    if (mv) mv.disabled = ui.saving || S.devices.length < 2;
    const un = root.querySelector("[data-move-undo]");
    if (un) un.disabled = ui.saving;
  }
  // Only a change rebuilds the buttons: a redraw from a field's change event
  // (on blur, as the pointer goes down on Save) must not swap Save under it.
  function setButtons(el, html) {
    if (el.dataset.html === html) return;
    el.dataset.html = html;
    el.innerHTML = html;
  }
  function paintFooter() {
    const btns = $("dxe-actionbar-btns");
    const state = $("dxe-save-state");
    if (S.isNew) {
      const ready = newReady();
      state.className =
        "dxe-actionbar-status" + (ui.msgKind ? ` is-${ui.msgKind}` : ready ? " is-ok" : "");
      state.textContent = ui.msg || newStatus();
      // One Cancel link, then the one primary action. It stays focusable while it
      // can't be used, and says why (the line beside it, and on a press).
      setButtons(
        btns,
        `<a class="dxe-link quiet dxe-cancel" href="${esc(S.urls.back)}" data-fk="f-cancel">Cancel</a>` +
          `<button type="button" class="lu-btn-primary" data-create data-fk="f-create" aria-describedby="dxe-save-state"${ui.saving || ui.duplicating ? " disabled" : ready ? "" : ' aria-disabled="true"'}>${ui.saving ? "Creating…" : "Create lineup"}</button>`
      );
      return;
    }
    const d = dirty();
    state.className =
      "dxe-actionbar-status" + (ui.msgKind ? ` is-${ui.msgKind}` : d ? " is-dirty" : "");
    state.textContent = ui.msg || (d ? "Unsaved changes" : "All changes saved");
    setButtons(
      btns,
      (ui.reload
        ? `<button type="button" class="lu-btn-ghost lu-btn-line" data-reload data-fk="f-reload">Reload</button>`
        : "") +
        `<button type="button" class="lu-btn-ghost lu-btn-line" data-discard data-fk="f-discard"${d && !ui.saving && !ui.duplicating ? "" : " disabled"}>Discard</button>` +
        `<button type="button" class="lu-btn-primary" data-save data-fk="f-save"${ui.saving || ui.duplicating ? " disabled" : ""}>${ui.saving ? "Saving…" : "Save lineup"}</button>`
    );
  }

  /* ------------------------------------------------------------ edits */
  function change(fn, opts) {
    if (ui.saving) return; // see drawFooter
    const was = kind();
    fn();
    // A cycle cut down to one dashboard is an interval: that dashboard's dwell
    // is its cadence now (the server does the same on save).
    if (was === "cycle" && kind() === "interval" && st.steps[0].dwell) {
      st.interval = st.steps[0].dwell;
      st.steps[0].dwell = null;
    }
    ui.msg = "";
    ui.msgKind = "";
    if (!opts || opts.draw !== false) draw();
    else {
      drawFooter();
      drawHeader();
    }
  }
  const stepOf = (id) => st.steps.find((s) => s.id === id);

  // A new lineup's first time is out of the display's quiet hours, until the
  // time is picked by hand.
  function newStartAfterPause() {
    if (!S.isNew || st.whenChanged || !inPause(T(st.when.start))) return;
    st.when.start = R.hhmm(slotAfterPause(pause().end_min));
  }

  function setWhen(mode) {
    change(() => {
      if (mode === "between" && st.when.mode !== "between") {
        const s = T(st.when.start);
        const span = Math.max(60, loopMinutes() || 60);
        let hi = Number.isFinite(s) ? Math.min(DAY_MIN, s + span) : 17 * 60;
        // Not a window that is all quiet hours: end it a loop after they do.
        if (Number.isFinite(s) && allInPause(s, hi)) {
          const p = pause();
          hi = Math.min(DAY_MIN, slotAfterPause(p.end_min) + span);
        }
        st.when.end = hi >= DAY_MIN ? "24:00" : R.hhmm(hi);
        if (!st.when.start) st.when.start = "07:00";
      }
      if (mode !== "manual" && !st.when.start) st.when.start = "07:00";
      st.when.mode = mode;
      st.whenChanged = true;
    });
    live(
      mode === "between"
        ? "Runs between two times."
        : mode === "at"
          ? "Sends at one time."
          : "Runs by hand."
    );
  }

  function move(id, delta) {
    const i = st.steps.findIndex((s) => s.id === id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= st.steps.length) return;
    change(() => {
      const [one] = st.steps.splice(i, 1);
      st.steps.splice(j, 0, one);
    });
    const grip = root.querySelector(`[data-step="${CSS.escape(id)}"] [data-grip]`);
    if (grip) grip.focus();
    live(`Moved ${pageOf(id).name} to ${j + 1} of ${st.steps.length}.`);
  }

  async function duplicateStep(id) {
    const source = stepOf(id);
    const p = pageOf(id);
    if (!source || p.missing || ui.saving || ui.duplicating) return;
    const copy = { ...source };
    ui.duplicating = id;
    draw();
    live(`Duplicating ${p.name}.`);
    try {
      const body = new FormData();
      body.append("respond", "json");
      const data = await request(S.urls.duplicatePage.replace("__ID__", encodeURIComponent(id)), {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
      });
      ui.duplicating = null;
      change(() => {
        S.pages[data.page.id] = { ...p, ...data.page, shared: 0, recent: [] };
        copy.id = data.page.id;
        st.steps.splice(st.steps.findIndex((s) => s.id === id) + 1, 0, copy);
        if (hasConditions(source)) stepHadConditions.add(copy.id);
        if (ui.consent[id]) ui.consent[copy.id] = ui.consent[id];
        ui.open = new Set([copy.id]);
      });
      const tile = root.querySelector(`[data-step="${CSS.escape(copy.id)}"] .dxe-sel`);
      tile?.focus();
      live(`Duplicated ${p.name}.`);
    } catch (err) {
      ui.duplicating = null;
      ui.msg = `Couldn't duplicate ${p.name}: ${err.message}`;
      ui.msgKind = "err";
      draw();
      root.querySelector(`[data-duplicate="${CSS.escape(id)}"]`)?.focus();
      live(ui.msg);
    }
  }

  // A dashboard picked in the picker (its bind already agreed): it joins the end of
  // the lineup, and its tile takes the focus.
  function addStep(id, conditions) {
    change(() => {
      st.steps.push({
        id,
        dwell: null,
        override: null,
        conditions: conditions || "",
        refresh: pageOf(id).refresh || 0,
        pin: undefined,
      });
      if ((conditions || "").trim()) stepHadConditions.add(id);
      if (!st.home) st.home = "";
      ui.picker = false;
      ui.open.clear();
      ui.open.add(id);
    });
    if (!S.isNew) loadStrip(id);
    live(`Added ${pageOf(id).name}.`);
    // After the closing dialog hands the focus back to what opened it.
    window.setTimeout(() => {
      const tile = root.querySelector(`[data-step="${CSS.escape(id)}"]`);
      if (!tile) return root.querySelector("[data-picker]")?.focus();
      tile.querySelector(".dxe-sel")?.focus({ preventScroll: true });
      tile.scrollIntoView({ block: "nearest", inline: "nearest" });
      tile.classList.add("is-flash");
      window.setTimeout(() => tile.classList.remove("is-flash"), 1800);
    }, 0);
  }

  function removeStep(id) {
    const i = st.steps.findIndex((s) => s.id === id);
    change(() => {
      st.steps = st.steps.filter((s) => s.id !== id);
      if (st.home === id) st.home = "";
      ui.open.delete(id);
      ui.condOpen.delete(id);
      delete ui.choice[id];
      delete ui.consent[id];
    });
    live(`Removed ${pageOf(id).name}.`);
    const next = root.querySelectorAll("[data-step]")[Math.max(0, i - 1)];
    (next ? next.querySelector(".dxe-sel") : root.querySelector("[data-picker]"))?.focus();
  }

  /* --- item lists (dashboard-items) */
  async function fetchItems(id, offset, limit, q) {
    const u = new URLSearchParams({ page_id: id, offset: String(offset), limit: String(limit) });
    if (q) u.set("q", q);
    if (st.display) u.set("display_id", st.display);
    return request(`${S.urls.items}?${u}`, { headers: { Accept: "application/json" } });
  }
  async function loadStrip(id) {
    const p = pageOf(id);
    // A failed load is tried again the next time the row opens.
    if (!p.items || p.items.mode !== "pick" || (ui.strip[id] && !ui.strip[id].err)) return;
    ui.strip[id] = { busy: true };
    try {
      ui.strip[id] = { data: await fetchItems(id, 0, STRIP + 1, "") };
      renderStripThumbs(id);
    } catch (err) {
      ui.strip[id] = { err: err.net ? "Can't reach Tesserae; open it again." : err.message };
    }
    draw();
  }
  // An item with no picture of its own is drawn by the same endpoint the arrows
  // use, a few at a time; its tile shows its name until the picture lands.
  function renderStripThumbs(id) {
    const strip = ui.strip[id];
    if (!strip || !strip.data || !canPick(id)) return;
    const todo = strip.data.items.slice(0, STRIP).filter((x) => !x.thumb_url && x.key);
    let next = 0;
    let timer = 0;
    const worker = async () => {
      while (next < todo.length && ui.strip[id] === strip) {
        const x = todo[next++];
        const e = { key: x.key };
        await pollItem(id, e, () => ui.strip[id] === strip);
        if (e.state === "ready" && ui.strip[id] === strip) {
          x.thumb_url = e.thumb;
          window.clearTimeout(timer);
          timer = window.setTimeout(itemChanged, 120);
        }
      }
    };
    for (let i = 0; i < Math.min(3, todo.length); i++) worker();
  }
  const gridTimers = {}; // page id -> its search's debounce
  async function loadGrid(id) {
    const g = ui.grid[id];
    if (!g) return;
    const want = `${g.offset}|${g.q}`;
    g.want = want;
    g.busy = true;
    try {
      const data = await fetchItems(id, g.offset, GRID_PAGE, g.q);
      if (g.want !== want) return;
      g.data = data;
    } catch (err) {
      if (g.want !== want) return; // a newer search is on its way
      g.data = { items: [], matched: 0 };
      ui.msg = err.message;
      ui.msgKind = "err";
    }
    g.busy = false;
    draw();
  }

  /* --- push now, draw another */
  function postJSON(url, body) {
    return request(url, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  // One push at a time: a double click must not send twice to the display.
  async function pushNow(id) {
    if (ui.pushing) return;
    ui.pushing = true;
    const mark = focusMark();
    ui.msg = `Pushing ${pageOf(id).name}…`;
    ui.msgKind = "";
    draw();
    syncZoom();
    // The item shown on its tile (chosen with the arrows) is the one sent.
    const c = choiceView(id);
    const body = { page_id: id, display_id: st.display };
    if (c && c.key && c.state === "ready") body.item = c.key;
    try {
      const res = await postJSON(S.urls.pushStep, body);
      ui.msg = res.message || "Pushed.";
      ui.msgKind = "ok";
    } catch (err) {
      ui.msg = err.net ? "Can't reach Tesserae; not pushed, try again." : err.message;
      ui.msgKind = "err";
    } finally {
      ui.pushing = false;
    }
    if (zoom) zoom.msg = { text: ui.msg, kind: ui.msgKind === "ok" ? "ok" : "err" };
    draw();
    refocus(mark);
    syncZoom();
    live(ui.msg);
  }
  /* --- a tile's arrows: choose which item to show and push (the Lineups page's
     rules, lineups.js stepItem / drawStep). A named item is asked for once the
     arrows rest; a draw renders the dashboard once more, one at a time, and the
     previous arrow walks back through what was drawn here. */
  const ITEM_DEBOUNCE = 280;
  const ITEM_POLL = 1500;
  const ITEM_POLLS = 40;
  const DRAW_KEEP = 8;
  let pickSeq = 0;
  function choiceView(id) {
    const c = ui.choice[id];
    if (!c) return null;
    if (c.mode === "draw") {
      const e = c.pos >= 0 ? c.hist[c.pos] : null;
      return e
        ? {
            mode: "draw",
            key: e.key,
            state: e.state,
            thumb_url: e.thumb,
            large_url: e.large,
            note: e.note,
            label: "",
            pos: c.pos,
            of: c.hist.length,
          }
        : null;
    }
    return {
      mode: "pick",
      idx: c.idx,
      key: c.key,
      label: c.label,
      state: c.state,
      thumb_url: c.thumb,
      large_url: c.large,
      note: c.note,
    };
  }
  function itemChanged() {
    const mark = focusMark();
    draw();
    refocus(mark);
    syncZoom();
  }
  // Ask for ``e``'s picture until it is drawn; ``alive()`` says it is still wanted.
  async function pollItem(id, e, alive) {
    const u = new URLSearchParams({ page_id: id, item: e.key, display_id: st.display });
    for (let n = 0, busy = 0; n < ITEM_POLLS; n++) {
      let r;
      let data = {};
      try {
        r = await fetch(`${S.urls.stepItem}?${u}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        data = await r.json().catch(() => ({}));
      } catch {
        r = null; // offline for a moment: ask again
      }
      if (!alive()) return;
      if (r && r.status === 200 && data.state === "ready") {
        e.state = "ready";
        e.thumb = data.thumb_url;
        e.large = data.large_url;
        e.sig = data.sig || "";
        return;
      }
      if (r && r.status === 503 && busy++ < 8) {
        // Other previews are being drawn; wait our turn instead of piling on.
        await new Promise((ok) => window.setTimeout(ok, 5000));
        continue;
      }
      if (r && r.status >= 400) {
        e.state = "error";
        e.note =
          r.status === 503
            ? "Too many previews are being drawn; try again in a moment."
            : data.error || "It could not be drawn.";
        return;
      }
      await new Promise((ok) => window.setTimeout(ok, ITEM_POLL));
      if (!alive()) return;
    }
    e.state = "error";
    e.note = "It took too long to draw.";
  }
  async function drawItem(id, fresh) {
    const p = pageOf(id);
    let c = ui.choice[id];
    if (!c || c.mode !== "draw") c = ui.choice[id] = { mode: "draw", hist: [], pos: -1, n: 0 };
    c.note = "";
    if (!fresh && c.pos < c.hist.length - 1) {
      c.pos++;
      return itemChanged();
    }
    if (c.hist.some((x) => x.state === "loading")) return;
    const e = { key: `draw:${nonce()}:1`, state: "loading", thumb: "", large: "", note: "" };
    e.n = ++c.n;
    c.hist.push(e);
    if (c.hist.length > DRAW_KEEP) c.hist.shift();
    c.pos = c.hist.length - 1;
    live(`${p.name}: drawing another picture.`);
    itemChanged();
    await pollItem(id, e, () => ui.choice[id] === c && c.hist.includes(e));
    if (ui.choice[id] !== c || !c.hist.includes(e)) return;
    const twin = e.sig && c.hist.find((x) => x !== e && x.sig === e.sig);
    if (twin) {
      // The widget drew what it already drew: not a new picture, so it is not kept.
      c.hist.splice(c.hist.indexOf(e), 1);
      c.pos = Math.min(c.pos, c.hist.length - 1);
      c.note = "That came out the same as one already drawn.";
    }
    live(
      e.state === "ready" ? `${p.name}: ${twin ? c.note : "drew another."}` : `${p.name}: ${e.note}`
    );
    itemChanged();
  }
  function stepItem(id, delta) {
    const p = pageOf(id);
    const it = p.items || {};
    if (!canPick(id)) return;
    if (it.mode === "draw") {
      const c = ui.choice[id];
      if (delta < 0) {
        if (!c || c.mode !== "draw" || c.pos < 0) return;
        c.pos--;
        c.note = "";
        return itemChanged();
      }
      return drawItem(id, false);
    }
    const n = Number(it.count) || 0;
    if (n < 2) return;
    const cur = ui.choice[id];
    const idx = cur && cur.mode === "pick" ? (cur.idx + delta + n) % n : delta > 0 ? 0 : n - 1;
    const c = {
      mode: "pick",
      idx,
      key: "",
      label: "",
      state: "loading",
      thumb: "",
      large: "",
      note: "",
      token: ++pickSeq,
    };
    ui.choice[id] = c;
    if (cur && cur.timer) window.clearTimeout(cur.timer);
    const alive = () => ui.choice[id] === c;
    c.timer = window.setTimeout(async () => {
      try {
        const data = await fetchItems(id, idx, 1, "");
        const x = data.items && data.items[0];
        if (!alive()) return;
        if (!x) throw new Error("That item is gone.");
        c.key = x.key;
        c.label = x.label || `#${idx + 1}`;
        live(`${p.name}: ${c.label}, ${idx + 1} of ${n}. Drawing it.`);
        itemChanged();
        await pollItem(id, c, alive);
      } catch (err) {
        if (!alive()) return;
        c.state = "error";
        c.note = err.message;
      }
      if (alive()) itemChanged();
    }, ITEM_DEBOUNCE);
    itemChanged();
  }

  /* --- zoom: the Lineups lightbox, with the arrows and Push now */
  const LB = window.TesseraeLineupsLightbox;
  let zoom = null; // {id, msg}
  function zoomModel() {
    const id = zoom && zoom.id;
    const i = st.steps.findIndex((s) => s.id === id);
    if (i < 0) return null;
    const p = pageOf(id);
    const it = p.items || {};
    const d = displayModel();
    const c = choiceView(id);
    const big = (u) => (u ? u + (u.includes("?") ? "&" : "?") + "lg=1" : "");
    const url = c && c.state === "ready" ? c.large_url || c.thumb_url : big(p.thumb);
    let nav = null;
    if (canPick(id) && it.mode === "draw")
      nav = {
        text: c ? `Drawn here: ${c.pos + 1} of ${c.of}` : "As last sent",
        label: `Draw another ${p.name} picture`,
        prev: "Previous picture",
        next: "Draw the next picture",
        hasPrev: !!c,
        hasNext: !(c && c.pos === c.of - 1 && c.state === "loading"),
      };
    else if (canPick(id))
      nav = {
        text: c
          ? `${c.idx + 1} of ${it.count}${c.label ? ` · ${c.label}` : ""}`
          : `As last sent · ${it.count} ${it.noun || "items"}`,
        label: `Choose which ${p.name} item to push`,
      };
    return {
      title: p.name,
      sub: `${d.name} · ${i + 1}/${st.steps.length}`,
      img: url ? { src: url, alt: c && c.label ? `${p.name}: ${c.label}` : p.name } : null,
      loading: !!c && c.state === "loading",
      notice: c && c.state === "error" ? `Could not draw this one: ${c.note}` : "",
      aspect: d.aspect,
      orientation: d.orientation,
      gamut: d.gamut,
      greyLevels: d.grey_levels,
      nav,
      push: canPush(id)
        ? {
            label: "Push now",
            title:
              c && c.state !== "ready" ? "Wait for this picture to finish" : `Push ${p.name} now`,
            busy: ui.pushing,
            disabled: !!c && c.state !== "ready",
          }
        : null,
      msg: zoom.msg || null,
      onStep: (delta) => stepItem(id, delta),
      onPush: () => pushNow(id),
      onClose: () => {
        zoom = null;
        root.querySelector(`[data-step="${CSS.escape(id)}"] [data-zoom]`)?.focus();
      },
    };
  }
  function syncZoom() {
    if (!zoom || !LB || !LB.isOpen()) return;
    const m = zoomModel();
    if (m) LB.update(m);
  }
  function openZoom(id) {
    if (!LB) return;
    zoom = { id, msg: null };
    const m = zoomModel();
    // The rest of the editor sits still behind it.
    if (m)
      LB.open(root, m, () => {
        form.inert = true;
        return () => (form.inert = false);
      });
    else zoom = null;
  }

  /* --- save, create, discard */
  // What the editor shows, and nothing else: a field left out keeps its
  // stored value (app.deck_routes.editor_save).
  function formData() {
    const fd = new FormData();
    const k = kind();
    const windowed = k === "cycle" || k === "interval";
    fd.append("deck_id", S.deckId);
    fd.append("revision", S.revision || "");
    fd.append("respond", "json");
    fd.append("name", st.name.trim() || "Lineup");
    fd.append("enabled", st.enabled ? "on" : "0");
    [st.display, ...otherDisplays()].filter(Boolean).forEach((d) => fd.append("device_ids", d));
    fd.append("pages", st.steps.map((s) => s.id).join(","));
    if (k !== "manual") st.when.days.forEach((on, i) => on && fd.append("advance_days", String(i)));
    if (st.whenChanged) {
      fd.append("when_changed", "1");
      fd.append("when_mode", st.when.mode);
      fd.append("when_start", st.when.start || "");
      fd.append("when_end", st.when.mode === "between" ? st.when.end : "");
    }
    if (showTaps()) fd.append("taps_flip", st.taps ? "1" : "0");
    if (windowed) fd.append("advance_interval_minutes", String(st.interval));
    // Home: only once picked (or its dashboard removed); unpicked, it stays
    // whichever dashboard is first.
    if (st.home !== (S.home || "")) fd.append("home", st.home || "");
    if (showHome()) fd.append("timeout", String(st.timeout));
    if (showSmart()) {
      fd.append("advance_smart_sync", "0");
      if (st.smartSync) fd.append("advance_smart_sync", "on");
      fd.append("advance_smart_sync_lead_s", String(st.lead));
    }
    fd.append("advance_priority", String(st.priority));
    if (showMode()) fd.append("advance_mode", st.mode);
    if (showMinHold()) fd.append("advance_min_hold_minutes", String(st.minHold));
    if (showKeepReady()) fd.append("refresh_interval_minutes", String(st.cadence));
    st.steps.forEach((s) => {
      if (windowed) fd.append(`dwell[${s.id}]`, s.dwell ? String(s.dwell) : "");
      if (showKeepReady())
        fd.append(
          `override[${s.id}]`,
          s.override === null || s.override === undefined ? "" : String(s.override)
        );
      if (showConditions(s)) fd.append(`conditions[${s.id}]`, s.conditions || "");
      if (s.refresh !== (pageOf(s.id).refresh || 0))
        fd.append(`page_refresh[${s.id}]`, String(s.refresh));
      if (s.pin !== undefined) fd.append(`pin[${s.id}]`, s.pin);
    });
    consentFields(fd);
    return fd;
  }

  async function save() {
    if (ui.saving || ui.duplicating) return;
    if (!st.steps.length) {
      ui.msg = "Add at least one dashboard.";
      ui.msgKind = "err";
      drawFooter();
      return;
    }
    if (!st.when.days.some(Boolean)) {
      ui.msg = "Pick at least one day.";
      ui.msgKind = "err";
      drawFooter();
      return;
    }
    if (
      !(await confirmBind(
        st.steps.map((s) => s.id),
        st.display
      ))
    )
      return;
    if (endBeforeStart()) {
      ui.msg = "End must be after start.";
      ui.msgKind = "err";
      drawFooter();
      return;
    }
    // What is sent is what counts as saved; nothing can change meanwhile.
    const body = formData();
    const sent = snapshot();
    ui.saving = true;
    ui.msg = "";
    ui.reload = false;
    drawFooter();
    try {
      const data = await request(S.urls.save, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
      });
      saved = sent;
      window.location.assign(data.url);
    } catch (err) {
      ui.saving = false;
      ui.reload = !!err.reload;
      ui.msg = err.net || err.reload ? err.message : `Not saved: ${err.message}`;
      ui.msgKind = "err";
      drawFooter();
      live(ui.msg);
    }
  }

  async function create() {
    if (ui.saving || ui.duplicating || !st.display || !st.steps.length || !whenReady()) return;
    if (
      !(await confirmBind(
        st.steps.map((s) => s.id),
        st.display
      ))
    )
      return;
    const fd = new FormData();
    const dev = deviceOf(st.display);
    if (S.returnTo) fd.append("return", S.returnTo);
    fd.append("name", st.name.trim() || `${dev ? dev.name : "New"} lineup`);
    if (st.when.mode === "manual") fd.append("intent", "manual");
    else {
      fd.append("when", st.when.mode);
      fd.append("start", st.when.start);
      if (st.when.mode === "between") fd.append("end", st.when.end);
      st.when.days.forEach((on, i) => on && fd.append("days", String(i)));
    }
    fd.append("device_ids", st.display);
    st.steps.forEach((s) => {
      fd.append("page_ids", s.id);
      if (hasConditions(s)) fd.append(`conditions[${s.id}]`, s.conditions || "");
    });
    fd.append("interval_minutes", String(st.interval));
    consentFields(fd);
    const sent = snapshot();
    ui.saving = true;
    drawFooter();
    try {
      const data = await request(S.urls.create, {
        method: "POST",
        body: fd,
        headers: { Accept: "application/json" },
      });
      saved = sent;
      window.location.assign(data.editor_url);
    } catch (err) {
      ui.saving = false;
      ui.msg = err.message;
      ui.msgKind = "err";
      drawFooter();
    }
  }

  function discard() {
    st = initial();
    ui.consent = {};
    ui.consentDisplay = S.display;
    const title = $("dxe-title");
    if (title) title.value = st.name;
    const on = $("dxe-enabled");
    if (on) on.checked = st.enabled;
    const word = $("dxe-on-word");
    if (word) word.textContent = st.enabled ? "On" : "Off";
    ui.msg = "";
    ui.msgKind = "";
    ui.reload = false;
    draw();
    live("Changes discarded.");
  }

  /* ------------------------------------------------------------ events */
  // A dashboard's tile opens its details under the strip (one at a time); again, or
  // Esc, closes them.
  function select(id, toggle) {
    if (!id) return;
    const open = ui.open.has(id);
    if (open && !toggle) return;
    ui.open.clear();
    if (!open) {
      ui.open.add(id);
      loadStrip(id);
    }
    draw();
    root.querySelector(`[data-step="${CSS.escape(id)}"] .dxe-sel`)?.focus();
  }

  root.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-goto]");
    if (!t) {
      // Anywhere on a tile or its caption opens it, as its button does.
      const tile = e.target.closest && e.target.closest(".dxe-step[data-step]");
      if (tile) select(tile.dataset.step, true);
      return;
    }
    if (t.disabled) return;
    const d = t.dataset;
    if (d.duplicate) return duplicateStep(d.duplicate);
    if (d.day !== undefined && t.classList.contains("lu-dt")) {
      const i = Number(d.day);
      if (st.when.days[i] && st.when.days.filter(Boolean).length === 1) {
        ui.msg = "It needs at least one day.";
        ui.msgKind = "err";
        drawFooter();
        return;
      }
      ui.justDay = i;
      change(() => (st.when.days[i] = !st.when.days[i]));
      root.querySelector(`.lu-dt[data-day="${i}"]`)?.focus();
      return;
    }
    if (d.overridden) {
      select(d.overridden, false);
      const tile = root.querySelector(`[data-step="${CSS.escape(d.overridden)}"]`);
      if (tile) {
        tile.scrollIntoView({ block: "center", behavior: "smooth" });
        tile.classList.add("is-flash");
        window.setTimeout(() => tile.classList.remove("is-flash"), 1800);
      }
      return;
    }
    if (d.when) return setWhen(d.when);
    if (d.wakeFix === "smart") {
      change(() => (st.smartSync = true));
      live("Sends just before each wake.");
      root.querySelector("[data-wake-timer]")?.focus();
      return;
    }
    if (d.wakeFix === "dwell") {
      const w = wakeOf();
      if (!w || !w.min) return;
      change(() => {
        // Only ever up to a wake: a default already longer stays as it is.
        st.interval = Math.max(st.interval, w.min);
        st.steps.forEach((s) => s.dwell && s.dwell < w.min && (s.dwell = null));
      });
      live(`Each dashboard shows for ${mins(st.interval)}.`);
      root.querySelector("[data-interval]")?.focus();
      return;
    }
    if (d.wakeTimer !== undefined) {
      change(() => (st.smartSync = false));
      live(`Sent every ${mins(st.interval)} on a timer.`);
      root.querySelector("[data-interval]")?.focus();
      return;
    }
    if (d.wakeUpdates) {
      const s = stepOf(d.wakeUpdates);
      if (s) change(() => (s.refresh = Number(d.to) || 0));
      live(
        `${pageOf(d.wakeUpdates).name} updates ${Number(d.to) ? `every ${mins(Number(d.to))}` : "only when sent"}.`
      );
      return;
    }
    if (d.end === "add") {
      setWhen("between");
      root.querySelector('[data-time="end"]')?.focus();
      return;
    }
    if (d.end === "rm") {
      setWhen("at");
      root.querySelector('[data-time="start"]')?.focus();
      return;
    }
    if (d.start) {
      change(() => ((st.when.start = d.start), (st.whenChanged = true)));
      // The fix goes with the warning: the time it set takes the focus.
      root.querySelector('[data-time="start"]')?.focus();
      return;
    }
    if (d.move !== undefined) return moveDisplay();
    if (d.moveUndo !== undefined) {
      const was = (deviceOf(st.display) || { name: st.display }).name;
      dropMoveConsent();
      change(() => (st.display = S.display));
      live(`Move to ${was} undone.`);
      root.querySelector("[data-loc-more]")?.focus();
      return;
    }
    if (d.pickDisplay) {
      const id = d.pickDisplay;
      if (
        !(await confirmBind(
          st.steps.map((s) => s.id),
          id
        ))
      )
        return;
      change(() => {
        st.display = id;
        newStartAfterPause();
      });
      ui.step = Math.max(ui.step, 2);
      draw();
      root.querySelector("#dxe-h-when")?.scrollIntoView({ block: "nearest" });
      root.querySelector('[data-when][aria-pressed="true"]')?.focus();
      return;
    }
    if (d.goto) {
      const n = Number(d.goto);
      if (n === 3 && !whenReady()) {
        ui.msg = endBeforeStart() ? "End must be after start." : "Pick a day and a time.";
        ui.msgKind = "err";
        drawFooter();
        return;
      }
      ui.step = n;
      draw();
      (
        root.querySelector(
          n === 1
            ? "[data-pick-display]"
            : n === 2
              ? '[data-when][aria-pressed="true"]'
              : "[data-picker]"
        ) || root
      ).focus();
      return;
    }
    if (d.picker !== undefined) return openPicker();
    if (d.remove) return removeStep(d.remove);
    if (d.home) {
      if (t.getAttribute("aria-disabled") === "true") return; // already home
      change(() => (st.home = d.home));
      live(`${pageOf(d.home).name} is home.`);
      return;
    }
    if (d.expand) return select(d.expand, true);
    if (d.peek) return select(st.steps[Number(d.peek.split(":")[1])]?.id, false);
    if (d.item) {
      const [, i, delta] = d.item.split(":");
      const s = st.steps[Number(i)];
      if (s) stepItem(s.id, Number(delta));
      return;
    }
    if (d.zoom) {
      const s = st.steps[Number(d.zoom.split(":")[2])];
      if (s) openZoom(s.id);
      return;
    }
    if (d.drawPos) {
      const c = ui.choice[d.drawPos];
      const pos = Number(d.pos);
      if (pos < 0) delete ui.choice[d.drawPos];
      else if (c && c.mode === "draw") c.pos = pos;
      return itemChanged();
    }
    if (d.clearCond) {
      const s = stepOf(d.clearCond);
      if (!s) return;
      change(() => {
        s.conditions = "";
        stepHadConditions.delete(s.id);
        ui.condOpen.delete(s.id);
        ui.rawOpen.delete(s.id);
      });
      live(`${pageOf(s.id).name} shows whenever the lineup runs.`);
      root.querySelector(`[data-add-cond="${CSS.escape(s.id)}"]`)?.focus();
      return;
    }
    if (d.addCond) {
      ui.condOpen.add(d.addCond);
      draw();
      // The picker is built a moment after the draw; its first control takes focus.
      queueMicrotask(() =>
        root
          .querySelector(`[data-cond="${CSS.escape(d.addCond)}"]`)
          ?.closest("[data-condition-picker]")
          ?.querySelector("button, select, input, summary, textarea")
          ?.focus()
      );
      return;
    }
    if (d.edit) {
      if (ui.editing.has(d.edit)) ui.editing.delete(d.edit);
      else ui.editing.add(d.edit);
      draw();
      root.querySelector(`[data-edit="${CSS.escape(d.edit)}"]`)?.focus();
      return;
    }
    if (d.grid) {
      const g = (ui.grid[d.grid] = ui.grid[d.grid] || { q: "", offset: 0, data: null });
      g.open = !g.open;
      draw();
      if (g.open) {
        loadGrid(d.grid);
        root.querySelector(`[data-grid-q="${CSS.escape(d.grid)}"]`)?.focus();
      }
      return;
    }
    if (d.gpage) {
      const host = t.closest("[data-grid-for]");
      const g = host && ui.grid[host.dataset.gridFor];
      if (!g) return;
      g.offset = Math.max(0, (Number(d.gpage) - 1) * GRID_PAGE);
      // The page it was on stays (dimmed) until the new one lands, so the
      // pager, and the focus on it, stay too.
      g.busy = true;
      draw();
      loadGrid(host.dataset.gridFor);
      return;
    }
    if (d.pinMode) {
      const s = stepOf(d.pinMode);
      const p = pageOf(d.pinMode);
      if (!s) return;
      if (d.mode === "walk") change(() => (s.pin = ""));
      else {
        const first =
          (ui.strip[p.id] && ui.strip[p.id].data && ui.strip[p.id].data.items[0]) || null;
        change(() => (s.pin = pinnedKey(s, p) || (first ? first.key : "")));
      }
      live(
        d.mode === "walk"
          ? `${p.name}: rotating items.`
          : `${p.name}: showing the chosen item, pick one.`
      );
      return;
    }
    if (d.pinItem) {
      const s = stepOf(d.pinItem);
      if (s) change(() => (s.pin = d.key));
      live(`Showing ${d.key}.`);
      return;
    }
    if (d.draw) return drawItem(d.draw, true);
    if (d.push) return pushNow(d.push);
    if (d.pushLineup !== undefined) return pushNow(st.home || st.steps[0].id);
    if (d.reload !== undefined) return window.location.reload();
    if (d.save !== undefined) return save();
    if (d.create !== undefined) {
      if (t.getAttribute("aria-disabled") === "true") return missingStep();
      return create();
    }
    if (d.discard !== undefined) return discard();
    if (d.delete !== undefined) {
      if (window.confirm(`Delete ${st.name || "this lineup"}?`)) $("dxe-delete-form").submit();
      return;
    }
  });

  root.addEventListener("input", (e) => {
    const t = e.target;
    const d = t.dataset;
    if (t.id === "dxe-title") return change(() => (st.name = t.value), { draw: false });
    if (d.time) {
      if (!t.value) return;
      change(
        () => {
          // 11:59 PM (how midnight shows) and 12:00 AM both end the day.
          const v =
            d.time === "end" && (t.value === "23:59" || t.value === "00:00") ? "24:00" : t.value;
          st.when[d.time] = v;
          st.whenChanged = true;
        },
        { draw: false }
      );
      const lead = root.querySelector("#dxe-h-when + .dxe-lead");
      if (lead) lead.textContent = whenLead();
      return;
    }
    if (d.gridQ !== undefined) {
      const g = ui.grid[d.gridQ];
      if (!g) return;
      g.q = t.value;
      g.offset = 0;
      window.clearTimeout(gridTimers[d.gridQ]);
      gridTimers[d.gridQ] = window.setTimeout(() => loadGrid(d.gridQ), 220);
      return;
    }
    if (d.cond) {
      const s = stepOf(d.cond);
      if (s) change(() => (s.conditions = t.value), { draw: false });
      return;
    }
    if (d.lead !== undefined)
      return change(() => (st.lead = Number(t.value) || 0), { draw: false });
    if (d.priority !== undefined)
      return change(() => (st.priority = Number(t.value) || 0), { draw: false });
    if (d.hold !== undefined) {
      change(() => (st.minHold = Number(t.value) || 0), { draw: false });
      // The line under it follows as you type; nothing else depends on it.
      const why = root.querySelector("#dxe-hold-why span");
      const who = interrupters();
      if (why)
        why.textContent = holdWhy(
          who.length > 1 ? `${who.slice(0, -1).join(", ")} or ${who[who.length - 1]}` : who[0]
        );
      return;
    }
  });

  root.addEventListener("change", async (e) => {
    const t = e.target;
    const d = t.dataset;
    if (t.id === "dxe-enabled") {
      $("dxe-on-word").textContent = t.checked ? "On" : "Off";
      return change(() => (st.enabled = t.checked));
    }
    if (d.time) return draw();
    if (d.cond) {
      const s = stepOf(d.cond);
      if (s) change(() => (s.conditions = t.value), { draw: false });
      return;
    }
    if (d.dwell) {
      const s = stepOf(d.dwell);
      if (s) change(() => (s.dwell = t.value ? Number(t.value) : null));
      return;
    }
    if (d.updates) {
      const s = stepOf(d.updates);
      if (s) change(() => (s.refresh = Number(t.value) || 0));
      return;
    }
    if (d.override) {
      const s = stepOf(d.override);
      if (s) change(() => (s.override = t.value === "" ? null : Number(t.value)));
      return;
    }
    if (d.interval !== undefined) return change(() => (st.interval = Number(t.value) || 30));
    if (d.taps !== undefined) return change(() => (st.taps = t.checked));
    if (d.timeout !== undefined) return change(() => (st.timeout = Number(t.value) || 0));
    if (d.cadence !== undefined) return change(() => (st.cadence = Number(t.value) || 0));
    if (d.modeSel !== undefined) return change(() => (st.mode = t.value));
    // Nothing else is drawn from these: the footer alone, so tabbing on
    // keeps the next field in place.
    if (d.lead !== undefined || d.priority !== undefined || d.hold !== undefined)
      return drawFooter();
  });

  root.addEventListener(
    "toggle",
    (e) => {
      const f = e.target.dataset && e.target.dataset.fold;
      if (f === "options") ui.optOpen = e.target.open;
    },
    true
  );

  // Alt+Up / Alt+Down move a dashboard; Enter in a field never submits the form.
  root.addEventListener("keydown", (e) => {
    const row = e.target.closest && e.target.closest("[data-step]");
    if (row && e.altKey && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
      e.preventDefault();
      move(row.dataset.step, e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 1);
      return;
    }
    // Esc closes the open details and goes back to its tile.
    if (e.key === "Escape" && ui.open.size && !(LB && LB.isOpen())) {
      const inside = e.target.closest && e.target.closest("#dxe-detail, .dxe-tiles");
      if (inside && !e.target.matches("input, textarea, select")) {
        e.preventDefault();
        select([...ui.open][0], true);
        return;
      }
    }
    // The display cards are a radio group: arrows move between them.
    const card = e.target.closest && e.target.closest("[data-pick-display]");
    if (card && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
      e.preventDefault();
      const cards = [...root.querySelectorAll("[data-pick-display]")];
      const step = e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1;
      const next = cards[(cards.indexOf(card) + step + cards.length) % cards.length];
      cards.forEach((c) => (c.tabIndex = c === next ? 0 : -1));
      next.focus();
      return;
    }
    if (e.key === "Enter" && e.target.matches("input:not([type=checkbox])")) e.preventDefault();
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (S.isNew) create();
    else save();
  });

  /* --- drag to reorder: the row moves as it is dragged, the order commits on drop */
  let dragId = null;
  root.addEventListener("dragstart", (e) => {
    const row = e.target.closest && e.target.closest("[data-step]");
    if (!row) return;
    dragId = row.dataset.step;
    row.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragId);
  });
  root.addEventListener("dragover", (e) => {
    if (!dragId) return;
    const list = $("dxe-steps");
    if (!list || !list.contains(e.target)) return;
    e.preventDefault(); // anywhere on the list is a drop
    const over = e.target.closest && e.target.closest("[data-step]");
    if (!over) return;
    const dragged = list.querySelector(`[data-step="${CSS.escape(dragId)}"]`);
    if (!dragged || over === dragged) return;
    // Tiles wrap: across a line, left or right half; between lines, the upper or lower.
    const r = over.getBoundingClientRect();
    const after =
      e.clientY > r.bottom ? true : e.clientY < r.top ? false : e.clientX > r.left + r.width / 2;
    list.insertBefore(dragged, after ? over.nextSibling : over);
  });
  root.addEventListener("drop", (e) => {
    if (dragId) e.preventDefault(); // the order is read on dragend
  });
  root.addEventListener("dragend", (e) => {
    if (!dragId) return;
    const list = $("dxe-steps");
    const id = dragId;
    dragId = null;
    // Esc, or a drop outside the list, cancels: the rows go back.
    if (!list || !e.dataTransfer || e.dataTransfer.dropEffect === "none") return draw();
    const order = [...list.querySelectorAll("[data-step]")].map((n) => n.dataset.step);
    change(() => (st.steps = order.map((x) => stepOf(x)).filter(Boolean)));
    live(`Moved ${pageOf(id).name} to ${order.indexOf(id) + 1} of ${order.length}.`);
  });

  root.addEventListener("keydown", (e) => {
    const blk = e.target.closest && e.target.closest(".lu-blk[data-edit]");
    if (!blk || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    root.querySelector('[data-time="start"]')?.focus();
  });

  // Another lineup's block on the day bar: a plain navigation, so the
  // beforeunload guard below asks first when there are unsaved edits.
  // The header's ⋯ menu closes after a choice, on Escape and on an outside click.
  const closeMenus = (keep) =>
    root.querySelectorAll(".dxe-top-ctl details.lu-more[open]").forEach((m) => {
      if (m !== keep) m.open = false;
    });
  root.addEventListener("click", (e) => {
    const t = e.target.closest ? e.target : null;
    const item = t && t.closest(".dxe-top-ctl .lu-more-menu button");
    if (item) item.closest("details").open = false;
    else closeMenus(t && t.closest("details.lu-more"));
  });
  document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) closeMenus(null);
  });
  document.addEventListener(
    "keydown",
    (e) => {
      const m = root.querySelector(".dxe-top-ctl details.lu-more[open]");
      if (e.key !== "Escape" || !m || document.querySelector("dialog:modal")) return;
      m.open = false;
      m.querySelector("summary").focus();
      e.preventDefault();
      e.stopPropagation();
    },
    true
  );
  const SCROLL_KEY = "tesserae.lineupEditor.scroll";
  root.addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest("[data-week-toggle]")) return;
    weekOn = !weekOn;
    store.set(WEEK_KEY, weekOn ? "1" : "0");
    drawDayBar();
    root.querySelector("[data-week-toggle]")?.focus();
    live(weekOn ? "Showing the whole week." : "Showing today.");
  });
  const openBlock = (e) => {
    const blk = e.target.closest && e.target.closest(".dxe-daybar .lu-blk.is-link[data-href]");
    if (!blk) return false;
    try {
      window.sessionStorage.setItem(SCROLL_KEY, String(window.scrollY));
    } catch {
      /* the next page just opens at the top */
    }
    window.location.href = blk.dataset.href;
    return true;
  };
  root.addEventListener("click", (e) => {
    if (!e.defaultPrevented && e.button === 0) openBlock(e);
  });
  root.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && openBlock(e)) e.preventDefault();
  });

  window.addEventListener("beforeunload", (e) => {
    if (dirty() && !ui.saving && (!S.isNew || st.steps.length)) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // A picture that fails to load (a dashboard not drawn yet) shows the
  // placeholder letter, as on the Lineups page, not a broken image.
  root.addEventListener(
    "error",
    (e) => {
      const img = e.target;
      if (!(img instanceof HTMLImageElement)) return;
      const box = img.closest(".lu-thumb, .dxe-it-pic");
      if (!box) return;
      const ph = document.createElement("span");
      ph.className = box.classList.contains("lu-thumb") ? "lu-thumb-ph" : "dxe-it-word";
      ph.setAttribute("aria-hidden", "true");
      ph.textContent = (box.getAttribute("title") || img.alt || "?").slice(0, 1);
      img.replaceWith(ph);
    },
    true
  );

  root.classList.add("is-enhanced"); // the tiles' zoom and item arrows (dx-lineups.css)
  if (S.isNew && S.displayLocked) ui.step = 2;
  if (S.isNew) {
    newStartAfterPause();
    saved = snapshot();
  }
  draw();

  // What is on screen, NOW / NEXT and the bar's now mark come from the
  // Lineups view; it is read again every minute (the edits stay as they
  // are), so a page left open doesn't promise a time that has passed.
  const LIVE_EVERY = 60 * 1000;
  async function refreshLive() {
    if (S.isNew || ui.saving || document.hidden || dragId || barEdit.active()) return;
    if (!S.urls.view || !S.display) return;
    let vm;
    try {
      const r = await fetch(`${S.urls.view}?display=${encodeURIComponent(S.display)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!r.ok) return;
      vm = await r.json();
    } catch {
      return; // the next minute tries again
    }
    const d = vm && (vm.displays || []).find((x) => x.id === S.display);
    if (!d || ui.saving) return;
    S.views[d.id] = d;
    if (vm.now) S.now = vm.now;
    S.lineup = d.lineups.find((x) => x.id === S.deckId) || S.lineup;
    // A field being typed in keeps its draw; the header alone is redrawn.
    const a = document.activeElement;
    if (a && root.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) drawHeader();
    else draw();
    syncZoom();
  }
  if (!S.isNew) window.setInterval(refreshLive, LIVE_EVERY);

  // Switching lineups from the week view lands where it was left.
  try {
    const y = Number(window.sessionStorage.getItem(SCROLL_KEY));
    window.sessionStorage.removeItem(SCROLL_KEY);
    if (y > 0) window.scrollTo(0, y);
  } catch {
    /* no saved position */
  }
})();
