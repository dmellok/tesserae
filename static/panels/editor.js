/* Panels canvas editor (issue #60).
 *
 * The editing loop: hydrate a canvas document, render its elements (charts
 * via Chart.js), create elements by dragging from the palette, select, move,
 * and resize them with grid snap, edit live data, undo/redo, and autosave.
 * Following the design handoff's performance model, an in-flight drag/resize
 * mutates the DOM node directly and commits to state only on pointer-up, so
 * interaction stays smooth regardless of element count.
 *
 * Selection is a Set of element ids: single-click selects one, Shift-click
 * toggles, a marquee drag on empty canvas selects intersecting elements, and
 * grouped elements select as a unit. Dragging any selected element moves the
 * whole selection together. Plain vanilla JS, no build step.
 */
(function () {
  "use strict";

  var GRID = 4; // snap step; e-ink quantization is cleaner on 4px bounds.
  var MIN = 14; // minimum element size, px.
  var HIST_CAP = 80;
  var INKS = ["#1B1A16", "#F7F5F0", "#A84B2A", "#C28A04", "#3F5A88", "#4F6F36"];
  var HANDLES = ["tl", "tm", "tr", "rm", "br", "bm", "bl", "lm"];

  var PALETTE = [
    { type: "big", label: "Big number", icon: "ph-number-square-one", w: 160, h: 90 },
    { type: "small", label: "Small number", icon: "ph-number-square-two", w: 110, h: 54 },
    { type: "text", label: "Text label", icon: "ph-text-t", w: 160, h: 34 },
    { type: "icon", label: "Icon", icon: "ph-smiley", w: 64, h: 64 },
    { type: "spark", label: "Sparkline", icon: "ph-chart-line", w: 180, h: 70 },
    { type: "bar", label: "Bar chart", icon: "ph-chart-bar", w: 180, h: 90 },
    { type: "chip", label: "Chip / pill", icon: "ph-seal", w: 120, h: 40 },
    { type: "progress", label: "Progress", icon: "ph-gauge", w: 180, h: 22 },
    { type: "list", label: "List", icon: "ph-list-bullets", w: 200, h: 120 },
    { type: "image", label: "Image", icon: "ph-image", w: 120, h: 120 },
    { type: "shape", label: "Shape", icon: "ph-square", w: 100, h: 100 },
  ];

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function snap(v) { return Math.round(v / GRID) * GRID; }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  function uid() {
    return "el_" + Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-3);
  }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }

  var S = {
    cfg: null,
    doc: null,
    catalog: [],
    sel: new Set(), // selected element ids
    saveTimer: null,
    past: [],
    future: [],
    charts: {},
    clip: null, // array of cloned elements
    sim: false,
    devices: [],
    ov: {}, // per-source live-preview sample overrides: {sid: {field: value}}
  };

  // ---- selection --------------------------------------------------------
  function isSel(id) { return S.sel.has(id); }
  function selArr() { return Array.from(S.sel); }
  function selEls() { return S.doc.els.filter(function (e) { return S.sel.has(e.id); }); }
  function selCount() { return S.sel.size; }

  // Expand a list of ids so that selecting any grouped element pulls in the
  // rest of its group.
  function withGroups(ids) {
    var groups = {};
    var wanted = {};
    ids.forEach(function (id) { wanted[id] = 1; });
    S.doc.els.forEach(function (e) { if (e.group && wanted[e.id]) groups[e.group] = 1; });
    var out = {};
    ids.forEach(function (id) { out[id] = 1; });
    S.doc.els.forEach(function (e) { if (e.group && groups[e.group]) out[e.id] = 1; });
    return Object.keys(out);
  }

  // Replace the whole selection (group-expanded) and repaint.
  function setSel(ids) {
    S.sel = new Set(withGroups(ids || []));
    paint();
  }
  // Shift-click: toggle an element's group in/out of the selection.
  function toggleSel(id) {
    var grp = withGroups([id]);
    var next = selArr();
    if (S.sel.has(id)) {
      next = next.filter(function (x) { return grp.indexOf(x) < 0; });
    } else {
      next = next.concat(grp);
    }
    S.sel = new Set(next);
    paint();
  }
  // Drop ids that no longer exist (after undo/redo swaps the element list).
  function pruneSel() {
    S.sel = new Set(selArr().filter(function (id) { return byId(id); }));
  }

  // Text-like elements that read poorly below this size on e-ink panels.
  var READ_MIN = 16;
  var TEXTLIKE = { big: 1, small: 1, text: 1, chip: 1 };

  var artboard, scaler;

  // ---- history ----------------------------------------------------------
  function snapshot() { return clone(S.doc.els); }
  function commitHistory(before) {
    S.past.push(before);
    if (S.past.length > HIST_CAP) S.past.shift();
    S.future = [];
  }
  function pushHistory() { commitHistory(snapshot()); } // caller mutates after
  function undo() {
    if (!S.past.length) return;
    S.future.push(snapshot());
    S.doc.els = S.past.pop();
    pruneSel();
    scheduleSave();
    paint();
  }
  function redo() {
    if (!S.future.length) return;
    S.past.push(snapshot());
    S.doc.els = S.future.pop();
    pruneSel();
    scheduleSave();
    paint();
  }
  function updateUndoButtons() {
    var u = $("panels-undo"), r = $("panels-redo");
    if (u) u.classList.toggle("is-disabled", !S.past.length);
    if (r) r.classList.toggle("is-disabled", !S.future.length);
  }

  // ---- element defaults + data -----------------------------------------
  function defaultsFor(type, x, y, w, h) {
    return {
      id: uid(), type: type, name: type, x: x, y: y, w: w, h: h,
      binding: null, text: type === "text" ? "Label" : "", prefix: "", suffix: "",
      upper: false, weight: 700, color: "#1B1A16", align: "left",
      font_size: type === "big" ? 56 : type === "small" ? 28 : 18,
      icon: type === "icon" ? "star" : "", dither: true, visible: true, locked: false,
      group: null, shape_kind: "rect", mode: "fill", stroke: 2, radius: 0,
    };
  }

  function widgetFor(key) {
    return S.catalog.filter(function (c) { return c.key === key; })[0] || null;
  }
  function sourceFor(sid) {
    return (S.doc.sources || []).filter(function (s) { return s.sid === sid; })[0] || null;
  }
  function newSid() { return "src_" + Math.random().toString(36).slice(2, 8); }
  // A binding is ``<source-sid>.<field>``. Resolve to the source instance's
  // live-preview override if set, else the widget's declared sample value.
  function sampleAt(binding) {
    if (!binding) return null;
    var i = binding.indexOf(".");
    if (i < 0) return null;
    var sid = binding.slice(0, i), field = binding.slice(i + 1);
    var src = sourceFor(sid);
    if (!src) return null;
    var ov = S.ov[sid];
    if (ov && field in ov) return ov[field];
    var w = widgetFor(src.key);
    return w && w.sample && field in w.sample ? w.sample[field] : null;
  }
  // Resolver handed to the shared renderer (PanelsRender): map a binding path
  // to its value, with live edits applied in place.
  function resolve(binding) { return sampleAt(binding); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function destroyCharts() {
    Object.keys(S.charts).forEach(function (id) {
      try { S.charts[id].destroy(); } catch (e) { /* already gone */ }
    });
    S.charts = {};
  }

  // ---- artboard ---------------------------------------------------------
  function elNode(e) {
    var node = el("div", "el" + (isSel(e.id) ? " psel" : ""));
    node.dataset.id = e.id;
    node.style.cssText = "position:absolute;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w +
      "px;height:" + e.h + "px;" + (e.visible ? "" : "opacity:.4;");
    node.appendChild(PanelsRender.element(e, resolve));
    // Readability warning in simulate mode: small text dithers into mush on
    // low-palette panels.
    if (S.sim && TEXTLIKE[e.type] && e.font_size < READ_MIN) {
      node.appendChild(el("div", "elwarn", '<i class="ph-bold ph-warning"></i>Small text'));
    }
    if (isSel(e.id)) {
      node.appendChild(el("div", "ring" + (selCount() > 1 ? " multi" : "")));
      // Resize handles only when a single element is selected; a multi
      // selection shows just the rings and moves as a unit.
      if (!e.locked && selCount() === 1) {
        HANDLES.forEach(function (h) {
          var hd = el("div", "hd " + h);
          hd.dataset.h = h;
          hd.addEventListener("pointerdown", function (ev) { onHandleDown(ev, h); });
          node.appendChild(hd);
        });
      }
    }
    node.addEventListener("pointerdown", onElDown);
    return node;
  }

  function paint() {
    destroyCharts();
    artboard.style.width = S.doc.w + "px";
    artboard.style.height = S.doc.h + "px";
    artboard.textContent = "";
    S.doc.els.forEach(function (e) { artboard.appendChild(elNode(e)); });
    // Instantiate charts once nodes are in the DOM.
    S.doc.els.forEach(function (e) {
      if (e.type === "spark" || e.type === "bar") {
        var c = artboard.querySelector('[data-id="' + e.id + '"] canvas');
        if (c) S.charts[e.id] = PanelsRender.chart(e, c, resolve);
      }
    });
    // Drag overlays (re-created because textContent wiped the old ones).
    S.gV = el("div", "ov-guide v");
    S.gH = el("div", "ov-guide h");
    S.badge = el("div", "ov-badge");
    [S.gV, S.gH, S.badge].forEach(function (o) {
      o.style.display = "none";
      artboard.appendChild(o);
    });
    fitZoom();
    renderLayers();
    renderProps();
    updateUndoButtons();
  }

  function fitZoom() {
    var vp = scaler.parentElement;
    var z = Math.min((vp.clientWidth - 56) / S.doc.w, (vp.clientHeight - 56) / S.doc.h, 1);
    z = Math.max(z, 0.5);
    scaler.style.transform = "scale(" + z + ")";
    scaler.dataset.zoom = z;
  }
  function currentZoom() { return Number(scaler.dataset.zoom || 1); }

  // ---- snapping + alignment guides -------------------------------------
  var SNAP_THRESHOLD = 6; // artboard px

  function nearestTarget(anchors, targets) {
    var best = null;
    anchors.forEach(function (a) {
      targets.forEach(function (t) {
        var d = Math.abs(a.p - t);
        if (d <= SNAP_THRESHOLD && (!best || d < best.d)) best = { d: d, pos: t - a.off, guide: t };
      });
    });
    return best;
  }

  // Snap the dragged element's left/centre/right (and top/middle/bottom) to
  // other elements' equivalents and the panel edges/centre; fall back to the
  // grid on any axis with no alignment hit. Returns the snapped position plus
  // the guide coordinates to draw (null when that axis fell back to grid).
  function computeSnap(e, nx, ny, skip) {
    var xt = [0, S.doc.w / 2, S.doc.w];
    var yt = [0, S.doc.h / 2, S.doc.h];
    S.doc.els.forEach(function (o) {
      if (o.id === e.id || (skip && skip.has(o.id))) return;
      xt.push(o.x, o.x + o.w / 2, o.x + o.w);
      yt.push(o.y, o.y + o.h / 2, o.y + o.h);
    });
    var mx = nearestTarget([{ p: nx, off: 0 }, { p: nx + e.w / 2, off: e.w / 2 }, { p: nx + e.w, off: e.w }], xt);
    var my = nearestTarget([{ p: ny, off: 0 }, { p: ny + e.h / 2, off: e.h / 2 }, { p: ny + e.h, off: e.h }], yt);
    return {
      x: mx ? mx.pos : snap(nx),
      y: my ? my.pos : snap(ny),
      gx: mx ? mx.guide : null,
      gy: my ? my.guide : null,
    };
  }

  function showGuides(gx, gy, e) {
    if (gx != null) { S.gV.style.left = gx + "px"; S.gV.style.display = "block"; } else S.gV.style.display = "none";
    if (gy != null) { S.gH.style.top = gy + "px"; S.gH.style.display = "block"; } else S.gH.style.display = "none";
    S.badge.textContent = Math.round(e.x) + " · " + Math.round(e.y);
    S.badge.style.left = e.x + "px";
    S.badge.style.top = Math.max(0, e.y - 22) + "px";
    S.badge.style.display = "block";
  }
  function hideGuides() {
    if (S.gV) S.gV.style.display = "none";
    if (S.gH) S.gH.style.display = "none";
    if (S.badge) S.badge.style.display = "none";
  }

  // ---- clipboard + z-order + nudge -------------------------------------
  function idxOf(id) {
    for (var i = 0; i < S.doc.els.length; i++) if (S.doc.els[i].id === id) return i;
    return -1;
  }
  function copySel() { var els = selEls(); if (els.length) S.clip = els.map(clone); }
  // Place cloned elements offset from their source, remapping group ids so the
  // copies form their own group(s), then select the new set.
  function placeCopies(list) {
    if (!list || !list.length) return;
    pushHistory();
    var gmap = {};
    var ids = [];
    list.forEach(function (src) {
      var d = clone(src);
      d.id = uid();
      d.x = clamp(d.x + 14, 0, S.doc.w - d.w);
      d.y = clamp(d.y + 14, 0, S.doc.h - d.h);
      if (d.group) {
        if (!gmap[d.group]) gmap[d.group] = "g_" + uid();
        d.group = gmap[d.group];
      }
      S.doc.els.push(d);
      ids.push(d.id);
    });
    S.sel = new Set(ids);
    scheduleSave();
    paint();
  }
  function paste() { placeCopies(S.clip); }
  function duplicate() { placeCopies(selEls()); }
  // Move the whole selection to front / back, preserving relative paint order.
  function toFront() {
    if (!S.sel.size) return;
    pushHistory();
    var keep = S.doc.els.filter(function (e) { return !S.sel.has(e.id); });
    var moved = S.doc.els.filter(function (e) { return S.sel.has(e.id); });
    S.doc.els = keep.concat(moved);
    scheduleSave();
    paint();
  }
  function toBack() {
    if (!S.sel.size) return;
    pushHistory();
    var keep = S.doc.els.filter(function (e) { return !S.sel.has(e.id); });
    var moved = S.doc.els.filter(function (e) { return S.sel.has(e.id); });
    S.doc.els = moved.concat(keep);
    scheduleSave();
    paint();
  }
  // Fine z-shift by one, single selection only (order is ambiguous for many).
  function shift(dir) {
    if (S.sel.size !== 1) return;
    var i = idxOf(selArr()[0]);
    var j = i + dir;
    if (i < 0 || j < 0 || j >= S.doc.els.length) return;
    pushHistory();
    var tmp = S.doc.els[i];
    S.doc.els[i] = S.doc.els[j];
    S.doc.els[j] = tmp;
    scheduleSave();
    paint();
  }
  // Clamp a delta so the selection's bounding box stays inside the artboard,
  // keeping every element's relative position intact. Each entry carries the
  // element's origin (ox, oy) at the start of the gesture.
  function clampGroupDelta(items, dx, dy) {
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    items.forEach(function (m) {
      minX = Math.min(minX, m.ox); maxX = Math.max(maxX, m.ox + m.e.w);
      minY = Math.min(minY, m.oy); maxY = Math.max(maxY, m.oy + m.e.h);
    });
    return { dx: clamp(dx, -minX, S.doc.w - maxX), dy: clamp(dy, -minY, S.doc.h - maxY) };
  }
  function nudge(dx, dy) {
    var els = selEls().filter(function (e) { return !e.locked; });
    if (!els.length) return;
    pushHistory();
    var items = els.map(function (e) { return { e: e, ox: e.x, oy: e.y }; });
    var c = clampGroupDelta(items, dx, dy);
    items.forEach(function (m) { m.e.x = m.ox + c.dx; m.e.y = m.oy + c.dy; });
    scheduleSave();
    paint();
  }
  // Rectangle overlap test against a marquee box (artboard px).
  function intersects(e, x1, y1, x2, y2) {
    return e.x < x2 && e.x + e.w > x1 && e.y < y2 && e.y + e.h > y1;
  }

  // ---- move + resize ----------------------------------------------------
  function select(id) { setSel(id ? [id] : []); }

  function onElDown(ev) {
    ev.stopPropagation();
    var id = ev.currentTarget.dataset.id;
    var e = byId(id);
    if (!e) return;
    // Shift-click toggles this element (and its group) without starting a drag.
    if (ev.shiftKey) { toggleSel(id); return; }
    // Clicking outside the current selection collapses to this element (its
    // group). Clicking a member of a multi selection keeps it, so the whole
    // set can be dragged together.
    var wasSel = isSel(id);
    if (!wasSel) select(id); // repaints; nodes below are re-queried afterwards
    if (e.locked) return;

    // The primary (clicked) element drives snapping; every unlocked selected
    // element moves with it. Re-query live nodes since select() may have
    // repainted the artboard.
    var items = selEls().filter(function (x) { return !x.locked; }).map(function (x) {
      return { e: x, ox: x.x, oy: x.y, node: artboard.querySelector('[data-id="' + x.id + '"]') };
    });
    var clicked = artboard.querySelector('[data-id="' + id + '"]');
    if (!clicked) return;
    var z = currentZoom(), sx = ev.clientX, sy = ev.clientY, ox = e.x, oy = e.y;
    var before = snapshot(), moved = false;
    clicked.setPointerCapture(ev.pointerId);
    function move(m) {
      var dx = (m.clientX - sx) / z, dy = (m.clientY - sy) / z;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 2) return;
      moved = true;
      var rx = ox + dx, ry = oy + dy, nx, ny, gx = null, gy = null;
      if (m.altKey) {
        // Free placement: no grid, no alignment snapping.
        nx = Math.round(rx); ny = Math.round(ry);
      } else {
        var s = computeSnap(e, rx, ry, S.sel);
        nx = s.x; ny = s.y; gx = s.gx; gy = s.gy;
      }
      var c = clampGroupDelta(items, nx - ox, ny - oy);
      items.forEach(function (mi) {
        mi.e.x = mi.ox + c.dx; mi.e.y = mi.oy + c.dy;
        if (mi.node) { mi.node.style.left = mi.e.x + "px"; mi.node.style.top = mi.e.y + "px"; }
      });
      if (m.altKey) hideGuides(); else showGuides(gx, gy, e);
    }
    function up() {
      clicked.releasePointerCapture(ev.pointerId);
      clicked.removeEventListener("pointermove", move);
      clicked.removeEventListener("pointerup", up);
      hideGuides();
      if (moved) { commitHistory(before); scheduleSave(); renderProps(); updateUndoButtons(); }
      else if (wasSel && selCount() > 1) { select(id); } // click-through collapses
    }
    clicked.addEventListener("pointermove", move);
    clicked.addEventListener("pointerup", up);
  }

  function onHandleDown(ev, dir) {
    ev.stopPropagation();
    var e = byId(selArr()[0]);
    if (!e || e.locked) return;
    var node = ev.currentTarget.parentElement;
    var z = currentZoom(), sx = ev.clientX, sy = ev.clientY;
    var o = { x: e.x, y: e.y, w: e.w, h: e.h };
    var before = snapshot(), changed = false;
    ev.currentTarget.setPointerCapture(ev.pointerId);
    function move(m) {
      var dx = (m.clientX - sx) / z, dy = (m.clientY - sy) / z;
      changed = true;
      var nx = o.x, ny = o.y, nw = o.w, nh = o.h;
      if (dir.indexOf("l") >= 0) { nw = Math.max(MIN, o.w - dx); nx = o.x + o.w - nw; }
      if (dir.indexOf("r") >= 0) { nw = Math.max(MIN, o.w + dx); }
      if (dir.indexOf("t") >= 0) { nh = Math.max(MIN, o.h - dy); ny = o.y + o.h - nh; }
      if (dir.indexOf("b") >= 0) { nh = Math.max(MIN, o.h + dy); }
      nx = clamp(snap(nx), 0, S.doc.w - MIN);
      ny = clamp(snap(ny), 0, S.doc.h - MIN);
      nw = clamp(snap(nw), MIN, S.doc.w - nx);
      nh = clamp(snap(nh), MIN, S.doc.h - ny);
      e.x = nx; e.y = ny; e.w = nw; e.h = nh;
      node.style.left = nx + "px"; node.style.top = ny + "px";
      node.style.width = nw + "px"; node.style.height = nh + "px";
      if (S.charts[e.id]) { try { S.charts[e.id].resize(); } catch (err) { /* noop */ } }
    }
    function up(m) {
      ev.currentTarget.releasePointerCapture(ev.pointerId);
      ev.currentTarget.removeEventListener("pointermove", move);
      ev.currentTarget.removeEventListener("pointerup", up);
      if (changed) { commitHistory(before); scheduleSave(); }
      paint();
    }
    ev.currentTarget.addEventListener("pointermove", move);
    ev.currentTarget.addEventListener("pointerup", up);
  }

  // ---- marquee multi-select --------------------------------------------
  // Drag on the empty artboard to rubber-band a selection; Shift extends the
  // current one. A plain click (no drag) clears. Preview highlights live via
  // an inline outline so charts aren't torn down on every move; the real
  // selection paint happens once on pointer-up.
  function onArtboardDown(ev) {
    if (ev.target !== artboard) return;
    var r = artboard.getBoundingClientRect(), z = currentZoom();
    var sx = (ev.clientX - r.left) / z, sy = (ev.clientY - r.top) / z;
    var additive = ev.shiftKey;
    var base = additive ? selArr() : [];
    var box = el("div", "ov-marquee");
    artboard.appendChild(box);
    var moved = false, hit = [];
    artboard.setPointerCapture(ev.pointerId);
    function preview(ids) {
      var want = {};
      ids.forEach(function (id) { want[id] = 1; });
      S.doc.els.forEach(function (e) {
        var node = artboard.querySelector('[data-id="' + e.id + '"]');
        if (node) node.style.outline = want[e.id] ? "1.5px solid var(--t-accent)" : "";
      });
    }
    function move(m) {
      var cx = clamp((m.clientX - r.left) / z, 0, S.doc.w);
      var cy = clamp((m.clientY - r.top) / z, 0, S.doc.h);
      if (!moved && Math.abs(cx - sx) + Math.abs(cy - sy) < 3) return;
      moved = true;
      var x1 = Math.min(sx, cx), y1 = Math.min(sy, cy), x2 = Math.max(sx, cx), y2 = Math.max(sy, cy);
      box.style.left = x1 + "px"; box.style.top = y1 + "px";
      box.style.width = (x2 - x1) + "px"; box.style.height = (y2 - y1) + "px";
      box.style.display = "block";
      hit = S.doc.els
        .filter(function (e) { return e.visible !== false && intersects(e, x1, y1, x2, y2); })
        .map(function (e) { return e.id; });
      preview(withGroups(base.concat(hit)));
    }
    function up() {
      artboard.releasePointerCapture(ev.pointerId);
      artboard.removeEventListener("pointermove", move);
      artboard.removeEventListener("pointerup", up);
      box.remove();
      if (!moved) { if (!additive) select(null); return; }
      setSel(base.concat(hit)); // full paint clears the inline preview outlines
    }
    artboard.addEventListener("pointermove", move);
    artboard.addEventListener("pointerup", up);
  }

  // ---- palette create-drag ---------------------------------------------
  function renderPalette(mount) {
    PALETTE.forEach(function (p) {
      var tile = el("div", "pi");
      tile.dataset.type = p.type; tile.title = p.label;
      tile.innerHTML = '<span class="ico"><i class="ph-bold ' + p.icon + '"></i></span><span class="lab"></span>';
      tile.querySelector(".lab").textContent = p.label;
      tile.addEventListener("pointerdown", function (ev) { onPaletteDown(ev, p); });
      mount.appendChild(tile);
    });
  }

  function onPaletteDown(ev, p) {
    ev.preventDefault();
    var ghost = el("div", "ghost", p.label);
    ghost.style.cssText = "position:fixed;pointer-events:none;z-index:9999;left:0;top:0;padding:6px 10px;" +
      "background:var(--t-surface);border:1px solid var(--t-border-strong);border-radius:8px;" +
      "font-size:12px;font-weight:600;box-shadow:0 4px 12px rgba(16,12,8,.14)";
    document.body.appendChild(ghost);
    function move(m) { ghost.style.transform = "translate(" + (m.clientX + 8) + "px," + (m.clientY + 8) + "px)"; }
    function up(m) {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      ghost.remove();
      var r = artboard.getBoundingClientRect(), z = currentZoom();
      var cx = (m.clientX - r.left) / z, cy = (m.clientY - r.top) / z;
      if (cx < 0 || cy < 0 || cx > S.doc.w || cy > S.doc.h) return;
      pushHistory();
      var x = clamp(snap(cx - p.w / 2), 0, S.doc.w - p.w);
      var y = clamp(snap(cy - p.h / 2), 0, S.doc.h - p.h);
      var e = defaultsFor(p.type, x, y, p.w, p.h);
      S.doc.els.push(e);
      S.sel = new Set([e.id]);
      scheduleSave();
      paint();
    }
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
    move(ev);
  }

  // ---- layers -----------------------------------------------------------
  function renderLayers() {
    var mount = $("panels-layers");
    if (!mount) return;
    mount.textContent = "";
    if (!S.doc.els.length) {
      var empty = el("div", "note"); empty.style.padding = "14px";
      empty.textContent = "Drag an element from the palette onto the artboard.";
      mount.appendChild(empty); return;
    }
    S.doc.els.slice().reverse().forEach(function (e) {
      var row = el("div", "lrow" + (isSel(e.id) ? " psel" : "") + (e.visible ? "" : " hidden"));
      row.innerHTML =
        '<i class="ph-bold ' + (e.group ? "ph-link" : "ph-square") + ' ic"></i>' +
        '<span class="nm"></span>' +
        '<span class="act">' +
          '<i class="ph-bold ' + (e.visible ? "ph-eye" : "ph-eye-slash") + ' li" data-act="vis" title="Show / hide"></i>' +
          '<i class="ph-bold ' + (e.locked ? "ph-lock-simple" : "ph-lock-simple-open") + ' li" data-act="lock" title="Lock"></i>' +
        "</span>";
      row.querySelector(".nm").textContent = e.name || e.type;
      row.addEventListener("pointerdown", function (ev) {
        var act = ev.target && ev.target.dataset ? ev.target.dataset.act : null;
        if (act === "vis") { ev.stopPropagation(); pushHistory(); e.visible = !e.visible; scheduleSave(); paint(); return; }
        if (act === "lock") { ev.stopPropagation(); pushHistory(); e.locked = !e.locked; scheduleSave(); paint(); return; }
        if (ev.shiftKey) toggleSel(e.id); else select(e.id);
      });
      mount.appendChild(row);
    });
  }

  // ---- data + properties ------------------------------------------------
  function bindOptions() {
    var opts = ['<option value="">— none —</option>'];
    (S.doc.sources || []).forEach(function (src) {
      var w = widgetFor(src.key);
      if (!w) return;
      var label = src.name || w.name;
      (w.fields || []).forEach(function (f) {
        var path = src.sid + "." + f.name;
        opts.push('<option value="' + esc(path) + '">' + esc(label) + " · " +
          esc(f.label || f.name) + "</option>");
      });
    });
    return opts.join("");
  }

  // ---- data sources -----------------------------------------------------
  function addSource(key) {
    var w = widgetFor(key);
    if (!w) return;
    pushHistory();
    if (!S.doc.sources) S.doc.sources = [];
    var src = { sid: newSid(), key: key, name: "", options: {} };
    S.doc.sources.push(src);
    scheduleSave();
    renderProps();
    openConfig(src.sid); // straight into configuration
  }
  function renameSource(sid, name) {
    var src = sourceFor(sid);
    if (!src) return;
    src.name = name;
    scheduleSave();
  }
  // Removing a source unbinds any element pointing at it.
  function removeSource(sid) {
    if (!sourceFor(sid)) return;
    pushHistory();
    S.doc.sources = (S.doc.sources || []).filter(function (s) { return s.sid !== sid; });
    S.doc.els.forEach(function (e) {
      if (e.binding && e.binding.slice(0, e.binding.indexOf(".")) === sid) e.binding = null;
    });
    delete S.ov[sid];
    scheduleSave();
    paint();
  }

  function sourceValue(sid, key, field) {
    var ov = S.ov[sid];
    if (ov && field in ov) return ov[field];
    var w = widgetFor(key);
    return w && w.sample ? w.sample[field] : null;
  }

  function renderDataPanel(mount, count) {
    var sources = S.doc.sources || [];
    if (count) count.textContent = sources.length + " source" + (sources.length === 1 ? "" : "s");
    mount.textContent = "";

    var add = el("button", "minibtn", '<i class="ph-bold ph-plus"></i> Add data source');
    add.style.cssText = "width:100%;justify-content:center;margin-bottom:10px";
    add.addEventListener("click", function () { openAddMenu(add); });
    mount.appendChild(add);

    if (!S.catalog.length) {
      var none = el("div", "note"); none.style.padding = "10px 2px";
      none.textContent = "No widgets declare a data schema yet.";
      mount.appendChild(none); return;
    }
    if (!sources.length) {
      var empty = el("div", "note"); empty.style.padding = "10px 2px";
      empty.textContent = "Add a data source, then bind an element's field to it.";
      mount.appendChild(empty); return;
    }

    sources.forEach(function (src) {
      var w = widgetFor(src.key);
      var head = el("div", "wgh");
      var badge = el("span", "wi");
      badge.style.background = (w && w.color) || "#256E6B";
      badge.innerHTML = '<i class="ph-bold ' + ((w && w.icon) || "ph-puzzle-piece") + '"></i>';
      head.appendChild(badge);
      var nameInput = el("input", "srcname");
      nameInput.value = src.name || (w ? w.name : src.key);
      nameInput.placeholder = w ? w.name : src.key;
      nameInput.addEventListener("input", function () { renameSource(src.sid, nameInput.value); });
      nameInput.addEventListener("change", function () { paint(); });
      nameInput.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
      head.appendChild(nameInput);
      var cfg = el("i", "ph-bold ph-sliders li", "");
      cfg.title = "Configure"; cfg.style.cursor = "pointer";
      cfg.addEventListener("click", function () { openConfig(src.sid); });
      head.appendChild(cfg);
      var rm = el("i", "ph-bold ph-trash li", "");
      rm.title = "Remove source"; rm.style.cursor = "pointer";
      rm.addEventListener("click", function () { removeSource(src.sid); });
      head.appendChild(rm);
      mount.appendChild(head);

      if (!w) {
        var miss = el("div", "note"); miss.style.padding = "4px 2px";
        miss.textContent = "Widget \"" + src.key + "\" is unavailable.";
        mount.appendChild(miss); return;
      }
      (w.fields || []).forEach(function (f) {
        var row = el("div", "fld");
        var k = el("span", "fk"); k.textContent = f.label || f.name;
        var vwrap = el("span", "dfield-val");
        var sv = sourceValue(src.sid, src.key, f.name);
        if (f.type === "arr") {
          vwrap.innerHTML = '<span class="v"></span>';
          vwrap.querySelector(".v").textContent = (Array.isArray(sv) ? sv.length : 0) + " items";
        } else {
          var input = el("input", "dinput");
          input.value = sv == null ? "" : String(sv);
          input.addEventListener("input", function () {
            if (!S.ov[src.sid]) S.ov[src.sid] = {};
            S.ov[src.sid][f.name] = f.type === "num" ? Number(input.value) || 0 : input.value;
            repaintBound(src.sid + "." + f.name);
          });
          input.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
          vwrap.appendChild(input);
        }
        row.appendChild(k); row.appendChild(vwrap); mount.appendChild(row);
      });
    });
  }

  // A small popover listing catalog widgets to add as a source. Multiple
  // instances of the same widget are allowed (two cities, two batteries).
  function openAddMenu(anchor) {
    var existing = document.querySelector(".src-add-menu");
    if (existing) { existing.remove(); return; }
    var menu = el("div", "src-add-menu");
    S.catalog.forEach(function (w) {
      var item = el("button", "src-add-item");
      item.innerHTML = '<span class="wi" style="background:' + ((w.color) || "#256E6B") +
        '"><i class="ph-bold ' + (w.icon || "ph-puzzle-piece") + '"></i></span>';
      item.appendChild(document.createTextNode(w.name || w.key));
      item.addEventListener("click", function () { menu.remove(); addSource(w.key); });
      menu.appendChild(item);
    });
    if (!S.catalog.length) {
      var n = el("div", "note"); n.style.padding = "8px"; n.textContent = "No widgets available.";
      menu.appendChild(n);
    }
    anchor.parentNode.insertBefore(menu, anchor.nextSibling);
    setTimeout(function () {
      document.addEventListener("pointerdown", function close(ev) {
        if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("pointerdown", close); }
      });
    }, 0);
  }

  // Live-data: repaint only elements bound to the edited field, cheaply.
  function repaintBound(path) {
    S.doc.els.forEach(function (e) {
      if (e.binding !== path) return;
      var node = artboard.querySelector('[data-id="' + e.id + '"]');
      if (!node) return;
      if ((e.type === "spark" || e.type === "bar") && S.charts[e.id]) {
        var arr = PanelsRender.seriesOf(e, resolve) || PanelsRender.DEMO_SERIES;
        S.charts[e.id].data.datasets[0].data = arr;
        S.charts[e.id].data.labels = arr.map(function () { return ""; });
        S.charts[e.id].update("none");
        return;
      }
      var fresh = PanelsRender.element(e, resolve);
      node.replaceChild(fresh, node.firstChild);
    });
  }

  // ---- grouping + alignment --------------------------------------------
  function groupSel() {
    var els = selEls();
    if (els.length < 2) return;
    pushHistory();
    var gid = "g_" + uid();
    els.forEach(function (e) { e.group = gid; });
    scheduleSave();
    paint();
  }
  function ungroupSel() {
    var els = selEls();
    if (!els.some(function (e) { return e.group; })) return;
    pushHistory();
    els.forEach(function (e) { e.group = null; });
    scheduleSave();
    paint();
  }
  // Align selected elements to their shared bounding box on one edge / centre.
  function alignSel(kind) {
    var els = selEls().filter(function (e) { return !e.locked; });
    if (els.length < 2) return;
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    els.forEach(function (e) {
      minX = Math.min(minX, e.x); maxX = Math.max(maxX, e.x + e.w);
      minY = Math.min(minY, e.y); maxY = Math.max(maxY, e.y + e.h);
    });
    var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    pushHistory();
    els.forEach(function (e) {
      if (kind === "left") e.x = minX;
      else if (kind === "right") e.x = maxX - e.w;
      else if (kind === "hcenter") e.x = Math.round(cx - e.w / 2);
      else if (kind === "top") e.y = minY;
      else if (kind === "bottom") e.y = maxY - e.h;
      else if (kind === "vcenter") e.y = Math.round(cy - e.h / 2);
      e.x = clamp(e.x, 0, S.doc.w - e.w);
      e.y = clamp(e.y, 0, S.doc.h - e.h);
    });
    scheduleSave();
    paint();
  }
  function colorSel(ink) {
    var els = selEls();
    if (!els.length) return;
    pushHistory();
    els.forEach(function (e) { e.color = ink; });
    scheduleSave();
    paint();
  }

  function renderGroupProps(mount) {
    mount.textContent = "";
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-selection-all"></i>' + selCount() + " selected"));

    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-align-left"></i>Align'));
    var arow = el("div", "prow");
    arow.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
    [["left", "Left"], ["hcenter", "Center"], ["right", "Right"],
      ["top", "Top"], ["vcenter", "Middle"], ["bottom", "Bottom"]].forEach(function (a) {
      var b = el("button", "minibtn", a[1]);
      b.addEventListener("click", function () { alignSel(a[0]); });
      arow.appendChild(b);
    });
    mount.appendChild(arow);

    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-palette"></i>Colour'));
    var swrow = el("div", "prow");
    INKS.forEach(function (ink) {
      var sw = el("span");
      sw.style.cssText = "width:22px;height:22px;border-radius:6px;margin-right:6px;cursor:pointer;background:" +
        ink + ";border:1px solid var(--t-border)";
      sw.addEventListener("click", function () { colorSel(ink); });
      swrow.appendChild(sw);
    });
    mount.appendChild(swrow);

    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-stack"></i>Group'));
    var grow = el("div", "prow");
    grow.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
    var grp = el("button", "minibtn", '<i class="ph-bold ph-link"></i> Group');
    var ungrp = el("button", "minibtn", '<i class="ph-bold ph-link-break"></i> Ungroup');
    grp.addEventListener("click", groupSel);
    ungrp.addEventListener("click", ungroupSel);
    grow.appendChild(grp); grow.appendChild(ungrp);
    mount.appendChild(grow);

    var zr = el("div", "prow");
    zr.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
    var front = el("button", "minibtn", '<i class="ph-bold ph-arrow-line-up"></i> Front');
    var back = el("button", "minibtn", '<i class="ph-bold ph-arrow-line-down"></i> Back');
    var dup = el("button", "minibtn", '<i class="ph-bold ph-copy"></i> Duplicate');
    front.addEventListener("click", toFront);
    back.addEventListener("click", toBack);
    dup.addEventListener("click", duplicate);
    zr.appendChild(front); zr.appendChild(back); zr.appendChild(dup);
    mount.appendChild(zr);

    var del = el("div", "prow");
    var btn = el("button", "minibtn", '<i class="ph-bold ph-trash"></i> Delete');
    btn.addEventListener("click", deleteSel);
    del.appendChild(btn); mount.appendChild(del);
  }

  function renderProps() {
    var mount = $("panels-data"), count = $("panels-source-count");
    if (!mount) return;
    if (selCount() > 1) { renderGroupProps(mount); return; }
    var e = selCount() ? byId(selArr()[0]) : null;
    if (!e) { renderDataPanel(mount, count); return; }

    mount.textContent = "";
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-database"></i>Binding'));
    var brow = el("div", "prow"); brow.innerHTML = '<span class="plab">Field</span>';
    var sel = el("select", "psel"); sel.innerHTML = bindOptions(); sel.value = e.binding || "";
    sel.addEventListener("change", function () { pushHistory(); e.binding = sel.value || null; scheduleSave(); paint(); });
    brow.appendChild(sel); mount.appendChild(brow);

    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-palette"></i>Colour'));
    var swrow = el("div", "prow");
    INKS.forEach(function (ink) {
      var sw = el("span");
      sw.style.cssText = "width:22px;height:22px;border-radius:6px;margin-right:6px;cursor:pointer;background:" +
        ink + (ink === e.color ? ";outline:2px solid var(--t-accent);outline-offset:2px" : ";border:1px solid var(--t-border)");
      sw.addEventListener("click", function () { pushHistory(); e.color = ink; scheduleSave(); paint(); });
      swrow.appendChild(sw);
    });
    mount.appendChild(swrow);

    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-ruler"></i>Arrange'));
    var arr = el("div", "prow");
    arr.innerHTML = '<span class="plab">Position</span><span class="mono">' + e.x + " · " + e.y +
      '</span>';
    mount.appendChild(arr);
    var sz = el("div", "prow");
    sz.innerHTML = '<span class="plab">Size</span><span class="mono">' + e.w + " × " + e.h + "</span>";
    mount.appendChild(sz);

    var zr = el("div", "prow");
    zr.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
    var front = el("button", "minibtn", '<i class="ph-bold ph-arrow-line-up"></i> Front');
    var back = el("button", "minibtn", '<i class="ph-bold ph-arrow-line-down"></i> Back');
    var dup = el("button", "minibtn", '<i class="ph-bold ph-copy"></i> Duplicate');
    front.addEventListener("click", toFront);
    back.addEventListener("click", toBack);
    dup.addEventListener("click", duplicate);
    zr.appendChild(front); zr.appendChild(back); zr.appendChild(dup);
    mount.appendChild(zr);

    var del = el("div", "prow");
    var btn = el("button", "minibtn", '<i class="ph-bold ph-trash"></i> Delete');
    btn.addEventListener("click", function () { deleteEl(e.id); });
    del.appendChild(btn); mount.appendChild(del);
  }

  // ---- helpers ----------------------------------------------------------
  function byId(id) { return S.doc.els.filter(function (e) { return e.id === id; })[0] || null; }
  function deleteEl(id) {
    pushHistory();
    S.doc.els = S.doc.els.filter(function (e) { return e.id !== id; });
    S.sel.delete(id);
    scheduleSave(); paint();
  }
  function deleteSel() {
    if (!S.sel.size) return;
    pushHistory();
    S.doc.els = S.doc.els.filter(function (e) { return !S.sel.has(e.id); });
    S.sel = new Set();
    scheduleSave(); paint();
  }

  function scheduleSave() {
    var status = $("panels-status");
    if (status) status.textContent = "saving…";
    clearTimeout(S.saveTimer);
    S.saveTimer = setTimeout(save, 400);
  }
  function save() {
    fetch(S.cfg.saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(S.doc),
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function () { var s = $("panels-status"); if (s) s.textContent = "saved"; })
      .catch(function () { var s = $("panels-status"); if (s) s.textContent = "save failed"; });
  }

  // ---- source config drawer --------------------------------------------
  // Loads the widget's cell_options form (rendered server-side with the grid
  // editor's macros) into a drawer, wires the shared interactive controls,
  // and parses the submitted form back into an options dict server-side.
  function openConfig(sid) {
    var src = sourceFor(sid);
    if (!src) return;
    var w = widgetFor(src.key);
    var overlay = $("panels-drawer"), body = $("panels-drawer-body"), title = $("panels-drawer-title");
    if (!overlay || !body || !S.cfg.sourceFormUrl) return;
    title.textContent = (src.name || (w ? w.name : src.key)) + " · configure";
    body.innerHTML = '<div class="note" style="padding:12px">Loading…</div>';
    body.dataset.sid = sid;
    overlay.classList.add("open");
    fetch(S.cfg.sourceFormUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: src.key, sid: src.sid, options: src.options || {} }),
    })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        if (body.dataset.sid !== sid) return; // drawer switched while loading
        body.innerHTML = html;
        var tc = window.tesseraeComponents;
        if (tc) {
          if (tc.attachLocationSearch) tc.attachLocationSearch(body);
          if (tc.attachSliders) tc.attachSliders(body);
          if (tc.attachPresetNumbers) tc.attachPresetNumbers(body);
        }
      })
      .catch(function () { body.innerHTML = '<div class="note" style="padding:12px">Failed to load options.</div>'; });
  }
  function closeConfig() {
    var overlay = $("panels-drawer");
    if (overlay) overlay.classList.remove("open");
  }
  function saveConfig() {
    var body = $("panels-drawer-body");
    if (!body) return;
    var src = sourceFor(body.dataset.sid);
    if (!src) { closeConfig(); return; }
    var form = new FormData();
    body.querySelectorAll("input,select,textarea").forEach(function (node) {
      if (!node.name) return;
      if (node.type === "checkbox" || node.type === "radio") {
        if (node.checked) form.append(node.name, node.value || "on");
      } else if (node.tagName === "SELECT" && node.multiple) {
        Array.prototype.forEach.call(node.selectedOptions, function (o) { form.append(node.name, o.value); });
      } else {
        form.append(node.name, node.value);
      }
    });
    form.append("key", src.key);
    fetch(S.cfg.sourceOptionsUrl, { method: "POST", body: form })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (j) {
        pushHistory();
        src.options = j.options || {};
        scheduleSave();
        closeConfig();
        paint();
      })
      .catch(function () { var s = $("panels-status"); if (s) s.textContent = "config save failed"; });
  }

  // ---- devices + send ---------------------------------------------------
  function initDevices() {
    var sel = $("panels-device");
    var btn = $("panels-send");
    if (sel && S.cfg.devicesUrl) {
      fetch(S.cfg.devicesUrl)
        .then(function (r) { return r.json(); })
        .then(function (p) {
          S.devices = p.devices || [];
          S.devices.forEach(function (d) {
            var o = document.createElement("option");
            o.value = d.id;
            // The label carries the panel resolution, since picking the
            // device is also how the canvas resolution is set.
            o.textContent = (d.name || d.id) + (d.w && d.h ? "  ·  " + d.w + "×" + d.h : "");
            if (d.w) o.dataset.w = d.w;
            if (d.h) o.dataset.h = d.h;
            sel.appendChild(o);
          });
          syncDeviceSelection();
        })
        .catch(function () { /* no devices endpoint */ });
      sel.addEventListener("change", onDeviceChange);
    }
    if (btn) btn.addEventListener("click", sendCanvas);
  }

  function syncDeviceSelection() {
    var sel = $("panels-device");
    if (sel && S.doc && S.doc.device_ids && S.doc.device_ids.length) sel.value = S.doc.device_ids[0];
  }

  // Picking the target device binds it AND sets the canvas resolution to that
  // device's real panel dims.
  function onDeviceChange() {
    var sel = $("panels-device");
    if (!sel) return;
    S.doc.device_ids = sel.value ? [sel.value] : [];
    var opt = sel.options[sel.selectedIndex];
    var w = opt ? Number(opt.dataset.w) : 0;
    var h = opt ? Number(opt.dataset.h) : 0;
    if (w && h && (w !== S.doc.w || h !== S.doc.h)) setPanelSize(w, h);
    else scheduleSave();
  }

  function sendCanvas() {
    var sel = $("panels-device");
    var status = $("panels-status");
    var did = sel ? sel.value : "";
    if (!did) { if (status) status.textContent = "pick a device first"; return; }
    if (status) status.textContent = "sending…";
    fetch(S.cfg.sendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_ids: [did] }),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!status) return;
        status.textContent =
          res.ok && res.j.sent && res.j.sent.length
            ? "sent to panel"
            : (res.j && res.j.error) || "send failed";
      })
      .catch(function () { if (status) status.textContent = "send failed"; });
  }

  // ---- panel size + simulate -------------------------------------------
  function setPanelSize(w, h) {
    pushHistory();
    S.doc.w = w;
    S.doc.h = h;
    // Keep every element inside the new bounds.
    S.doc.els.forEach(function (e) {
      e.w = Math.min(e.w, w);
      e.h = Math.min(e.h, h);
      e.x = clamp(e.x, 0, w - e.w);
      e.y = clamp(e.y, 0, h - e.h);
    });
    scheduleSave();
    paint();
  }
  function toggleSim() {
    S.sim = !S.sim;
    artboard.classList.toggle("sim", S.sim);
    var btn = $("panels-sim");
    if (btn) btn.classList.toggle("on", S.sim);
    paint();
  }

  // ---- boot -------------------------------------------------------------
  function init() {
    var root = document.querySelector(".ed");
    if (!root) return;
    S.cfg = {
      docUrl: root.dataset.docUrl,
      saveUrl: root.dataset.saveUrl,
      catalogUrl: root.dataset.catalogUrl,
      devicesUrl: root.dataset.devicesUrl,
      sendUrl: root.dataset.sendUrl,
      sourceFormUrl: root.dataset.sourceFormUrl,
      sourceOptionsUrl: root.dataset.sourceOptionsUrl,
    };
    artboard = $("panels-artboard");
    scaler = $("panels-scaler");
    var palette = $("panels-palette");
    if (palette) renderPalette(palette);

    var drawerSave = $("panels-drawer-save");
    if (drawerSave) drawerSave.addEventListener("click", saveConfig);
    ["panels-drawer-cancel", "panels-drawer-close", "panels-drawer-scrim"].forEach(function (id) {
      var node = $(id);
      if (node) node.addEventListener("click", closeConfig);
    });

    artboard.addEventListener("pointerdown", onArtboardDown);
    var undoBtn = $("panels-undo"), redoBtn = $("panels-redo");
    if (undoBtn) undoBtn.addEventListener("click", undo);
    if (redoBtn) redoBtn.addEventListener("click", redo);
    var simBtn = $("panels-sim");
    if (simBtn) simBtn.addEventListener("click", toggleSim);
    initDevices();

    document.addEventListener("keydown", function (ev) {
      var t = document.activeElement;
      var typing = t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
      var mod = ev.metaKey || ev.ctrlKey;
      // Undo/redo work globally (even while a field is focused).
      if (mod && (ev.key === "z" || ev.key === "Z")) {
        ev.preventDefault(); if (ev.shiftKey) redo(); else undo(); return;
      }
      if (mod && (ev.key === "y" || ev.key === "Y")) { ev.preventDefault(); redo(); return; }
      if (typing) return; // leave native editing shortcuts alone in inputs
      if (ev.key === "Escape") { select(null); return; }
      if (mod && (ev.key === "a" || ev.key === "A")) {
        ev.preventDefault(); setSel(S.doc.els.map(function (e) { return e.id; })); return;
      }
      if (mod && (ev.key === "g" || ev.key === "G")) {
        ev.preventDefault(); if (ev.shiftKey) ungroupSel(); else groupSel(); return;
      }
      if (mod && (ev.key === "c" || ev.key === "C")) { ev.preventDefault(); copySel(); return; }
      if (mod && (ev.key === "v" || ev.key === "V")) { ev.preventDefault(); paste(); return; }
      if (mod && (ev.key === "d" || ev.key === "D")) { ev.preventDefault(); duplicate(); return; }
      if (!S.sel.size) return;
      if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); deleteSel(); return; }
      if (ev.key === "[") { ev.preventDefault(); shift(-1); return; }
      if (ev.key === "]") { ev.preventDefault(); shift(1); return; }
      var step = ev.shiftKey ? 10 : 1;
      if (ev.key === "ArrowLeft") { ev.preventDefault(); nudge(-step, 0); }
      else if (ev.key === "ArrowRight") { ev.preventDefault(); nudge(step, 0); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); nudge(0, -step); }
      else if (ev.key === "ArrowDown") { ev.preventDefault(); nudge(0, step); }
    });
    window.addEventListener("resize", function () { if (S.doc) fitZoom(); });

    Promise.all([
      fetch(S.cfg.catalogUrl).then(function (r) { return r.json(); }),
      fetch(S.cfg.docUrl).then(function (r) { return r.json(); }),
    ])
      .then(function (res) {
        S.catalog = (res[0] && res[0].widgets) || [];
        S.doc = res[1];
        if (!S.doc.els) S.doc.els = [];
        if (!S.doc.sources) S.doc.sources = [];
        S.ov = {};
        var title = $("panels-title");
        if (title) title.textContent = S.doc.name || "Untitled Panel";
        syncDeviceSelection();
        paint();
      })
      .catch(function () { var s = $("panels-status"); if (s) s.textContent = "load failed"; });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
