/* The lineup editor (templates/deck_editor.html).
 *
 * One state, one draw: the server's JSON island (app.deck_routes._editor_state)
 * becomes `st`, every control edits `st`, and draw() repaints the sections from
 * it. Nothing is sent until Save lineup, which posts the whole lineup to
 * /decks/editor-save (respond=json) and reloads; the new-lineup flow posts to
 * /decks/new/lineup and opens the editor of what it made. Push now (a row, or
 * the footer) and Draw another act at once, like on the Lineups page.
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
  const STRIP = 5;
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
    grid: {}, // page id -> {q, offset, data, busy, open}
    strip: {}, // page id -> {data} (the first few items)
    draws: {}, // page id -> [{key, state, thumb, note}]
    picker: false,
    elsewhere: false,
    changeDisplay: false,
    step: S.isNew ? (S.display ? 2 : 1) : 0, // the new flow's open step
    saving: false,
    pushing: false,
    reload: false,
    msg: "",
    msgKind: "",
    justDay: null,
    consent: {}, // page id -> "move" | "also", for the display chosen
    consentDisplay: S.display,
  };
  const snapshot = () => JSON.stringify(Object.assign({}, st, { whenChanged: false }));
  const stepHadConditions = new Set(
    S.steps.filter((s) => s.conditions && s.conditions.length).map((s) => s.id)
  );
  // Conditions are a legacy, per-dashboard rule: only a step that has some
  // shows (and posts) its editor.
  const hasConditions = (s) => stepHadConditions.has(s.id) || !!(s.conditions || "").trim();
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
  function inPause(m) {
    const p = pause();
    if (!p || !Number.isFinite(m)) return false;
    return R.overlapsPause(p, m, m);
  }
  // The whole of s..e (minutes, e up to 1440) inside quiet hours.
  function allInPause(s, e) {
    const p = pause();
    if (!p || !Number.isFinite(s) || !Number.isFinite(e) || e <= s) return false;
    const a = p.start_min;
    const b = p.end_min;
    return a < b ? s >= a && e <= b : e <= b || s >= a;
  }
  // The first quarter hour at or after quiet hours end (or `m` when it's out).
  function slotAfterPause(m) {
    let t = Math.ceil(m / 15) * 15;
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
        bound_to: S.pages[id].devices.map(devInfo),
      })),
    });
    if (!mode) return false;
    need.forEach((id) => (ui.consent[id] = mode));
    return true;
  }
  function consentFields(fd) {
    st.steps.forEach((s) => {
      if (!strandedFor(s.id, st.display) || !ui.consent[s.id]) return;
      fd.append("bind_pages", s.id);
      if (ui.consent[s.id] === "also") fd.append("bind_also", s.id);
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
  function stackedThumb(p) {
    const it = p.items || {};
    const many = (it.mode === "pick" && it.count > 1) || it.mode === "draw";
    if (!many) return `<span class="dxe-th">${thumbOf(p)}</span>`;
    const badge =
      it.mode === "draw" && !it.count
        ? `<span class="dxe-badge" title="A fresh draw each time">draw</span>`
        : `<span class="dxe-badge" title="${esc(plural(it.count, it.noun || "items"))}">×${esc(it.count)}</span>`;
    return `<span class="dxe-th is-stack">${thumbOf(p)}${badge}</span>`;
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
      if (d && d.pause) bits.push(`quiet ${d.pause.label}`);
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
    drawDayBar();
  }

  function statusLines(d) {
    const l = S.lineup;
    const line = (text, cls) =>
      `<p class="dxe-live-line${cls ? ` ${cls}` : ""}"><i class="dxe-dot" aria-hidden="true"></i>${text}</p>`;
    if (!st.enabled) return line("Off, nothing runs.", "off");
    if (!l || !d) return "";
    if (S.when.mode === "manual" && l.status !== "live")
      return line("<b>By hand: taps and buttons flip it.</b>");
    // A daily push in quiet hours is skipped, not held: the scheduler counts
    // it as done for the day (as the When section says).
    if (
      S.when.kind === "daily" &&
      l.status !== "live" &&
      S.display === st.display &&
      inPause(T(S.when.start))
    )
      return line(`<b>Skipped: ${esc(clockOf(S.when.start))} is in quiet hours.</b>`);
    if (l.status === "live") {
      const cur = l.steps.find((s) => s.current) || l.steps.find((s) => s.on_screen);
      const nx = l.steps.find((s) => s.next);
      const nl = d.next_line || {};
      const pos = cur ? cur.position.replace("/", " of ") : "";
      const first = cur
        ? `Live: dashboard ${pos}, ${cur.name}`
        : `Live: ${l.live_now && l.live_now.on_screen_name ? l.live_now.on_screen_name : "on screen"}`;
      const nextAt =
        nx && nx.pill && /\d/.test(nx.pill.label) ? nx.pill.label.replace(/^Next\s*/, "") : "";
      const second = nx
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
  function drawDayBar() {
    const bar = $("dxe-daybar");
    const d = viewOf(st.display);
    if (!bar) return;
    if (!d || !d.week || !S.now || S.isNew) {
      bar.hidden = true;
      bar.innerHTML = "";
      return;
    }
    const id = S.deckId || "__new__";
    let shown = d;
    if (dirty() || S.isNew) {
      const copy = JSON.parse(JSON.stringify(d));
      copy.lineups = copy.lineups.filter((x) => x.id !== id);
      const timed =
        kind() !== "manual" && st.enabled && st.steps.length && Number.isFinite(T(st.when.start));
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
    bar.innerHTML =
      `<p class="dxe-bar-day">${esc(label)}</p>` +
      `<div class="lu-today-bar">${R.dayTrack(shown, dd, [], true, "dxe-trk")}${R.dayScale(dd)}</div>`;
    bar.querySelectorAll(".lu-blk").forEach((b) => {
      if (b.dataset.lineup === id) b.classList.add("is-mine");
      b.removeAttribute("tabindex");
    });
    bar.querySelectorAll(".lu-blk-side").forEach((b) => {
      if (b.title && b.title.startsWith(st.name || "This lineup")) b.classList.add("is-mine");
    });
  }

  /* ------------------------------------------------------------ 1 When it runs */
  function whenLead() {
    const dev = deviceOf(st.display);
    const on = dev ? ` on ${dev.name}` : "";
    if (st.when.mode === "manual") return `No set time${on}; taps and buttons flip it.`;
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
        `<p class="dxe-warn">${ico("info")}<span>Only dashboard 1 is ever sent.</span><button type="button" class="dxe-link" data-end="add">Add end time</button></p>`
      );
    const p = pause();
    if (p && st.when.mode === "at" && inPause(w.s)) {
      const after = R.hhmm(p.end_min + 2);
      out.push(
        `<p class="dxe-warn">${ico("moon")}<span>${esc(clockOf(st.when.start))} is in quiet hours, so it's skipped.</span><button type="button" class="dxe-link" data-start="${esc(after)}" data-fk="start-fix">Start at ${esc(R.clock(p.end_min + 2))}</button></p>`
      );
    } else if (p && st.when.mode === "between" && !endBeforeStart() && allInPause(w.s, w.e)) {
      out.push(`<p class="dxe-warn">${ico("moon")}<span>All of this is in quiet hours.</span></p>`);
    } else if (p && st.when.mode === "between" && inPause(w.s)) {
      out.push(
        `<p class="dxe-note">${ico("moon")}<span>Quiet until ${esc(R.clock(p.end_min))}; it picks up then.</span></p>`
      );
    }
    return out.join("");
  }

  function displayRow() {
    const dev = deviceOf(st.display);
    if (S.isNew) return "";
    const more = otherDisplays().map((id) => (deviceOf(id) || { name: id }).name);
    const choose =
      ui.changeDisplay || !dev
        ? `<select class="dxe-in dxe-select" data-fk="display" data-display-select aria-label="Display">` +
          `<option value="">Choose a display…</option>` +
          S.devices
            .map(
              (d) =>
                `<option value="${esc(d.id)}"${d.id === st.display ? " selected" : ""}>${esc(d.name)}</option>`
            )
            .join("") +
          `</select>`
        : `<span class="dxe-disp"><b>${esc(dev.name)}</b>${more.length ? `<b class="dxe-also">+ ${esc(more.join(" + "))}</b>` : dev.model ? `<span class="dxe-soft">${esc(dev.model)}</span>` : ""}</span><button type="button" class="dxe-link" data-change-display data-fk="change-display" aria-label="Change display${more.length ? ` (${esc(dev.name)})` : ""}">Change</button>`;
    return `<div class="dxe-field"><span class="dxe-label">Display</span><div class="dxe-ctl">${choose}</div></div>`;
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
      displayRow() +
      `</div>`
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
    if (k === "manual") return "Taps and buttons step through them.";
    if (k === "daily") return n > 1 ? "A push only sends the first one." : "Sent once, then held.";
    if (k === "interval") return `Sent again every ${mins(st.interval)}.`;
    const same = st.steps.every((s) => dwellOf(s) === st.interval);
    return same
      ? `${mins(st.interval)} each, full loop ${mins(loopMinutes())}.`
      : `Full loop ${mins(loopMinutes())}.`;
  }

  // NOW / NEXT from the Lineups view, only while the steps are as saved.
  function liveFlags(id) {
    const l = S.lineup;
    if (!l || dirtySteps()) return {};
    const s = l.steps.find((x) => x.dashboard_id === id);
    return s
      ? { now: !!s.current, next: !!s.next, nextLabel: s.pill && s.next ? s.pill.label : "" }
      : {};
  }
  const savedIds = S.steps.map((s) => s.id).join(",");
  const dirtySteps = () => st.steps.map((s) => s.id).join(",") !== savedIds;
  const isSavedStep = (id) =>
    !S.isNew && S.steps.some((s) => s.id === id) && st.display === S.display;

  function rowHtml(s, i) {
    const p = pageOf(s.id);
    const n = st.steps.length;
    const k = kind();
    const open = ui.open.has(s.id);
    const f = liveFlags(s.id);
    const notSent = k === "daily" && i > 0;
    const home = st.home === s.id || (!st.home && i === 0);
    const stranded = strandedFor(s.id, st.display);
    let tags = "";
    if (f.now) tags += `<span class="dxe-tag now">Now</span>`;
    if (f.next) tags += `<span class="dxe-tag next" title="${esc(f.nextLabel)}">Next</span>`;
    if (notSent) tags += `<span class="dxe-tag mute">Not sent by a push</span>`;
    if (stranded)
      tags += `<span class="dxe-tag warn" title="Bound to another display; saving asks to bind it here">Not on this display</span>`;
    if (s.dwell && s.dwell !== st.interval && k === "cycle")
      tags += `<span class="dxe-tag time" title="Shows ${esc(mins(s.dwell))}, not the lineup's ${esc(mins(st.interval))}">${ico("clock")}${esc(mins(s.dwell))}</span>`;
    if (home && (takesInput() || k === "manual") && n > 1)
      tags += `<span class="dxe-tag home" title="Home dashboard">★ home</span>`;
    const pushOk = isSavedStep(s.id) && !p.missing && !stranded;
    return (
      `<li class="dxe-step${open ? " is-open" : ""}${notSent ? " is-muted" : ""}${p.missing ? " is-missing" : ""}" data-step="${esc(s.id)}" draggable="true">` +
      `<div class="dxe-step-head">` +
      `<button type="button" class="dxe-grip" data-grip data-fk="grip-${esc(s.id)}" aria-label="Move ${esc(p.name)}, ${i + 1} of ${n}. Alt+Up or Alt+Down" title="Drag, or Alt+↑↓">${gripIco}</button>` +
      `<span class="dxe-pos" aria-hidden="true">${i + 1}/${n}</span>` +
      stackedThumb(p) +
      `<span class="dxe-step-text"><b class="dxe-step-name">${esc(p.name)}</b><span class="dxe-step-sub">${esc(p.subtitle || "")}</span></span>` +
      `<span class="dxe-step-tags">${tags}</span>` +
      (S.isNew
        ? `<span class="dxe-step-acts"><button type="button" class="lu-ibtn" data-remove="${esc(s.id)}" data-fk="rm-${esc(s.id)}" title="Remove ${esc(p.name)}" aria-label="Remove ${esc(p.name)}">${ico("x")}</button></span></div>`
        : `<span class="dxe-step-acts">` +
          `<button type="button" class="lu-ibtn" data-push="${esc(s.id)}" data-fk="push-${esc(s.id)}"${pushOk && !ui.pushing ? "" : " disabled"} title="${pushOk ? `Push ${esc(p.name)} now` : "Save first to push it"}" aria-label="Push ${esc(p.name)} now">${ico("send")}</button>` +
          `<button type="button" class="lu-ibtn dxe-exp" data-expand="${esc(s.id)}" data-fk="exp-${esc(s.id)}" aria-expanded="${open}" aria-controls="dxe-sp-${esc(s.id)}" aria-label="${open ? "Hide" : "Show"} ${esc(p.name)} details">${ico("caret")}</button>` +
          `</span></div>`) +
      (open && !S.isNew
        ? `<div class="dxe-step-body" id="dxe-sp-${esc(s.id)}">${childrenHtml(s, p)}${settingsHtml(s, p, i)}</div>`
        : "") +
      `</li>`
    );
  }
  const gripIco =
    '<svg class="lu-ico" viewBox="0 0 16 16" aria-hidden="true"><circle cx="6" cy="4" r="1.1" fill="currentColor"/><circle cx="10" cy="4" r="1.1" fill="currentColor"/><circle cx="6" cy="8" r="1.1" fill="currentColor"/><circle cx="10" cy="8" r="1.1" fill="currentColor"/><circle cx="6" cy="12" r="1.1" fill="currentColor"/><circle cx="10" cy="12" r="1.1" fill="currentColor"/></svg>';

  /* --- children: what a dashboard shows one of */
  function pinnedKey(s, p) {
    return s.pin !== undefined ? s.pin : (p.items && p.items.pinned) || "";
  }
  function itemTile(p, it, mark, pinMode) {
    const label = it.label || `#${it.index + 1}`;
    const pic = it.thumb_url
      ? `<img src="${esc(it.thumb_url)}" alt="" loading="lazy" decoding="async">`
      : `<span class="dxe-it-word">${esc(label)}</span>`;
    const tag = mark
      ? `<span class="dxe-tag ${mark === "Pinned" ? "now" : "mute"}">${esc(mark)}</span>`
      : "";
    const inner = `<span class="dxe-it-pic">${pic}</span><span class="dxe-it-l">${esc(`#${it.index + 1} ${it.thumb_url ? label : ""}`.trim())}</span>${tag}`;
    return pinMode
      ? `<button type="button" class="dxe-it${mark ? " is-marked" : ""}" data-pin-item="${esc(p.id)}" data-key="${esc(it.key)}" data-fk="pin-${esc(p.id)}-${esc(it.key)}" aria-pressed="${mark === "Pinned"}" title="Pin ${esc(label)}">${inner}</button>`
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
        `<span class="dxe-soft">${pinned ? `Pinned: the others are skipped.` : `One per visit, ${it.order === "random" ? "random" : "in turn"}.`}</span>` +
        (it.pinnable
          ? `<span class="lu-seg dxe-seg sm" role="group" aria-label="${esc(p.name)}: one per visit or pinned"><button type="button" data-pin-mode="${esc(p.id)}" data-mode="walk" data-fk="pinmode-${esc(p.id)}-walk" aria-pressed="${!pinned}">One per visit</button><button type="button" data-pin-mode="${esc(p.id)}" data-mode="pin" data-fk="pinmode-${esc(p.id)}-pin" aria-pressed="${!!pinned}">Pinned</button></span>`
          : "") +
        // A pin is the dashboard's own setting, so it moves every lineup that shows it.
        (it.pinnable && p.shared
          ? `<small class="dxe-soft dxe-shared">Pins for every lineup using it.</small>`
          : "") +
        `</div>`;
      const strip = ui.strip[p.id];
      let tiles = "";
      if (!strip || strip.busy) tiles = `<span class="dxe-soft dxe-loading">Loading…</span>`;
      else if (strip.err) tiles = `<span class="dxe-soft">${esc(strip.err)}</span>`;
      else {
        const gridOpen = !!(ui.grid[p.id] && ui.grid[p.id].open);
        tiles = strip.data.items
          .slice(0, gridOpen ? 0 : STRIP)
          .map((x) => itemTile(p, x, pinned && x.key === pinned ? "Pinned" : "", pinMode))
          .join("");
        const more = it.count - Math.min(STRIP, strip.data.items.length);
        const g = ui.grid[p.id];
        if (more > 0)
          tiles += `<button type="button" class="dxe-more" data-grid="${esc(p.id)}" data-fk="grid-${esc(p.id)}" aria-expanded="${!!(g && g.open)}">${ico("layers")}<span><b>${g && g.open ? "Hide" : `+ ${more} more`}</b><small>${g && g.open ? "" : "Browse all"}</small></span></button>`;
      }
      return `<div class="dxe-kids">${head}<div class="dxe-strip">${tiles}</div>${gridHtml(s, p)}</div>`;
    }
    if (it.mode === "draw") {
      const draws = ui.draws[p.id] || [];
      const canDraw = isSavedStep(s.id) && !!st.display;
      const latest = `<span class="dxe-it is-marked"><span class="dxe-it-pic">${p.thumb ? `<img src="${esc(p.thumb)}" alt="" loading="lazy">` : ""}</span><span class="dxe-it-l">Latest</span></span>`;
      const drawn = draws
        .map(
          (d, i) =>
            `<span class="dxe-it"><span class="dxe-it-pic">${d.thumb ? `<img src="${esc(d.thumb)}" alt="">` : `<span class="dxe-it-word">${d.state === "error" ? "!" : "…"}</span>`}</span><span class="dxe-it-l">${esc(d.state === "error" ? d.note : d.state === "ready" ? `Draw ${i + 1}` : "Drawing…")}</span></span>`
        )
        .join("");
      return (
        `<div class="dxe-kids"><div class="dxe-kids-head"><span class="dxe-mono">${esc(it.count ? plural(it.count, it.noun || "items") : "Draw")}</span>` +
        `<span class="dxe-soft">A new one each render.</span>` +
        `<button type="button" class="lu-btn-ghost sm dxe-draw" data-draw="${esc(p.id)}" data-fk="draw-${esc(p.id)}"${canDraw ? "" : ' disabled title="Save first to draw"'}>${ico("refresh")}<span>Draw another</span></button></div>` +
        `<div class="dxe-strip">${drawn}${latest}</div></div>`
      );
    }
    return `<p class="dxe-one">${ico("refresh")}<span>${esc(p.fresh || "")}</span></p>`;
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
      : `<div class="dxe-grid-items${g.busy ? " is-busy" : ""}"${g.busy ? ' aria-busy="true"' : ""}>${data.items.map((x) => itemTile(p, x, pinned && x.key === pinned ? "Pinned" : "", pinMode)).join("")}</div>`;
    return (
      `<div class="dxe-grid" data-grid-for="${esc(p.id)}">` +
      `<div class="dxe-grid-tb"><label class="lu-tb-search dxe-search">${ico("search")}<input type="search" data-fk="q-${esc(p.id)}" data-grid-q="${esc(p.id)}" value="${esc(g.q)}" placeholder="Search ${esc(plural(it.count, it.noun || "items"))}" aria-label="Search ${esc(p.name)}" autocomplete="off" spellcheck="false"></label>` +
      `<span class="dxe-mono dxe-range" aria-live="polite">${esc(range)}</span></div>` +
      body +
      pager +
      `</div>`
    );
  }

  /* --- per-dashboard settings, one summary line and an Edit */
  function settingsHtml(s, p, i) {
    const k = kind();
    const window = k === "cycle";
    const bits = [];
    if (window && s.dwell && s.dwell !== st.interval) bits.push(`shows ${mins(s.dwell)}`);
    bits.push(s.refresh ? `updates every ${mins(s.refresh)}` : "updates when sent");
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
      const upd =
        `<label class="dxe-mini"><span>Updates</span><select class="dxe-in dxe-select" data-fk="upd-${esc(s.id)}" data-updates="${esc(s.id)}">` +
        (UPDATES.includes(s.refresh)
          ? ""
          : `<option value="${s.refresh}" selected>every ${esc(mins(s.refresh))}</option>`) +
        UPDATES.map(
          (v) =>
            `<option value="${v}"${s.refresh === v ? " selected" : ""}>${v ? `every ${esc(mins(v))}` : "only when sent"}</option>`
        ).join("") +
        `</select><small class="dxe-soft">Changes this dashboard everywhere.</small></label>`;
      const cond = hasConditions(s)
        ? `<div class="dxe-mini dxe-cond"><span>Conditions</span><div class="condition-picker" data-condition-picker><textarea rows="2" spellcheck="false" data-cond="${esc(s.id)}" data-fk="cond-${esc(s.id)}" placeholder="[]" aria-label="Conditions for ${esc(p.name)}">${esc(s.conditions)}</textarea></div><small class="dxe-soft">Skipped while they fail.</small></div>`
        : "";
      edit = `<div class="dxe-edit">${showFor}${upd}${cond}</div>`;
    }
    return (
      `<div class="dxe-sum"><span class="dxe-soft">${esc(sum.charAt(0).toUpperCase() + sum.slice(1))}</span>` +
      `<button type="button" class="dxe-link" data-edit="${esc(s.id)}" data-fk="edit-${esc(s.id)}" aria-expanded="${editing}" aria-label="${editing ? "Done with" : "Edit"} ${esc(p.name)}">${editing ? "Done" : "Edit"}</button>` +
      `<span class="dxe-sum-r">${homeBtn}<button type="button" class="dxe-link quiet" data-remove="${esc(s.id)}" data-fk="rm-${esc(s.id)}" aria-label="Remove ${esc(p.name)}">Remove</button></span></div>` +
      edit
    );
  }

  /* --- the add-dashboard picker */
  function candidates() {
    const inList = new Set(st.steps.map((s) => s.id));
    const all = Object.values(S.pages).filter((p) => !p.missing && !inList.has(p.id));
    const here = all.filter((p) => !strandedFor(p.id, st.display));
    const other = all.filter((p) => strandedFor(p.id, st.display));
    return { here, other };
  }
  function pickerHtml() {
    const { here, other } = candidates();
    const dev = deviceOf(st.display);
    const cards = (list, elsewhere) =>
      list
        .map((p) => {
          const where = elsewhere
            ? `<span class="dxe-soft dxe-where">On ${esc(p.devices.map((d) => (deviceOf(d) || { name: d }).name).join(", "))}</span>`
            : "";
          return (
            `<li class="dxe-pick"><span class="dxe-pick-th">${stackedThumb(p)}</span><span class="dxe-step-text"><b>${esc(p.name)}</b><span class="dxe-step-sub">${esc(p.subtitle || "")}</span>${where}</span>` +
            `<button type="button" class="lu-btn-ghost sm dxe-add-btn" data-add="${esc(p.id)}" data-fk="add-${esc(p.id)}"${elsewhere ? " data-elsewhere" : ""} aria-label="Add ${esc(p.name)}">${ico("plus")}<span>Add</span></button></li>`
          );
        })
        .join("");
    const none = !here.length
      ? `<p class="dxe-empty">${ico("layers")}<span>${dev ? `No more dashboards for ${esc(dev.name)}.` : "Pick a display first."}</span></p>`
      : "";
    const more =
      `<p class="dxe-soft dxe-pick-more">Not here? ` +
      (S.urls.newDashboard
        ? `<a href="${esc(S.urls.newDashboard)}">Make a new dashboard</a>`
        : "") +
      (other.length
        ? `${S.urls.newDashboard ? " or " : ""}<button type="button" class="dxe-link" data-elsewhere-toggle data-fk="elsewhere" aria-expanded="${ui.elsewhere}">${ui.elsewhere ? "hide" : "show"} dashboards from other displays (${other.length})</button>`
        : "") +
      `</p>`;
    return (
      `<div class="dxe-picker" id="dxe-picker">${none}<ul class="dxe-picks">${cards(here, false)}</ul>${more}` +
      (ui.elsewhere && other.length
        ? `<ul class="dxe-picks is-other">${cards(other, true)}</ul>`
        : "") +
      `</div>`
    );
  }

  function stepsCard() {
    const rows = st.steps.map(rowHtml).join("");
    const empty = st.steps.length
      ? ""
      : `<p class="dxe-empty">${ico("layers")}<span>No dashboards yet.</span></p>`;
    const addRow =
      `<div class="dxe-add-row"><button type="button" class="lu-btn-ghost dxe-add" data-picker data-fk="picker" aria-expanded="${ui.picker}" aria-controls="dxe-picker"${st.display ? "" : " disabled"}>${ico(ui.picker ? "x" : "plus")}<span>${ui.picker ? "Close" : "Add dashboard"}</span></button>` +
      (st.steps.length > 1
        ? `<span class="dxe-soft dxe-hint">Drag or Alt+↑↓ to reorder.</span>`
        : "") +
      `</div>`;
    return (
      `<div class="dxe-card dxe-steps-card">${empty}<ol class="dxe-steps" id="dxe-steps" aria-label="Dashboards in order">${rows}</ol>` +
      addRow +
      (ui.picker ? pickerHtml() : "") +
      `</div>`
    );
  }

  /* ------------------------------------------------------------ 3 Pace */
  function paceLead() {
    const k = kind();
    if (k === "manual") return "Taps flip; there's no timer.";
    if (k === "daily") return "A push doesn't rotate.";
    if (k === "interval") return `Sent again every ${mins(st.interval)}.`;
    return `Next dashboard every ${mins(st.interval)}.`;
  }
  function freshLine() {
    const pages = st.steps.map((s) => s.refresh);
    if (!pages.length) return "Nothing to refresh yet.";
    const first = pages[0];
    const same = pages.every((r) => r === first);
    const n = pages.length;
    // A push renders live; only a window's next dashboard is drawn ahead.
    const ahead = kind() === "daily" ? "" : ", pre-rendered automatically";
    if (same && first)
      return `${n === 1 ? "It updates" : `All ${n} update`} every ${mins(first)}${ahead}.`;
    if (same) return `Drawn when sent${ahead}.`;
    return `Each on its own Updates (see its row)${ahead}.`;
  }
  function selectHtml(fk, attr, values, cur, label, fmt) {
    const opts = values.includes(cur) ? values : values.concat([cur]).sort((a, b) => a - b);
    return (
      `<select class="dxe-in dxe-select" data-fk="${fk}" ${attr} aria-label="${esc(label)}">` +
      opts
        .map((v) => `<option value="${v}"${v === cur ? " selected" : ""}>${esc(fmt(v))}</option>`)
        .join("") +
      `</select>`
    );
  }
  function switchHtml(fk, attr, on, label) {
    return `<span class="switch dxe-switch"><input type="checkbox" data-fk="${fk}" ${attr}${on ? " checked" : ""} aria-label="${esc(label)}"><span class="switch-track"><span class="switch-thumb"></span></span></span>`;
  }

  function optionsHtml() {
    const dev = deviceOf(st.display);
    if (!dev) return "";
    const k = kind();
    const caps = dev.caps || {};
    const rows = [];
    if (caps.inputs && k === "cycle")
      rows.push(
        `<div class="dxe-opt"><div><b>Taps also flip</b><p class="dxe-soft">A tap moves on; the timer carries on.</p></div>${switchHtml("taps", "data-taps", st.taps, "Taps also flip")}</div>`
      );
    if (caps.inputs && (k === "manual" || (k === "cycle" && st.taps))) {
      const home = pageOf(st.home || (st.steps[0] || {}).id || "");
      rows.push(
        `<div class="dxe-opt"><div><b>Back to ★ ${esc(home.name || "home")}</b><p class="dxe-soft">Returns home when nobody taps.</p></div>${selectHtml("timeout", "data-timeout", RETURN_HOME, st.timeout, "Return home after", (v) => (v ? `after ${v} idle min` : "never"))}</div>`
      );
    }
    if (caps.sleeps && k !== "manual")
      rows.push(
        `<div class="dxe-opt"><div><b>Send just before it wakes</b><p class="dxe-soft">${caps.wake_learned ? `${ico("check")}Wake times learned.` : "Learns wake times from check-ins."}</p></div>` +
          `<span class="dxe-opt-ctl"><label class="dxe-lead-s"><input type="number" class="dxe-in dxe-num" min="0" max="600" data-fk="lead" data-lead value="${esc(st.lead)}" aria-label="Seconds ahead"${st.smartSync ? "" : " disabled"}><span>s</span></label>${switchHtml("smart", "data-smart", st.smartSync, "Send just before it wakes")}</span></div>`
      );
    if (!rows.length) {
      const why = caps.inputs
        ? "nothing here applies to this lineup"
        : caps.sleeps
          ? "nothing here applies"
          : "always on, no buttons";
      return `<p class="dxe-one dxe-opts-none">${ico("info")}<span>${esc(dev.name)}: ${esc(why)}.</span></p>`;
    }
    return (
      `<details class="dxe-fold" data-fold="options"${ui.optOpen ? " open" : ""}><summary>${ico("caret", "dxe-caret")}<b>Options for ${esc(dev.name)}</b><span class="dxe-soft">${esc(plural(rows.length, "settings"))}</span></summary>` +
      `<div class="dxe-fold-body">${rows.join("")}</div></details>`
    );
  }

  // Keep ready (a hidden re-render) only helps a display that flips by button
  // or touch; a push renders live, and a by-hand lineup on a display with no
  // input never flips (the settings audit).
  const showKeepReady = () => takesInput() && kind() !== "daily";
  // "First whose conditions pass" is a legacy mode: offered only where set.
  const showMode = () => S.mode === "priority" && kind() === "cycle";
  const showMinHold = () => kind() !== "manual";
  function advancedHtml() {
    const mode = st.mode === "priority" ? "first match" : "timed rotation";
    const keep = st.cadence ? `keep ready ${mins(st.cadence)}` : "keep ready off";
    const sum = [`priority ${st.priority}`];
    if (showMode()) sum.push(mode);
    if (showMinHold()) sum.push(`min hold ${st.minHold} min`);
    if (showKeepReady()) sum.push(keep);
    const overrides = st.steps
      .map((s) => {
        const p = pageOf(s.id);
        const cur = s.override === null || s.override === undefined ? "" : String(s.override);
        const opts = [
          ["", "lineup's"],
          ...KEEP_READY.map((v) => [String(v), v ? `every ${mins(v)}` : "never"]),
        ];
        if (cur && !opts.some(([v]) => v === cur)) opts.push([cur, `every ${mins(Number(cur))}`]);
        return `<label class="dxe-mini row"><span>${esc(p.name)}</span><select class="dxe-in dxe-select" data-fk="ov-${esc(s.id)}" data-override="${esc(s.id)}">${opts.map(([v, l]) => `<option value="${v}"${v === cur ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
      })
      .join("");
    return (
      `<details class="dxe-fold" data-fold="advanced"${ui.advOpen ? " open" : ""}><summary>${ico("caret", "dxe-caret")}<b>Advanced</b><span class="dxe-soft">${esc(sum.join(" · "))}</span></summary>` +
      `<div class="dxe-fold-body dxe-adv">` +
      `<label class="dxe-mini"><span>Priority</span><input type="number" class="dxe-in dxe-num" data-fk="prio" data-priority min="-1000" max="1000" value="${esc(st.priority)}"><small class="dxe-soft">Higher wins a tie.</small></label>` +
      (showMode()
        ? `<label class="dxe-mini"><span>Mode</span><select class="dxe-in dxe-select" data-fk="mode" data-mode-sel><option value="scheduled"${st.mode === "scheduled" ? " selected" : ""}>Timed rotation</option><option value="priority"${st.mode === "priority" ? " selected" : ""}>First whose conditions pass</option></select></label>`
        : "") +
      (showMinHold()
        ? `<label class="dxe-mini"><span>Min hold</span><span class="dxe-unit"><input type="number" class="dxe-in dxe-num" data-fk="hold" data-hold min="0" max="120" value="${esc(st.minHold)}"><span>min</span></span><small class="dxe-soft">Shortest time on one.</small></label>`
        : "") +
      (showKeepReady()
        ? `<label class="dxe-mini"><span>Keep ready</span>${selectHtml("cadence", "data-cadence", KEEP_READY, st.cadence, "Keep ready every", (v) => (v ? `every ${mins(v)}` : "off"))}<small class="dxe-soft">Hidden re-render for flips.</small></label>` +
          (st.steps.length
            ? `<div class="dxe-mini dxe-ovs"><span>Keep ready, per dashboard</span>${overrides}</div>`
            : "")
        : "") +
      `</div></details>`
    );
  }

  function paceHtml() {
    const k = kind();
    let each = "";
    if (k === "daily")
      each = `<span class="dxe-soft">Only for a window.</span> <button type="button" class="dxe-link" data-end="add">Add an end time</button>`;
    else if (k === "manual") each = "";
    else
      each =
        selectHtml(
          "interval",
          "data-interval",
          SHOW_FOR,
          st.interval,
          k === "interval" ? "Sent again every" : "Each dashboard shows for",
          mins
        ) + (k === "cycle" ? `<span class="dxe-soft">A row with its own time says so.</span>` : "");
    return (
      `<div class="dxe-pace">` +
      (each
        ? `<div class="dxe-field"><span class="dxe-label">${k === "interval" ? "Sent again every" : "Each dashboard shows for"}</span><div class="dxe-ctl">${each}</div></div>`
        : "") +
      `<div class="dxe-field"><span class="dxe-label">Fresh pictures</span><div class="dxe-ctl"><span>${esc(freshLine())}</span></div></div>` +
      optionsHtml() +
      advancedHtml() +
      `</div>`
    );
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
  function newFlowHtml() {
    const dev = deviceOf(st.display);
    let html = "";
    // 1 Display
    // A lineup started from a display's + is for that display: no Change.
    if (ui.step > 1 && dev) html += doneLine(1, "Display", dev.name, 1, S.displayLocked);
    else
      html += `<section class="dxe-sec">${secHead(1, "Display", "Pick the display it runs on.", "dxe-h-display")}${displayCards()}</section>`;
    // 2 When
    if (ui.step > 2) html += doneLine(2, "When it runs", whenSummary(), 2);
    else if (ui.step === 2)
      html +=
        `<section class="dxe-sec" aria-labelledby="dxe-h-when">${secHead(2, "When it runs", whenLead(), "dxe-h-when")}${whenCard()}` +
        `<div class="dxe-next-step"><button type="button" class="lu-btn-primary" data-goto="3" data-fk="goto-3"${whenReady() ? "" : ' aria-disabled="true"'}>Continue</button></div></section>`;
    else html += waitingHead(2, "When it runs", "Pick a display first.");
    // 3 Dashboards
    if (ui.step === 3) {
      const chosen = st.steps.length
        ? `<ol class="dxe-steps" id="dxe-steps" aria-label="Dashboards in order">${st.steps.map(rowHtml).join("")}</ol>`
        : `<p class="dxe-empty">${ico("layers")}<span>No dashboards yet.</span></p>`;
      html +=
        `<section class="dxe-sec" aria-labelledby="dxe-h-dash">${secHead(3, "Dashboards", "Pick what it shows, in order.", "dxe-h-dash")}` +
        `<div class="dxe-card dxe-steps-card">${chosen}${st.steps.length > 1 ? `<div class="dxe-add-row"><span class="dxe-soft dxe-hint">Drag or Alt+↑↓ to reorder.</span></div>` : ""}</div>` +
        pickerHtml() +
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
        `<section class="dxe-sec dxe-sec-pace" aria-labelledby="dxe-h-pace">${secHead(3, "Pace", paceLead(), "dxe-h-pace")}${paceHtml()}</section>`;
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
    refocus(mark);
    // Nothing can be edited while a save is on its way: it would be marked
    // saved and lost.
    const sections = $("dxe-sections");
    const hero = $("dxe-hero");
    if (sections) sections.inert = ui.saving;
    if (hero) hero.inert = ui.saving;
    const on = $("dxe-enabled");
    if (on) on.disabled = ui.saving;
  }
  function paintFooter() {
    const btns = $("dxe-actionbar-btns");
    const state = $("dxe-save-state");
    if (S.isNew) {
      const ready = !!st.display && st.steps.length > 0;
      state.className = "dxe-actionbar-status" + (ui.msgKind ? ` is-${ui.msgKind}` : "");
      state.textContent =
        ui.msg ||
        (ready
          ? "Ready to create."
          : ui.step < 3
            ? `Step ${ui.step} of 3`
            : "Add a dashboard to create it.");
      btns.innerHTML =
        `<a class="lu-btn-ghost lu-btn-line" href="${esc(S.urls.lineups)}" data-fk="f-cancel">Cancel</a>` +
        `<button type="button" class="lu-btn-primary" data-create data-fk="f-create"${ready && !ui.saving ? "" : " disabled"}>${ui.saving ? "Creating…" : "Create lineup"}</button>`;
      return;
    }
    const d = dirty();
    state.className =
      "dxe-actionbar-status" + (ui.msgKind ? ` is-${ui.msgKind}` : d ? " is-dirty" : "");
    state.textContent = ui.msg || (d ? "Unsaved changes" : "All changes saved");
    const home = st.home || (st.steps[0] || {}).id || "";
    btns.innerHTML =
      (ui.reload
        ? `<button type="button" class="lu-btn-ghost lu-btn-line" data-reload data-fk="f-reload">Reload</button>`
        : "") +
      `<button type="button" class="lu-btn-ghost" data-push-lineup data-fk="f-push"${S.display && home && isSavedStep(home) && !ui.pushing ? "" : " disabled"} title="Send ${esc(pageOf(home).name || "it")} now">${ico("send")}<span>Push now</span></button>` +
      `<details class="lu-more dxe-more-menu"><summary title="More" aria-label="More actions">${ico("dots")}</summary><div class="lu-more-menu"><button type="button" class="danger" data-delete>Delete lineup</button></div></details>` +
      `<button type="button" class="lu-btn-ghost lu-btn-line" data-discard data-fk="f-discard"${d && !ui.saving ? "" : " disabled"}>Discard</button>` +
      `<button type="button" class="lu-btn-primary" data-save data-fk="f-save"${ui.saving ? " disabled" : ""}>${ui.saving ? "Saving…" : "Save lineup"}</button>`;
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

  async function addStep(id, elsewhere) {
    if (elsewhere && !(await confirmBind([id], st.display))) return;
    const adds = [...root.querySelectorAll("[data-add]")];
    const at = Math.max(
      0,
      adds.findIndex((b) => b.dataset.add === id)
    );
    change(() => {
      st.steps.push({
        id,
        dwell: null,
        override: null,
        conditions: "",
        refresh: pageOf(id).refresh || 0,
        pin: undefined,
      });
      if (!st.home) st.home = "";
    });
    live(`Added ${pageOf(id).name}.`);
    // The card it was on is gone: the next Add takes its place.
    const left = [...root.querySelectorAll("[data-add]")];
    (left[Math.min(at, left.length - 1)] || root.querySelector("[data-picker]"))?.focus();
  }

  function removeStep(id) {
    const i = st.steps.findIndex((s) => s.id === id);
    change(() => {
      st.steps = st.steps.filter((s) => s.id !== id);
      if (st.home === id) st.home = "";
      ui.open.delete(id);
    });
    live(`Removed ${pageOf(id).name}.`);
    const next = root.querySelectorAll("[data-step]")[Math.max(0, i - 1)];
    (next ? next.querySelector("[data-expand]") : root.querySelector("[data-picker]"))?.focus();
  }

  /* --- item lists (dashboard-items) */
  async function fetchItems(id, offset, limit, q) {
    const u = new URLSearchParams({ page_id: id, offset: String(offset), limit: String(limit) });
    if (q) u.set("q", q);
    return request(`${S.urls.items}?${u}`, { headers: { Accept: "application/json" } });
  }
  async function loadStrip(id) {
    const p = pageOf(id);
    // A failed load is tried again the next time the row opens.
    if (!p.items || p.items.mode !== "pick" || (ui.strip[id] && !ui.strip[id].err)) return;
    ui.strip[id] = { busy: true };
    try {
      ui.strip[id] = { data: await fetchItems(id, 0, STRIP + 1, "") };
    } catch (err) {
      ui.strip[id] = { err: err.net ? "Can't reach Tesserae; open it again." : err.message };
    }
    draw();
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
    try {
      const res = await postJSON(S.urls.pushStep, { page_id: id, display_id: st.display });
      ui.msg = res.message || "Pushed.";
      ui.msgKind = "ok";
    } catch (err) {
      ui.msg = err.net ? "Can't reach Tesserae; not pushed, try again." : err.message;
      ui.msgKind = "err";
    } finally {
      ui.pushing = false;
    }
    draw();
    refocus(mark);
    live(ui.msg);
  }
  async function drawAnother(id) {
    const list = (ui.draws[id] = ui.draws[id] || []);
    const entry = { key: `draw:${nonce()}:1`, state: "loading", thumb: "", note: "" };
    list.unshift(entry);
    draw();
    const u = new URLSearchParams({ page_id: id, item: entry.key, display_id: st.display });
    for (let tries = 0; tries < 60; tries++) {
      try {
        const r = await fetch(`${S.urls.stepItem}?${u}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const data = await r.json().catch(() => ({}));
        if (r.status === 200 && data.state === "ready") {
          entry.state = "ready";
          entry.thumb = data.thumb_url;
          break;
        }
        if (r.status >= 400 && r.status !== 503) {
          entry.state = "error";
          entry.note = data.error || "Could not draw it";
          break;
        }
      } catch {
        /* try again */
      }
      await new Promise((ok) => window.setTimeout(ok, 1500));
    }
    if (entry.state === "loading") {
      entry.state = "error";
      entry.note = "Took too long";
    }
    live(
      entry.state === "ready"
        ? `${pageOf(id).name}: drew another.`
        : `${pageOf(id).name}: ${entry.note}.`
    );
    draw();
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
    if (takesInput() && k === "cycle") fd.append("taps_flip", st.taps ? "1" : "0");
    if (windowed) fd.append("advance_interval_minutes", String(st.interval));
    // Home: only once picked (or its dashboard removed); unpicked, it stays
    // whichever dashboard is first.
    if (st.home !== (S.home || "")) fd.append("home", st.home || "");
    const showHome = takesInput() && (k === "manual" || (k === "cycle" && st.taps));
    if (showHome) fd.append("timeout", String(st.timeout));
    if (sleeps() && k !== "manual") {
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
      if (hasConditions(s)) fd.append(`conditions[${s.id}]`, s.conditions || "");
      if (s.refresh !== (pageOf(s.id).refresh || 0))
        fd.append(`page_refresh[${s.id}]`, String(s.refresh));
      if (s.pin !== undefined) fd.append(`pin[${s.id}]`, s.pin);
    });
    consentFields(fd);
    return fd;
  }

  async function save() {
    if (ui.saving) return;
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
    if (ui.saving || !st.display || !st.steps.length || !whenReady()) return;
    if (
      !(await confirmBind(
        st.steps.map((s) => s.id),
        st.display
      ))
    )
      return;
    const fd = new FormData();
    const dev = deviceOf(st.display);
    fd.append("name", st.name.trim() || `${dev ? dev.name : "New"} lineup`);
    if (st.when.mode === "manual") fd.append("intent", "manual");
    else {
      fd.append("when", st.when.mode);
      fd.append("start", st.when.start);
      if (st.when.mode === "between") fd.append("end", st.when.end);
      st.when.days.forEach((on, i) => on && fd.append("days", String(i)));
    }
    fd.append("device_ids", st.display);
    st.steps.forEach((s) => fd.append("page_ids", s.id));
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
    ui.changeDisplay = false;
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
  root.addEventListener("click", async (e) => {
    const t = e.target.closest("button, [data-goto]");
    if (!t || t.disabled) return;
    const d = t.dataset;
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
    if (d.when) return setWhen(d.when);
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
    if (d.changeDisplay !== undefined) {
      ui.changeDisplay = true;
      draw();
      root.querySelector("[data-display-select]")?.focus();
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
              : "[data-add]"
        ) || root
      ).focus();
      return;
    }
    if (d.picker !== undefined) {
      ui.picker = !ui.picker;
      draw();
      (ui.picker ? root.querySelector("[data-add]") : root.querySelector("[data-picker]"))?.focus();
      return;
    }
    if (d.elsewhereToggle !== undefined) {
      ui.elsewhere = !ui.elsewhere;
      draw();
      root.querySelector("[data-elsewhere-toggle]")?.focus();
      return;
    }
    if (d.add) return addStep(d.add, d.elsewhere !== undefined);
    if (d.remove) return removeStep(d.remove);
    if (d.home) {
      if (t.getAttribute("aria-disabled") === "true") return; // already home
      change(() => (st.home = d.home));
      live(`${pageOf(d.home).name} is home.`);
      return;
    }
    if (d.expand) {
      const id = d.expand;
      if (ui.open.has(id)) ui.open.delete(id);
      else {
        ui.open.add(id);
        loadStrip(id);
      }
      draw();
      root.querySelector(`[data-expand="${CSS.escape(id)}"]`)?.focus();
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
        d.mode === "walk" ? `${p.name}: one per visit.` : `${p.name}: pinned, pick one to keep.`
      );
      return;
    }
    if (d.pinItem) {
      const s = stepOf(d.pinItem);
      if (s) change(() => (s.pin = d.key));
      live(`Pinned ${d.key}.`);
      return;
    }
    if (d.draw) return drawAnother(d.draw);
    if (d.push) return pushNow(d.push);
    if (d.pushLineup !== undefined) return pushNow(st.home || st.steps[0].id);
    if (d.reload !== undefined) return window.location.reload();
    if (d.save !== undefined) return save();
    if (d.create !== undefined) return create();
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
    if (d.hold !== undefined)
      return change(() => (st.minHold = Number(t.value) || 0), { draw: false });
  });

  root.addEventListener("change", async (e) => {
    const t = e.target;
    const d = t.dataset;
    if (t.id === "dxe-enabled") {
      $("dxe-on-word").textContent = t.checked ? "On" : "Off";
      return change(() => (st.enabled = t.checked));
    }
    if (d.time) return draw();
    if (d.displaySelect !== undefined) {
      const dev = t.value;
      if (
        !(await confirmBind(
          st.steps.map((s) => s.id),
          dev
        ))
      ) {
        t.value = st.display;
        return;
      }
      ui.changeDisplay = false;
      return change(() => (st.display = dev));
    }
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
    if (d.smart !== undefined) return change(() => (st.smartSync = t.checked));
    if (d.cadence !== undefined) return change(() => (st.cadence = Number(t.value) || 0));
    if (d.modeSel !== undefined) return change(() => (st.mode = t.value));
    if (d.lead !== undefined || d.priority !== undefined || d.hold !== undefined) return draw();
  });

  root.addEventListener(
    "toggle",
    (e) => {
      const f = e.target.dataset && e.target.dataset.fold;
      if (f === "options") ui.optOpen = e.target.open;
      if (f === "advanced") ui.advOpen = e.target.open;
    },
    true
  );

  // Alt+Up / Alt+Down move a dashboard; Enter in a field never submits the form.
  root.addEventListener("keydown", (e) => {
    const row = e.target.closest && e.target.closest("[data-step]");
    if (row && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      move(row.dataset.step, e.key === "ArrowUp" ? -1 : 1);
      return;
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
    const r = over.getBoundingClientRect();
    const after = e.clientY > r.top + r.height / 2;
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

  if (S.isNew && S.displayLocked) ui.step = 2;
  if (S.isNew) {
    newStartAfterPause();
    saved = snapshot();
  }
  draw();
})();
