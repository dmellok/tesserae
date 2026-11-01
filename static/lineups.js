// Lineups page (/decks): progressive enhancement over the server-rendered
// markup in templates/_lineups.html.
//
// One state, one draw: the view-model (embedded as JSON, refreshed from
// /decks/view.json) plus what the user changed here (folds, collapsed
// displays, the day each bar shows, filters, the view, unsaved edits) is drawn
// by lineups_render.js into [data-lu-displays]. Unsaved edits (bar drags,
// keyboard nudges, day tiles, typed windows) stay local until Save, which
// sends them as one batch; Undo drops them. Storage is guarded (private mode,
// blocked site data, junk) and falls back to memory.
(function () {
  "use strict";
  const root = document.querySelector("[data-lu]");
  const host = root && root.querySelector("[data-lu-displays]");
  const vmEl = root && root.querySelector("[data-lu-vm]");
  const R = window.TesseraeLineupsRender;
  const API = window.TesseraeLineupsAPI;
  if (!root || !host || !vmEl || !R || !API) return;

  let vm;
  try {
    vm = JSON.parse(vmEl.textContent);
  } catch {
    return; // keep the server's markup as is
  }
  if (!vm || !Array.isArray(vm.displays)) return;
  R.setTimeCycle(vm.time_cycle);

  const SNAP = 15;
  const DAY_MIN = 1440;
  const reduceMotion = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ?hl=<id> (after the wizard or an action) opens that row and marks it
  // once; #udeck-<id> is the old anchor for the same thing.
  const params = new URLSearchParams(location.search);
  let newId =
    params.get("hl") ||
    (location.hash.startsWith("#udeck-") ? decodeURIComponent(location.hash.slice(7)) : null);

  /* ------------------------------------------------------------ storage */
  const store = (() => {
    const mem = {};
    return {
      get(k) {
        try {
          const v = window.localStorage.getItem(k);
          return v === null && k in mem ? mem[k] : v;
        } catch {
          return k in mem ? mem[k] : null;
        }
      },
      set(k, v) {
        mem[k] = v;
        try {
          window.localStorage.setItem(k, v);
        } catch {
          /* memory only */
        }
      },
    };
  })();
  const readJSON = (k, fallback, ok) => {
    try {
      const v = JSON.parse(store.get(k) || "null");
      return ok(v) ? v : fallback;
    } catch {
      return fallback;
    }
  };
  // The fold choice per lineup keeps the previous page's key and meaning: a
  // remembered choice wins over the server's default.
  const FOLD_KEY = "tesserae.lineups.fold";
  const COLLAPSE_KEY = "tesserae.lineups.collapsed";
  const VIEW_KEY = "tesserae.lineups.view";
  const SCOPE_KEY = "tesserae.lineups.scope";
  const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
  const foldChoice = readJSON(FOLD_KEY, {}, isObj);
  const collapsed = new Set(
    readJSON(COLLAPSE_KEY, [], Array.isArray).filter((x) => typeof x === "string")
  );
  const saveFolds = () => store.set(FOLD_KEY, JSON.stringify(foldChoice));
  // Open week-grid rows per display (Timeline view): {display id: [weekday]}.
  const TL_KEY = "tesserae.lineups.timeline-open";
  const tlStored = readJSON(TL_KEY, {}, isObj);
  const saveTl = () =>
    store.set(
      TL_KEY,
      JSON.stringify(Object.fromEntries(Object.entries(st.tlOpen).map(([k, v]) => [k, [...v]])))
    );
  const saveCollapsed = () => store.set(COLLAPSE_KEY, JSON.stringify([...collapsed]));

  /* ------------------------------------------------------------ state */
  const st = {
    view: store.get(VIEW_KEY) === "timeline" ? "timeline" : "cards",
    scope: store.get(SCOPE_KEY) === "today" ? "today" : "all", // rows: only the day shown, or all
    q: "",
    fDisplay: "",
    fKind: "",
    fState: "",
    sort: "next",
    day: {}, // display id -> weekday shown on its bar
    pending: {}, // display id -> {lineup id -> {start_min, end_min, days[7]}}
    flash: {}, // display id -> {msg, err}
    saving: {},
    rowErr: {}, // lineup id -> the server's reason an edit was refused
    // display id -> Set of weekdays opened in the Timeline view (default: none)
    tlOpen: Object.fromEntries(
      Object.entries(tlStored).map(([k, v]) => [
        k,
        new Set(Array.isArray(v) ? v.filter((x) => Number.isInteger(x) && x >= 0 && x < 7) : []),
      ])
    ),
  };
  const displayById = (id) => vm.displays.find((d) => d.id === id);
  const lineupOf = (id, did) => {
    for (const d of vm.displays) {
      if (did !== undefined && d.id !== did) continue;
      const l = d.lineups.find((x) => x.id === id);
      if (l) return { d, l };
    }
    const l = (vm.unassigned || []).find((x) => x.id === id);
    return l ? { d: null, l } : null;
  };
  const displayOf = (el) => el.closest("[data-display]")?.dataset.display;
  const dayOf = (d) => (d.id in st.day ? st.day[d.id] : d.selected_day);
  const isFolded = (l) => (typeof foldChoice[l.id] === "boolean" ? foldChoice[l.id] : !!l.folded);

  /* ------------------------------------------------------------ filters */
  const KIND = { cycle: "cycle", daily: "send", interval: "send", manual: "nav" };
  const stateOf = (l) =>
    l.status === "off" ? "off" : l.status === "live" || l.holding ? "live" : "wait";
  function matches(l, d) {
    if (st.q) {
      const text = [l.name, l.badge, d ? d.name : "", ...l.steps.map((s) => s.name)]
        .join(" ")
        .toLowerCase();
      if (!text.includes(st.q.toLowerCase())) return false;
    }
    if (st.fDisplay && (!d || d.id !== st.fDisplay)) return false;
    if (st.fKind && KIND[l.kind] !== st.fKind) return false;
    if (st.fState && stateOf(l) !== st.fState) return false;
    return true;
  }
  const filtering = () => !!(st.q || st.fDisplay || st.fKind || st.fState);
  /* Today | All: "today" keeps what is still going to happen on the day the
     display's bar shows (today unless the day picker moved it). On the real
     current day that is a lineup live or holding now, or with a run later
     today; a run that already finished ("next Tue") or a lineup that does not
     run today (or is switched off) drops out, so the list is what is left of
     the day. On another day, past or future, it is every lineup that runs on
     that day. By-hand lineups have no days or runs, so they always stay. */
  const runsOn = (l, day) =>
    l.kind === "manual" ||
    (day === vm.now.weekday
      ? l.status === "live" || !!l.holding || l.status === "later_today"
      : !!(l.days && l.days[day]));
  const inScope = (l, d) =>
    st.scope !== "today" || st.view === "timeline" || runsOn(l, d ? dayOf(d) : vm.now.weekday);
  function ordered(ls, d) {
    const a = ls.filter((l) => matches(l, d) && inScope(l, d));
    if (st.sort === "name") a.sort((x, y) => x.name.localeCompare(y.name));
    else if (st.sort === "kind")
      a.sort((x, y) => x.badge.localeCompare(y.badge) || x.sort_minutes - y.sort_minutes);
    else if (st.sort === "screens") a.sort((x, y) => y.step_count - x.step_count);
    return a; // "next": the server's order (live first, then by next run)
  }

  /* ------------------------------------------------------------ edits */
  const origOf = (l) => ({
    start_min: l.window.start_min,
    end_min: l.window.end_min,
    days: l.days.slice(),
  });
  function effective(d) {
    let x = R.withPending(d, st.pending[d.id], vm.now.weekday, vm.now.minute);
    const day = dayOf(d);
    if (x.day.weekday !== day && x.week && x.week[day]) {
      if (x === d) x = Object.assign({}, d);
      x.day = x.week[day];
      x.selected_day = day;
    }
    return x;
  }
  const effLineup = (d, id) => effective(d).lineups.find((l) => l.id === id);
  const winOf = (l) => [
    l.window.start_min,
    l.window.is_point ? l.window.start_min : l.window.end_min,
  ];
  const sameDays = (a, b) => a.every((v, i) => !!v === !!b[i]);

  function setPending(d, l, change) {
    const base = (st.pending[d.id] && st.pending[d.id][l.id]) || origOf(l);
    const next = Object.assign({}, base, change);
    const o = origOf(l);
    // Windows and weekdays belong to the lineup, so every display copy
    // previews the same edit. Actions such as Push now still use their display.
    vm.displays
      .filter((x) => x.lineups.some((v) => v.id === l.id))
      .forEach((x) => {
        st.pending[x.id] = st.pending[x.id] || {};
        if (
          next.start_min === o.start_min &&
          (l.window.is_point || next.end_min === o.end_min) &&
          sameDays(next.days, o.days) &&
          !Object.values(st.saving).some(Boolean)
        )
          delete st.pending[x.id][l.id];
        else st.pending[x.id][l.id] = next;
        if (!Object.keys(st.pending[x.id]).length) delete st.pending[x.id];
      });
  }

  function applyEdit(d, l, change) {
    delete st.rowErr[l.id];
    const before = JSON.parse(JSON.stringify(st.pending));
    try {
      setPending(d, l, change);
      render();
    } catch (err) {
      // An edit can never blank the page: put the last good state back.
      console.warn("[lineups] edit undone:", err && err.message);
      st.pending = before;
      st.flash[d.id] = { msg: "That change could not be applied and was undone.", err: true };
      render();
    }
  }
  const pendingCount = (d) => Object.keys(st.pending[d.id] || {}).length;

  // Other lineups' windows on the given days (points for single pushes).
  function obstacles(ed, l, days) {
    const out = [];
    ed.lineups.forEach((o) => {
      if (o.id === l.id || !o.window || !o.enabled || o.window.wraps) return;
      if (!o.days.some((on, i) => on && days[i])) return;
      const [s, e] = winOf(o);
      out.push({ s, e, o });
    });
    return out;
  }
  function clash(ed, l, s, e, days) {
    return obstacles(ed, l, days).find((b) =>
      b.s === b.e
        ? b.s >= s && b.s < Math.max(e, s + 1)
        : s === e
          ? s >= b.s && s < b.e
          : s < b.e && e > b.s
    );
  }
  function room(ed, l, os, oe) {
    let lo = 0;
    let hi = DAY_MIN;
    let loBy = null;
    let hiBy = null;
    obstacles(ed, l, l.days).forEach((b) => {
      if (b.e <= os && b.e >= lo) {
        lo = b.s === b.e ? b.s + SNAP : b.e;
        loBy = b.o;
      }
      if (b.s >= oe && b.s <= hi && !(b.s === oe && b.s === b.e && os === oe)) {
        hi = b.s;
        hiBy = b.o;
      }
    });
    return { lo, hi, loBy, hiBy };
  }
  function proposal(ed, l, mode, os, oe, dm) {
    const { lo, hi, loBy, hiBy } = room(ed, l, os, oe);
    const push = !!l.window.is_point;
    const maxE = push ? Math.min(hi, DAY_MIN - SNAP) : hi;
    let s = os;
    let e = oe;
    let stop = null;
    if (mode === "move") {
      s = os + dm;
      e = oe + dm;
      if (s < lo) {
        e += lo - s;
        s = lo;
        stop = loBy;
      }
      if (e > maxE) {
        s -= e - maxE;
        e = maxE;
        stop = hiBy;
      }
    } else if (mode === "l") {
      s = Math.min(Math.max(os + dm, lo), oe - SNAP);
      if (os + dm < lo) stop = loBy;
    } else {
      e = Math.max(Math.min(oe + dm, hi), os + SNAP);
      if (oe + dm > hi) stop = hiBy;
    }
    return { s, e, stop, pause: R.overlapsPause(ed.pause, s, e) };
  }
  function commit(d, id, s, e) {
    const ed = effective(d);
    const l = ed.lineups.find((x) => x.id === id);
    if (!l || ![s, e].every(Number.isFinite)) return;
    s = Math.max(0, Math.min(DAY_MIN - SNAP, s));
    e = l.window.is_point ? s : Math.max(s + SNAP, Math.min(DAY_MIN, e));
    const orig = d.lineups.find((x) => x.id === id);
    applyEdit(d, orig, { start_min: s, end_min: e, days: l.days.slice() });
  }

  /* ------------------------------------------------------------ render */
  const ui = {
    get view() {
      return st.view;
    },
    get barReadOnly() {
      return barReadOnly();
    },
    folded: isFolded,
    rowError: (l) => st.rowErr[l.id] || "",
    tlOpen: (d, i) => !!(st.tlOpen[d.id] && st.tlOpen[d.id].has(i)),
    isNew: (l) => l.id === newId,
    collapsed: (d) => collapsed.has(d.id),
    day: dayOf,
    order: (ls) => ordered(ls, null),
    pendingBar: (d) => {
      const f = st.flash[d.id];
      delete st.flash[d.id];
      return R.pendingBar(d, pendingCount(d), f && f.msg, f && f.err);
    },
  };

  function localFocusKey(el) {
    if (!el || !host.contains(el)) return null;
    for (const a of [
      "data-fold",
      "data-collapse",
      "data-toggle-day",
      "data-dpick",
      "data-push",
      "data-peek",
      "data-win-edit",
      "data-undo",
      "data-save",
    ]) {
      const v = el.getAttribute(a);
      if (v !== null) return `[${a}="${CSS.escape(v)}"]`;
    }
    if (el.matches("[data-tl-row]"))
      return `[data-tl-row="${el.dataset.tlRow}"][data-display="${CSS.escape(el.dataset.display)}"]`;
    if (el.matches(".lu-blk[data-edit]"))
      return `.lu-blk[data-edit][data-lineup="${CSS.escape(el.dataset.lineup)}"]`;
    if (el.matches(".lu-strip-scroll")) {
      const r = el.closest(".lu-row");
      return r ? `#${CSS.escape(r.id)} .lu-strip-scroll` : null;
    }
    return null;
  }

  function focusKey(el) {
    const key = localFocusKey(el);
    if (!key) return null;
    const did = displayOf(el);
    const day = el.closest(".lu-trk[data-day], .lu-tlg-row[data-day]")?.dataset.day;
    return (
      (did === undefined ? "" : `.lu-ds[data-display="${CSS.escape(did)}"] `) +
      (day === undefined ? "" : `[data-day="${day}"] `) +
      key
    );
  }
  // Keep the actual decoded image elements when a poll rebuilds the cards.
  // Parse off-DOM: assigning src to connected replacement images can start
  // needless loads before we have a chance to reuse the original elements.
  function replaceCards(html) {
    const images = new Map();
    const key = (img) =>
      ["src", "loading", "fetchpriority"].map((a) => img.getAttribute(a) || "").join("|");
    host.querySelectorAll(".lu-thumb img").forEach((img) => {
      const k = key(img);
      if (!images.has(k)) images.set(k, []);
      images.get(k).push(img);
    });
    const template = document.createElement("template");
    template.innerHTML = html;
    template.content.querySelectorAll(".lu-thumb img").forEach((fresh) => {
      const previous = images.get(key(fresh))?.shift();
      if (!previous) return;
      // Copy current markup attributes (including display-gamut attributes)
      // without resetting src or discarding the browser's decoded pixels.
      for (const attr of [...previous.attributes]) {
        if (!fresh.hasAttribute(attr.name)) previous.removeAttribute(attr.name);
      }
      for (const attr of fresh.attributes) {
        if (previous.getAttribute(attr.name) !== attr.value)
          previous.setAttribute(attr.name, attr.value);
      }
      fresh.replaceWith(previous);
    });
    host.replaceChildren(template.content);
  }

  let thumbFollowups = 0;
  let thumbTimer = null;
  function followColdThumbs() {
    if (!host.querySelector(".lu-thumb:not(.empty) .lu-thumb-ph")) {
      clearTimeout(thumbTimer);
      thumbTimer = null;
      thumbFollowups = 0;
    } else if (thumbTimer === null && thumbFollowups < 5) {
      thumbTimer = setTimeout(
        () => {
          thumbTimer = null;
          thumbFollowups++;
          refresh();
        },
        3000 * 2 ** thumbFollowups
      );
    }
  }

  let hasRendered = false;

  let lastRO = null;
  function render() {
    const scrolls = {};
    if (hasRendered)
      host.querySelectorAll(".lu-row").forEach((r) => {
        const sc = r.querySelector(".lu-strip-scroll");
        if (sc) scrolls[r.id] = sc.scrollLeft;
      });
    hasRendered = true;
    const lanes = {};
    host.querySelectorAll("[data-tl-scroll]").forEach((x) => {
      lanes[x.dataset.tlScroll] = x.scrollLeft;
    });
    const fk = focusKey(document.activeElement);
    closeDayPop();
    closePeek();
    hideTip();

    ui.seen = new Set();
    let html = "";
    let shown = 0;
    let matched = 0; // what the search and filters keep, before Today | All
    let total = 0;
    vm.displays.forEach((d) => {
      total += d.lineups.length;
      const n = d.lineups.filter((l) => matches(l, d)).length;
      matched += n;
      shown += d.lineups.filter((l) => matches(l, d) && inScope(l, d)).length;
      if (filtering() && !n) return;
      try {
        const ed = effective(d);
        const uiD = Object.assign({}, ui, { order: (ls) => ordered(ls, d), seen: ui.seen });
        uiD.scopeEmpty = st.scope === "today" ? R.scopeEmpty : null;
        html += R.display(vm, ed, uiD);
      } catch (err) {
        console.error("[lineups] could not draw", d.id, err);
        html += `<div class="lu-render-error" role="alert"><b>Could not draw ${R.esc(d.name)}.</b> ${R.esc(err && err.message ? err.message : err)}</div>`;
      }
    });
    const orphans = vm.unassigned || [];
    total += orphans.length;
    const on = orphans.filter((l) => matches(l, null)).length;
    matched += on;
    const onScoped = orphans.filter((l) => matches(l, null) && inScope(l, null)).length;
    shown += onScoped;
    if (orphans.length && onScoped) html += R.unassigned(vm, ui);
    if (!vm.displays.length && !orphans.length) {
      html = host.innerHTML; // the server's empty state
    }
    replaceCards(html);
    followColdThumbs();
    lastRO = barReadOnly();
    root.classList.toggle("bar-ro", lastRO);
    const nm = root.querySelector("[data-lu-nomatch]");
    if (nm) nm.hidden = !(filtering() && matched === 0);
    const count = root.querySelector("[data-lu-count]");
    if (count)
      count.textContent =
        shown === total
          ? `${total} lineup${total === 1 ? "" : "s"}`
          : `${shown} of ${total} lineups`;
    root
      .querySelectorAll("[data-view]")
      .forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === st.view)));
    syncFoldAll();
    syncScope();

    host.querySelectorAll(".lu-row").forEach((r) => {
      const sc = r.querySelector(".lu-strip-scroll");
      if (sc && r.id in scrolls) sc.scrollLeft = scrolls[r.id];
    });
    host.querySelectorAll("[data-tl-scroll]").forEach((x) => {
      if (x.dataset.tlScroll in lanes) x.scrollLeft = lanes[x.dataset.tlScroll];
    });
    if (fk) {
      const el = host.querySelector(fk);
      if (el) el.focus({ preventScroll: true });
    }
    requestAnimationFrame(() => {
      fit();
      host.querySelectorAll(".lu-row:not(.is-folded) .lu-strip-scroll").forEach((sc) => {
        const r = sc.closest(".lu-row");
        if (!(r.id in scrolls)) scrollToNow(sc);
        stripFades(sc);
      });
      // A week grid wider than its card opens scrolled to now.
      host.querySelectorAll("[data-tl-scroll]").forEach((sc) => {
        if (sc.dataset.tlScroll in lanes || sc.scrollWidth <= sc.clientWidth) return;
        const trk = sc.querySelector(".lu-tlg-trk");
        if (!trk) return;
        const x = trk.offsetLeft + (vm.now.minute / DAY_MIN) * trk.clientWidth;
        sc.scrollLeft = Math.max(0, x - sc.clientWidth * 0.55);
      });
    });
  }

  function scrollToNow(sc) {
    const now = sc.querySelector(".lu-step.is-now, .lu-step.is-waiting");
    if (now && sc.scrollWidth > sc.clientWidth) sc.scrollLeft = Math.max(0, now.offsetLeft - 24);
  }
  function stripFades(sc) {
    const max = sc.scrollWidth - sc.clientWidth;
    sc.classList.toggle("fade-l", max > 1 && sc.scrollLeft > 2);
    sc.classList.toggle("fade-r", max > 1 && sc.scrollLeft < max - 2);
  }
  document.addEventListener(
    "scroll",
    (e) => {
      if (e.target.classList && e.target.classList.contains("lu-strip-scroll"))
        stripFades(e.target);
    },
    true
  );

  // Today | All: the toggle's pressed state, plus the container attribute that
  // the server's first-paint rule hands over to (drawing is done in render).
  function syncScope() {
    root.removeAttribute("data-lu-boot-scope");
    root.dataset.luScope = st.scope;
    // The Timeline always shows the whole week, so the toggle waits for Cards.
    root.querySelectorAll("[data-lu-scope-seg]").forEach((g) => {
      g.hidden = st.view === "timeline";
    });
    root
      .querySelectorAll("[data-lu-scope-seg] [data-scope]")
      .forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.scope === st.scope)));
  }

  function syncFoldAll() {
    const fa = root.querySelector("[data-fold-all]");
    if (!fa) return;
    const anyOpen = vm.displays.some(
      (d) => !collapsed.has(d.id) && (st.view !== "cards" || d.lineups.some((l) => !isFolded(l)))
    );
    fa.querySelector("span").textContent = anyOpen ? "Fold all" : "Unfold all";
    fa.dataset.mode = anyOpen ? "fold" : "unfold";
  }

  /* Layout passes that need pixels: hide labels that do not fit, place lane
     thumbnails without overlap (now/next/waiting first), keep edge labels
     inside their track. */
  function fit() {
    try {
      host.querySelectorAll(".lu-strip-scroll").forEach(stripFades);
      host.querySelectorAll(".lu-blk-l").forEach((el) => {
        el.classList.remove("gone");
        if (el.parentElement.clientWidth < 40) el.classList.add("gone");
      });
      host
        .querySelectorAll(".lu-pz")
        .forEach((el) => el.classList.toggle("noicon", el.clientWidth < 24));
      host.querySelectorAll(".lu-blk-side").forEach((el) => {
        el.classList.remove("gone");
        if (el.offsetLeft + el.offsetWidth > el.parentElement.clientWidth - 4)
          el.classList.add("gone");
      });
      host.querySelectorAll(".lu-tlg-trk").forEach(fitWeekRow);
      host.querySelectorAll(".lu-blk").forEach(thinTicks);
      updateFlags();
    } catch (err) {
      console.error("[lineups] layout pass failed", err);
    }
  }

  /* Later advances are hairlines; a fast rotation would turn them into a
     smear, so keep one per TICK_GAP pixels and none under the label (never
     hiding the next one). */
  const TICK_GAP = 6;
  function thinTicks(blk) {
    const ts = blk.querySelectorAll(".lu-tick");
    if (!ts.length) return;
    const w = blk.clientWidth;
    const nx = blk.querySelector(".lu-tick.next");
    const nextX = nx ? (parseFloat(nx.style.left) / 100) * w : null;
    const lab = blk.querySelector(".lu-blk-l:not(.gone)");
    let last = lab && lab.offsetWidth ? lab.offsetLeft + lab.offsetWidth : -Infinity;
    ts.forEach((t) => {
      if (t === nx) return;
      const x = (parseFloat(t.style.left) / 100) * w;
      const thin =
        x - last < TICK_GAP ||
        x < TICK_GAP / 2 ||
        x > w - TICK_GAP / 2 ||
        (nextX !== null && Math.abs(x - nextX) < TICK_GAP);
      t.classList.toggle("thin", thin);
      if (!thin) last = x;
    });
  }

  /* The flag over the next advance ("Update in 5 min", a .lu-nflag child of
     the bar). Its arrow points at the line; the body is centred on it, kept
     inside the bar, moved to the right of the line when the now marker would
     sit under it, and lifted over the marker's knob when that is too close. The text counts down every 30 s (and on every
     redraw) from data-in, the seconds left when the view-model was built;
     once that is past, it hides until the next re-read redraws the bar. */
  function placeFlag(f) {
    const trk = f.parentElement;
    const w = trk.clientWidth;
    const v = (k) => parseFloat(f.style.getPropertyValue(k));
    const x = ((v("--bl") + (v("--tp") * v("--bw")) / 100) / 100) * w;
    const fw = f.offsetWidth;
    f.classList.toggle("gone", fw + 8 > w);
    let left = x - fw / 2;
    const now = trk.querySelector(".lu-nowm");
    const nowX = now ? (parseFloat(now.style.left) / 100) * w : null;
    if (nowX !== null && nowX > left - 6 && nowX < left + fw + 6) left = x - 8;
    left = Math.max(0, Math.min(left, w - fw));
    // Still over the now marker's knob: ride above it.
    f.classList.toggle("lift", nowX !== null && nowX > left - 5 && nowX < left + fw + 5);
    f.style.left = left + "px";
    f.style.setProperty("--ax", Math.max(8, Math.min(x - left, fw - 8)) + "px");
    f.classList.add("placed");
  }
  function updateFlags() {
    const t = Date.now();
    host.querySelectorAll(".lu-nflag").forEach((f) => {
      if (!f.dataset.due) f.dataset.due = String(t + Number(f.dataset.in) * 1000);
      const left = Number(f.dataset.due) - t;
      f.hidden = left < 0;
      if (f.hidden) return;
      const text = R.updateInLabel(Math.floor(left / 60000));
      if (text && f.textContent !== text) f.textContent = text;
      placeFlag(f);
    });
  }
  setInterval(updateFlags, 30000);

  /* Instant tooltip for the advance ticks (data-tip). The native title takes
     about a second; this one sits in <body>, outside every clipping bar, above
     the line (above the flag, for the next one), kept inside the bar and the
     screen, and below the bar when there is no room above. */
  const tip = document.createElement("div");
  tip.className = "lu-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);
  const hideTip = () => {
    tip.hidden = true;
  };
  function showTip(tick) {
    const trk = tick.closest(".lu-trk");
    if (!trk) return;
    tip.textContent = tick.dataset.tip;
    tip.hidden = false;
    const flag = tick.classList.contains("next")
      ? trk.querySelector(".lu-nflag:not([hidden])")
      : null;
    const tr = tick.getBoundingClientRect();
    const br = trk.getBoundingClientRect();
    const above = (flag ? flag.getBoundingClientRect().top : br.top) - 6;
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const lo = Math.max(4, br.left);
    const hi = Math.min(window.innerWidth - 4, br.right);
    const x = Math.max(lo, Math.min(tr.left + tr.width / 2 - w / 2, hi - w));
    tip.style.left = x + "px";
    tip.style.top = (above - h < 4 ? br.bottom + 6 : above - h) + "px";
  }
  host.addEventListener("pointerover", (e) => {
    const tick = e.target.closest && e.target.closest(".lu-tick[data-tip]");
    if (tick && e.pointerType !== "touch") showTip(tick);
  });
  host.addEventListener("pointerout", (e) => {
    const tick = e.target.closest && e.target.closest(".lu-tick");
    if (tick && !tick.contains(e.relatedTarget)) hideTip();
  });
  host.addEventListener("pointerdown", hideTip);
  window.addEventListener("scroll", hideTip, { capture: true, passive: true });
  document.addEventListener("keydown", (e) => e.key === "Escape" && hideTip());

  /* Timeline: an open block's steps sit left-aligned inside it with 10px
     padding and one fixed gap (--lu-wb-gap); when they do not all fit, as many
     as fit show (from NOW on the live block) and a "+N" chip lists them all.
     The next pill flips or shortens to stay inside; hold labels hide when
     their stretch is too short. */
  function placeBlockThumbs(blk) {
    const ts = [...blk.querySelectorAll(".lu-wb-th")];
    blk.querySelectorAll(".lu-wb-more").forEach((x) => x.remove());
    const rep = blk.querySelector(".lu-wb-rep");
    if (!ts.length) return;
    const PAD = 10;
    const GAP = parseFloat(getComputedStyle(blk).getPropertyValue("--lu-wb-gap")) || 6;
    const bw = blk.clientWidth;
    const n = ts.length;
    ts.forEach((t) => {
      t.hidden = false;
    });
    const room = bw - 2 * PAD;
    const ws = ts.map((t) => t.getBoundingClientRect().width);
    const fits = n === 1 || ws.reduce((a, w) => a + w, 0) + (n - 1) * GAP <= room;
    const start = fits
      ? 0
      : Math.max(
          0,
          ts.findIndex((t) => t.dataset.role === "now" || t.dataset.role === "waiting")
        );
    const order = ts.slice(start).concat(ts.slice(0, start));
    const CHIP = 30;
    let x = PAD;
    let m = 0;
    order.forEach((t, j) => {
      const w = ws[ts.indexOf(t)];
      if (fits || (j === m && x + w + CHIP <= PAD + room)) {
        t.style.left = x + "px";
        x += w + GAP;
        m++;
      } else t.hidden = true;
    });
    if (m < n) {
      const chip = document.createElement("span");
      chip.className = "lu-wb-more";
      chip.textContent = `+${n - m}`;
      chip.title = "All steps: " + ts.map((t) => t.title).join(", ");
      chip.style.left = x + "px";
      blk.appendChild(chip);
    }
    if (rep) {
      // "×N updates" follows the one thumb at the same gap; it shortens to
      // "×N", then hides, when the block is too narrow (the thumb's tooltip
      // still lists every refresh).
      rep.hidden = false;
      rep.classList.remove("short");
      rep.style.left = x + "px";
      if (x + rep.offsetWidth > bw - PAD) rep.classList.add("short");
      if (x + rep.offsetWidth > bw - PAD) rep.hidden = true;
    }
    const lab = blk.querySelector(".lu-blk-l");
    const pill = blk.querySelector(".lu-tnext-l");
    if (lab) lab.classList.remove("gone");
    if (pill) {
      const nd = pill.querySelector(".lu-nx-d");
      pill.style.visibility = "";
      pill.style.transform = "";
      if (nd) nd.style.display = "";
      const fits = () => pill.offsetLeft + pill.offsetWidth <= bw - 4;
      if (!fits() && nd) nd.style.display = "none";
      if (!fits()) {
        pill.style.transform = "translateX(calc(-100% - 10px))";
        if (pill.offsetLeft - pill.offsetWidth - 10 < 4) pill.style.visibility = "hidden";
      }
      const pl = pill.style.transform ? pill.offsetLeft - pill.offsetWidth - 10 : pill.offsetLeft;
      if (
        lab &&
        pill.style.visibility !== "hidden" &&
        lab.offsetLeft + lab.offsetWidth > pl - 4 &&
        lab.offsetLeft < pl + pill.offsetWidth
      )
        lab.classList.add("gone");
    }
  }
  function fitWeekRow(trk) {
    trk.querySelectorAll(".lu-blk.open").forEach(placeBlockThumbs);
    trk.querySelectorAll(".lu-wb-hold").forEach((el) => {
      el.classList.remove("gone");
      const room = (Number(el.dataset.hw) / 100) * trk.clientWidth - 16;
      if (trk.classList.contains("xopen") && el.offsetWidth > room) el.classList.add("gone");
    });
  }

  function toggleWeekRow(did, day, open, batch) {
    const set = (st.tlOpen[did] = st.tlOpen[did] || new Set());
    if (open === undefined) open = !set.has(day);
    if (open) set.add(day);
    else set.delete(day);
    const row = host.querySelector(
      `.lu-tlg-row[data-display="${CSS.escape(did)}"][data-day="${day}"]`
    );
    if (row) {
      row.classList.toggle("is-open", open);
      row.querySelector(".lu-tlg-lab").setAttribute("aria-expanded", String(open));
      const trk = row.querySelector(".lu-tlg-trk");
      trk.classList.toggle("xopen", open && !!trk.querySelector(".lu-blk:not(.hold):not(.push)"));
      trk.querySelectorAll(".lu-blk:not(.hold):not(.push)").forEach((b) => {
        b.classList.toggle("open", open);
        b.setAttribute("aria-expanded", String(open));
      });
      if (open) fitWeekRow(trk);
    }
    if (!batch) {
      saveTl();
      syncTlAll(did);
    }
  }
  function syncTlAll(did) {
    const b = host.querySelector(`[data-tl-all="${CSS.escape(did)}"]`);
    if (!b) return;
    const all = (st.tlOpen[did] || new Set()).size === 7;
    b.dataset.mode = all ? "fold" : "unfold";
    b.querySelector("span").textContent = all ? "Fold all" : "Unfold all";
  }

  /* ------------------------------------------------------------ accent */
  // The accent is the app's (server colour or design default). Text on it
  // must reach 4.5:1: nudge it a step at a time toward black under light
  // text, toward white under dark text. A warm accent (red, orange, amber)
  // flags .acc-warm so Quiet hours and queued dots never read as the accent.
  const probe = document.createElement("i");
  probe.style.cssText = "position:absolute;width:0;height:0;overflow:hidden";
  function rgbOf(cssColor) {
    probe.style.color = "";
    probe.style.color = cssColor;
    root.appendChild(probe);
    const c = getComputedStyle(probe).color;
    probe.remove();
    let m = c.match(/rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)/);
    if (m) return [+m[1] / 255, +m[2] / 255, +m[3] / 255];
    m = c.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
    if (m) return [+m[1], +m[2], +m[3]];
    return null;
  }
  const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const lum = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
  const contrast = (a, b) => {
    const x = lum(a);
    const y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const hex = (rgb) =>
    "#" +
    rgb
      .map((c) =>
        Math.round(Math.min(1, Math.max(0, c)) * 255)
          .toString(16)
          .padStart(2, "0")
      )
      .join("");
  function hueOf(rgb) {
    const [r, g, b] = rgb;
    const mx = Math.max(r, g, b);
    const mn = Math.min(r, g, b);
    const dl = mx - mn;
    if (dl < 0.08) return -1; // grey / black / white: no hue
    const hu = mx === r ? ((g - b) / dl) % 6 : mx === g ? (b - r) / dl + 2 : (r - g) / dl + 4;
    return (hu * 60 + 360) % 360;
  }
  function applyAccent() {
    root.style.removeProperty("--lu-acc");
    const acc = rgbOf("var(--lu-acc)");
    const fg = rgbOf("var(--lu-acc-fg)");
    if (!acc || !fg) return;
    let c = acc;
    const target = lum(fg) > 0.5 ? 0 : 1;
    for (let i = 0; i < 30 && contrast(c, fg) < 4.5; i++) c = c.map((x) => x + (target - x) * 0.04);
    if (c !== acc) root.style.setProperty("--lu-acc", hex(c));
    const hu = hueOf(c);
    root.classList.toggle("acc-warm", hu >= 0 && (hu < 65 || hu > 340));
  }

  /* ------------------------------------------------------------ sheets */
  // On phones the day picker and the items popover open as bottom sheets
  // (CSS): a scrim closes them on tap and the page behind stops scrolling.
  const sheetMQ = window.matchMedia(
    "(max-width: 600px), (pointer: coarse) and (max-height: 500px)"
  );
  let lockedScroll = null;
  let modal = null;
  function activateModal(pop, opener) {
    const inert = [];
    for (let node = pop; node && node !== document.body; node = node.parentElement) {
      [...node.parentElement.children].forEach((sibling) => {
        if (sibling === node || sibling.hasAttribute("data-sheet-scrim")) return;
        inert.push([sibling, sibling.inert]);
        sibling.inert = true;
      });
    }
    modal = { pop, opener, inert };
  }
  document.addEventListener(
    "keydown",
    (e) => {
      if (!modal || e.key !== "Tab") return;
      const items = [
        ...modal.pop.querySelectorAll("button:not(:disabled), a[href], input, [tabindex='0']"),
      ].filter((el) => el.getClientRects().length);
      if (!items.length) return;
      const first = items[0],
        last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    },
    true
  );
  function releaseModal() {
    if (!modal || modal.pop.isConnected) return;
    const { opener, inert } = modal;
    modal = null;
    inert.forEach(([el, was]) => {
      el.inert = was;
    });
    if (opener.isConnected) opener.focus({ preventScroll: true });
  }
  function openSheet() {
    if (!sheetMQ.matches) return;
    if (!root.querySelector("[data-sheet-scrim]"))
      root.insertAdjacentHTML(
        "beforeend",
        '<div class="lu-sheet-scrim" data-sheet-scrim aria-hidden="true"></div>'
      );
    if (lockedScroll === null) {
      lockedScroll = [document.documentElement.style.overflow, document.body.style.overflow];
      document.documentElement.style.overflow = "hidden";
      document.body.style.overflow = "hidden";
    }
  }
  function closeSheet() {
    releaseModal();
    if (root.querySelector(".lu-dp-pop, .lu-peek")) return;
    root.querySelectorAll("[data-sheet-scrim]").forEach((x) => x.remove());
    if (lockedScroll !== null) {
      document.documentElement.style.overflow = lockedScroll[0];
      document.body.style.overflow = lockedScroll[1];
      lockedScroll = null;
    }
  }

  /* ------------------------------------------------------------ day picker */
  function closeDayPop() {
    root.querySelectorAll(".lu-dp-pop").forEach((p) => p.remove());
    root
      .querySelectorAll('.lu-dp-btn[aria-expanded="true"]')
      .forEach((b) => b.setAttribute("aria-expanded", "false"));
    closeSheet();
  }
  function openDayPop(btn) {
    const was = btn.getAttribute("aria-expanded") === "true";
    closeDayPop();
    closePeek();
    if (was) return;
    const d = displayById(btn.dataset.dpick);
    if (!d) return;
    const where = btn.closest(".lu-dpick");
    where.insertAdjacentHTML("beforeend", R.dayPop(d, dayOf(d), vm.now.weekday));
    const pop = where.lastElementChild;
    const card = btn.closest(".lu-ds-card").getBoundingClientRect();
    const r = pop.getBoundingClientRect();
    if (r.left < card.left + 6) pop.style.transform = `translateX(${card.left + 6 - r.left}px)`;
    btn.setAttribute("aria-expanded", "true");
    openSheet();
    activateModal(pop, btn);
    (pop.querySelector("[data-sel]") || pop.querySelector(".lu-dp-day")).focus();
    pop.addEventListener("keydown", (e) => {
      const days = [...pop.querySelectorAll(".lu-dp-day")];
      const i = days.indexOf(document.activeElement);
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        days[(Math.max(i, 0) + (e.key === "ArrowRight" ? 1 : 6)) % 7].focus();
      } else if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        days[e.key === "Home" ? 0 : 6].focus();
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        closeDayPop();
        btn.focus();
      }
    });
  }

  /* ------------------------------------------------------------ peek */
  function closePeek() {
    root.querySelectorAll(".lu-peek").forEach((p) => p.remove());
    root
      .querySelectorAll('[data-peek][aria-expanded="true"]')
      .forEach((b) => b.setAttribute("aria-expanded", "false"));
    closeSheet();
  }
  function openPeek(btn) {
    const was = btn.getAttribute("aria-expanded") === "true";
    closePeek();
    closeDayPop();
    if (was) return;
    const [lid, i] = btn.dataset.peek.split(":");
    const found = lineupOf(lid, displayOf(btn));
    if (!found) return;
    const s = found.l.steps[Number(i)];
    const d = found.d || { id: "", name: "", orientation: "l" };
    const body = btn.closest(".lu-row-body");
    body.insertAdjacentHTML("beforeend", R.peek(d, found.l, s));
    const pop = body.lastElementChild;
    const br = body.getBoundingClientRect();
    const tr = btn.closest(".lu-tile").getBoundingClientRect();
    pop.style.left = Math.max(0, Math.min(tr.left - br.left, br.width - pop.offsetWidth)) + "px";
    pop.style.top = tr.bottom - br.top + 8 + "px";
    btn.setAttribute("aria-expanded", "true");
    openSheet();
    activateModal(pop, btn);
    pop.querySelector("[data-peek-close]").focus({ preventScroll: true });
  }

  /* ------------------------------------------------------------ push now */
  let lastPointer = "mouse";
  let pressX = 0;
  let pressY = 0;
  document.addEventListener(
    "pointerdown",
    (e) => {
      lastPointer = e.pointerType || "mouse";
      pressX = e.clientX;
      pressY = e.clientY;
    },
    true
  );
  const travelled = (e) =>
    e.detail > 0 && (Math.abs(e.clientX - pressX) > 4 || Math.abs(e.clientY - pressY) > 4);
  const say = (msg) => {
    const live = root.querySelector("[data-lu-live]");
    if (live) live.textContent = msg;
  };
  async function pushNow(btn) {
    const tile = btn.closest(".lu-tile, .lu-pk-item");
    // Touch: the first tap only reveals the button.
    if (
      lastPointer === "touch" &&
      tile &&
      tile.classList.contains("lu-tile") &&
      !tile.classList.contains("reveal")
    ) {
      root.querySelectorAll(".lu-tile.reveal").forEach((x) => x.classList.remove("reveal"));
      tile.classList.add("reveal");
      return;
    }
    const [lid, i] = btn.dataset.push.split(":");
    const found = lineupOf(lid, displayOf(btn));
    if (!found || !tile) return;
    const step = found.l.steps[Number(i)];
    const msgEl = tile.querySelector(".lu-pushed-msg");
    btn.disabled = true;
    try {
      const res = await API.pushStep(found.l, step, found.d ? found.d.id : "");
      const lands = (res && res.lands) || (found.d ? found.d.push_lands : "");
      // A sleeping frame only fetches it at its next check-in: the tile says
      // "Queued · picks up ~3:48 PM", the live region the whole sentence.
      const waits = ((res && res.delivery) || []).filter((n) => n.mode !== "live");
      const mine = waits.find((n) => found.d && n.device_id === found.d.id) || waits[0];
      const msg = mine ? mine.short : (res && res.message) || `Pushed${lands ? " · " + lands : ""}`;
      tile.classList.remove("push-failed");
      tile.classList.toggle("push-queued", !!mine);
      tile.classList.add("pushed");
      if (msgEl) msgEl.textContent = msg;
      const fresh = res && res.fresh && step.composition_digest ? res.message : "";
      say(mine ? [fresh, ...waits.map((n) => n.text)].filter(Boolean).join(" ") : msg);
      scheduleRefresh(2500);
    } catch (err) {
      tile.classList.remove("push-queued");
      tile.classList.add("pushed", "push-failed");
      if (msgEl) msgEl.textContent = "Could not push";
      say(`Could not push ${step.name}: ${err.message}`);
    } finally {
      btn.disabled = false;
      clearTimeout(tile._luPushT);
      // A queued note stays up longer: it carries a time to read.
      tile._luPushT = setTimeout(
        () => tile.classList.remove("pushed", "push-failed", "push-queued", "reveal"),
        tile.classList.contains("push-queued") ? 6000 : 2400
      );
    }
  }

  // A step whose dashboard is bound to another display can never show here.
  // The button is the consent: it adds this display to the dashboard's
  // bindings (its other display keeps working) and the page redraws.
  async function bindStep(btn) {
    const [lid, i] = btn.dataset.bind.split(":");
    const found = lineupOf(lid, displayOf(btn));
    if (!found || !found.d) return;
    const step = found.l.steps[Number(i)];
    btn.disabled = true;
    try {
      await API.bindPages(found.l, [step.dashboard_id], found.d.id);
      say(`${step.name} is now bound to ${found.d.name}`);
      scheduleRefresh(0);
    } catch (err) {
      btn.disabled = false;
      say(`Could not bind ${step.name}: ${err.message}`);
    }
  }

  /* ------------------------------------------------------------ day tiles */
  function toggleDay(btn) {
    const [lid, i] = btn.dataset.toggleDay.split(":");
    const day = Number(i);
    const found = lineupOf(lid, displayOf(btn));
    if (!found || !found.d) return;
    const d = found.d;
    const ed = effective(d);
    const l = ed.lineups.find((x) => x.id === lid);
    const days = l.days.slice();
    if (days[day]) {
      if (days.filter(Boolean).length === 1) {
        st.flash[d.id] = { msg: `${l.name} needs at least one day.` };
        render();
        return;
      }
      days[day] = false;
    } else {
      const [s, e] = winOf(l);
      const one = [0, 0, 0, 0, 0, 0, 0].map((_, j) => j === day);
      const c = clash(ed, l, s, e, one);
      if (c) {
        st.flash[d.id] = { msg: `${R.DAYS[day]} clashes with ${c.o.name} (${c.o.window.label}).` };
        render();
        return;
      }
      days[day] = true;
    }
    const p = (st.pending[d.id] && st.pending[d.id][lid]) || origOf(found.l);
    applyEdit(d, found.l, { start_min: p.start_min, end_min: p.end_min, days });
  }

  /* ------------------------------------------------------------ window editor */
  const T = (v) => {
    const m = /^(\d{1,2}):(\d{2})/.exec(v || "");
    return m ? Number(m[1]) * 60 + Number(m[2]) : NaN;
  };
  function openWinEdit(btn) {
    const found = lineupOf(btn.dataset.winEdit, displayOf(btn));
    if (!found || !found.d) return;
    const d = found.d;
    const l = effLineup(d, found.l.id);
    const point = l.window.is_point;
    const wrap = document.createElement("span");
    wrap.className = "lu-win-edit";
    const endVal = l.window.end === "24:00" ? "23:59" : l.window.end;
    wrap.innerHTML =
      `<input type="time" step="900" value="${l.window.start}" aria-label="Start">` +
      (point
        ? ""
        : `<span>–</span><input type="time" step="900" value="${endVal}" aria-label="End">`) +
      `<button type="button" class="lu-ibtn sm" data-win-ok title="Apply (Enter)" aria-label="Apply">${R.ico("check")}</button><span class="lu-win-err" role="alert"></span>`;
    btn.replaceWith(wrap);
    wrap.querySelector("input").focus();
    const apply = () => {
      const ins = wrap.querySelectorAll("input");
      const s = T(ins[0].value);
      let e = point ? s : T(ins[1].value);
      if (!point && ins[1].value === "23:59") e = DAY_MIN;
      const err = wrap.querySelector(".lu-win-err");
      if (!Number.isFinite(s) || !Number.isFinite(e)) {
        err.textContent = "Enter a time";
        return;
      }
      if (!point && e - s < SNAP) {
        err.textContent = "End must be after start";
        return;
      }
      const c = clash(effective(d), l, s, e, l.days);
      if (c) {
        err.textContent = `Overlaps ${c.o.name}`;
        return;
      }
      commit(d, l.id, s, e);
    };
    wrap.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        apply();
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        render();
      }
    });
    wrap.querySelector("[data-win-ok]").addEventListener("click", apply);
  }

  /* ------------------------------------------------------------ bar drag */
  let drag = null;
  let justDragged = false;
  // Phones and narrow cards get a read-only bar (as in the mockup); edits go
  // through the time inputs and day tiles. Tablets drag with any pointer.
  const phoneMQ = window.matchMedia(
    "(pointer: coarse) and (max-width: 600px), (pointer: coarse) and (max-height: 500px)"
  );
  const barReadOnly = () => phoneMQ.matches || host.clientWidth <= 602;
  const tipText = (l, p, grid) => {
    const t = l.window.is_point ? `at ${R.clock(p.s)}` : R.clockRange(p.s, p.e);
    const days = l.days.map((on, i) => (on ? R.DAYS[i] : "")).filter(Boolean);
    const applies =
      grid && days.length > 1
        ? `<span class="lu-tip-stop">applies to ${days.join(", ")}</span>`
        : "";
    return `<b>${t}</b>${applies}${p.stop ? `<span class="lu-tip-stop">stops at ${R.esc(p.stop.name)}</span>` : ""}${p.pause ? `<span class="lu-tip-warn">${R.ico("pause")}Quiet hours</span>` : ""}`;
  };
  document.addEventListener("pointerdown", (e) => {
    if (drag) endDrag(false); // a lost pointerup never leaves a drag stuck
    const blk = e.target.closest && e.target.closest(".lu .lu-blk[data-edit]");
    if (!blk || e.button !== 0) return;
    const trk = blk.closest(".lu-trk");
    if (!trk || barReadOnly() || trk.clientWidth <= 0) return;
    const d = displayById(blk.dataset.display);
    const ed = d && effective(d);
    const l = ed && ed.lineups.find((x) => x.id === blk.dataset.lineup);
    if (!l || !l.window) return;
    const [os, oe] = winOf(l);
    const h = e.target.closest(".lu-h");
    const mode = h ? h.dataset.h : "move";
    trk.insertAdjacentHTML("beforeend", '<span class="lu-drag-tip" role="status"></span>');
    const tip = trk.lastElementChild;
    const grid = trk.classList.contains("tl");
    const ghosts = [];
    if (grid) {
      trk.classList.add("dragging");
      host.querySelectorAll(`.lu-tlg-trk`).forEach((other) => {
        if (other === trk || !other.closest(`.lu-ds[data-display="${CSS.escape(d.id)}"]`)) return;
        if (!l.days[Number(other.dataset.day)]) return;
        other.insertAdjacentHTML(
          "beforeend",
          `<i class="lu-ghost" style="left:${(os / DAY_MIN) * 100}%;width:${((oe - os) / DAY_MIN) * 100}%" title="Applies here too"></i>`
        );
        ghosts.push(other.lastElementChild);
      });
    }
    drag = {
      grid,
      ghosts,
      blk,
      trk,
      d,
      ed,
      l,
      os,
      oe,
      mode,
      x0: e.clientX,
      W: trk.clientWidth,
      moved: false,
      left: blk.style.left,
      width: blk.style.width,
      tip,
      pid: e.pointerId,
    };
    try {
      blk.setPointerCapture(e.pointerId);
    } catch {
      /* the document listeners still drive the drag */
    }
    blk.classList.add("is-drag");
    e.preventDefault();
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (!drag.blk.isConnected) {
      endDrag(false);
      return;
    }
    let dm = Math.round((((e.clientX - drag.x0) / drag.W) * DAY_MIN) / SNAP) * SNAP;
    if (!Number.isFinite(dm)) dm = 0;
    if (dm) drag.moved = true;
    const p = proposal(drag.ed, drag.l, drag.mode, drag.os, drag.oe, dm);
    drag.p = p;
    const pc = (m) => (m / DAY_MIN) * 100;
    drag.blk.style.left = pc(p.s) + "%";
    if (!drag.l.window.is_point) drag.blk.style.width = pc(p.e - p.s) + "%";
    drag.blk.classList.toggle("is-stopped", !!p.stop);
    drag.tip.innerHTML = tipText(drag.l, p, drag.grid);
    if (drag.grid) {
      // The window belongs to the lineup: ghost the change on its other days.
      drag.ghosts.forEach((g) => {
        g.style.left = pc(p.s) + "%";
        g.style.width = drag.l.window.is_point ? "10px" : pc(p.e - p.s) + "%";
      });
    }
    const mid = ((pc(p.s) + pc(p.e)) / 2 / 100) * drag.W;
    const w = drag.tip.offsetWidth;
    drag.tip.style.left = Math.max(0, Math.min(drag.W - w, mid - w / 2)) + "px";
  });
  function endDrag(apply) {
    const g = drag;
    drag = null;
    if (!g) return;
    if (g.tip) g.tip.remove();
    (g.ghosts || []).forEach((x) => x.remove());
    g.trk.classList.remove("dragging");
    g.blk.classList.remove("is-drag", "is-stopped");
    try {
      if (g.blk.hasPointerCapture && g.blk.hasPointerCapture(g.pid))
        g.blk.releasePointerCapture(g.pid);
    } catch {
      /* already released */
    }
    if (!g.moved) return;
    justDragged = true;
    setTimeout(() => {
      justDragged = false;
    }, 0);
    if (!apply || !g.p || (g.p.s === g.os && g.p.e === g.oe) || !g.blk.isConnected) {
      g.blk.style.left = g.left;
      g.blk.style.width = g.width;
      return;
    }
    commit(g.d, g.l.id, g.p.s, g.p.e);
  }
  document.addEventListener("pointerup", () => drag && endDrag(true));
  document.addEventListener("pointercancel", () => drag && endDrag(false));
  window.addEventListener("blur", () => drag && endDrag(false));
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Escape" && drag) {
        drag.moved = true;
        endDrag(false);
        e.stopPropagation();
        return;
      }
      const gridBlk = e.target.closest && e.target.closest(".lu .lu-tlg-trk .lu-blk[tabindex]");
      if (gridBlk && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        const row = gridBlk.closest(".lu-tlg-row");
        toggleWeekRow(row.dataset.display, Number(row.dataset.day));
        return;
      }
      const blk = e.target.closest && e.target.closest(".lu .lu-blk[data-edit]");
      if (!blk) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openRow(blk.dataset.lineup, blk.dataset.display);
        return;
      }
      if (!["ArrowLeft", "ArrowRight"].includes(e.key) || barReadOnly()) return;
      e.preventDefault();
      const d = displayById(blk.dataset.display);
      const ed = effective(d);
      const l = ed.lineups.find((x) => x.id === blk.dataset.lineup);
      const [os, oe] = winOf(l);
      const dm = e.key === "ArrowLeft" ? -SNAP : SNAP;
      const mode = e.shiftKey && !l.window.is_point ? (e.altKey ? "l" : "r") : "move";
      const p = proposal(ed, l, mode, os, oe, dm);
      if (p.s === os && p.e === oe) return;
      commit(d, l.id, p.s, p.e);
    },
    true
  );

  /* ------------------------------------------------------------ open a row */
  function openRow(id, did) {
    const found = lineupOf(id, did);
    if (!found) return;
    st.view = "cards";
    foldChoice[id] = false;
    saveFolds();
    if (found.d) collapsed.delete(found.d.id);
    saveCollapsed();
    render();
    requestAnimationFrame(() => {
      const r = host.querySelector(
        `.lu-row[data-lineup="${CSS.escape(id)}"][data-display="${CSS.escape(found.d?.id || "")}"]`
      );
      if (!r) return;
      r.scrollIntoView({ block: "center", behavior: reduceMotion() ? "auto" : "smooth" });
      r.querySelector(".lu-fold").focus({ preventScroll: true });
    });
  }

  /* ------------------------------------------------------------ clicks */
  function toggleRow(btn) {
    const id = btn.dataset.fold;
    const row = btn.closest(".lu-row");
    const fold = !row.classList.contains("is-folded");
    foldChoice[id] = fold;
    saveFolds();
    row.classList.toggle("is-folded", fold);
    row.classList.toggle("just-opened", !fold);
    btn.setAttribute("aria-expanded", String(!fold));
    if (!fold) {
      const sc = row.querySelector(".lu-strip-scroll");
      if (sc) {
        scrollToNow(sc);
        stripFades(sc);
      }
    }
    syncFoldAll();
  }

  host.addEventListener("submit", (e) => {
    const form = e.target;
    if (form.matches(".lu-push-form")) {
      e.preventDefault();
      const b = form.querySelector("[data-push]");
      if (b) pushNow(b);
      return;
    }
    const q = form.getAttribute("data-confirm");
    if (q && !window.confirm(q)) e.preventDefault();
  });

  document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) {
      closePeek();
      closeDayPop();
      return;
    }
    const t = e.target.closest("button, a, summary");
    if (!e.target.closest(".lu-peek, [data-peek]")) closePeek();
    if (!e.target.closest(".lu-dpick")) closeDayPop();
    root.querySelectorAll("details.lu-more[open]").forEach((m) => {
      if (!m.contains(e.target)) m.open = false;
    });
    if (!e.target.closest(".lu-tile"))
      root.querySelectorAll(".lu-tile.reveal").forEach((x) => x.classList.remove("reveal"));

    // Timeline: a day's label, a block, or anywhere in its row opens or
    // folds the whole row; never at the end of a drag or a press that
    // travelled (a slipped click on Firefox).
    const wr = e.target.closest(".lu-tlg-row");
    if (wr && !e.target.closest(".lu-wb-more")) {
      if (!justDragged && !travelled(e)) toggleWeekRow(wr.dataset.display, Number(wr.dataset.day));
      return;
    }
    // A block on the day bar opens its lineup.
    const blk = e.target.closest(".lu-blk:not(.hold)");
    if (blk && !justDragged) {
      openRow(blk.dataset.lineup, blk.dataset.display);
      return;
    }

    if (!t) {
      // The whole row header / display header folds, except its own
      // controls. Ignore text selections and the end of a drag.
      if (justDragged || String(window.getSelection ? window.getSelection() : "") !== "") return;
      if (
        e.target.closest(
          "input, select, textarea, label, .lu-win-edit, .lu-days, .lu-more, .lu-peek, .lu-today, .lu-row-actions"
        )
      )
        return;
      const rh = e.target.closest(".lu-row-head");
      const dh = e.target.closest(".lu-ds-hdr");
      const btn = rh
        ? rh.querySelector("[data-fold]")
        : dh
          ? dh.querySelector("[data-collapse]")
          : null;
      if (btn) btn.click();
      return;
    }
    const ds = t.dataset;
    if (ds.bind) {
      bindStep(t);
      return;
    }
    if (ds.view) {
      st.view = ds.view === "timeline" ? "timeline" : "cards";
      store.set(VIEW_KEY, st.view);
      render();
    } else if (ds.scope && t.matches("[data-scope]")) {
      st.scope = ds.scope === "today" ? "today" : "all";
      store.set(SCOPE_KEY, st.scope);
      const fromEmpty = !!t.closest(".lu-scope-empty");
      render();
      say(
        st.scope === "today"
          ? "Showing only the lineups that run on the day shown."
          : "Showing all lineups."
      );
      if (fromEmpty) {
        const b = root.querySelector('[data-lu-scope-seg] [data-scope="all"]');
        if (b) b.focus({ preventScroll: true });
      }
    } else if (ds.tlAll !== undefined && t.matches("[data-tl-all]")) {
      const open = ds.mode !== "fold";
      for (let i = 0; i < 7; i++) toggleWeekRow(ds.tlAll, i, open, true);
      saveTl();
      syncTlAll(ds.tlAll);
    } else if (ds.fold) {
      toggleRow(t);
    } else if (t.matches("[data-fold-all]")) {
      const fold = ds.mode === "fold";
      vm.displays.forEach((d) => {
        if (fold) collapsed.add(d.id);
        else collapsed.delete(d.id);
        d.lineups.forEach((l) => {
          foldChoice[l.id] = fold;
        });
      });
      (vm.unassigned || []).forEach((l) => {
        foldChoice[l.id] = fold;
      });
      saveFolds();
      saveCollapsed();
      render();
    } else if (ds.collapse !== undefined && t.matches("[data-collapse]")) {
      const id = ds.collapse;
      if (collapsed.has(id)) collapsed.delete(id);
      else collapsed.add(id);
      saveCollapsed();
      render();
      const sec = host.querySelector(`.lu-ds[data-display="${CSS.escape(id)}"]`);
      if (sec && !collapsed.has(id)) sec.classList.add("just-opened");
      const b = host.querySelector(`[data-collapse="${CSS.escape(id)}"]`);
      if (b) b.focus({ preventScroll: true });
    } else if (ds.dpClose !== undefined && t.matches("[data-dp-close]")) {
      const b = root.querySelector('.lu-dp-btn[aria-expanded="true"]');
      closeDayPop();
      if (b) b.focus();
    } else if (ds.dpick) {
      openDayPop(t);
    } else if (
      ds.day !== undefined &&
      ds.display !== undefined &&
      t.matches("[data-day][data-display]")
    ) {
      const did = ds.display;
      const fromPick = !!t.closest(".lu-dpick");
      st.day[did] = Number(ds.day);
      // In the Timeline the picked day opens in the week grid.
      if (st.view === "timeline")
        (st.tlOpen[did] = st.tlOpen[did] || new Set()).add(Number(ds.day));
      render();
      if (fromPick) {
        const g = host.querySelector(`.lu-ds[data-display="${CSS.escape(did)}"] .lu-dp-btn`);
        if (g) g.focus({ preventScroll: true });
      }
    } else if (ds.push && t.matches(".lu-push-mini")) {
      pushNow(t);
    } else if (ds.toggleDay) {
      toggleDay(t);
    } else if (ds.winEdit) {
      openWinEdit(t);
    } else if (ds.undo !== undefined && t.matches("[data-undo]")) {
      const ids = Object.keys(st.pending[ds.undo] || {});
      Object.keys(st.pending).forEach((did) => {
        ids.forEach((id) => delete st.pending[did][id]);
        if (!Object.keys(st.pending[did]).length) delete st.pending[did];
      });
      st.flash[ds.undo] = { msg: "Changes undone." };
      render();
    } else if (ds.save !== undefined && t.matches("[data-save]")) {
      save(ds.save, t);
    } else if (ds.peek) {
      openPeek(t);
    } else if (ds.peekClose !== undefined) {
      const b = root.querySelector('[data-peek][aria-expanded="true"]');
      closePeek();
      if (b) b.focus();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const openRowEl =
      document.activeElement &&
      document.activeElement.closest &&
      document.activeElement.closest(".lu-tlg-row.is-open");
    if (openRowEl && !drag) {
      toggleWeekRow(openRowEl.dataset.display, Number(openRowEl.dataset.day), false);
      const lab = openRowEl.querySelector(".lu-tlg-lab");
      if (lab) lab.focus();
    }
    if (root.querySelector(".lu-dp-pop")) {
      const b = root.querySelector('.lu-dp-btn[aria-expanded="true"]');
      closeDayPop();
      if (b) b.focus();
    }
    if (root.querySelector(".lu-peek")) {
      const b = root.querySelector('[data-peek][aria-expanded="true"]');
      closePeek();
      if (b) b.focus();
    }
    root.querySelectorAll("details.lu-more[open]").forEach((m) => {
      m.open = false;
      m.querySelector("summary").focus();
    });
  });

  /* Hover links a lineup across its bar block, week grid and row. */
  let linked = [];
  host.addEventListener("mouseover", (e) => {
    const h = e.target.closest("[data-lineup][data-display]");
    linked.forEach((el) => el.classList.remove("is-linked"));
    linked = [];
    if (!h) return;
    linked = [
      ...host.querySelectorAll(
        `[data-display="${CSS.escape(h.dataset.display)}"][data-lineup="${CSS.escape(h.dataset.lineup)}"]`
      ),
    ].filter((el) => el.matches(".lu-row, .lu-blk:not(.hold), .lu-wb:not(.hold)"));
    linked.forEach((el) => el.classList.add("is-linked"));
  });

  /* ------------------------------------------------------------ toolbar */
  const tb = root.querySelector("[data-lu-toolbar]");
  if (tb) {
    tb.hidden = false;
    const q = tb.querySelector("[data-lu-q]");
    if (q)
      q.addEventListener("input", () => {
        st.q = q.value.trim();
        render();
      });
    const keys = { display: "fDisplay", kind: "fKind", state: "fState", sort: "sort" };
    tb.querySelectorAll("[data-lu-filter]").forEach((sel) => {
      sel.addEventListener("change", () => {
        st[keys[sel.dataset.luFilter]] = sel.value;
        render();
      });
    });
  }

  /* ------------------------------------------------------------ save */
  async function save(did, btn) {
    const d = displayById(did);
    const pend = st.pending[did];
    if (!d || !pend || Object.values(st.saving).some(Boolean)) return;
    const sent = JSON.parse(JSON.stringify(pend));
    generation++;
    const edits = Object.entries(sent).map(([id, p]) => {
      const l = d.lineups.find((x) => x.id === id);
      return {
        id,
        start: R.hhmm(p.start_min),
        end: l && l.window && l.window.is_point ? null : R.endHhmm(p.end_min),
        days: p.days.map((on, i) => (on ? i : -1)).filter((i) => i >= 0),
      };
    });
    st.saving[did] = true;
    btn.setAttribute("aria-busy", "true");
    btn.textContent = "Saving…";
    try {
      const res = await API.saveEdits(edits);
      // The saved state, drawn now: the server's fresh copy of the touched
      // displays when it sent one, else our own preview of the edits.
      const fresh = res && Array.isArray(res.displays) ? res.displays : [];
      vm.displays = vm.displays.map((x) => {
        const updated = fresh.find((f) => f.id === x.id);
        const preview = JSON.parse(
          JSON.stringify(updated || R.withPending(x, sent, vm.now.weekday, vm.now.minute))
        );
        preview.lineups.forEach((l) => {
          delete l.pending;
          delete l.pending_days;
        });
        preview.week.forEach((w) => w.segments.forEach((sg) => delete sg.pending));
        return preview;
      });
      // Keep edits made while the request was in flight, including edits on
      // another display copy of a shared lineup.
      Object.keys(st.pending).forEach((key) => {
        Object.keys(sent).forEach((id) => {
          if (JSON.stringify(st.pending[key][id]) === JSON.stringify(sent[id]))
            delete st.pending[key][id];
        });
        if (!Object.keys(st.pending[key]).length) delete st.pending[key];
      });
      Object.keys(st.rowErr).forEach((id) => pend[id] && delete st.rowErr[id]);
      const notes = [edits.length > 1 ? `${edits.length} changes saved.` : "Saved."];
      if (res && Array.isArray(res.warnings)) notes.push(...res.warnings);
      if (res && res.warning) notes.push(res.warning);
      st.flash[did] = { msg: notes.join(" "), err: !!(res && res.warning) };
      say(`Saved ${edits.length} change${edits.length > 1 ? "s" : ""} to ${d.name}.`);
      if (!fresh.length) scheduleRefresh(300);
    } catch (err) {
      const id = err.data && err.data.id;
      if (id) st.rowErr[id] = err.message;
      st.flash[did] = { msg: `Not saved: ${err.message}`, err: true };
      say(`Not saved: ${err.message}`);
    } finally {
      st.saving[did] = false;
      render();
    }
  }

  /* ------------------------------------------------------------ refresh */
  // Re-read the view-model when the scheduler fires something (SSE, debounced)
  // and on a slow poll. Folds, scroll, the day shown and unsaved edits are
  // ours, not the server's, so they survive the redraw. Skipped while the
  // user is mid-gesture so nothing closes under the pointer.
  let generation = 0;
  let refreshDirty = false;
  let refreshing = false;
  let refreshT = null;
  const busy = () => {
    const wizard = document.getElementById("timed-wizard");
    return (
      document.hidden ||
      !!drag ||
      (wizard && wizard.open) ||
      // A tile's "Queued · picks up ~3:48 PM" note is read before a redraw drops it.
      !!root.querySelector(
        ".lu-peek, .lu-dp-pop, details.lu-more[open], .lu-win-edit, .lu-tile.push-queued"
      ) ||
      Object.values(st.saving).some(Boolean)
    );
  };
  async function refresh() {
    if (refreshing || busy()) {
      refreshDirty = true;
      scheduleRefresh(250);
      return;
    }
    refreshDirty = false;
    const started = generation;
    refreshing = true;
    try {
      const next = await API.view(params.has("day") ? params.get("day") : undefined);
      if (started !== generation || busy()) {
        refreshDirty = true;
      } else if (next && Array.isArray(next.displays)) {
        vm = next;
        R.setTimeCycle(vm.time_cycle);
        // Drop edits for lineups that no longer exist.
        Object.keys(st.pending).forEach((did) => {
          const d = displayById(did);
          if (!d) delete st.pending[did];
          else
            Object.keys(st.pending[did]).forEach(
              (id) => d.lineups.some((l) => l.id === id) || delete st.pending[did][id]
            );
        });
        render();
      }
    } catch {
      /* offline or the endpoint is missing: keep what is drawn */
    } finally {
      refreshing = false;
      if (refreshDirty) scheduleRefresh(250);
    }
  }
  function scheduleRefresh(ms) {
    clearTimeout(refreshT);
    refreshT = setTimeout(refresh, ms);
  }
  setInterval(refresh, 60000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleRefresh(0);
  });
  const eventsUrl = root.getAttribute("data-events-url");
  if (eventsUrl && typeof EventSource === "function") {
    try {
      const es = new EventSource(eventsUrl);
      es.addEventListener("log", () => scheduleRefresh(1500));
    } catch {
      /* no live events; the poll still runs */
    }
  }

  // Cold dashboards have no img at all. An evicted artifact may disappear
  // between view generation and loading; keep the same cheap initial then.
  root.addEventListener(
    "error",
    (e) => {
      const img = e.target;
      if (!(img instanceof HTMLImageElement) || !img.closest(".lu-thumb")) return;
      const placeholder = document.createElement("span");
      placeholder.className = "lu-thumb-ph";
      placeholder.setAttribute("aria-hidden", "true");
      placeholder.textContent = (img.parentElement.title || "?").slice(0, 1);
      img.replaceWith(placeholder);
      followColdThumbs();
    },
    true
  );

  /* ------------------------------------------------------------ keyboard */
  // While the on-screen keyboard is up, the sticky Save bar steps back into
  // the card so it never sits over the field being typed in.
  const vv = window.visualViewport;
  if (vv) {
    const onVV = () =>
      root.classList.toggle(
        "kb-open",
        document.activeElement?.matches("input, textarea, [contenteditable=true]") &&
          window.innerHeight - vv.height > 150
      );
    root.addEventListener("focusin", onVV);
    root.addEventListener("focusout", () => requestAnimationFrame(onVV));
    vv.addEventListener("resize", onVV);
  }
  root.addEventListener("focusin", (e) => {
    if (e.target.matches && e.target.matches(".lu-win-edit input") && phoneMQ.matches) {
      setTimeout(() => e.target.scrollIntoView({ block: "center", behavior: "auto" }), 250);
    }
  });

  /* ------------------------------------------------------------ start */
  window.addEventListener("error", (e) => {
    if (!e.filename || !/lineups(_render|_api)?\.js/.test(e.filename)) return;
    if (host.querySelector(".lu-render-error")) return;
    host.insertAdjacentHTML(
      "afterbegin",
      '<div class="lu-render-error" role="alert"><b>Something on this page failed.</b> <span></span></div>'
    );
    host.querySelector(".lu-render-error span").textContent =
      `${e.message || "Script error"} Reload to try again.`;
  });
  applyAccent();
  new MutationObserver(() => applyAccent()).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme", "data-ui", "data-server-colour"],
  });
  render();
  if ("ResizeObserver" in window) {
    new ResizeObserver(() =>
      requestAnimationFrame(() => {
        if (barReadOnly() !== lastRO && !drag && !busy()) render();
        else fit();
      })
    ).observe(host);
  }
  if (document.fonts && document.fonts.ready)
    document.fonts.ready.then(() => requestAnimationFrame(fit));
  root.classList.add("is-enhanced");
  if (newId) {
    const id = newId;
    foldChoice[id] = false;
    saveFolds();
    const r = document.getElementById("lu-row-" + id);
    if (r) {
      const found = lineupOf(id);
      if (found && found.d && collapsed.has(found.d.id)) {
        collapsed.delete(found.d.id);
        render();
      }
      requestAnimationFrame(() => {
        const row = document.getElementById("lu-row-" + id);
        if (row)
          row.scrollIntoView({ block: "center", behavior: reduceMotion() ? "auto" : "smooth" });
      });
    }
    setTimeout(() => {
      newId = null;
    }, 3000);
  }
  window.TesseraeLineups = {
    refresh,
    render,
    state: st,
    get vm() {
      return vm;
    },
  };
})();
