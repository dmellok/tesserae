/* Panels canvas editor (issue #60).
 *
 * A freeform canvas of REAL widget renders. Each element is a widget instance
 * rendered as one of its fragments (ctx.fragment); the editor mounts the
 * widget's own client.js into a shadow root so what you place is what the
 * panel paints. Drag a widget/fragment from the palette, select, move, resize,
 * group, align, configure per-element options, undo, autosave, and Send.
 *
 * Selection is a Set of element ids: single-click selects one, Shift-click
 * toggles, a marquee drag selects intersecting elements, grouped elements
 * select as a unit, and dragging any selected element moves the set. In-flight
 * drag/resize mutates the DOM node directly and commits on pointer-up. Mounted
 * widget shadows are cached by fingerprint so unchanged elements aren't
 * re-rendered on every repaint. Plain vanilla JS, no build step.
 */
(function () {
  "use strict";

  var GRID = 4; // snap step; e-ink quantization is cleaner on 4px bounds.
  var MIN = 14; // minimum element size, px.
  var HIST_CAP = 80;
  var HANDLES = ["tl", "tm", "tr", "rm", "br", "bm", "bl", "lm"];
  var SIZE_THRESHOLDS = [
    { size: "xs", max: 200 },
    { size: "sm", max: 400 },
    { size: "md", max: 700 },
  ];
  var DEFAULT_FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

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
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function resolveSize(w, h) {
    var longer = Math.max(w, h);
    for (var i = 0; i < SIZE_THRESHOLDS.length; i++) {
      if (longer <= SIZE_THRESHOLDS[i].max) return SIZE_THRESHOLDS[i].size;
    }
    return "lg";
  }

  var S = {
    cfg: null,
    doc: null,
    catalog: [],
    sel: new Set(), // selected element ids
    saveTimer: null,
    past: [],
    future: [],
    clip: null, // array of cloned elements
    sim: false,
    devices: [],
    mount: {}, // elId -> { fp, host } cached widget shadow hosts
    pq: "", // palette search query (lowercased)
  };

  var artboard, scaler;

  // ---- catalog lookups --------------------------------------------------
  function widgetFor(key) {
    return S.catalog.filter(function (c) { return c.key === key; })[0] || null;
  }
  function fragmentsOf(key) {
    var w = widgetFor(key);
    return (w && w.fragments) || [];
  }
  function elLabel(e) {
    var w = widgetFor(e.widget);
    if (!e.widget) return "Empty";
    var base = w ? w.name : e.widget;
    if (e.fragment && e.fragment !== "full") {
      var f = fragmentsOf(e.widget).filter(function (x) { return x.id === e.fragment; })[0];
      base += " · " + (f ? f.label : e.fragment);
    }
    return base;
  }

  // ---- selection --------------------------------------------------------
  function isSel(id) { return S.sel.has(id); }
  function selArr() { return Array.from(S.sel); }
  function selEls() { return S.doc.els.filter(function (e) { return S.sel.has(e.id); }); }
  function selCount() { return S.sel.size; }

  function withGroups(ids) {
    var groups = {}, wanted = {};
    ids.forEach(function (id) { wanted[id] = 1; });
    S.doc.els.forEach(function (e) { if (e.group && wanted[e.id]) groups[e.group] = 1; });
    var out = {};
    ids.forEach(function (id) { out[id] = 1; });
    S.doc.els.forEach(function (e) { if (e.group && groups[e.group]) out[e.id] = 1; });
    return Object.keys(out);
  }
  function setSel(ids) { S.sel = new Set(withGroups(ids || [])); paint(); }
  function toggleSel(id) {
    var grp = withGroups([id]);
    var next = selArr();
    if (S.sel.has(id)) next = next.filter(function (x) { return grp.indexOf(x) < 0; });
    else next = next.concat(grp);
    S.sel = new Set(next);
    paint();
  }
  function pruneSel() { S.sel = new Set(selArr().filter(function (id) { return byId(id); })); }

  // ---- history ----------------------------------------------------------
  function snapshot() { return clone(S.doc.els); }
  function commitHistory(before) {
    S.past.push(before);
    if (S.past.length > HIST_CAP) S.past.shift();
    S.future = [];
  }
  function pushHistory() { commitHistory(snapshot()); }
  function undo() {
    if (!S.past.length) return;
    S.future.push(snapshot());
    S.doc.els = S.past.pop();
    pruneSel(); scheduleSave(); paint();
  }
  function redo() {
    if (!S.future.length) return;
    S.past.push(snapshot());
    S.doc.els = S.future.pop();
    pruneSel(); scheduleSave(); paint();
  }
  function updateUndoButtons() {
    var u = $("panels-undo"), r = $("panels-redo");
    if (u) u.classList.toggle("is-disabled", !S.past.length);
    if (r) r.classList.toggle("is-disabled", !S.future.length);
  }

  // ---- element factory --------------------------------------------------
  function makeElement(widget, fragment, x, y, w, h) {
    return {
      id: uid(), widget: widget || "", fragment: fragment || "full",
      options: {}, x: x, y: y, w: w, h: h,
      dither: true, visible: true, locked: false, group: null,
    };
  }

  // ---- live widget mount (cached) --------------------------------------
  function fpOf(e) {
    return e.widget + "|" + (e.fragment || "full") + "|" + e.w + "x" + e.h + "|" +
      JSON.stringify(e.options || {});
  }
  function ctxFor(e) {
    var w = widgetFor(e.widget);
    return {
      cell: {
        w: e.w, h: e.h, size: resolveSize(e.w, e.h),
        plugin: e.widget, plugin_id: e.widget,
        options: e.options || {}, fragment: e.fragment || "full",
      },
      panel: { w: S.doc.w, h: S.doc.h, portrait: S.doc.h > S.doc.w },
      font: { family: DEFAULT_FONT, weight: 400 },
      data: (w && w.sample) || null,
      fragment: e.fragment || "full",
      preview: false,
    };
  }
  // Mount the real widget into a fresh shadow-rooted host. Async import is
  // cached by the browser, so this is cheap after the first mount of a widget.
  function mountWidget(e, host) {
    var shadow = host.attachShadow({ mode: "open" });
    if (!e.widget) return;
    var prefix = window.TESSERAE_URL_PREFIX || "";
    import(prefix + "/plugins/" + e.widget + "/client.js")
      .then(function (mod) {
        if (typeof mod.default !== "function") throw new Error("no default export");
        return mod.default(shadow, ctxFor(e));
      })
      .catch(function (err) {
        shadow.innerHTML =
          '<div style="font:11px/1.3 system-ui;color:#a3402a;padding:6px">' +
          esc(e.widget) + ": " + esc(err && err.message ? err.message : err) + "</div>";
      });
  }

  // ---- artboard ---------------------------------------------------------
  function elNode(e) {
    var node = el("div", "el" + (isSel(e.id) ? " psel" : "") + (e.widget ? "" : " el-empty"));
    node.dataset.id = e.id;
    node.style.cssText = "position:absolute;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w +
      "px;height:" + e.h + "px;" + (e.visible ? "" : "opacity:.4;");

    // Reuse the cached widget host when nothing that affects the render changed;
    // otherwise mount fresh. The host is pointer-transparent so drag/select hit
    // the element wrapper, not the widget preview.
    var fp = fpOf(e);
    var cached = S.mount[e.id];
    var host;
    if (cached && cached.fp === fp && cached.host) {
      host = cached.host;
    } else {
      host = el("div", "elhost");
      host.style.cssText = "width:100%;height:100%;container-type:size;overflow:hidden;pointer-events:none";
      S.mount[e.id] = { fp: fp, host: host };
      mountWidget(e, host);
    }
    node.appendChild(host);
    if (!e.widget) node.appendChild(el("div", "elplace", '<i class="ph-bold ph-cards-three"></i>'));

    if (isSel(e.id)) {
      node.appendChild(el("div", "ring" + (selCount() > 1 ? " multi" : "")));
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
    // Drop cached mounts for elements that no longer exist.
    var live = {};
    S.doc.els.forEach(function (e) { live[e.id] = 1; });
    Object.keys(S.mount).forEach(function (id) { if (!live[id]) delete S.mount[id]; });

    artboard.style.width = S.doc.w + "px";
    artboard.style.height = S.doc.h + "px";
    artboard.textContent = "";
    S.doc.els.forEach(function (e) { artboard.appendChild(elNode(e)); });
    S.gV = el("div", "ov-guide v");
    S.gH = el("div", "ov-guide h");
    S.badge = el("div", "ov-badge");
    [S.gV, S.gH, S.badge].forEach(function (o) { o.style.display = "none"; artboard.appendChild(o); });
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
  var SNAP_THRESHOLD = 6;
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
  function placeCopies(list) {
    if (!list || !list.length) return;
    pushHistory();
    var gmap = {}, ids = [];
    list.forEach(function (src) {
      var d = clone(src);
      d.id = uid();
      d.x = clamp(d.x + 14, 0, S.doc.w - d.w);
      d.y = clamp(d.y + 14, 0, S.doc.h - d.h);
      if (d.group) { if (!gmap[d.group]) gmap[d.group] = "g_" + uid(); d.group = gmap[d.group]; }
      S.doc.els.push(d);
      ids.push(d.id);
    });
    S.sel = new Set(ids);
    scheduleSave(); paint();
  }
  function paste() { placeCopies(S.clip); }
  function duplicate() { placeCopies(selEls()); }
  function toFront() {
    if (!S.sel.size) return;
    pushHistory();
    var keep = S.doc.els.filter(function (e) { return !S.sel.has(e.id); });
    var moved = S.doc.els.filter(function (e) { return S.sel.has(e.id); });
    S.doc.els = keep.concat(moved);
    scheduleSave(); paint();
  }
  function toBack() {
    if (!S.sel.size) return;
    pushHistory();
    var keep = S.doc.els.filter(function (e) { return !S.sel.has(e.id); });
    var moved = S.doc.els.filter(function (e) { return S.sel.has(e.id); });
    S.doc.els = moved.concat(keep);
    scheduleSave(); paint();
  }
  function shift(dir) {
    if (S.sel.size !== 1) return;
    var i = idxOf(selArr()[0]);
    var j = i + dir;
    if (i < 0 || j < 0 || j >= S.doc.els.length) return;
    pushHistory();
    var tmp = S.doc.els[i];
    S.doc.els[i] = S.doc.els[j];
    S.doc.els[j] = tmp;
    scheduleSave(); paint();
  }
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
    scheduleSave(); paint();
  }
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
    if (ev.shiftKey) { toggleSel(id); return; }
    var wasSel = isSel(id);
    if (!wasSel) select(id);
    if (e.locked) return;

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
      if (m.altKey) { nx = Math.round(rx); ny = Math.round(ry); }
      else { var s = computeSnap(e, rx, ry, S.sel); nx = s.x; ny = s.y; gx = s.gx; gy = s.gy; }
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
      else if (wasSel && selCount() > 1) { select(id); }
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
    }
    function up() {
      ev.currentTarget.releasePointerCapture(ev.pointerId);
      ev.currentTarget.removeEventListener("pointermove", move);
      ev.currentTarget.removeEventListener("pointerup", up);
      if (changed) { commitHistory(before); scheduleSave(); }
      paint(); // re-mounts the widget at the new size
    }
    ev.currentTarget.addEventListener("pointermove", move);
    ev.currentTarget.addEventListener("pointerup", up);
  }

  // ---- marquee multi-select --------------------------------------------
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
      setSel(base.concat(hit));
    }
    artboard.addEventListener("pointermove", move);
    artboard.addEventListener("pointerup", up);
  }

  // ---- palette (widgets -> fragments) ----------------------------------
  function renderPalette(mount) {
    mount.textContent = "";
    if (!S.catalog.length) {
      var none = el("div", "note"); none.style.padding = "14px";
      none.textContent = "No widgets available.";
      mount.appendChild(none); return;
    }
    var q = S.pq || "";
    var shown = 0;
    S.catalog.forEach(function (w) {
      var wname = (w.name || w.key).toLowerCase();
      // Match the widget by name/id (show all its parts) or a fragment by its
      // label (show just the matching parts).
      var wMatch = !q || wname.indexOf(q) >= 0 || w.key.toLowerCase().indexOf(q) >= 0;
      var frags = (w.fragments || []).filter(function (f) {
        return wMatch || (f.label || f.id).toLowerCase().indexOf(q) >= 0;
      });
      if (!frags.length) return;
      shown++;
      // A widget with more than the implicit "full" fragment is decomposed;
      // tint its header so composable widgets stand out in the palette.
      var hasParts = (w.fragments || []).length > 1;
      var group = el("div", "pwg");
      var head = el("div", "pwgh" + (hasParts ? " frag" : ""));
      head.innerHTML = '<i class="ph-bold ' + (w.icon || "ph-puzzle-piece") + '"></i>';
      head.appendChild(document.createTextNode(w.name || w.key));
      group.appendChild(head);
      frags.forEach(function (f) {
        var tile = el("div", "pi");
        tile.title = (w.name || w.key) + " · " + (f.label || f.id);
        tile.innerHTML = '<span class="ico"><i class="ph-bold ' + (f.icon || w.icon || "ph-puzzle-piece") +
          '"></i></span><span class="lab"></span>';
        tile.querySelector(".lab").textContent = f.id === "full" ? (w.name || w.key) : (f.label || f.id);
        tile.addEventListener("pointerdown", function (ev) {
          onPaletteDown(ev, { key: w.key, fragment: f.id, w: f.w || 160, h: f.h || 120, label: tile.title });
        });
        group.appendChild(tile);
      });
      mount.appendChild(group);
    });
    if (!shown) {
      var no = el("div", "note"); no.style.padding = "14px";
      no.textContent = "No widgets match “" + q + "”.";
      mount.appendChild(no);
    }
  }

  function onPaletteDown(ev, item) {
    ev.preventDefault();
    var ghost = el("div", "ghost", item.label);
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
      var x = clamp(snap(cx - item.w / 2), 0, S.doc.w - item.w);
      var y = clamp(snap(cy - item.h / 2), 0, S.doc.h - item.h);
      var e = makeElement(item.key, item.fragment, x, y, item.w, item.h);
      S.doc.els.push(e);
      S.sel = new Set([e.id]);
      scheduleSave(); paint();
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
      empty.textContent = "Drag a widget from the palette onto the canvas.";
      mount.appendChild(empty); return;
    }
    S.doc.els.slice().reverse().forEach(function (e) {
      var row = el("div", "lrow" + (isSel(e.id) ? " psel" : "") + (e.visible ? "" : " hidden"));
      row.innerHTML =
        '<i class="ph-bold ' + (e.group ? "ph-link" : "ph-cards-three") + ' ic"></i>' +
        '<span class="nm"></span>' +
        '<span class="act">' +
          '<i class="ph-bold ' + (e.visible ? "ph-eye" : "ph-eye-slash") + ' li" data-act="vis" title="Show / hide"></i>' +
          '<i class="ph-bold ' + (e.locked ? "ph-lock-simple" : "ph-lock-simple-open") + ' li" data-act="lock" title="Lock"></i>' +
        "</span>";
      row.querySelector(".nm").textContent = elLabel(e);
      row.addEventListener("pointerdown", function (ev) {
        var act = ev.target && ev.target.dataset ? ev.target.dataset.act : null;
        if (act === "vis") { ev.stopPropagation(); pushHistory(); e.visible = !e.visible; scheduleSave(); paint(); return; }
        if (act === "lock") { ev.stopPropagation(); pushHistory(); e.locked = !e.locked; scheduleSave(); paint(); return; }
        if (ev.shiftKey) toggleSel(e.id); else select(e.id);
      });
      mount.appendChild(row);
    });
  }

  // ---- grouping + alignment --------------------------------------------
  function groupSel() {
    var els = selEls();
    if (els.length < 2) return;
    pushHistory();
    var gid = "g_" + uid();
    els.forEach(function (e) { e.group = gid; });
    scheduleSave(); paint();
  }
  function ungroupSel() {
    var els = selEls();
    if (!els.some(function (e) { return e.group; })) return;
    pushHistory();
    els.forEach(function (e) { e.group = null; });
    scheduleSave(); paint();
  }
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
    scheduleSave(); paint();
  }

  // ---- properties -------------------------------------------------------
  function propRowBtns(mount, e) {
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
    btn.addEventListener("click", e ? function () { deleteEl(e.id); } : deleteSel);
    del.appendChild(btn); mount.appendChild(del);
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
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-stack"></i>Group'));
    var grow = el("div", "prow");
    grow.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
    var grp = el("button", "minibtn", '<i class="ph-bold ph-link"></i> Group');
    var ungrp = el("button", "minibtn", '<i class="ph-bold ph-link-break"></i> Ungroup');
    grp.addEventListener("click", groupSel);
    ungrp.addEventListener("click", ungroupSel);
    grow.appendChild(grp); grow.appendChild(ungrp);
    mount.appendChild(grow);
    propRowBtns(mount, null);
  }

  function renderEmptyProps(mount) {
    mount.textContent = "";
    var note = el("div", "note");
    note.style.cssText = "padding:16px 4px;line-height:1.5";
    note.textContent = "Drag a widget from the palette onto the canvas, then select it here to pick a part and configure it.";
    mount.appendChild(note);
  }

  function renderProps() {
    var mount = $("panels-data");
    if (!mount) return;
    if (selCount() > 1) { renderGroupProps(mount); return; }
    var e = selCount() ? byId(selArr()[0]) : null;
    if (!e) { renderEmptyProps(mount); return; }

    mount.textContent = "";
    // Widget picker.
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-puzzle-piece"></i>Widget'));
    var wrow = el("div", "prow"); wrow.innerHTML = '<span class="plab">Source</span>';
    var wsel = el("select", "psel");
    var wopts = ['<option value="">— choose widget —</option>'];
    S.catalog.forEach(function (w) { wopts.push('<option value="' + esc(w.key) + '">' + esc(w.name || w.key) + "</option>"); });
    wsel.innerHTML = wopts.join("");
    wsel.value = e.widget || "";
    wsel.addEventListener("change", function () {
      pushHistory();
      e.widget = wsel.value;
      var frs = fragmentsOf(e.widget);
      e.fragment = frs.length ? frs[0].id : "full";
      e.options = {};
      scheduleSave(); paint();
    });
    wrow.appendChild(wsel); mount.appendChild(wrow);

    // Fragment picker (only when the widget declares more than the full one).
    var frags = fragmentsOf(e.widget);
    if (frags.length > 1) {
      var frow = el("div", "prow"); frow.innerHTML = '<span class="plab">Part</span>';
      var fsel = el("select", "psel");
      fsel.innerHTML = frags.map(function (f) { return '<option value="' + esc(f.id) + '">' + esc(f.label || f.id) + "</option>"; }).join("");
      fsel.value = e.fragment || "full";
      fsel.addEventListener("change", function () { pushHistory(); e.fragment = fsel.value; scheduleSave(); paint(); });
      frow.appendChild(fsel); mount.appendChild(frow);
    }

    // Configure (per-instance options drawer).
    if (e.widget) {
      var cfgrow = el("div", "prow");
      var cfg = el("button", "minibtn", '<i class="ph-bold ph-sliders"></i> Configure');
      cfg.style.width = "100%"; cfg.style.justifyContent = "center";
      cfg.addEventListener("click", function () { openConfig(e.id); });
      cfgrow.appendChild(cfg); mount.appendChild(cfgrow);
    }

    // Arrange.
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-ruler"></i>Arrange'));
    var arr = el("div", "prow");
    arr.innerHTML = '<span class="plab">Position</span><span class="mono">' + e.x + " · " + e.y + "</span>";
    mount.appendChild(arr);
    var sz = el("div", "prow");
    sz.innerHTML = '<span class="plab">Size</span><span class="mono">' + e.w + " × " + e.h + "</span>";
    mount.appendChild(sz);
    var drow = el("div", "prow");
    drow.innerHTML = '<span class="plab">Dither</span>';
    var dbtn = el("button", "minibtn",
      e.dither ? '<i class="ph-bold ph-check-square"></i> On' : '<i class="ph-bold ph-square"></i> Flat');
    dbtn.addEventListener("click", function () { pushHistory(); e.dither = !e.dither; scheduleSave(); renderProps(); });
    drow.appendChild(dbtn); mount.appendChild(drow);

    propRowBtns(mount, e);
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

  // ---- per-element config drawer ---------------------------------------
  function openConfig(eid) {
    var e = byId(eid);
    if (!e || !e.widget) return;
    var w = widgetFor(e.widget);
    var overlay = $("panels-drawer"), body = $("panels-drawer-body"), title = $("panels-drawer-title");
    if (!overlay || !body || !S.cfg.sourceFormUrl) return;
    title.textContent = (w ? w.name : e.widget) + " · configure";
    body.innerHTML = '<div class="note" style="padding:12px">Loading…</div>';
    body.dataset.eid = eid;
    overlay.classList.add("open");
    fetch(S.cfg.sourceFormUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: e.widget, sid: e.id, options: e.options || {} }),
    })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        if (body.dataset.eid !== eid) return;
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
    var e = byId(body.dataset.eid);
    if (!e) { closeConfig(); return; }
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
    form.append("key", e.widget);
    fetch(S.cfg.sourceOptionsUrl, { method: "POST", body: form })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (j) {
        pushHistory();
        e.options = j.options || {};
        scheduleSave(); closeConfig(); paint();
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
          res.ok && res.j.sent && res.j.sent.length ? "sent to panel" : (res.j && res.j.error) || "send failed";
      })
      .catch(function () { if (status) status.textContent = "send failed"; });
  }

  // ---- panel size + simulate -------------------------------------------
  function setPanelSize(w, h) {
    pushHistory();
    S.doc.w = w;
    S.doc.h = h;
    S.doc.els.forEach(function (e) {
      e.w = Math.min(e.w, w);
      e.h = Math.min(e.h, h);
      e.x = clamp(e.x, 0, w - e.w);
      e.y = clamp(e.y, 0, h - e.h);
    });
    scheduleSave(); paint();
  }
  function toggleSim() {
    S.sim = !S.sim;
    artboard.classList.toggle("sim", S.sim);
    var btn = $("panels-sim");
    if (btn) btn.classList.toggle("on", S.sim);
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
    var psearch = $("panels-palette-search");
    if (psearch) psearch.addEventListener("input", function () {
      S.pq = psearch.value.trim().toLowerCase();
      var m = $("panels-palette");
      if (m) renderPalette(m);
    });
    initDevices();

    document.addEventListener("keydown", function (ev) {
      var t = document.activeElement;
      var typing = t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
      var mod = ev.metaKey || ev.ctrlKey;
      if (mod && (ev.key === "z" || ev.key === "Z")) { ev.preventDefault(); if (ev.shiftKey) redo(); else undo(); return; }
      if (mod && (ev.key === "y" || ev.key === "Y")) { ev.preventDefault(); redo(); return; }
      if (typing) return;
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
        var palette = $("panels-palette");
        if (palette) renderPalette(palette);
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
