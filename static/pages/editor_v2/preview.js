// Dashboard editor v2: live preview pane.
//
// Two parts in one file (no build step):
//
// 1. window.EV2PreviewGeom: pure layout geometry, no DOM. Cells are
//    {id, x, y, w, h} in stored panel pixels (the saved model, the same units
//    layout_editor.js and the batch_cells route use). Covered by
//    tests/test_editor_v2_preview_browser.py.
// 2. The pane itself (templates/partials/page_editor_v2_preview.html): one
//    composer iframe per display shape, a transparent overlay of cell buttons
//    and draggable lines on top, Live / "As the panel shows it", reload and
//    open-at-100%.
//
// Contract with the steps side (page_editor_v2.html), all CustomEvents on
// document:
//   ev2:cell-select    {cellId}            out: a cell was clicked (not in edit mode)
//   ev2:layout-changed {cells}             out: a resize / split / remove was saved;
//                                           cells = [{id, x, y, w, h, plugin}]
//   ev2:cell-focus     {cellId|null}       in:  outline this cell (a row opened/closed)
//   ev2:edit-mode      {on, snap?}         in:  "Edit on preview" toggle. Optional
//                                           snap = {cols, rows} turns grid snapping on,
//                                           snap = null turns it off, omitted = unchanged.
//   ev2:snap           {on, cols, rows}    in:  snap-to-grid changed (steps.js)
//   ev2:saved          {cells?}            in:  something that changes rendering was
//                                           saved; the pane reloads and re-reads the
//                                           layout and displays. Pass cells to skip
//                                           the re-read of the layout.
// window.EV2Preview = {reload(), setTab(groupKey), sync()}. groupKey is the
// group's "WxH" (e.g. "800x480") or its index.

(function () {
  "use strict";

  const EPS = 0.5; // stored coords are integer px; anything closer is the same line
  const SNAP_FRACTIONS = [
    [1 / 4, "¼"],
    [1 / 3, "⅓"],
    [1 / 2, "½"],
    [2 / 3, "⅔"],
    [3 / 4, "¾"],
  ];

  // Python's round(): halves go to the even neighbour. Keeps fitCells
  // identical to app.panel.fit_cells_to_panel.
  function pyRound(v) {
    const f = Math.floor(v);
    const d = v - f;
    if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
    return Math.round(v);
  }

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const same = (a, b) => Math.abs(a - b) < EPS;
  // Along a line of direction `dir` ("v" = vertical line, cells side by side):
  const perp = (c, dir) => (dir === "v" ? [c.y, c.y + c.h] : [c.x, c.x + c.w]);
  const start = (c, dir) => (dir === "v" ? c.x : c.y);
  const end = (c, dir) => (dir === "v" ? c.x + c.w : c.y + c.h);

  // Every draggable line in the layout. A line is shared by the cells that end
  // on it (a) and the cells that start on it (b); a cell joins when its span
  // overlaps the run found so far, so a T-junction moves as one piece (one
  // tall cell left of two stacked cells is one line, not two). Separate runs
  // on the same coordinate stay separate (a + crossing gives four half-lines).
  function edgeGroups(cells, opts) {
    const skip = new Set((opts && opts.exclude) || []);
    const cs = cells.filter((c) => !skip.has(c.id));
    const out = [];
    const seen = new Set();
    ["v", "h"].forEach((dir) => {
      cs.forEach((first) => {
        const pos = end(first, dir);
        let [s0, s1] = perp(first, dir);
        const a = new Set([first.id]);
        const b = new Set();
        let changed = true;
        while (changed) {
          changed = false;
          cs.forEach((d) => {
            const [d0, d1] = perp(d, dir);
            if (!(d0 < s1 - EPS && d1 > s0 + EPS)) return;
            if (same(end(d, dir), pos) && !a.has(d.id)) {
              a.add(d.id);
              s0 = Math.min(s0, d0);
              s1 = Math.max(s1, d1);
              changed = true;
            }
            if (same(start(d, dir), pos) && !b.has(d.id)) {
              b.add(d.id);
              s0 = Math.min(s0, d0);
              s1 = Math.max(s1, d1);
              changed = true;
            }
          });
        }
        if (!b.size) return; // panel border or a gap: nothing on the far side
        const key = `${dir}:${pos}:${s0}:${s1}`;
        if (seen.has(key)) return;
        seen.add(key);
        const order = (set) => cs.filter((c) => set.has(c.id)).map((c) => c.id);
        out.push({ dir, pos, a: order(a), b: order(b), s0, s1, key });
      });
    });
    return out;
  }

  // How far a line may move: every cell on it keeps at least `minSize`, and a
  // cell growing past a gap stops at the next cell in its way (same rule as
  // layout_editor.js edgeLimits).
  function edgeLimits(cells, edge, minSize) {
    const dir = edge.dir;
    const byId = new Map(cells.map((c) => [c.id, c]));
    const A = edge.a.map((id) => byId.get(id)).filter(Boolean);
    const B = edge.b.map((id) => byId.get(id)).filter(Boolean);
    const moving = new Set([...edge.a, ...edge.b]);
    const others = cells.filter((c) => !moving.has(c.id));
    const overlapsPerp = (p, q) => {
      const [p0, p1] = perp(p, dir);
      const [q0, q1] = perp(q, dir);
      return p0 < q1 - EPS && q0 < p1 - EPS;
    };
    let lo = Math.max(...A.map((c) => start(c, dir))) + minSize;
    let hi = Math.min(...B.map((c) => end(c, dir))) - minSize;
    B.forEach((u) => {
      others.forEach((o) => {
        if (overlapsPerp(o, u) && end(o, dir) <= start(u, dir) + EPS)
          lo = Math.max(lo, end(o, dir));
      });
    });
    A.forEach((l) => {
      others.forEach((o) => {
        if (overlapsPerp(o, l) && start(o, dir) >= end(l, dir) - EPS)
          hi = Math.min(hi, start(o, dir));
      });
    });
    if (lo > hi) return [edge.pos, edge.pos];
    return [Math.ceil(lo), Math.floor(hi)];
  }

  // Move a line to `pos`. Returns a new cells array; untouched cells keep
  // their objects.
  function applyEdge(cells, edge, pos) {
    const A = new Set(edge.a);
    const B = new Set(edge.b);
    return cells.map((c) => {
      if (A.has(c.id)) {
        return edge.dir === "v" ? { ...c, w: pos - c.x } : { ...c, h: pos - c.y };
      }
      if (B.has(c.id)) {
        if (edge.dir === "v") return { ...c, x: pos, w: c.x + c.w - pos };
        return { ...c, y: pos, h: c.y + c.h - pos };
      }
      return c;
    });
  }

  // Snap a raw position. With a grid (n columns or rows over r0..r1) it
  // rounds to the nearest grid line; otherwise it sticks to ¼ ⅓ ½ ⅔ ¾ of
  // r0..r1 when within `threshold`. Always integer, always inside lo..hi.
  // Returns {pos, label}: label is the fraction glyph when it snapped.
  function snapPos(raw, o) {
    const r0 = o.r0;
    const len = o.r1 - o.r0;
    let p = raw;
    let label = null;
    if (o.grid && o.grid > 1 && len > 0) {
      const step = len / o.grid;
      p = r0 + Math.round((raw - r0) / step) * step;
    } else if (len > 0) {
      let best = null;
      SNAP_FRACTIONS.forEach(([f, glyph]) => {
        const t = r0 + f * len;
        const d = Math.abs(raw - t);
        if (d <= (o.threshold || 0) && (best === null || d < best.d)) best = { t, d, glyph };
      });
      if (best) {
        p = best.t;
        label = best.glyph;
      }
    }
    const pos = clamp(Math.round(p), o.lo, o.hi);
    if (label && Math.abs(pos - p) >= 1) label = null; // the clamp moved it off the fraction
    return { pos, label };
  }

  // Split a cell in two. dir "v" puts the new cell on the right, "h" below.
  // The cut is the middle, or with grid = {r0, step} the grid line nearest the
  // middle that leaves both parts at least minSize. Returns the batch_cells
  // payload, or null when the cell is too small to split.
  function splitAt(start, size, min, grid) {
    let half = Math.floor(size / 2);
    if (grid && grid.step > 0) {
      const mid = start + size / 2;
      const k = Math.round((mid - grid.r0) / grid.step);
      const lines = [k, k - 1, k + 1, k - 2, k + 2].map(
        (n) => Math.round(grid.r0 + n * grid.step) - start
      );
      const fit = lines.find((h) => h >= min && size - h >= min);
      if (fit !== undefined) half = fit;
    }
    return half >= min && size - half >= min ? half : null;
  }
  function split(cell, dir, minSize, grid) {
    const min = minSize || 1;
    if (dir === "v") {
      const half = splitAt(cell.x, cell.w, min, grid);
      if (half === null) return null;
      return {
        updates: [{ id: cell.id, x: cell.x, y: cell.y, w: half, h: cell.h }],
        creates: [{ x: cell.x + half, y: cell.y, w: cell.w - half, h: cell.h }],
      };
    }
    const half = splitAt(cell.y, cell.h, min, grid);
    if (half === null) return null;
    return {
      updates: [{ id: cell.id, x: cell.x, y: cell.y, w: cell.w, h: half }],
      creates: [{ x: cell.x, y: cell.y + half, w: cell.w, h: cell.h - half }],
    };
  }

  // Remove a cell and let neighbours take its space: one neighbour sharing a
  // full edge, or a row / column of neighbours that tile that edge exactly
  // (layout_editor.js findAbsorbers). Cells in `exclude` (the status bar)
  // never absorb. Returns the batch_cells payload or null.
  function removeMerge(cells, id, opts) {
    const skip = new Set((opts && opts.exclude) || []);
    const del = cells.find((c) => c.id === id);
    if (!del || skip.has(id)) return null;
    const others = cells.filter((c) => c.id !== id && !skip.has(c.id));
    if (!others.length) return null;
    const x0 = del.x;
    const x1 = del.x + del.w;
    const y0 = del.y;
    const y1 = del.y + del.h;
    const tiles = (cs, key, len, lo, hi) => {
      if (!cs.length) return false;
      let cursor = lo;
      for (const c of cs) {
        if (!same(c[key], cursor)) return false;
        cursor = c[key] + c[len];
      }
      return same(cursor, hi);
    };
    const tries = [
      () => {
        const cs = others
          .filter((c) => same(c.x + c.w, x0) && c.y >= y0 - EPS && c.y + c.h <= y1 + EPS)
          .sort((p, q) => p.y - q.y);
        return (
          tiles(cs, "y", "h", y0, y1) &&
          cs.map((c) => ({ id: c.id, x: c.x, y: c.y, w: c.w + del.w, h: c.h }))
        );
      },
      () => {
        const cs = others
          .filter((c) => same(c.x, x1) && c.y >= y0 - EPS && c.y + c.h <= y1 + EPS)
          .sort((p, q) => p.y - q.y);
        return (
          tiles(cs, "y", "h", y0, y1) &&
          cs.map((c) => ({ id: c.id, x: x0, y: c.y, w: c.w + del.w, h: c.h }))
        );
      },
      () => {
        const cs = others
          .filter((c) => same(c.y + c.h, y0) && c.x >= x0 - EPS && c.x + c.w <= x1 + EPS)
          .sort((p, q) => p.x - q.x);
        return (
          tiles(cs, "x", "w", x0, x1) &&
          cs.map((c) => ({ id: c.id, x: c.x, y: c.y, w: c.w, h: c.h + del.h }))
        );
      },
      () => {
        const cs = others
          .filter((c) => same(c.y, y1) && c.x >= x0 - EPS && c.x + c.w <= x1 + EPS)
          .sort((p, q) => p.x - q.x);
        return (
          tiles(cs, "x", "w", x0, x1) &&
          cs.map((c) => ({ id: c.id, x: c.x, y: y0, w: c.w, h: c.h + del.h }))
        );
      },
    ];
    for (const t of tries) {
      const updates = t();
      if (updates) return { deletes: [id], updates };
    }
    return null;
  }

  // Port of app.panel.fit_cells_to_panel: where the composer draws each cell
  // on a target display (scale to the layout's bounding box, turn 90° when
  // the orientation differs, keep the status bar strip on top).
  function fitImpl(coords, tw, th) {
    let dw = Math.max(...coords.map(([x, , w]) => x + w));
    let dh = Math.max(...coords.map(([, y, , h]) => y + h));
    if (dw <= 0 || dh <= 0) return coords.slice();
    if (dw >= dh !== tw >= th) {
      coords = coords.map(([x, y, w, h]) => [dh - y - h, x, h, w]);
      [dw, dh] = [dh, dw];
    }
    const sx = tw / dw;
    const sy = th / dh;
    return coords.map(([x, y, w, h]) => {
      const nx = Math.max(0, Math.min(tw - 1, pyRound(x * sx)));
      const ny = Math.max(0, Math.min(th - 1, pyRound(y * sy)));
      const nw = Math.max(1, Math.min(tw - nx, pyRound(w * sx)));
      const nh = Math.max(1, Math.min(th - ny, pyRound(h * sy)));
      return [nx, ny, nw, nh];
    });
  }

  function stripInfo(cells, tw, th, stripId) {
    const idx = stripId ? cells.findIndex((c) => c.id === stripId) : -1;
    if (idx < 0) return null;
    const strip = cells[idx];
    const others = cells.filter((_, i) => i !== idx);
    const yOff = Math.max(0, strip.y + strip.h);
    const shifted = others.map((c) => [c.x, Math.max(0, c.y - yOff), c.w, c.h]);
    let dw;
    let dh;
    if (shifted.length) {
      dw = Math.max(...shifted.map(([x, , w]) => x + w));
      dh = Math.max(...shifted.map(([, y, , h]) => y + h));
    } else {
      dw = Math.max(strip.w, 1);
      dh = Math.max(strip.h, 1);
    }
    // The strip scales with the short side of the unshifted layout, as in
    // _fit_with_top_strip; the band below uses the shifted bounds.
    const rawW = others.length ? Math.max(...others.map((c) => c.x + c.w)) : Math.max(strip.w, 1);
    const rawH = others.length ? Math.max(...others.map((c) => c.y + c.h)) : Math.max(strip.h, 1);
    const designShort = Math.min(rawW, rawH);
    const targetShort = Math.min(tw, th);
    let stripH =
      designShort <= 0 ? strip.h : Math.max(1, pyRound((strip.h * targetShort) / designShort));
    stripH = Math.min(stripH, Math.max(1, th - 1));
    return { idx, others, shifted, yOff, stripH, dw, dh };
  }

  function fitCells(cells, tw, th, stripId) {
    if (!cells.length) return [];
    const s = stripInfo(cells, tw, th, stripId);
    let fitted;
    if (s) {
      const band = Math.max(1, th - s.stripH);
      const rest = s.shifted.length
        ? fitImpl(s.shifted, tw, band).map(([x, y, w, h]) => [x, y + s.stripH, w, h])
        : [];
      fitted = rest.slice();
      fitted.splice(s.idx, 0, [0, 0, tw, s.stripH]);
    } else {
      fitted = fitImpl(
        cells.map((c) => [c.x, c.y, c.w, c.h]),
        tw,
        th
      );
    }
    return cells.map((c, i) => ({
      id: c.id,
      x: fitted[i][0],
      y: fitted[i][1],
      w: fitted[i][2],
      h: fitted[i][3],
    }));
  }

  // Linear map between stored px and a target display's px, per axis, for
  // the cells under the status bar. rotated = the composer turns this layout
  // 90° on this display, so dragging there can't map back to stored px.
  function axisMap(cells, tw, th, stripId) {
    const s = stripInfo(cells, tw, th, stripId);
    let dw;
    let dh;
    let y0 = 0;
    let f0 = 0;
    let band = th;
    if (s) {
      if (!s.shifted.length)
        return {
          rotated: false,
          x: { s0: 0, s1: s.dw, f0: 0, f1: tw },
          y: { s0: s.yOff, s1: s.yOff + 1, f0: s.stripH, f1: th },
        };
      dw = s.dw;
      dh = s.dh;
      y0 = s.yOff;
      f0 = s.stripH;
      band = Math.max(1, th - s.stripH);
    } else {
      const own = cells.length ? cells : [{ x: 0, y: 0, w: tw, h: th }];
      dw = Math.max(...own.map((c) => c.x + c.w));
      dh = Math.max(...own.map((c) => c.y + c.h));
    }
    return {
      rotated: dw >= dh !== tw >= band,
      x: { s0: 0, s1: dw, f0: 0, f1: tw },
      y: { s0: y0, s1: y0 + dh, f0, f1: th },
    };
  }
  const toFitted = (ax, v) => ax.f0 + ((v - ax.s0) * (ax.f1 - ax.f0)) / (ax.s1 - ax.s0 || 1);
  const toStored = (ax, v) => ax.s0 + ((v - ax.f0) * (ax.s1 - ax.s0)) / (ax.f1 - ax.f0 || 1);

  // Smallest cell side while dragging or splitting: 6% of the axis, at least
  // 8 px (the classic editor's step).
  const minSizeFor = (axisLen) => Math.max(8, Math.round(axisLen * 0.06));

  window.EV2PreviewGeom = {
    edgeGroups,
    edgeLimits,
    applyEdge,
    snapPos,
    split,
    removeMerge,
    fitCells,
    axisMap,
    toFitted,
    toStored,
    minSizeFor,
    pyRound,
    SNAP_FRACTIONS: SNAP_FRACTIONS.map(([f]) => f),
  };
})();

(function () {
  "use strict";
  const G = window.EV2PreviewGeom;
  const root = document.querySelector("[data-ev2-preview]");
  if (!root || !G || root.dataset.ev2pvBound) return;
  root.dataset.ev2pvBound = "1";

  const q = (sel, el) => (el || root).querySelector(sel);
  const make = (tag, cls) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    return el;
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const emit = (name, detail) => document.dispatchEvent(new CustomEvent(name, { detail }));
  const pct = (v) => `${v}%`;

  function readData(scope) {
    const el = scope.querySelector("[data-ev2pv-data]");
    if (!el) return null;
    let out;
    try {
      out = JSON.parse(el.textContent);
    } catch {
      return null;
    }
    // Display colour mode per shape: the server's data-gamut / data-grey-levels
    // (same macro as the classic editor) win over the JSON copy.
    (out.groups || []).forEach((g) => {
      const c = scope.querySelector(`[data-ev2pv-color="${CSS.escape(String(g.key))}"]`);
      if (!c) return;
      if (c.dataset.gamut) g.gamut = c.dataset.gamut;
      const lv = Number(c.dataset.greyLevels);
      if (Number.isFinite(lv)) g.greyLevels = lv;
    });
    return out;
  }

  let data = readData(root);
  if (!data) return;

  const store = {
    get(k) {
      try {
        return sessionStorage.getItem(k);
      } catch {
        return null;
      }
    },
    set(k, v) {
      try {
        sessionStorage.setItem(k, v);
      } catch {
        /* best effort */
      }
    },
  };
  const TAB_KEY = `tesserae:ev2pv-tab:${data.pageId}`;
  const MODE_KEY = "tesserae:ev2pv-mode";

  const state = {
    group: 0,
    mode: store.get(MODE_KEY) === "panel" ? "panel" : "live",
    edit: false,
    snap: null, // {cols, rows}
    selected: null, // edit mode: the cell the Split / Remove tools act on
    focused: null, // the cell whose row is open on the left
    drag: null,
    saving: false,
    // A new dashboard that hasn't reached the layout step: a screen-sized
    // placeholder instead of the panel-pixel render (steps.js decides).
    placeholder: Boolean(window.EV2 && window.EV2.placeholder),
  };
  const frames = []; // per group: {wrap, canvas, iframe, img, throbber, overlay, loaded, stale}

  const hintEl = q("[data-ev2pv-hint]");
  const statusEl = q("[data-ev2pv-status]");
  const tabsEl = q("[data-ev2pv-tabs]");
  const groupsEl = q("[data-ev2pv-groups]");

  const sbId = () => data.statusBarCellId || null;
  const group = () => data.groups[state.group] || data.groups[0];
  const frame = () => frames[state.group];
  const cellIndex = (id) => data.cells.findIndex((c) => c.id === id);
  // Cell numbers match the steps list: 1..N in page order, status bar skipped.
  const cellNumber = (id) =>
    data.cells.filter((c) => c.id !== sbId()).findIndex((c) => c.id === id) + 1;
  const cellName = (c) => (c.plugin ? data.widgetNames[c.plugin] || c.plugin : "Empty");

  let statusTimer = null;
  function setStatus(text, sticky) {
    clearTimeout(statusTimer);
    statusEl.textContent = text || "";
    statusEl.classList.toggle("is-error", Boolean(sticky));
    if (text && !sticky) statusTimer = setTimeout(() => (statusEl.textContent = ""), 2500);
  }

  // ------------------------------------------------------------------ frames
  function groupSig(groups) {
    return (groups || [])
      .map((g) => [g.key, g.kind, g.gamut, g.greyLevels, (g.deviceIds || []).join(",")].join("/"))
      .join("|");
  }

  function composeUrl(g, fresh) {
    // No preview=1: that adds the classic editor's number tags and click
    // shims inside the frame; here the overlay draws those.
    const u = new URL(data.urls.compose, location.origin);
    u.searchParams.set("w", String(g.w));
    u.searchParams.set("h", String(g.h));
    if (data.version) u.searchParams.set("v", data.version);
    if (fresh) u.searchParams.set("fresh", "1");
    u.searchParams.set("_t", String(Date.now()));
    return u.pathname + u.search;
  }

  const ro =
    typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver((entries) => {
          entries.forEach((e) => {
            const f = frames.find((x) => x.canvas === e.target);
            if (f) fitFrame(f);
          });
        });

  function fitFrame(f) {
    const w = f.canvas.clientWidth;
    if (!w) return;
    f.iframe.style.transform = `scale(${w / f.g.w})`;
  }

  function buildGroups() {
    if (ro) ro.disconnect();
    frames.forEach((f) => clearPanelPoll(f));
    frames.length = 0;
    groupsEl.replaceChildren();
    data.groups.forEach((g, i) => {
      const wrap = make("div", "ev2pv-group");
      wrap.dataset.groupKey = g.key;
      wrap.style.setProperty("--ev2pv-ar", String(g.w / g.h));
      wrap.hidden = i !== state.group;
      wrap.classList.toggle("is-placeholder", state.placeholder);
      const stage = make("div", "ev2pv-stage");
      const bezel = make("div", `ev2pv-bezel ev2pv-bezel--${g.kind}`);
      const canvas = make("div", "ev2pv-canvas");
      canvas.style.aspectRatio = `${g.w} / ${g.h}`;
      if (g.gamut) canvas.dataset.gamut = g.gamut;
      if (g.greyLevels) canvas.dataset.greyLevels = String(g.greyLevels);
      const iframe = make("iframe", "ev2pv-iframe");
      iframe.title = `Preview at ${g.w} × ${g.h}`;
      iframe.setAttribute("sandbox", "allow-scripts allow-same-origin");
      iframe.setAttribute("inert", ""); // the overlay owns every click and key
      iframe.tabIndex = -1;
      iframe.style.width = `${g.w}px`;
      iframe.style.height = `${g.h}px`;
      const img = make("img", "ev2pv-panel-img");
      img.alt = `What the panel shows at ${g.w} × ${g.h}`;
      img.dataset.deviceQuantised = "";
      img.hidden = true;
      const throbber = make("div", "ev2pv-throbber");
      throbber.hidden = true;
      throbber.setAttribute("aria-hidden", "true");
      throbber.innerHTML = '<i class="ph ph-circle-notch"></i>';
      const overlay = make("div", "ev2pv-overlay");
      overlay.dataset.ev2pvOverlay = "";
      const ph = make("div", "ev2pv-ph");
      ph.textContent = "Your dashboard appears here.";
      canvas.append(iframe, img, throbber, overlay, ph);
      bezel.append(canvas);
      stage.append(bezel);
      const cap = make("p", "ev2pv-cap");
      const dims = make("b");
      dims.textContent = `${g.w} × ${g.h}`;
      const label = make("span");
      label.textContent = g.label;
      cap.append(dims, label);
      wrap.append(stage, cap);
      groupsEl.append(wrap);
      const f = {
        g,
        wrap,
        canvas,
        iframe,
        img,
        throbber,
        overlay,
        loaded: false,
        stale: true,
        panelGen: 0,
        panelTimer: null,
      };
      frames.push(f);
      if (ro) ro.observe(canvas);
      bindOverlay(f);
    });
  }
  if (!ro) window.addEventListener("resize", () => frames.forEach(fitFrame));

  // Each cell's widget data as the frame got it (data-data on .cell), for
  // editor-side status lines (status.js). Same-origin, so read it directly.
  function emitFrameData(f) {
    let doc;
    try {
      doc = f.iframe.contentDocument;
    } catch {
      return;
    }
    if (!doc) return;
    const cells = [];
    doc.querySelectorAll(".cell[data-cell-id]").forEach((el) => {
      let cellData = null;
      try {
        cellData = JSON.parse(el.dataset.data || "null");
      } catch {
        cellData = null;
      }
      cells.push({ id: el.dataset.cellId, plugin: el.dataset.plugin || "", data: cellData });
    });
    const src = new URL(f.iframe.src, location.origin);
    src.searchParams.delete("fresh");
    emit("ev2:frame-data", { cells, src: src.pathname + src.search });
  }

  function loadFrame(f, opts) {
    const fresh = Boolean(opts && opts.fresh);
    const blank = Boolean(opts && opts.blank);
    f.stale = false;
    f.throbber.hidden = false;
    if (f.loaded) f.iframe.classList.add("is-loading");
    const go = () => {
      f.iframe.addEventListener(
        "load",
        () => {
          f.loaded = true;
          f.iframe.classList.remove("is-loading");
          if (state.mode !== "panel") f.throbber.hidden = true;
          emitFrameData(f);
        },
        { once: true }
      );
      f.iframe.src = composeUrl(f.g, fresh);
    };
    if (blank && f.loaded) {
      // about:blank first so the browser drops the old document and every
      // widget timer in it before the fresh one loads.
      f.iframe.addEventListener("load", go, { once: true });
      f.iframe.src = "about:blank";
    } else {
      go();
    }
    if (state.mode === "panel") loadPanel(f);
  }

  // "As the panel shows it": the server quantises + dithers the render to the
  // panel's palette. It answers 202 while that render runs, so poll.
  function clearPanelPoll(f) {
    if (f.panelTimer) clearTimeout(f.panelTimer);
    f.panelTimer = null;
  }
  function loadPanel(f) {
    clearPanelPoll(f);
    const gen = ++f.panelGen;
    const u = new URL(data.urls.panel, location.origin);
    if (f.g.deviceIds && f.g.deviceIds.length) u.searchParams.set("device", f.g.deviceIds[0]);
    u.searchParams.set("w", String(f.g.w));
    u.searchParams.set("h", String(f.g.h));
    u.searchParams.set("_t", String(Date.now()));
    const url = u.pathname + u.search;
    f.throbber.hidden = false;
    if (!f.img.getAttribute("src")) f.img.classList.add("is-pending");
    let tries = 0;
    const live = () => state.mode === "panel" && f.panelGen === gen;
    const attempt = () => {
      if (!live()) return;
      fetch(url, { cache: "no-store", credentials: "same-origin" })
        .then((r) => {
          if (!live()) return;
          if (r.status === 200) {
            f.img.onload = () => {
              if (!live()) return;
              f.throbber.hidden = true;
              f.img.classList.remove("is-pending");
            };
            f.img.onerror = () => live() && (f.throbber.hidden = true);
            f.img.src = url;
          } else if (r.status === 202 && tries++ < 40) {
            f.panelTimer = setTimeout(attempt, 500);
          } else {
            f.throbber.hidden = true;
            setStatus("Couldn't render what the panel shows. Try Live.", true);
          }
        })
        .catch(() => {
          if (live()) f.throbber.hidden = true;
        });
    };
    attempt();
  }

  function setMode(mode) {
    state.mode = mode === "panel" ? "panel" : "live";
    store.set(MODE_KEY, state.mode);
    root.querySelectorAll("[data-ev2pv-mode]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.ev2pvMode === state.mode));
    });
    frames.forEach((f) => {
      clearPanelPoll(f);
      f.panelGen++;
      const panel = state.mode === "panel";
      f.iframe.hidden = panel;
      f.img.hidden = !panel;
      if (!panel) {
        f.img.removeAttribute("src");
        f.throbber.hidden = f.loaded || f.stale;
      }
    });
    if (state.mode === "panel") loadPanel(frame());
  }

  let reloadTimer = null;
  function reload(opts) {
    clearTimeout(reloadTimer);
    frames.forEach((f) => (f.stale = true));
    const f = frame();
    if (f) loadFrame(f, opts);
  }
  // Coalesce bursts of ev2:saved into one reload.
  function reloadSoon() {
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(() => reload(), 120);
  }

  // Long sessions: widget timers in the composer accumulate memory (see
  // editor.js). Start the visible preview from a blank document every hour.
  setInterval(
    () => {
      if (!document.hidden && !state.drag) reload({ blank: true });
    },
    60 * 60 * 1000
  );

  // ------------------------------------------------------------------ tabs
  function renderTabs() {
    const many = data.groups.length > 1;
    tabsEl.hidden = !many;
    tabsEl.replaceChildren();
    if (!many) return;
    data.groups.forEach((g, i) => {
      const b = make("button");
      b.type = "button";
      b.dataset.ev2pvTab = g.key;
      b.setAttribute("aria-pressed", String(i === state.group));
      b.append(document.createTextNode(`${g.label} `));
      const dims = make("span", "ev2pv-tab-dims");
      dims.textContent = `${g.w}×${g.h}`;
      b.append(dims);
      tabsEl.append(b);
    });
  }

  function setTab(key) {
    let i = typeof key === "number" ? key : data.groups.findIndex((g) => g.key === String(key));
    if (!(i >= 0 && i < data.groups.length)) i = 0;
    if (state.drag) return;
    state.group = i;
    store.set(TAB_KEY, data.groups[i].key);
    frames.forEach((f, k) => (f.wrap.hidden = k !== i));
    renderTabs();
    const f = frame();
    if (f.stale || !f.loaded) loadFrame(f);
    else if (state.mode === "panel" && !f.img.getAttribute("src")) loadPanel(f);
    fitFrame(f);
    renderOverlay();
  }

  // ------------------------------------------------------------------ overlay
  function axisLen(map, axis) {
    return map[axis].s1 - map[axis].s0;
  }
  // With snap to grid on, splits cut on a grid line too.
  function splitGrid(axis) {
    const f = frame();
    if (!state.snap || !f || !f.map) return null;
    const ax = f.map[axis];
    return {
      r0: ax.s0,
      step: (ax.s1 - ax.s0) / (axis === "x" ? state.snap.cols : state.snap.rows),
    };
  }

  function hint() {
    const f = frame();
    // "Tap" on a touch screen, "Click" with a mouse.
    const click = window.matchMedia && window.matchMedia("(hover: none)").matches ? "Tap" : "Click";
    let text;
    if (state.placeholder || !data.cells.length) text = "Pick a layout to split the screen.";
    else if (f && f.map && f.map.rotated) {
      const flat = data.groups.find((g) => !G.axisMap(data.cells, g.w, g.h, sbId()).rotated);
      text = flat
        ? `This display is turned the other way. Resize, split and remove on the ${flat.w}×${flat.h} tab.`
        : `${click} a cell to edit its widget.`;
    } else if (state.edit)
      text = `${click} a cell to split or remove it. Drag the line between two cells to resize.`;
    else text = `${click} a cell to edit its widget. Drag the line between two cells to resize.`;
    hintEl.textContent = text;
  }

  function focusKey(el) {
    return el && el.dataset ? el.dataset.fk || null : null;
  }

  function renderOverlay() {
    const f = frame();
    if (!f) return;
    const g = f.g;
    const sb = sbId();
    const keepFocus = f.overlay.contains(document.activeElement)
      ? focusKey(document.activeElement)
      : null;
    const cells = data.cells;
    const fitted = G.fitCells(cells, g.w, g.h, sb);
    const map = G.axisMap(cells, g.w, g.h, sb);
    f.map = map;
    const editable = !map.rotated;
    if (state.selected && cellIndex(state.selected) < 0) state.selected = null;

    const nodes = [];
    cells.forEach((c, i) => {
      const r = fitted[i];
      const isSb = c.id === sb;
      const b = make("button", "ev2pv-cell");
      b.type = "button";
      b.dataset.cellId = c.id;
      b.dataset.fk = `cell:${c.id}`;
      b.style.left = pct((r.x / g.w) * 100);
      b.style.top = pct((r.y / g.h) * 100);
      b.style.width = pct((r.w / g.w) * 100);
      b.style.height = pct((r.h / g.h) * 100);
      if (isSb) b.classList.add("is-statusbar");
      if (!c.plugin) b.classList.add("is-empty");
      const selected = state.edit ? state.selected === c.id : state.focused === c.id;
      b.classList.toggle("is-selected", selected);
      b.setAttribute("aria-pressed", String(selected));
      const name = isSb ? "Status bar" : cellName(c);
      b.setAttribute(
        "aria-label",
        state.edit && !isSb
          ? `Cell ${cellNumber(c.id)}, ${name}. Select to split or remove`
          : isSb
            ? name
            : `Cell ${cellNumber(c.id)}, ${name}`
      );
      if (!isSb) {
        const badge = make("span", "ev2pv-badge");
        badge.textContent = String(cellNumber(c.id));
        badge.setAttribute("aria-hidden", "true");
        b.append(badge);
      }
      nodes.push(b);
      if (state.edit && editable && state.selected === c.id && !isSb) nodes.push(tools(c, r, g));
    });

    if (editable && cells.length > 1) {
      G.edgeGroups(cells, { exclude: sb ? [sb] : [] }).forEach((e) =>
        nodes.push(edgeNode(e, map, g))
      );
    }

    f.overlay.replaceChildren(...nodes);
    f.overlay.classList.toggle("is-edit", state.edit);
    f.overlay.classList.toggle("is-rotated", !editable);
    paintGrid(f);
    placeTools(f);
    hint();
    if (keepFocus) {
      const again = f.overlay.querySelector(`[data-fk="${CSS.escape(keepFocus)}"]`);
      if (again) again.focus({ preventScroll: true });
    }
  }

  function paintGrid(f) {
    const on = state.edit && state.snap && f.map && !f.map.rotated;
    f.overlay.classList.toggle("has-grid", Boolean(on));
    if (!on) {
      f.overlay.style.removeProperty("--ev2pv-grid-x");
      f.overlay.style.removeProperty("--ev2pv-grid-y");
      f.overlay.style.removeProperty("--ev2pv-grid-top");
      return;
    }
    const top = (G.toFitted(f.map.y, f.map.y.s0) / f.g.h) * 100;
    f.overlay.style.setProperty("--ev2pv-grid-x", pct(100 / state.snap.cols));
    f.overlay.style.setProperty("--ev2pv-grid-y", pct(100 / state.snap.rows));
    f.overlay.style.setProperty("--ev2pv-grid-top", pct(top));
  }

  function edgeLabel(e) {
    const names = (ids) => ids.map((id) => cellNumber(id)).join(", ");
    return e.dir === "v"
      ? `Line between cells ${names(e.a)} and ${names(e.b)}. Arrow keys move it left or right`
      : `Line between cells ${names(e.a)} and ${names(e.b)}. Arrow keys move it up or down`;
  }

  function placeEdgeNode(node, e, map, g) {
    if (e.dir === "v") {
      node.style.left = pct((G.toFitted(map.x, e.pos) / g.w) * 100);
      node.style.top = pct((G.toFitted(map.y, e.s0) / g.h) * 100);
      node.style.height = pct(((G.toFitted(map.y, e.s1) - G.toFitted(map.y, e.s0)) / g.h) * 100);
    } else {
      node.style.top = pct((G.toFitted(map.y, e.pos) / g.h) * 100);
      node.style.left = pct((G.toFitted(map.x, e.s0) / g.w) * 100);
      node.style.width = pct(((G.toFitted(map.x, e.s1) - G.toFitted(map.x, e.s0)) / g.w) * 100);
    }
  }

  function readout(e, pos, map, label) {
    const ax = e.dir === "v" ? map.x : map.y;
    const px = Math.round(G.toFitted(ax, pos));
    return label ? `${px} px · ${label}` : `${px} px`;
  }

  function edgeNode(e, map, g) {
    const node = make("div", `ev2pv-edge ev2pv-edge--${e.dir}`);
    node.dataset.fk = `edge:${e.dir}:${e.a[0]}:${e.b[0]}`;
    node.tabIndex = 0;
    node.setAttribute("role", "separator");
    node.setAttribute("aria-orientation", e.dir === "v" ? "vertical" : "horizontal");
    node.setAttribute("aria-label", edgeLabel(e));
    const ax = e.dir === "v" ? map.x : map.y;
    node.setAttribute("aria-valuemin", String(Math.round(G.toFitted(ax, ax.s0))));
    node.setAttribute("aria-valuemax", String(Math.round(G.toFitted(ax, ax.s1))));
    node.setAttribute("aria-valuenow", String(Math.round(G.toFitted(ax, e.pos))));
    node.title = "Drag to resize";
    placeEdgeNode(node, e, map, g);
    const px = make("span", "ev2pv-px");
    px.textContent = readout(e, e.pos, map);
    node.append(px);
    node._edge = e;
    return node;
  }

  function tools(c, r, g) {
    const sb = sbId();
    const lenX = axisLen(frame().map, "x");
    const lenY = axisLen(frame().map, "y");
    const bar = make("div", "ev2pv-tools-bar");
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", `Cell ${cellNumber(c.id)}`);
    bar.style.left = pct(((r.x + r.w / 2) / g.w) * 100);
    bar.style.top = pct(((r.y + r.h / 2) / g.h) * 100);
    const btn = (key, icon, text, title, disabled, why) => {
      const b = make("button");
      b.type = "button";
      b.dataset.fk = `tool:${key}`;
      b.dataset.ev2pvTool = key;
      b.innerHTML = `<i class="ph ph-${icon}" aria-hidden="true"></i>`;
      b.append(document.createTextNode(text));
      b.setAttribute("aria-label", title);
      b.title = disabled ? why : title;
      if (disabled) b.setAttribute("aria-disabled", "true");
      return b;
    };
    const canV = G.split(c, "v", G.minSizeFor(lenX), splitGrid("x")) !== null;
    const canH = G.split(c, "h", G.minSizeFor(lenY), splitGrid("y")) !== null;
    const canRemove = G.removeMerge(data.cells, c.id, { exclude: sb ? [sb] : [] }) !== null;
    bar.append(
      btn(
        "split-v",
        "square-split-horizontal",
        "Split",
        "Split into left and right",
        !canV,
        "Too narrow to split"
      ),
      btn(
        "split-h",
        "square-split-vertical",
        "Split",
        "Split into top and bottom",
        !canH,
        "Too short to split"
      ),
      btn(
        "remove",
        "trash",
        "Remove",
        "Remove this cell",
        !canRemove,
        "No single neighbour shares a full edge"
      )
    );
    return bar;
  }

  // Keep the Split / Remove bar inside the canvas (one layout read per render).
  function placeTools(f) {
    const bar = f.overlay.querySelector(".ev2pv-tools-bar");
    if (!bar) return;
    const W = f.overlay.clientWidth;
    const H = f.overlay.clientHeight;
    const bw = bar.offsetWidth;
    const bh = bar.offsetHeight;
    const cx = (parseFloat(bar.style.left) / 100) * W;
    const cy = (parseFloat(bar.style.top) / 100) * H;
    if (bw + 8 <= W) bar.style.left = `${clamp(cx, bw / 2 + 4, W - bw / 2 - 4)}px`;
    if (bh + 8 <= H) bar.style.top = `${clamp(cy, bh / 2 + 4, H - bh / 2 - 4)}px`;
  }

  function markFocus() {
    const f = frame();
    if (!f) return;
    f.overlay.querySelectorAll(".ev2pv-cell").forEach((b) => {
      const on = state.edit
        ? state.selected === b.dataset.cellId
        : state.focused === b.dataset.cellId;
      b.classList.toggle("is-selected", on);
      b.setAttribute("aria-pressed", String(on));
    });
  }

  // ------------------------------------------------------------------ saving
  async function saveBatch(payload, previous) {
    state.saving = true;
    root.classList.add("is-saving");
    setStatus("Saving the layout…");
    try {
      // In the full editor every write goes through the shell's one queue, so
      // a layout change can't interleave with a pending autosave (both
      // endpoints save the whole page) and flush() waits for it. The partial
      // on its own falls back to a direct request.
      const EV2 = window.EV2;
      let body;
      if (EV2 && typeof EV2.post === "function") {
        await EV2.flush(); // a pending autosave that fails stops the layout change too
        body = await EV2.post(data.urls.batch, payload, { json: true });
      } else {
        const res = await fetch(data.urls.batch, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        body = await res.json();
        if (body.ok === false) throw new Error(body.message || "save failed");
      }
      if (Array.isArray(body.cells)) {
        data.cells = body.cells.map((c) => ({
          id: c.id,
          x: c.x,
          y: c.y,
          w: c.w,
          h: c.h,
          plugin: c.plugin || null,
        }));
      }
      renderOverlay();
      reload();
      setStatus("Layout saved.");
      emit("ev2:layout-changed", { cells: data.cells.map((c) => ({ ...c })) });
      return true;
    } catch (err) {
      console.error("[ev2 preview] layout save failed:", err);
      if (previous) data.cells = previous;
      renderOverlay();
      setStatus("Couldn't save the layout. Try again.", true);
      return false;
    } finally {
      state.saving = false;
      root.classList.remove("is-saving");
    }
  }

  // ------------------------------------------------------------------ live resize
  // One session per drag (pointer) or per burst of arrow keys. Pointer moves
  // only record the position; one rAF per frame writes styles to the moving
  // cells, the line and the readout. Layout is read once, at the start.
  function beginSession(f, node) {
    const e = node._edge;
    const g = f.g;
    const map = f.map;
    const axis = e.dir === "v" ? "x" : "y";
    const ax = map[axis];
    const rect = f.overlay.getBoundingClientRect();
    const screenPerFitted = e.dir === "v" ? rect.width / g.w : rect.height / g.h;
    const screenPerStored = (screenPerFitted * (ax.f1 - ax.f0)) / (ax.s1 - ax.s0 || 1);
    const min = G.minSizeFor(ax.s1 - ax.s0);
    const [lo, hi] = G.edgeLimits(data.cells, e, min);
    const moving = new Set([...e.a, ...e.b]);
    const cellNodes = new Map();
    f.overlay.querySelectorAll(".ev2pv-cell").forEach((b) => {
      if (moving.has(b.dataset.cellId)) cellNodes.set(b.dataset.cellId, b);
    });
    return {
      f,
      node,
      e,
      g,
      map,
      ax,
      rect,
      screenPerFitted,
      threshold: 8 / (screenPerStored || 1),
      lo,
      hi,
      start: e.pos,
      pos: e.pos,
      label: null,
      before: data.cells,
      working: data.cells,
      cellNodes,
      pending: null,
      raf: 0,
    };
  }

  function sessionSnap(s, stored) {
    const grid = state.snap ? (s.e.dir === "v" ? state.snap.cols : state.snap.rows) : 0;
    return G.snapPos(stored, {
      lo: s.lo,
      hi: s.hi,
      r0: s.ax.s0,
      r1: s.ax.s1,
      threshold: s.threshold,
      grid,
    });
  }

  function paintSession(s, pos, label) {
    if (pos === s.pos && label === s.label) return;
    s.pos = pos;
    s.label = label;
    s.working = G.applyEdge(s.before, s.e, pos);
    const sb = sbId();
    const fitted = G.fitCells(s.working, s.g.w, s.g.h, sb);
    const geom = [];
    s.working.forEach((c, i) => {
      const n = s.cellNodes.get(c.id);
      if (!n) return;
      const r = fitted[i];
      n.style.left = pct((r.x / s.g.w) * 100);
      n.style.top = pct((r.y / s.g.h) * 100);
      n.style.width = pct((r.w / s.g.w) * 100);
      n.style.height = pct((r.h / s.g.h) * 100);
      geom.push(r);
    });
    const fp = (G.toFitted(s.ax, pos) / (s.e.dir === "v" ? s.g.w : s.g.h)) * 100;
    if (s.e.dir === "v") s.node.style.left = pct(fp);
    else s.node.style.top = pct(fp);
    s.node.setAttribute("aria-valuenow", String(Math.round(G.toFitted(s.ax, pos))));
    const px = s.node.querySelector(".ev2pv-px");
    if (px) px.textContent = readout(s.e, pos, s.map, label);
    // Let the composer reflow the moving cells in place (composer.js
    // applyGeom); the reload after the save settles charts.
    if (state.mode === "live" && s.f.loaded) {
      try {
        s.f.iframe.contentWindow.postMessage(
          { type: "tesserae-geom", srcW: s.g.w, srcH: s.g.h, cells: geom },
          location.origin
        );
      } catch {
        /* best effort */
      }
    }
  }

  function endSession(s, commit) {
    state.drag = null;
    root.classList.remove("is-dragging");
    s.f.overlay.classList.remove("is-dragging");
    s.node.classList.remove("is-active");
    if (!commit || s.pos === s.start) {
      data.cells = s.before;
      renderOverlay();
      if (commit === false && s.pos !== s.start && s.f.loaded) reload(); // undo the live reflow
      return;
    }
    const moving = new Set([...s.e.a, ...s.e.b]);
    const updates = s.working
      .filter((c) => moving.has(c.id))
      .map((c) => ({ id: c.id, x: c.x, y: c.y, w: c.w, h: c.h }));
    data.cells = s.working;
    renderOverlay();
    saveBatch({ updates }, s.before);
  }

  function onEdgePointerDown(ev, f, node) {
    if (ev.pointerType === "mouse" && ev.button !== 0) return;
    if (state.saving || state.drag) return;
    ev.preventDefault();
    const s = beginSession(f, node);
    s.pointerId = ev.pointerId;
    state.drag = s;
    try {
      node.setPointerCapture(ev.pointerId);
    } catch {
      /* best effort */
    }
    node.classList.add("is-active");
    f.overlay.classList.add("is-dragging");
    root.classList.add("is-dragging");
    root.dataset.dragDir = s.e.dir;

    const at = (e) => (s.e.dir === "v" ? e.clientX - s.rect.left : e.clientY - s.rect.top);
    const frameTick = () => {
      s.raf = 0;
      if (s.pending == null) return;
      const stored = G.toStored(s.ax, s.pending / s.screenPerFitted);
      s.pending = null;
      const snapped = sessionSnap(s, stored);
      paintSession(s, snapped.pos, snapped.label);
    };
    const move = (e) => {
      if (e.pointerId !== s.pointerId) return;
      s.pending = at(e);
      if (!s.raf) s.raf = requestAnimationFrame(frameTick);
    };
    const finish = (commit) => (e) => {
      if (e && e.pointerId !== undefined && e.pointerId !== s.pointerId) return;
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
      node.removeEventListener("pointercancel", cancel);
      document.removeEventListener("keydown", esc, true);
      if (s.raf) cancelAnimationFrame(s.raf);
      s.raf = 0;
      if (commit && s.pending != null) frameTick();
      endSession(s, commit);
    };
    const up = finish(true);
    const cancel = finish(false);
    const esc = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      cancel({});
    };
    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", up);
    node.addEventListener("pointercancel", cancel);
    document.addEventListener("keydown", esc, true);
  }

  // Arrow keys on a focused line: move it one step (1% of the axis, or one
  // grid step), Shift for ten. Saves once the keys stop for a moment.
  let kbd = null;
  function onEdgeKey(ev, f, node) {
    const vKeys = { ArrowLeft: -1, ArrowRight: 1 };
    const hKeys = { ArrowUp: -1, ArrowDown: 1 };
    const e = node._edge;
    const dirMap = e.dir === "v" ? vKeys : hKeys;
    if (ev.key === "Escape" && kbd && kbd.node === node) {
      clearTimeout(kbd.timer);
      const s = kbd;
      kbd = null;
      endSession(s, false);
      return;
    }
    if (ev.key === "Enter" && kbd && kbd.node === node) {
      ev.preventDefault();
      clearTimeout(kbd.timer);
      const s = kbd;
      kbd = null;
      endSession(s, true);
      return;
    }
    const sign = dirMap[ev.key];
    if (!sign || state.saving || (state.drag && state.drag !== kbd)) return;
    ev.preventDefault();
    if (!kbd || kbd.node !== node) {
      kbd = beginSession(f, node);
      state.drag = kbd;
      node.classList.add("is-active");
    }
    const s = kbd;
    const len = s.ax.s1 - s.ax.s0;
    const grid = state.snap ? (e.dir === "v" ? state.snap.cols : state.snap.rows) : 0;
    const step = grid ? len / grid : Math.max(1, Math.round(len / 100));
    let next = s.pos + sign * step * (ev.shiftKey ? 10 : 1);
    if (grid) next = s.ax.s0 + Math.round((next - s.ax.s0) / step) * step;
    next = clamp(Math.round(next), s.lo, s.hi);
    const glyph = G.SNAP_FRACTIONS.findIndex((fr) => Math.abs(s.ax.s0 + fr * len - next) < 1);
    paintSession(s, next, glyph >= 0 ? ["¼", "⅓", "½", "⅔", "¾"][glyph] : null);
    clearTimeout(s.timer);
    s.timer = setTimeout(() => {
      if (kbd !== s) return;
      kbd = null;
      endSession(s, true);
    }, 700);
  }

  // ------------------------------------------------------------------ edit tools
  async function runTool(key, cellId) {
    if (state.saving) return;
    const c = data.cells.find((x) => x.id === cellId);
    if (!c) return;
    const f = frame();
    const sb = sbId();
    if (key === "split-v" || key === "split-h") {
      const dir = key === "split-v" ? "v" : "h";
      const axis = dir === "v" ? "x" : "y";
      const payload = G.split(c, dir, G.minSizeFor(axisLen(f.map, axis)), splitGrid(axis));
      if (!payload) return;
      await saveBatch(payload, data.cells);
      return;
    }
    if (key === "remove") {
      const payload = G.removeMerge(data.cells, c.id, { exclude: sb ? [sb] : [] });
      if (!payload) return;
      const i = cellNumber(c.id);
      if (c.plugin && !window.confirm(`Remove cell ${i} and its ${cellName(c)} widget?`)) return;
      const ok = await saveBatch(payload, data.cells);
      if (ok) {
        state.selected = null;
        renderOverlay();
        const fallback = f.overlay.querySelector(".ev2pv-cell");
        if (fallback && !f.overlay.contains(document.activeElement))
          fallback.focus({ preventScroll: true });
      }
    }
  }

  // ------------------------------------------------------------------ overlay events
  function bindOverlay(f) {
    f.overlay.addEventListener("click", (ev) => {
      const tool = ev.target.closest("[data-ev2pv-tool]");
      if (tool) {
        ev.preventDefault();
        if (tool.getAttribute("aria-disabled") === "true") return;
        runTool(tool.dataset.ev2pvTool, state.selected);
        return;
      }
      const cell = ev.target.closest(".ev2pv-cell");
      if (!cell || state.drag) return;
      const id = cell.dataset.cellId;
      if (state.edit) {
        if (id === sbId() || (f.map && f.map.rotated)) return;
        state.selected = state.selected === id ? null : id;
        renderOverlay();
        return;
      }
      state.focused = id;
      markFocus();
      emit("ev2:cell-select", { cellId: id });
    });
    f.overlay.addEventListener("pointerdown", (ev) => {
      const node = ev.target.closest(".ev2pv-edge");
      if (node) onEdgePointerDown(ev, f, node);
    });
    f.overlay.addEventListener("keydown", (ev) => {
      const node = ev.target.closest(".ev2pv-edge");
      if (node) {
        onEdgeKey(ev, f, node);
        return;
      }
      if (ev.key === "Escape" && state.edit && state.selected) {
        // First Esc drops the selection; the next one reaches the steps
        // side, which leaves edit mode.
        ev.stopPropagation();
        const id = state.selected;
        state.selected = null;
        renderOverlay();
        const back = f.overlay.querySelector(`[data-fk="cell:${CSS.escape(id)}"]`);
        if (back) back.focus({ preventScroll: true });
      }
    });
    f.overlay.addEventListener("focusout", (ev) => {
      if (kbd && kbd.node === ev.target) {
        clearTimeout(kbd.timer);
        const s = kbd;
        kbd = null;
        endSession(s, true);
      }
    });
  }

  // ------------------------------------------------------------------ sync with the server
  // Re-read the layout and the display shapes from the editor page itself
  // (the partial's JSON), so a preset, a status bar toggle or a display change
  // made on the left is reflected here without a full page reload.
  let syncSeq = 0;
  async function sync() {
    const seq = ++syncSeq;
    let next = null;
    try {
      const res = await fetch(data.urls.edit, {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });
      if (!res.ok) return;
      const doc = new DOMParser().parseFromString(await res.text(), "text/html");
      next = readData(doc);
    } catch (err) {
      console.warn("[ev2 preview] sync failed:", err);
      return;
    }
    if (!next || seq !== syncSeq || state.drag || state.saving) return;
    adopt(next);
  }

  function adopt(next) {
    const groupsChanged = groupSig(next.groups) !== groupSig(data.groups);
    const layoutChanged =
      JSON.stringify(next.cells) !== JSON.stringify(data.cells) ||
      next.statusBarCellId !== data.statusBarCellId;
    const prevKey = group() && group().key;
    data = next;
    if (groupsChanged) {
      const keep = data.groups.findIndex((g) => g.key === prevKey);
      state.group = keep >= 0 ? keep : 0;
      buildGroups();
      setMode(state.mode);
      renderTabs();
      loadFrame(frame());
      fitFrame(frame());
      renderOverlay();
    } else if (layoutChanged) {
      renderOverlay();
    }
  }

  let syncTimer = null;
  function syncSoon() {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(sync, 250);
  }

  // ------------------------------------------------------------------ wiring
  root.addEventListener("click", (ev) => {
    const mode = ev.target.closest("[data-ev2pv-mode]");
    if (mode) {
      if (mode.dataset.ev2pvMode !== state.mode) setMode(mode.dataset.ev2pvMode);
      return;
    }
    const tab = ev.target.closest("[data-ev2pv-tab]");
    if (tab) {
      setTab(tab.dataset.ev2pvTab);
      return;
    }
    if (ev.target.closest("[data-ev2pv-reload]")) {
      reload({ fresh: true });
      setStatus("Re-rendering with fresh data…");
      return;
    }
    if (ev.target.closest("[data-ev2pv-native]")) {
      openNative();
      return;
    }
    if (ev.target.closest("[data-ev2pv-dialog-close]")) q("[data-ev2pv-dialog]").close();
  });

  function openNative() {
    const dialog = q("[data-ev2pv-dialog]");
    const host = q("[data-ev2pv-native-media]");
    const f = frame();
    if (!dialog || !f) return;
    const panel = state.mode === "panel";
    const source = panel ? f.img.getAttribute("src") : f.iframe.getAttribute("src");
    if (!source || source === "about:blank") return;
    const media = make(panel ? "img" : "iframe");
    media.src = source;
    media.style.width = `${f.g.w}px`;
    media.style.height = `${f.g.h}px`;
    if (panel) {
      media.alt = `What the panel shows, at ${f.g.w} × ${f.g.h}`;
      media.dataset.deviceQuantised = "";
    } else {
      media.title = `Preview at ${f.g.w} × ${f.g.h}`;
      media.setAttribute("sandbox", "allow-scripts allow-same-origin");
    }
    const holder = make("div");
    if (!panel && f.canvas.dataset.gamut) holder.dataset.gamut = f.canvas.dataset.gamut;
    if (!panel && f.canvas.dataset.greyLevels)
      holder.dataset.greyLevels = f.canvas.dataset.greyLevels;
    holder.append(media);
    host.replaceChildren(holder);
    dialog.showModal();
  }
  const dialog = q("[data-ev2pv-dialog]");
  if (dialog) {
    dialog.addEventListener("close", () => q("[data-ev2pv-native-media]").replaceChildren());
    dialog.addEventListener("click", (ev) => {
      if (ev.target === dialog) dialog.close(); // click on the backdrop
    });
  }

  document.addEventListener("ev2:cell-focus", (ev) => {
    const id = ev.detail && ev.detail.cellId ? String(ev.detail.cellId) : null;
    state.focused = id;
    markFocus();
  });

  // Snap to grid: {cols, rows} (optionally with on: false), or null for off.
  function readSnap(s) {
    return s && s.on !== false && Number(s.cols) > 1 && Number(s.rows) > 1
      ? { cols: Math.round(s.cols), rows: Math.round(s.rows) }
      : null;
  }
  document.addEventListener("ev2:placeholder", (ev) => {
    state.placeholder = Boolean(ev.detail && ev.detail.on);
    frames.forEach((f) => f.wrap.classList.toggle("is-placeholder", state.placeholder));
    renderOverlay();
  });

  document.addEventListener("ev2:snap", (ev) => {
    state.snap = readSnap(ev.detail);
    renderOverlay();
  });

  document.addEventListener("ev2:edit-mode", (ev) => {
    const d = ev.detail || {};
    const on = Boolean(d.on);
    if ("snap" in d) state.snap = readSnap(d.snap);
    else if (on && window.EV2 && window.EV2.snap) state.snap = readSnap(window.EV2.snap);
    if (!on) state.selected = null;
    state.edit = on;
    root.classList.toggle("is-edit", on);
    doneBtn.hidden = !on;
    renderOverlay();
  });
  // Stacked (phone) layout: the Edit on preview switch is far from the
  // preview, so the preview carries its own way out.
  const doneBtn = q("[data-ev2pv-done]");
  doneBtn.hidden = !state.edit;
  doneBtn.addEventListener("click", () => emit("ev2:edit-done", {}));

  document.addEventListener("ev2:saved", (ev) => {
    const d = ev.detail || {};
    if (Array.isArray(d.cells) && !state.drag) {
      data.cells = d.cells.map((c) => ({
        id: c.id,
        x: c.x,
        y: c.y,
        w: c.w,
        h: c.h,
        plugin: c.plugin || null,
      }));
      renderOverlay();
    }
    reloadSoon();
    syncSoon();
  });

  // ------------------------------------------------------------------ start
  if (window.EV2 && window.EV2.snap) state.snap = readSnap(window.EV2.snap);
  // Edit mode brought back after a split / remove reload: steps.js turned it
  // on before this deferred script was listening, so read it here.
  if (window.EV2 && window.EV2.ui && window.EV2.ui.draw) {
    state.edit = true;
    root.classList.add("is-edit");
    doneBtn.hidden = false;
  }
  const savedTab = store.get(TAB_KEY);
  const startTab = savedTab ? data.groups.findIndex((g) => g.key === savedTab) : -1;
  state.group = startTab >= 0 ? startTab : 0;
  buildGroups();
  renderTabs();
  setMode(state.mode);
  loadFrame(frame());
  fitFrame(frame());
  renderOverlay();

  window.EV2Preview = {
    reload: () => reload(),
    setTab,
    sync,
  };
})();
