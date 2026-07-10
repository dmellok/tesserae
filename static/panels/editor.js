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
  var DEMO_SERIES = [4, 6, 5, 8, 7, 9, 6, 8, 7, 10, 9, 11];

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
  };

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
  function valueOf(e) {
    if (e.binding) {
      var v = sampleAt(e.binding);
      return v == null ? "" : v;
    }
    return e.text;
  }
  function seriesOf(e) {
    var v = e.binding ? sampleAt(e.binding) : null;
    return Array.isArray(v) && v.length ? v.filter(function (n) { return typeof n === "number"; }) : null;
  }
  function listRowsOf(e) {
    var v = e.binding ? sampleAt(e.binding) : null;
    if (Array.isArray(v) && v.length) {
      return v.slice(0, 8).map(function (it) {
        if (it && typeof it === "object") return { label: it.label || it.name || "", meta: it.meta || "" };
        return { label: String(it), meta: "" };
      });
    }
    return [
      { label: "First item", meta: "10:30" },
      { label: "Second item", meta: "12:00" },
      { label: "Third item", meta: "14:15" },
    ];
  }

  // ---- element rendering ------------------------------------------------
  function renderElement(e) {
    var val = valueOf(e);
    var justify = e.align === "center" ? "center" : e.align === "right" ? "flex-end" : "flex-start";
    var base = "width:100%;height:100%;display:flex;align-items:center;overflow:hidden;justify-content:" + justify + ";";

    if (e.type === "big" || e.type === "small") {
      var d = el("div", null, (e.prefix || "") + (val === "" ? "0" : String(val)) + (e.suffix || ""));
      d.style.cssText = base + "font-weight:" + e.weight + ";font-size:" + e.font_size +
        "px;color:" + e.color + ";line-height:.92;letter-spacing:-.02em;font-variant-numeric:tabular-nums";
      return d;
    }
    if (e.type === "text") {
      var t = el("div", null, String(e.upper ? String(val).toUpperCase() : val));
      t.style.cssText = base + "font-weight:" + e.weight + ";font-size:" + e.font_size +
        "px;color:" + e.color + (e.upper ? ";letter-spacing:.05em" : "");
      return t;
    }
    if (e.type === "chip") {
      var chip = el("div", null,
        (e.icon ? '<i class="ph-bold ph-' + e.icon + '" style="margin-right:6px"></i>' : "") +
        (val === "" ? "Chip" : String(val)));
      chip.style.cssText = "display:inline-flex;align-items:center;padding:4px 12px;border:2px solid " +
        e.color + ";border-radius:999px;color:" + e.color + ";font-weight:700;font-size:" +
        Math.min(e.font_size, 18) + "px;white-space:nowrap";
      var w = el("div", null); w.style.cssText = base; w.appendChild(chip); return w;
    }
    if (e.type === "icon") {
      var glyph = e.binding ? String(val || e.icon) : e.icon || "star";
      var i = el("div", null, '<i class="ph-bold ph-' + glyph + '"></i>');
      i.style.cssText = base + "font-size:" + Math.min(e.w, e.h) + "px;color:" + e.color;
      return i;
    }
    if (e.type === "progress") {
      var pct = clamp(Number(val) || 0, 0, 100);
      var track = el("div", null, '<div style="width:' + pct + "%;height:100%;background:" + e.color + '"></div>');
      track.style.cssText = "width:100%;height:100%;border-radius:999px;background:#E1DDD2;overflow:hidden";
      return track;
    }
    if (e.type === "spark" || e.type === "bar") {
      var box = el("div", null, "<canvas></canvas>");
      box.style.cssText = "width:100%;height:100%;position:relative";
      return box;
    }
    if (e.type === "list") {
      var rows = listRowsOf(e);
      var ul = el("div", null);
      ul.style.cssText = "width:100%;height:100%;overflow:hidden;color:" + e.color + ";font-size:14px";
      rows.forEach(function (r, idx) {
        var row = el("div", null,
          '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>' +
          '<span style="opacity:.7;margin-left:8px;flex:none"></span>');
        row.style.cssText = "display:flex;justify-content:space-between;align-items:center;padding:5px 0" +
          (idx ? ";border-top:2px solid rgba(27,26,22,.12)" : "");
        row.children[0].textContent = r.label;
        row.children[1].textContent = r.meta;
        ul.appendChild(row);
      });
      return ul;
    }
    if (e.type === "image") {
      var img = el("div", null, '<i class="ph-bold ph-image" style="font-size:' + Math.min(e.w, e.h) / 3 + "px;color:" + e.color + '"></i>');
      img.style.cssText = base + "justify-content:center;background:#E1DDD2;border:2px solid " + e.color + ";border-radius:8px";
      return img;
    }
    if (e.type === "shape") {
      var s = el("div", null);
      var round = e.shape_kind === "ellipse";
      s.style.cssText = "width:100%;height:100%;" +
        (e.mode === "outline" ? "background:transparent;border:" + e.stroke + "px solid " + e.color : "background:" + e.color) +
        ";border-radius:" + (round ? "50%" : e.radius + "px");
      return s;
    }
    var ph = el("div", null, e.type);
    ph.style.cssText = base + "justify-content:center;border:1.5px dashed #B6B1A4;border-radius:6px";
    return ph;
  }

  function drawChart(e, canvas) {
    var arr = seriesOf(e) || DEMO_SERIES;
    var isBar = e.type === "bar";
    return new Chart(canvas, {
      type: isBar ? "bar" : "line",
      data: {
        labels: arr.map(function () { return ""; }),
        datasets: [{
          data: arr, borderColor: e.color,
          backgroundColor: isBar ? e.color : e.color + "2e",
          borderWidth: 3, tension: 0.38, pointRadius: 0, fill: !isBar,
          borderRadius: isBar ? 2 : 0, categoryPercentage: 0.82, barPercentage: 0.88,
        }],
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false, events: [],
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false } },
      },
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
    var node = el("div", "el" + (e.id === S.sel ? " psel" : ""));
    node.dataset.id = e.id;
    node.style.cssText = "position:absolute;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w +
      "px;height:" + e.h + "px;" + (e.visible ? "" : "opacity:.4;");
    node.appendChild(renderElement(e));
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
        if (c) S.charts[e.id] = drawChart(e, c);
      }
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
      var nx = clamp(snap(ox + dx), 0, S.doc.w - e.w);
      var ny = clamp(snap(oy + dy), 0, S.doc.h - e.h);
      node.style.left = nx + "px"; node.style.top = ny + "px";
      e.x = nx; e.y = ny;
    }
    function up() {
      node.releasePointerCapture(ev.pointerId);
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
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
      var row = el("div", "lrow" + (e.id === S.sel ? " psel" : ""));
      row.innerHTML = '<i class="ph-bold ph-square ic"></i><span class="nm"></span>';
      row.querySelector(".nm").textContent = e.name || e.type;
      row.addEventListener("pointerdown", function () { select(e.id); });
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
        var arr = seriesOf(e) || DEMO_SERIES;
        S.charts[e.id].data.datasets[0].data = arr;
        S.charts[e.id].data.labels = arr.map(function () { return ""; });
        S.charts[e.id].update("none");
        return;
      }
      var fresh = renderElement(e);
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

  // ---- boot -------------------------------------------------------------
  function init() {
    var root = document.querySelector(".ed");
    if (!root) return;
    S.cfg = { docUrl: root.dataset.docUrl, saveUrl: root.dataset.saveUrl, catalogUrl: root.dataset.catalogUrl };
    artboard = $("panels-artboard");
    scaler = $("panels-scaler");
    var palette = $("panels-palette");
    if (palette) renderPalette(palette);

    artboard.addEventListener("pointerdown", function (ev) { if (ev.target === artboard) select(null); });
    var undoBtn = $("panels-undo"), redoBtn = $("panels-redo");
    if (undoBtn) undoBtn.addEventListener("click", undo);
    if (redoBtn) redoBtn.addEventListener("click", redo);

    document.addEventListener("keydown", function (ev) {
      var t = document.activeElement;
      var typing = t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
      var mod = ev.metaKey || ev.ctrlKey;
      if (mod && (ev.key === "z" || ev.key === "Z")) {
        ev.preventDefault();
        if (ev.shiftKey) redo(); else undo();
        return;
      }
      if (mod && (ev.key === "y" || ev.key === "Y")) { ev.preventDefault(); redo(); return; }
      if ((ev.key === "Delete" || ev.key === "Backspace") && S.sel && !typing) {
        ev.preventDefault(); deleteEl(S.sel);
      }
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
