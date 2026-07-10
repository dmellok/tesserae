/* Panels canvas editor (issue #60).
 *
 * The editing loop: hydrate a canvas document, render its elements (charts
 * via Chart.js), create elements by dragging from the palette, select, move,
 * and resize them with grid snap, edit live data, undo/redo, and autosave.
 * Following the design handoff's performance model, an in-flight drag/resize
 * mutates the DOM node directly and commits to state only on pointer-up, so
 * interaction stays smooth regardless of element count.
 *
 * Deferred: marquee multi-select, grouping, alignment guides, simulate mode,
 * panel-size switcher, and the server-side compose render (Phase 4). Plain
 * vanilla JS, no build step.
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
    sel: null,
    saveTimer: null,
    past: [],
    future: [],
    charts: {},
    clip: null,
    sim: false,
    devices: [],
  };

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
    if (!byId(S.sel)) S.sel = null;
    scheduleSave();
    paint();
  }
  function redo() {
    if (!S.future.length) return;
    S.past.push(snapshot());
    S.doc.els = S.future.pop();
    if (!byId(S.sel)) S.sel = null;
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
  function sampleAt(binding) {
    if (!binding) return null;
    var p = binding.split(".");
    var w = widgetFor(p[0]);
    return w && w.sample && p[1] in w.sample ? w.sample[p[1]] : null;
  }
  // Resolver handed to the shared renderer (PanelsRender): map a binding path
  // to its value from the catalog samples, with live edits applied in place.
  function resolve(binding) { return sampleAt(binding); }

  function destroyCharts() {
    Object.keys(S.charts).forEach(function (id) {
      try { S.charts[id].destroy(); } catch (e) { /* already gone */ }
    });
    S.charts = {};
  }

  // ---- artboard ---------------------------------------------------------
  function elNode(e) {
    var node = el("div", "el" + (e.id === S.sel ? " psel" : ""));
    node.dataset.id = e.id;
    node.style.cssText = "position:absolute;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w +
      "px;height:" + e.h + "px;" + (e.visible ? "" : "opacity:.4;");
    node.appendChild(PanelsRender.element(e, resolve));
    // Readability warning in simulate mode: small text dithers into mush on
    // low-palette panels.
    if (S.sim && TEXTLIKE[e.type] && e.font_size < READ_MIN) {
      node.appendChild(el("div", "elwarn", '<i class="ph-bold ph-warning"></i>Small text'));
    }
    if (e.id === S.sel) {
      node.appendChild(el("div", "ring"));
      if (!e.locked) {
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
  function computeSnap(e, nx, ny) {
    var xt = [0, S.doc.w / 2, S.doc.w];
    var yt = [0, S.doc.h / 2, S.doc.h];
    S.doc.els.forEach(function (o) {
      if (o.id === e.id) return;
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
  function copySel() { var e = byId(S.sel); if (e) S.clip = clone(e); }
  function placeCopy(src) {
    pushHistory();
    var d = clone(src);
    d.id = uid();
    d.x = clamp(d.x + 14, 0, S.doc.w - d.w);
    d.y = clamp(d.y + 14, 0, S.doc.h - d.h);
    S.doc.els.push(d);
    S.sel = d.id;
    scheduleSave();
    paint();
  }
  function paste() { if (S.clip) placeCopy(S.clip); }
  function duplicate() { var e = byId(S.sel); if (e) placeCopy(e); }
  function reorder(fn) {
    var e = byId(S.sel);
    if (!e) return;
    pushHistory();
    S.doc.els = S.doc.els.filter(function (x) { return x.id !== e.id; });
    fn(e);
    scheduleSave();
    paint();
  }
  function toFront() { reorder(function (e) { S.doc.els.push(e); }); }
  function toBack() { reorder(function (e) { S.doc.els.unshift(e); }); }
  function shift(dir) {
    var i = idxOf(S.sel);
    var j = i + dir;
    if (i < 0 || j < 0 || j >= S.doc.els.length) return;
    pushHistory();
    var tmp = S.doc.els[i];
    S.doc.els[i] = S.doc.els[j];
    S.doc.els[j] = tmp;
    scheduleSave();
    paint();
  }
  function nudge(dx, dy) {
    var e = byId(S.sel);
    if (!e || e.locked) return;
    pushHistory();
    e.x = clamp(e.x + dx, 0, S.doc.w - e.w);
    e.y = clamp(e.y + dy, 0, S.doc.h - e.h);
    scheduleSave();
    paint();
  }

  // ---- move + resize ----------------------------------------------------
  function select(id) { S.sel = id; paint(); }

  function onElDown(ev) {
    ev.stopPropagation();
    var id = ev.currentTarget.dataset.id;
    if (id !== S.sel) select(id);
    var e = byId(id);
    if (!e || e.locked) return;
    var node = ev.currentTarget;
    var z = currentZoom(), sx = ev.clientX, sy = ev.clientY, ox = e.x, oy = e.y;
    var before = snapshot(), moved = false;
    node.setPointerCapture(ev.pointerId);
    function move(m) {
      var dx = (m.clientX - sx) / z, dy = (m.clientY - sy) / z;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 2) return;
      moved = true;
      var rx = ox + dx, ry = oy + dy, nx, ny, gx = null, gy = null;
      if (m.altKey) {
        // Free placement: no grid, no alignment snapping.
        nx = Math.round(rx); ny = Math.round(ry);
      } else {
        var s = computeSnap(e, rx, ry);
        nx = s.x; ny = s.y; gx = s.gx; gy = s.gy;
      }
      nx = clamp(nx, 0, S.doc.w - e.w);
      ny = clamp(ny, 0, S.doc.h - e.h);
      node.style.left = nx + "px"; node.style.top = ny + "px";
      e.x = nx; e.y = ny;
      if (m.altKey) hideGuides(); else showGuides(gx, gy, e);
    }
    function up() {
      node.releasePointerCapture(ev.pointerId);
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
      hideGuides();
      if (moved) { commitHistory(before); scheduleSave(); renderProps(); updateUndoButtons(); }
    }
    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", up);
  }

  function onHandleDown(ev, dir) {
    ev.stopPropagation();
    var e = byId(S.sel);
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
      S.sel = e.id;
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
      var row = el("div", "lrow" + (e.id === S.sel ? " psel" : "") + (e.visible ? "" : " hidden"));
      row.innerHTML =
        '<i class="ph-bold ph-square ic"></i>' +
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
        select(e.id);
      });
      mount.appendChild(row);
    });
  }

  // ---- data + properties ------------------------------------------------
  function bindOptions() {
    var opts = ['<option value="">— none —</option>'];
    S.catalog.forEach(function (w) {
      (w.fields || []).forEach(function (f) {
        var path = w.key + "." + f.name;
        opts.push('<option value="' + path + '">' + w.name + " · " + (f.label || f.name) + "</option>");
      });
    });
    return opts.join("");
  }

  function renderDataPanel(mount, count) {
    if (count) count.textContent = S.catalog.length + " source" + (S.catalog.length === 1 ? "" : "s");
    mount.textContent = "";
    if (!S.catalog.length) {
      var none = el("div", "note"); none.style.padding = "14px";
      none.textContent = "No widgets declare a data schema yet.";
      mount.appendChild(none); return;
    }
    S.catalog.forEach(function (w) {
      var head = el("div", "wgh");
      var badge = el("span", "wi"); badge.style.background = w.color || "#256E6B";
      badge.innerHTML = '<i class="ph-bold ' + (w.icon || "ph-puzzle-piece") + '"></i>';
      head.appendChild(badge);
      head.appendChild(document.createTextNode(w.name || w.key));
      var ct = el("span", "ct"); ct.textContent = (w.fields || []).length;
      head.appendChild(ct); mount.appendChild(head);
      (w.fields || []).forEach(function (f) {
        var row = el("div", "fld");
        var k = el("span", "fk"); k.textContent = f.label || f.name;
        var vwrap = el("span", "dfield-val");
        var sv = w.sample ? w.sample[f.name] : null;
        if (f.type === "arr") {
          vwrap.innerHTML = '<span class="v"></span>';
          vwrap.querySelector(".v").textContent = (Array.isArray(sv) ? sv.length : 0) + " items";
        } else {
          var input = el("input", "dinput");
          input.value = sv == null ? "" : String(sv);
          input.addEventListener("input", function () {
            if (!w.sample) w.sample = {};
            w.sample[f.name] = f.type === "num" ? Number(input.value) || 0 : input.value;
            repaintBound(w.key + "." + f.name);
          });
          input.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
          vwrap.appendChild(input);
        }
        row.appendChild(k); row.appendChild(vwrap); mount.appendChild(row);
      });
    });
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

  function renderProps() {
    var mount = $("panels-data"), count = $("panels-source-count");
    if (!mount) return;
    var e = S.sel ? byId(S.sel) : null;
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
    if (S.sel === id) S.sel = null;
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
    };
    artboard = $("panels-artboard");
    scaler = $("panels-scaler");
    var palette = $("panels-palette");
    if (palette) renderPalette(palette);

    artboard.addEventListener("pointerdown", function (ev) { if (ev.target === artboard) select(null); });
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
      if (mod && (ev.key === "c" || ev.key === "C")) { ev.preventDefault(); copySel(); return; }
      if (mod && (ev.key === "v" || ev.key === "V")) { ev.preventDefault(); paste(); return; }
      if (mod && (ev.key === "d" || ev.key === "D")) { ev.preventDefault(); duplicate(); return; }
      if (!S.sel) return;
      if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); deleteEl(S.sel); return; }
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
