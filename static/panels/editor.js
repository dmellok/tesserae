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

  // Static decoration primitives offered in the palette.
  var DECOS = [
    { kind: "text", label: "Text", icon: "ph-text-t", w: 180, h: 44 },
    { kind: "rect", label: "Rectangle", icon: "ph-square", w: 140, h: 90 },
    { kind: "ellipse", label: "Circle", icon: "ph-circle", w: 100, h: 100 },
    { kind: "line", label: "Line", icon: "ph-minus", w: 180, h: 16 },
    { kind: "icon", label: "Icon", icon: "ph-star", w: 72, h: 72 },
  ];
  // Base colour palette: Spectra semantic tokens (follow the theme) offered
  // alongside a native colour picker.
  var INKS = [
    "var(--text-primary)", "var(--bg)", "var(--surface-sunken)",
    "var(--accent-1)", "var(--accent-2)", "var(--accent-3)",
    "var(--accent-4)", "var(--accent-5)", "var(--accent-6)",
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
    data: {}, // live data keyed by "<widget>|<options-json>"
    dataPending: {}, // in-flight data fetches, same key
    appearance: { themes: [], styles: [], fonts: [] },
    zoom: null, // manual zoom multiplier; null = auto-fit
    panX: 0, panY: 0, // pan offset in screen px
    spaceDown: false, // space held = pan mode
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
      id: uid(), kind: "widget", widget: widget || "", fragment: fragment || "full",
      options: {}, x: x, y: y, w: w, h: h, opacity: 100,
      dither: true, visible: true, locked: false, group: null,
    };
  }
  function makeDecoration(kind, x, y, w, h) {
    var solidInk = kind === "line" || kind === "icon" || kind === "text";
    return {
      id: uid(), kind: kind, widget: "", fragment: "full", options: {},
      color: solidInk ? "var(--text-primary)" : "var(--accent-1)",
      fill: true, stroke: kind === "line" ? 3 : 2, radius: kind === "rect" ? 8 : 0,
      icon: kind === "icon" ? "star" : "", weight: "bold",
      text: kind === "text" ? "Text" : "", align: "left", size: 0, opacity: 100,
      x: x, y: y, w: w, h: h, dither: true, visible: true, locked: false, group: null,
    };
  }

  // ---- live widget mount (cached) --------------------------------------
  function fpOf(e) {
    return e.widget + "|" + (e.fragment || "full") + "|" + e.w + "x" + e.h + "|" +
      JSON.stringify(e.options || {});
  }
  // Live data is fetched per (widget, options); fragment/size don't change it.
  function dataKey(e) { return e.widget + "|" + JSON.stringify(e.options || {}); }
  function dataFor(e) {
    var k = dataKey(e);
    if (k in S.data) return S.data[k]; // fetched live value (may be null)
    var w = widgetFor(e.widget);
    return (w && w.sample) || null; // instant placeholder until live arrives
  }
  function ctxFor(e) {
    return {
      cell: {
        w: e.w, h: e.h, size: resolveSize(e.w, e.h),
        plugin: e.widget, plugin_id: e.widget,
        options: e.options || {}, fragment: e.fragment || "full",
      },
      panel: { w: S.doc.w, h: S.doc.h, portrait: S.doc.h > S.doc.w },
      font: { family: fontFamily(S.doc.font), weight: 400 },
      data: dataFor(e),
      fragment: e.fragment || "full",
      preview: false,
    };
  }

  // Fetch real data for every placed widget instance not already cached or
  // in-flight; when it lands, re-mount the affected elements so the editor
  // shows the live render (the same data a Send would use).
  function ensureData() {
    if (!S.cfg.dataUrl) return;
    var want = {};
    S.doc.els.forEach(function (e) {
      if (!e.widget) return;
      var k = dataKey(e);
      if (k in S.data || S.dataPending[k]) return;
      want[k] = { widget: e.widget, options: e.options || {}, w: e.w, h: e.h };
    });
    Object.keys(want).forEach(function (k) {
      var req = want[k];
      S.dataPending[k] = true;
      fetch(S.cfg.dataUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ widget: req.widget, options: req.options, w: req.w, h: req.h }),
      })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (j) { S.data[k] = j && "data" in j ? j.data : null; })
        .catch(function () { S.data[k] = null; })
        .then(function () {
          delete S.dataPending[k];
          // Invalidate cached mounts for elements on this key so they re-render
          // with the live data.
          var hit = false;
          S.doc.els.forEach(function (e) {
            if (e.widget && dataKey(e) === k && S.mount[e.id]) { delete S.mount[e.id]; hit = true; }
          });
          if (hit) paint();
        });
    });
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
      .then(function () { applyParts(e, shadow); })
      .catch(function (err) {
        shadow.innerHTML =
          '<div style="font:11px/1.3 system-ui;color:#a3402a;padding:6px">' +
          esc(e.widget) + ": " + esc(err && err.message ? err.message : err) + "</div>";
      });
  }

  // ---- fragment parts (zoom individual icons / text) --------------------
  // One CSS rule that scales the selector; icons (inline <i>) get
  // inline-block so the transform actually applies. Shared shape with
  // composer.js so the editor and a Send agree.
  function partRule(sel, scale) {
    var r = sel + "{transform:scale(" + (Number(scale) / 100) + ");transform-origin:center center;";
    if (/\.ph-/.test(sel)) r += "display:inline-block;";
    return r + "}";
  }
  // Inject a <style> into a widget's shadow root scaling each saved part.
  // ``overrideSel`` / ``overrideScale`` let a live slider drag preview a piece
  // that may not be saved yet, without mutating the model.
  function applyParts(e, shadow, overrideSel, overrideScale) {
    if (!shadow) return;
    var ex = shadow.querySelector("style#panels-parts");
    if (ex) ex.remove();
    var map = {};
    (Array.isArray(e.parts) ? e.parts : []).forEach(function (p) { if (p.sel) map[p.sel] = p.scale; });
    if (overrideSel != null) map[overrideSel] = overrideScale;
    var rules = Object.keys(map).map(function (sel) {
      var sc = map[sel];
      return (sc == null || Number(sc) === 100) ? "" : partRule(sel, sc);
    }).filter(Boolean).join("\n");
    if (!rules) return;
    var st = el("style"); st.id = "panels-parts"; st.textContent = rules;
    shadow.appendChild(st);
  }

  // Phosphor weight + layout-scaffold classes are noise, never a piece.
  var PART_SKIP = {
    w: 1, "w-body": 1, "w-title": 1, "w-title-meta": 1,
    ph: 1, "ph-bold": 1, "ph-thin": 1, "ph-light": 1, "ph-regular": 1, "ph-fill": 1, "ph-duotone": 1,
  };
  function humanGlyph(cls) {
    return cls.replace(/^ph-/, "").replace(/-/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
  }
  // Turn a selector into a friendly piece name: ".wx-temp" -> "Temp",
  // ".ph-cloud-sun" -> "Cloud sun icon".
  function humanizeSel(sel) {
    var s = sel.replace(/^[.#]/, "");
    if (/^ph-/.test(s)) return humanGlyph(s) + " icon";
    s = s.replace(/^(wx|st|sq|snp|saa|img|sensor|zone|climate|energy|hist|list)-/, "").replace(/[-_]/g, " ").trim();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : sel;
  }
  // The scalable "pieces" of a mounted widget: its icons and its text leaves,
  // each with a friendly label + a stable selector. This is what the picker
  // lists so the user zooms "Temperature" or "Weather icon", not a selector.
  function discoverPieces(e) {
    var m = S.mount[e.id];
    var root = m && m.host && m.host.shadowRoot;
    if (!root) return [];
    var out = [], seen = {};
    function push(sel, label) { if (sel && !seen[sel]) { seen[sel] = 1; out.push({ sel: sel, label: label }); } }
    root.querySelectorAll('i[class*="ph-"]').forEach(function (node) {
      var glyph = null, cl = node.classList;
      for (var i = 0; i < cl.length; i++) { if (/^ph-/.test(cl[i]) && !PART_SKIP[cl[i]]) glyph = cl[i]; }
      if (glyph) push("." + glyph, humanGlyph(glyph) + " icon");
    });
    root.querySelectorAll("span,div,h1,h2,h3,h4,p,small,strong,li").forEach(function (node) {
      var txt = (node.textContent || "").trim();
      if (!txt || txt.length > 48) return;
      for (var i = 0; i < node.children.length; i++) {
        if ((node.children[i].textContent || "").trim()) return; // not a text leaf
      }
      var sel = null;
      if (node.id) sel = "#" + node.id;
      else {
        var cls = null, cl = node.classList;
        for (var j = 0; j < cl.length; j++) { if (!PART_SKIP[cl[j]] && !/^ph-/.test(cl[j])) cls = cl[j]; }
        if (cls) sel = "." + cls;
      }
      if (!sel) return;
      var label = humanizeSel(sel);
      if (label.length <= 2) label = txt.length > 18 ? txt.slice(0, 18) + "…" : txt;
      push(sel, label);
    });
    return out.slice(0, 16);
  }

  // ---- artboard ---------------------------------------------------------
  // ---- contrast / legibility -------------------------------------------
  // Resolve any CSS colour (incl. Spectra tokens like var(--accent-1)) to
  // [r,g,b] by measuring it inside the themed artboard.
  var _probe = null;
  function resolveRGB(color) {
    if (!_probe) { _probe = el("span"); _probe.style.cssText = "position:absolute;left:-9999px;width:0;height:0"; }
    if (_probe.parentNode !== artboard) artboard.appendChild(_probe);
    _probe.style.color = "";
    _probe.style.color = color;
    var m = getComputedStyle(_probe).color.match(/(\d+),\s*(\d+),\s*(\d+)/);
    return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
  }
  function _lum(rgb) {
    var a = rgb.map(function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function contrastRatio(a, b) {
    var l1 = _lum(a), l2 = _lum(b), hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  }
  function effectiveBgRGB() {
    if (S.doc.bg_image) return null; // image background: can't judge contrast
    var c = S.doc.bg && /^#/.test(S.doc.bg) ? S.doc.bg : "var(--bg)";
    return resolveRGB(c);
  }
  // A text/icon decoration is flagged when it contrasts poorly (WCAG < 3:1)
  // with the canvas background, since low contrast dithers into mush on e-ink.
  function lowContrast(e) {
    if (isWidget(e) || (e.kind !== "text" && e.kind !== "icon")) return false;
    var bg = effectiveBgRGB();
    if (!bg) return false;
    var fg = resolveRGB(e.color || "var(--text-primary)");
    return !!fg && contrastRatio(fg, bg) < 3.0;
  }

  function isWidget(e) { return !e.kind || e.kind === "widget"; }

  function elNode(e) {
    var node = el("div", "el" + (isSel(e.id) ? " psel" : "") +
      (isWidget(e) && !e.widget ? " el-empty" : ""));
    node.dataset.id = e.id;
    var op = e.visible === false ? 0.4 : (e.opacity == null ? 1 : e.opacity / 100);
    node.style.cssText = "position:absolute;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w +
      "px;height:" + e.h + "px;opacity:" + op + ";" +
      (e.rotate ? "transform:rotate(" + e.rotate + "deg);" : "");

    if (!isWidget(e)) {
      // Static decoration: render fresh (cheap), pointer-transparent so clicks
      // hit the element wrapper.
      var deco = window.PanelsDecorate ? PanelsDecorate.render(e) : el("div");
      deco.style.pointerEvents = "none";
      node.appendChild(deco);
    } else {
      // Reuse the cached widget host when nothing that affects the render
      // changed; otherwise mount fresh. The host is pointer-transparent so
      // drag/select hit the element wrapper, not the widget preview.
      var fp = fpOf(e);
      var cached = S.mount[e.id];
      var host;
      if (cached && cached.fp === fp && cached.host) {
        host = cached.host;
      } else {
        host = el("div", "elhost");
        host.style.cssText = "width:100%;height:100%;container-type:size;overflow:hidden;pointer-events:none";
        // Widget backgrounds are transparent so the canvas background shows
        // through and elements read as one composed surface, not a card grid.
        // The card fill is var(--surface-gradient, var(--surface)), so both
        // are cleared (plus --bg for widgets that use it).
        host.style.setProperty("--bg", "transparent");
        host.style.setProperty("--surface", "transparent");
        host.style.setProperty("--surface-gradient", "transparent");
        S.mount[e.id] = { fp: fp, host: host };
        mountWidget(e, host);
      }
      node.appendChild(host);
      if (!e.widget) node.appendChild(el("div", "elplace", '<i class="ph-bold ph-cards-three"></i>'));
    }

    if (S.sim && lowContrast(e)) {
      node.appendChild(el("div", "el-warn", '<i class="ph-bold ph-warning"></i>Low contrast'));
    }

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
    if (S.doc.bg_image) {
      var bgimg = el("img", "canvas-bg");
      bgimg.src = S.doc.bg_image;
      var fit = S.doc.bg_fit || "cover";
      bgimg.style.cssText = "position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;" +
        "z-index:0;object-fit:" + (fit === "stretch" ? "fill" : fit);
      artboard.appendChild(bgimg);
    }
    S.doc.els.forEach(function (e) { artboard.appendChild(elNode(e)); });
    S.gV = el("div", "ov-guide v");
    S.gH = el("div", "ov-guide h");
    S.badge = el("div", "ov-badge");
    [S.gV, S.gH, S.badge].forEach(function (o) { o.style.display = "none"; artboard.appendChild(o); });
    fitZoom();
    renderLayers();
    renderProps();
    updateUndoButtons();
    ensureData();
  }

  function fitZoom() {
    var vp = scaler.parentElement;
    var z;
    if (S.zoom) {
      z = S.zoom;
    } else {
      z = Math.min((vp.clientWidth - 56) / S.doc.w, (vp.clientHeight - 56) / S.doc.h, 1);
      z = Math.max(z, 0.3);
      S.panX = 0; S.panY = 0; // auto-fit recentres
    }
    applyTransform(z);
    scaler.dataset.zoom = z;
    var lab = $("panels-zoom-label");
    if (lab) lab.textContent = S.zoom ? Math.round(z * 100) + "%" : "Fit";
    var zr = $("panels-zoom-range");
    if (zr && document.activeElement !== zr) zr.value = Math.round(z * 100);
  }
  function applyTransform(z) {
    scaler.style.transformOrigin = "center center";
    scaler.style.transform = "translate(" + (S.panX || 0) + "px," + (S.panY || 0) + "px) scale(" + z + ")";
  }
  function setZoom(z) { S.zoom = z ? clamp(z, 0.2, 4) : null; fitZoom(); }
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
      // Rotate the drag delta into the element's local axes so a handle drag
      // resizes along the rotated edges rather than the screen axes.
      if (e.rotate) {
        var a = -e.rotate * Math.PI / 180, c = Math.cos(a), sn = Math.sin(a);
        var ldx = dx * c - dy * sn, ldy = dx * sn + dy * c;
        dx = ldx; dy = ldy;
      }
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
    if (S.spaceDown) return; // space-drag pans instead of marquee-selecting
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

    // Shapes / lines / icons at the top of the palette.
    var decos = DECOS.filter(function (d) {
      return !q || d.label.toLowerCase().indexOf(q) >= 0 || d.kind.indexOf(q) >= 0;
    });
    if (decos.length) {
      shown++;
      var dg = el("div", "pwg");
      var dh = el("div", "pwgh");
      dh.innerHTML = '<i class="ph-bold ph-shapes"></i>';
      dh.appendChild(document.createTextNode("Shapes & elements"));
      dg.appendChild(dh);
      decos.forEach(function (d) {
        var tile = el("div", "pi");
        tile.title = d.label;
        tile.innerHTML = '<span class="ico"><i class="ph-bold ' + d.icon + '"></i></span><span class="lab"></span>';
        tile.querySelector(".lab").textContent = d.label;
        tile.addEventListener("pointerdown", function (ev) {
          onPaletteDown(ev, { kind: d.kind, w: d.w, h: d.h, label: d.label });
        });
        dg.appendChild(tile);
      });
      mount.appendChild(dg);
    }

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
    // Label pill following the cursor.
    var ghost = el("div", "ghost", item.label);
    ghost.style.cssText = "position:fixed;pointer-events:none;z-index:9999;left:0;top:0;padding:6px 10px;" +
      "background:var(--t-surface);border:1px solid var(--t-border-strong);border-radius:8px;" +
      "font-size:12px;font-weight:600;box-shadow:0 4px 12px rgba(16,12,8,.14)";
    document.body.appendChild(ghost);
    // Drop-footprint preview drawn on the artboard so you see exactly where and
    // how big the element will land.
    var preview = el("div", "drop-preview");
    preview.style.display = "none";
    artboard.appendChild(preview);
    document.body.classList.add("dragging-palette");

    // The snapped drop box for a given pointer position, or null when the
    // cursor is off the artboard.
    function dropBox(m) {
      var r = artboard.getBoundingClientRect(), z = currentZoom();
      var cx = (m.clientX - r.left) / z, cy = (m.clientY - r.top) / z;
      if (cx < 0 || cy < 0 || cx > S.doc.w || cy > S.doc.h) return null;
      return {
        x: clamp(snap(cx - item.w / 2), 0, S.doc.w - item.w),
        y: clamp(snap(cy - item.h / 2), 0, S.doc.h - item.h),
      };
    }
    function move(m) {
      ghost.style.transform = "translate(" + (m.clientX + 8) + "px," + (m.clientY + 8) + "px)";
      var box = dropBox(m);
      if (box) {
        preview.style.left = box.x + "px"; preview.style.top = box.y + "px";
        preview.style.width = item.w + "px"; preview.style.height = item.h + "px";
        preview.style.display = "block";
        ghost.style.opacity = "0.55"; // pill recedes once the footprint shows
        artboard.classList.add("drop-active");
      } else {
        preview.style.display = "none";
        ghost.style.opacity = "1";
        artboard.classList.remove("drop-active");
      }
    }
    function up(m) {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      ghost.remove();
      preview.remove();
      document.body.classList.remove("dragging-palette");
      artboard.classList.remove("drop-active");
      var box = dropBox(m);
      if (!box) return;
      pushHistory();
      var e = item.kind
        ? makeDecoration(item.kind, box.x, box.y, item.w, item.h)
        : makeElement(item.key, item.fragment, box.x, box.y, item.w, item.h);
      S.doc.els.push(e);
      S.sel = new Set([e.id]);
      scheduleSave(); paint();
    }
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
    move(ev);
  }

  // ---- appearance (theme / style / font / background) ------------------
  function fontFamily(id) {
    if (id) {
      var f = (S.appearance.fonts || []).filter(function (x) { return x.id === id; })[0];
      if (f) return '"' + f.name + '", ' + DEFAULT_FONT;
    }
    return DEFAULT_FONT;
  }
  function applyAppearance() {
    if (!artboard || !S.doc) return;
    artboard.setAttribute("data-theme", S.doc.theme || "light");
    artboard.setAttribute("data-style", S.doc.style || "standard");
    var fam = fontFamily(S.doc.font);
    artboard.style.setProperty("--font-family", fam);
    artboard.style.fontFamily = fam;
    artboard.style.background = S.doc.bg || "var(--bg)";
  }
  // theme/style/font change re-mounts widgets (some bake tokens/fonts at
  // render time); background only repaints the artboard.
  function appearanceChanged(remount) {
    scheduleSave();
    applyAppearance();
    if (remount) { S.mount = {}; paint(); }
  }
  function apSelect(field, options, valKey, labKey, cb) {
    var sel = el("select", "psel");
    sel.innerHTML = options.map(function (o) {
      return '<option value="' + esc(o[valKey]) + '">' + esc(o[labKey]) + "</option>";
    }).join("");
    sel.value = S.doc[field] || "";
    sel.addEventListener("change", function () { cb(sel.value); });
    return sel;
  }
  function apRow(label, control) {
    var row = el("div", "prow");
    row.innerHTML = '<span class="plab">' + label + "</span>";
    row.appendChild(control);
    return row;
  }
  function renderAppearance() {
    var mount = $("panels-appearance");
    if (!mount || !S.doc) return;
    mount.textContent = "";
    var ap = S.appearance || {};
    mount.appendChild(apRow("Theme", apSelect("theme", ap.themes || [], "value", "label",
      function (v) { S.doc.theme = v; appearanceChanged(true); })));
    mount.appendChild(apRow("Style", apSelect("style", ap.styles || [], "id", "label",
      function (v) { S.doc.style = v; appearanceChanged(true); })));
    var fontOpts = [{ id: "", name: "Default" }].concat(ap.fonts || []);
    mount.appendChild(apRow("Font", apSelect("font", fontOpts, "id", "name",
      function (v) { S.doc.font = v; appearanceChanged(true); })));

    var br = el("div", "prow");
    br.innerHTML = '<span class="plab">Background</span>';
    var wrap = el("span"); wrap.style.cssText = "display:flex;gap:6px;align-items:center";
    var color = el("input"); color.type = "color";
    color.value = /^#[0-9a-fA-F]{6}$/.test(S.doc.bg || "") ? S.doc.bg : "#f7f5f0";
    color.style.cssText = "width:30px;height:26px;padding:0;border:1px solid var(--t-border);border-radius:6px;cursor:pointer;background:none";
    color.addEventListener("input", function () { S.doc.bg = color.value; appearanceChanged(false); });
    var clr = el("button", "minibtn", "Theme");
    clr.title = "Use the theme background";
    clr.addEventListener("click", function () { S.doc.bg = ""; appearanceChanged(false); renderAppearance(); });
    wrap.appendChild(color); wrap.appendChild(clr);
    br.appendChild(wrap); mount.appendChild(br);

    // Canvas size (setPanelSize handles history + clamping elements inside).
    mount.appendChild(geomRow("Canvas",
      numField(S.doc.w, 1, function (v) { setPanelSize(Math.max(1, v), S.doc.h); }),
      numField(S.doc.h, 1, function (v) { setPanelSize(S.doc.w, Math.max(1, v)); })));

    // Background image (URL) + fit mode.
    var bir = el("div", "prow"); bir.innerHTML = '<span class="plab">Bg image</span>';
    var bi = el("input", "dinput"); bi.value = S.doc.bg_image || ""; bi.placeholder = "Image URL";
    bi.style.cssText = "width:100%;text-align:left";
    bi.addEventListener("change", function () { pushHistory(); S.doc.bg_image = bi.value.trim(); scheduleSave(); paint(); renderAppearance(); });
    bi.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
    bir.appendChild(bi); mount.appendChild(bir);
    if (S.doc.bg_image) {
      var fr = el("div", "prow"); fr.innerHTML = '<span class="plab">Fit</span>';
      var fs = el("select", "psel");
      fs.innerHTML = ["cover", "contain", "stretch"].map(function (x) { return '<option value="' + x + '">' + x + "</option>"; }).join("");
      fs.value = S.doc.bg_fit || "cover";
      fs.addEventListener("change", function () { pushHistory(); S.doc.bg_fit = fs.value; scheduleSave(); paint(); });
      fr.appendChild(fs); mount.appendChild(fr);
    }
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
      row.dataset.id = e.id;
      row.innerHTML =
        '<i class="ph-bold ph-dots-six-vertical grip" title="Drag to reorder"></i>' +
        '<i class="ph-bold ' + (e.group ? "ph-link" : "ph-cards-three") + ' ic"></i>' +
        '<span class="nm"></span>' +
        '<span class="act">' +
          '<i class="ph-bold ' + (e.visible ? "ph-eye" : "ph-eye-slash") + ' li" data-act="vis" title="Show / hide"></i>' +
          '<i class="ph-bold ' + (e.locked ? "ph-lock-simple" : "ph-lock-simple-open") + ' li" data-act="lock" title="Lock"></i>' +
        "</span>";
      row.querySelector(".nm").textContent = elLabel(e);
      (function (elem) {
        var grip = row.querySelector(".grip");
        grip.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); startLayerDrag(ev, elem.id, grip); });
      })(e);
      row.addEventListener("pointerdown", function (ev) {
        var act = ev.target && ev.target.dataset ? ev.target.dataset.act : null;
        if (act === "vis") { ev.stopPropagation(); pushHistory(); e.visible = !e.visible; scheduleSave(); paint(); return; }
        if (act === "lock") { ev.stopPropagation(); pushHistory(); e.locked = !e.locked; scheduleSave(); paint(); return; }
        if (ev.shiftKey) toggleSel(e.id); else select(e.id);
      });
      mount.appendChild(row);
    });
  }

  // Reorder z (paint order) by dragging a layer row's grip. The layers list is
  // reversed (top row = front = last in S.doc.els), so we reorder the reversed
  // view and flip it back.
  function startLayerDrag(ev, id, handle) {
    ev.preventDefault();
    var mount = $("panels-layers");
    handle.setPointerCapture(ev.pointerId);
    var targetIdx = null;
    function rows() { return Array.prototype.slice.call(mount.querySelectorAll(".lrow")); }
    function move(m) {
      var rs = rows(), y = m.clientY;
      targetIdx = rs.length;
      for (var i = 0; i < rs.length; i++) {
        var b = rs[i].getBoundingClientRect();
        if (y < b.top + b.height / 2) { targetIdx = i; break; }
      }
      rs.forEach(function (r, i) { r.classList.toggle("drop-before", i === targetIdx); });
    }
    function up() {
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      rows().forEach(function (r) { r.classList.remove("drop-before"); });
      if (targetIdx == null) return;
      var visual = S.doc.els.slice().reverse();
      var from = -1;
      for (var i = 0; i < visual.length; i++) if (visual[i].id === id) { from = i; break; }
      if (from < 0) return;
      var moved = visual.splice(from, 1)[0];
      var to = clamp(targetIdx > from ? targetIdx - 1 : targetIdx, 0, visual.length);
      visual.splice(to, 0, moved);
      pushHistory();
      S.doc.els = visual.reverse();
      scheduleSave(); paint();
    }
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
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
  // Align the selection to the canvas edges/centre ("canvas") or to the
  // selection's own bounding box ("selection", 2+ elements).
  function alignSel(kind, target) {
    var els = selEls().filter(function (e) { return !e.locked; });
    if (!els.length) return;
    var minX, maxX, minY, maxY;
    if (target === "canvas") {
      minX = 0; minY = 0; maxX = S.doc.w; maxY = S.doc.h;
    } else {
      if (els.length < 2) return;
      minX = Infinity; maxX = -Infinity; minY = Infinity; maxY = -Infinity;
      els.forEach(function (e) {
        minX = Math.min(minX, e.x); maxX = Math.max(maxX, e.x + e.w);
        minY = Math.min(minY, e.y); maxY = Math.max(maxY, e.y + e.h);
      });
    }
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
  // Distribute selected elements so the gaps between them are equal, keeping
  // the outermost two in place. Needs 3+ elements.
  function distribute(axis) {
    var els = selEls().filter(function (e) { return !e.locked; });
    if (els.length < 3) return;
    var pos = axis === "h" ? "x" : "y", dim = axis === "h" ? "w" : "h";
    els = els.slice().sort(function (a, b) { return a[pos] - b[pos]; });
    var first = els[0], last = els[els.length - 1];
    var span = (last[pos] + last[dim]) - first[pos];
    var used = els.reduce(function (s, e) { return s + e[dim]; }, 0);
    var gap = (span - used) / (els.length - 1);
    pushHistory();
    var cursor = first[pos];
    els.forEach(function (e) { e[pos] = Math.round(cursor); cursor += e[dim] + gap; });
    scheduleSave(); paint();
  }
  // Set every selected element's width or height to the primary (first) one.
  function matchSize(dim) {
    var els = selEls().filter(function (e) { return !e.locked; });
    if (els.length < 2) return;
    var ref = byId(selArr()[0]) || els[0];
    var v = ref[dim];
    pushHistory();
    els.forEach(function (e) {
      e[dim] = v;
      e.w = clamp(e.w, MIN, S.doc.w); e.h = clamp(e.h, MIN, S.doc.h);
      e.x = clamp(e.x, 0, S.doc.w - e.w); e.y = clamp(e.y, 0, S.doc.h - e.h);
    });
    scheduleSave(); paint();
  }
  // A row of six align buttons for the given target.
  function alignButtons(target) {
    var row = el("div", "prow");
    row.style.cssText = "display:flex;gap:5px;flex-wrap:wrap";
    [["left", "ph-align-left"], ["hcenter", "ph-align-center-horizontal"], ["right", "ph-align-right"],
      ["top", "ph-align-top"], ["vcenter", "ph-align-center-vertical"], ["bottom", "ph-align-bottom"]]
      .forEach(function (a) {
        var b = el("button", "minibtn", '<i class="ph-bold ' + a[1] + '"></i>');
        b.title = a[0];
        b.addEventListener("click", function () { alignSel(a[0], target); });
        row.appendChild(b);
      });
    return row;
  }
  // Rotation control shared by widget + decoration props. Live-previews on the
  // node during drag; commits on release.
  function rotationRow(e) {
    var row = el("div", "prow");
    row.innerHTML = '<span class="plab">Rotate</span>';
    var wrap = el("span"); wrap.style.cssText = "display:flex;align-items:center;gap:8px";
    var r = el("input"); r.type = "range"; r.min = 0; r.max = 359; r.value = e.rotate || 0; r.style.width = "86px";
    var val = el("span", "mono"); val.textContent = (e.rotate || 0) + "°";
    r.addEventListener("input", function () {
      val.textContent = r.value + "°";
      var n = artboard.querySelector('[data-id="' + e.id + '"]');
      if (n) n.style.transform = r.value === "0" ? "" : "rotate(" + r.value + "deg)";
    });
    r.addEventListener("change", function () { pushHistory(); e.rotate = Number(r.value); scheduleSave(); });
    wrap.appendChild(r); wrap.appendChild(val);
    row.appendChild(wrap);
    return row;
  }
  function opacityRow(e) {
    var row = el("div", "prow");
    row.innerHTML = '<span class="plab">Opacity</span>';
    var wrap = el("span"); wrap.style.cssText = "display:flex;align-items:center;gap:8px";
    var cur = e.opacity == null ? 100 : e.opacity;
    var r = el("input"); r.type = "range"; r.min = 0; r.max = 100; r.value = cur; r.style.width = "86px";
    var val = el("span", "mono"); val.textContent = cur + "%";
    r.addEventListener("input", function () {
      val.textContent = r.value + "%";
      var n = artboard.querySelector('[data-id="' + e.id + '"]');
      if (n) n.style.opacity = Number(r.value) / 100;
    });
    r.addEventListener("change", function () { pushHistory(); e.opacity = Number(r.value); scheduleSave(); });
    wrap.appendChild(r); wrap.appendChild(val);
    row.appendChild(wrap);
    return row;
  }
  // Proportional resize slider: scales the element's box around its centre
  // relative to the geometry captured when the panel rendered. Commits on
  // release; the panel then re-renders and the slider re-anchors at 100%.
  function scaleRow(e) {
    var row = el("div", "prow");
    row.innerHTML = '<span class="plab">Scale</span>';
    var wrap = el("span"); wrap.style.cssText = "display:flex;align-items:center;gap:8px";
    var r = el("input"); r.type = "range"; r.min = 25; r.max = 300; r.step = 1; r.value = 100; r.style.width = "86px";
    var val = el("span", "mono"); val.textContent = "100%";
    var base = { w: e.w, h: e.h, cx: e.x + e.w / 2, cy: e.y + e.h / 2 };
    function geomAt(f) {
      var nw = Math.max(MIN, Math.round(base.w * f));
      var nh = Math.max(MIN, Math.round(base.h * f));
      return { w: nw, h: nh, x: Math.round(base.cx - nw / 2), y: Math.round(base.cy - nh / 2) };
    }
    r.addEventListener("input", function () {
      val.textContent = r.value + "%";
      var g = geomAt(Number(r.value) / 100);
      var n = artboard.querySelector('[data-id="' + e.id + '"]');
      if (n) { n.style.width = g.w + "px"; n.style.height = g.h + "px"; n.style.left = g.x + "px"; n.style.top = g.y + "px"; }
    });
    r.addEventListener("change", function () { commitGeom(e, geomAt(Number(r.value) / 100)); });
    r.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
    wrap.appendChild(r); wrap.appendChild(val);
    row.appendChild(wrap);
    return row;
  }
  // Current saved zoom for a piece (100 = untouched).
  function pieceScale(e, sel) {
    var p = (e.parts || []).filter(function (x) { return x.sel === sel; })[0];
    return p ? (p.scale == null ? 100 : p.scale) : 100;
  }
  // Upsert a piece's zoom; 100 removes it so ``parts`` stays tidy.
  function setPieceScale(e, sel, scale) {
    if (!Array.isArray(e.parts)) e.parts = [];
    if (Number(scale) === 100) {
      e.parts = e.parts.filter(function (x) { return x.sel !== sel; });
      return;
    }
    var p = e.parts.filter(function (x) { return x.sel === sel; })[0];
    if (p) p.scale = Number(scale);
    else e.parts.push({ sel: sel, scale: Number(scale) });
  }
  // One friendly piece row: label + zoom slider.
  function pieceRow(e, pc) {
    var row = el("div", "prow");
    var lab = el("span", "plab"); lab.textContent = pc.label; lab.title = pc.sel;
    lab.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:104px";
    row.appendChild(lab);
    var wrap = el("span"); wrap.style.cssText = "display:flex;align-items:center;gap:8px";
    var cur = pieceScale(e, pc.sel);
    var r = el("input"); r.type = "range"; r.min = 50; r.max = 300; r.step = 1; r.value = cur; r.style.width = "76px";
    var val = el("span", "mono"); val.textContent = cur + "%";
    r.addEventListener("input", function () {
      val.textContent = r.value + "%";
      var m = S.mount[e.id];
      if (m && m.host) applyParts(e, m.host.shadowRoot, pc.sel, Number(r.value));
    });
    r.addEventListener("change", function () { pushHistory(); setPieceScale(e, pc.sel, Number(r.value)); scheduleSave(); });
    r.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
    r.addEventListener("dblclick", function () {
      r.value = 100; val.textContent = "100%";
      pushHistory(); setPieceScale(e, pc.sel, 100); scheduleSave();
      var m = S.mount[e.id]; if (m && m.host) applyParts(e, m.host.shadowRoot);
    });
    wrap.appendChild(r); wrap.appendChild(val);
    row.appendChild(wrap);
    return row;
  }
  // "Zoom parts": a plain list of the widget's icons + text, each with its own
  // zoom slider, so the user enlarges an individual piece without touching CSS.
  // Double-click a slider to reset it to 100%.
  function partsSection(mount, e) {
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-magnifying-glass-plus"></i>Zoom parts'));
    var pieces = discoverPieces(e);
    var known = {};
    pieces.forEach(function (p) { known[p.sel] = 1; });
    // Keep any saved piece whose selector isn't currently discovered so it can
    // still be seen and reset (e.g. after a fragment change).
    (e.parts || []).forEach(function (p) {
      if (p.sel && !known[p.sel]) { pieces.push({ sel: p.sel, label: humanizeSel(p.sel) }); known[p.sel] = 1; }
    });
    if (!pieces.length) {
      var hint = el("div", "note"); hint.style.cssText = "padding:2px 2px 8px;font-size:10.5px";
      hint.textContent = "Loads once the widget renders.";
      mount.appendChild(hint);
      return;
    }
    pieces.forEach(function (pc) { mount.appendChild(pieceRow(e, pc)); });
  }
  // Editable position/size/rotation/opacity, shared by widget + decoration.
  function numField(value, min, cb) {
    var inp = el("input", "dinput");
    inp.type = "number"; if (min != null) inp.min = min;
    inp.value = value; inp.style.cssText = "width:58px;text-align:left";
    inp.addEventListener("change", function () {
      var v = Math.round(Number(inp.value));
      if (isFinite(v)) cb(v);
    });
    inp.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
    return inp;
  }
  function geomRow(label, a, b) {
    var row = el("div", "prow");
    row.innerHTML = '<span class="plab">' + label + "</span>";
    var wrap = el("span"); wrap.style.cssText = "display:flex;gap:6px";
    wrap.appendChild(a); wrap.appendChild(b);
    row.appendChild(wrap);
    return row;
  }
  function commitGeom(e, patch) {
    pushHistory();
    for (var k in patch) e[k] = patch[k];
    e.w = clamp(e.w, MIN, S.doc.w); e.h = clamp(e.h, MIN, S.doc.h);
    e.x = clamp(e.x, 0, S.doc.w - e.w); e.y = clamp(e.y, 0, S.doc.h - e.h);
    scheduleSave(); paint();
  }
  function arrangeGeom(mount, e) {
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-ruler"></i>Arrange'));
    mount.appendChild(geomRow("Position",
      numField(e.x, 0, function (v) { commitGeom(e, { x: v }); }),
      numField(e.y, 0, function (v) { commitGeom(e, { y: v }); })));
    mount.appendChild(geomRow("Size",
      numField(e.w, MIN, function (v) { commitGeom(e, { w: v }); }),
      numField(e.h, MIN, function (v) { commitGeom(e, { h: v }); })));
    mount.appendChild(scaleRow(e));
    mount.appendChild(rotationRow(e));
    mount.appendChild(opacityRow(e));
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
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-align-center-horizontal"></i>Align in selection'));
    mount.appendChild(alignButtons("selection"));
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-frame-corners"></i>Align to canvas'));
    mount.appendChild(alignButtons("canvas"));

    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-arrows-out-line-horizontal"></i>Distribute &amp; size'));
    var drow = el("div", "prow"); drow.style.cssText = "display:flex;gap:6px;flex-wrap:wrap";
    [["ph-arrows-horizontal", "Space H", function () { distribute("h"); }],
      ["ph-arrows-vertical", "Space V", function () { distribute("v"); }],
      ["ph-arrows-out-line-horizontal", "Width", function () { matchSize("w"); }],
      ["ph-arrows-out-line-vertical", "Height", function () { matchSize("h"); }]].forEach(function (a) {
      var b = el("button", "minibtn", '<i class="ph-bold ' + a[0] + '"></i> ' + a[1]);
      b.addEventListener("click", a[2]);
      drow.appendChild(b);
    });
    mount.appendChild(drow);

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

  // ---- decoration properties -------------------------------------------
  // Re-render just this element's decoration node with patched props (live
  // slider/colour preview without committing to state or full repaint).
  function previewDeco(e, patch) {
    var node = artboard.querySelector('[data-id="' + e.id + '"]');
    if (!node || !window.PanelsDecorate) return;
    var temp = {};
    for (var k in e) temp[k] = e[k];
    for (var p in patch) temp[p] = patch[p];
    var deco = PanelsDecorate.render(temp);
    deco.style.pointerEvents = "none";
    if (node.firstChild) node.replaceChild(deco, node.firstChild);
    else node.appendChild(deco);
  }
  function colorControl(e) {
    var row = el("div", "prow");
    row.style.cssText = "display:flex;flex-wrap:wrap;gap:6px";
    // data-theme so the Spectra token swatches resolve to the canvas theme.
    row.setAttribute("data-theme", S.doc.theme || "light");
    function pick(c) { pushHistory(); e.color = c; scheduleSave(); paint(); }
    INKS.forEach(function (ink) {
      var s = el("span");
      var on = ink === e.color;
      s.style.cssText = "width:22px;height:22px;border-radius:6px;cursor:pointer;background:" + ink +
        (on ? ";outline:2px solid var(--t-accent);outline-offset:2px" : ";border:1px solid var(--t-border)");
      s.addEventListener("click", function () { pick(ink); });
      row.appendChild(s);
    });
    var native = el("input"); native.type = "color";
    native.value = /^#[0-9a-fA-F]{6}$/.test(e.color || "") ? e.color : "#000000";
    native.style.cssText = "width:22px;height:22px;padding:0;border:1px solid var(--t-border);border-radius:6px;cursor:pointer;background:none";
    native.addEventListener("input", function () { previewDeco(e, { color: native.value }); });
    native.addEventListener("change", function () { pick(native.value); });
    row.appendChild(native);
    return row;
  }
  function decoSlider(e, label, prop, min, max) {
    var row = el("div", "prow");
    row.innerHTML = '<span class="plab">' + label + "</span>";
    var wrap = el("span"); wrap.style.cssText = "display:flex;align-items:center;gap:8px";
    var r = el("input"); r.type = "range"; r.min = min; r.max = max; r.value = e[prop]; r.style.width = "96px";
    var val = el("span", "mono"); val.textContent = e[prop];
    r.addEventListener("input", function () {
      var patch = {}; patch[prop] = Number(r.value); val.textContent = r.value; previewDeco(e, patch);
    });
    r.addEventListener("change", function () { pushHistory(); e[prop] = Number(r.value); scheduleSave(); paint(); });
    wrap.appendChild(r); wrap.appendChild(val);
    row.appendChild(wrap);
    return row;
  }
  function renderDecoProps(mount, e) {
    mount.textContent = "";
    var name = { text: "Text", rect: "Rectangle", ellipse: "Circle", line: "Line", icon: "Icon" }[e.kind] || "Shape";
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-shapes"></i>' + name));

    if (e.kind === "text") {
      var trow = el("div", "prow"); trow.innerHTML = '<span class="plab">Text</span>';
      var tin = el("input", "dinput"); tin.value = e.text || ""; tin.style.cssText = "width:100%;text-align:left";
      tin.addEventListener("input", function () { previewDeco(e, { text: tin.value }); });
      tin.addEventListener("change", function () { pushHistory(); e.text = tin.value; scheduleSave(); });
      tin.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
      trow.appendChild(tin); mount.appendChild(trow);
      mount.appendChild(decoSlider(e, "Font size", "size", 0, 200)); // 0 = auto from box
      var alrow = el("div", "prow"); alrow.innerHTML = '<span class="plab">Align</span>';
      var alsel = el("select", "psel");
      alsel.innerHTML = ["left", "center", "right"].map(function (x) { return '<option value="' + x + '">' + x + "</option>"; }).join("");
      alsel.value = e.align || "left";
      alsel.addEventListener("change", function () { pushHistory(); e.align = alsel.value; scheduleSave(); paint(); });
      alrow.appendChild(alsel); mount.appendChild(alrow);
      var twrow = el("div", "prow"); twrow.innerHTML = '<span class="plab">Weight</span>';
      var twsel = el("select", "psel");
      twsel.innerHTML = ["regular", "bold"].map(function (x) { return '<option value="' + x + '">' + x + "</option>"; }).join("");
      twsel.value = e.weight === "regular" ? "regular" : "bold";
      twsel.addEventListener("change", function () { pushHistory(); e.weight = twsel.value; scheduleSave(); paint(); });
      twrow.appendChild(twsel); mount.appendChild(twrow);
    }

    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-palette"></i>Colour'));
    mount.appendChild(colorControl(e));

    if (e.kind === "rect" || e.kind === "ellipse") {
      var frow = el("div", "prow");
      frow.innerHTML = '<span class="plab">Style</span>';
      var fbtn = el("button", "minibtn", e.fill
        ? '<i class="ph-bold ph-square"></i> Filled' : '<i class="ph-bold ph-bounding-box"></i> Outlined');
      fbtn.addEventListener("click", function () { pushHistory(); e.fill = !e.fill; scheduleSave(); renderProps(); paint(); });
      frow.appendChild(fbtn); mount.appendChild(frow);
      if (!e.fill) mount.appendChild(decoSlider(e, "Thickness", "stroke", 1, 20));
      if (e.kind === "rect") mount.appendChild(decoSlider(e, "Radius", "radius", 0, 80));
    } else if (e.kind === "line") {
      mount.appendChild(decoSlider(e, "Thickness", "stroke", 1, 40));
    } else if (e.kind === "icon") {
      var irow = el("div", "prow");
      irow.innerHTML = '<span class="plab">Icon</span>';
      var ibtn = el("button", "minibtn", '<i class="ph-bold ph-' + (e.icon || "star") + '"></i> ' + esc(e.icon || "star"));
      ibtn.addEventListener("click", function () { openIconPicker(e); });
      irow.appendChild(ibtn); mount.appendChild(irow);

      var wrow = el("div", "prow");
      wrow.innerHTML = '<span class="plab">Weight</span>';
      var wsel = el("select", "psel");
      wsel.innerHTML = ["thin", "light", "regular", "bold", "fill", "duotone"]
        .map(function (x) { return '<option value="' + x + '">' + x + "</option>"; }).join("");
      wsel.value = e.weight || "bold";
      wsel.addEventListener("change", function () { pushHistory(); e.weight = wsel.value; scheduleSave(); paint(); });
      wrow.appendChild(wsel); mount.appendChild(wrow);
    }

    arrangeGeom(mount, e);
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-frame-corners"></i>Align to canvas'));
    mount.appendChild(alignButtons("canvas"));
    propRowBtns(mount, e);
  }

  // ---- icon picker (reuses the drawer) ---------------------------------
  var iconNames = null;
  function loadIconNames(cb) {
    if (iconNames) { cb(iconNames); return; }
    if (!S.cfg.iconCssUrl) { cb([]); return; }
    fetch(S.cfg.iconCssUrl)
      .then(function (r) { return r.text(); })
      .then(function (css) {
        var seen = {}, out = [], re = /\.ph-bold\.ph-([a-z0-9-]+):before/g, m;
        while ((m = re.exec(css))) { if (!seen[m[1]]) { seen[m[1]] = 1; out.push(m[1]); } }
        iconNames = out;
        cb(out);
      })
      .catch(function () { cb([]); });
  }
  function openIconPicker(e) {
    var overlay = $("panels-drawer"), body = $("panels-drawer-body"), title = $("panels-drawer-title");
    if (!overlay || !body) return;
    title.textContent = "Pick an icon";
    body.dataset.eid = ""; // Save just closes; selection is immediate
    body.innerHTML =
      '<input class="dinput wide" id="panels-icon-search" placeholder="Search icons…" style="width:100%;margin-bottom:8px" autocomplete="off">' +
      '<div class="icon-grid" id="panels-icon-grid"><div class="note" style="padding:10px">Loading…</div></div>';
    overlay.classList.add("open");
    loadIconNames(function (names) {
      var grid = $("panels-icon-grid"), search = $("panels-icon-search");
      if (!grid) return;
      function draw(q) {
        grid.textContent = "";
        var list = q ? names.filter(function (n) { return n.indexOf(q) >= 0; }) : names;
        list.slice(0, 400).forEach(function (n) {
          var t = el("button", "icon-tile" + (n === e.icon ? " on" : ""));
          t.title = n;
          t.innerHTML = '<i class="ph-bold ph-' + n + '"></i>';
          t.addEventListener("click", function () { pushHistory(); e.icon = n; scheduleSave(); closeConfig(); paint(); });
          grid.appendChild(t);
        });
        if (!list.length) { var no = el("div", "note"); no.style.padding = "10px"; no.textContent = "No icons match."; grid.appendChild(no); }
      }
      if (search) search.addEventListener("input", function () { draw(search.value.trim().toLowerCase()); });
      draw("");
    });
  }

  function renderEmptyProps(mount) {
    mount.textContent = "";
    var note = el("div", "note");
    note.style.cssText = "padding:16px 4px;line-height:1.5";
    note.textContent = "Drag a widget or a shape from the palette onto the canvas, then select it here to configure it.";
    mount.appendChild(note);
  }

  function renderProps() {
    var mount = $("panels-data");
    if (!mount) return;
    if (selCount() > 1) { renderGroupProps(mount); return; }
    var e = selCount() ? byId(selArr()[0]) : null;
    if (!e) { renderEmptyProps(mount); return; }
    if (!isWidget(e)) { renderDecoProps(mount, e); return; }

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

    // Fragment parts (scale individual pieces of the render).
    if (e.widget) partsSection(mount, e);

    // Arrange.
    arrangeGeom(mount, e);
    var drow = el("div", "prow");
    drow.innerHTML = '<span class="plab">Dither</span>';
    var dbtn = el("button", "minibtn",
      e.dither ? '<i class="ph-bold ph-check-square"></i> On' : '<i class="ph-bold ph-square"></i> Flat');
    dbtn.addEventListener("click", function () { pushHistory(); e.dither = !e.dither; scheduleSave(); renderProps(); });
    drow.appendChild(dbtn); mount.appendChild(drow);
    mount.appendChild(el("div", "psec", '<i class="ph-bold ph-frame-corners"></i>Align to canvas'));
    mount.appendChild(alignButtons("canvas"));

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
  // Flush any pending debounce and write immediately (Save button, Cmd/Ctrl+S).
  function saveNow() {
    clearTimeout(S.saveTimer);
    var status = $("panels-status");
    if (status) status.textContent = "saving…";
    save();
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

  // ---- dark-mode toggle (mirrors the admin shell in _base.html) --------
  function initThemeToggle() {
    var btn = $("panels-theme");
    if (!btn) return;
    function sync() {
      var dark = document.documentElement.getAttribute("data-theme") === "dark";
      btn.innerHTML = dark ? '<i class="ph-bold ph-sun"></i>' : '<i class="ph-bold ph-moon"></i>';
      btn.classList.toggle("on", dark);
      btn.title = dark ? "Switch to light mode" : "Toggle dark mode";
    }
    btn.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      if (next === "dark") document.documentElement.setAttribute("data-theme", "dark");
      else document.documentElement.removeAttribute("data-theme");
      try { localStorage.setItem("tesserae-theme", next); } catch (e) { /* ignore */ }
      sync();
    });
    sync();
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

  // ---- accurate preview (server render) --------------------------------
  // Saves the doc, then shows the real headless render at panel resolution in
  // the drawer, the same pipeline a Send screenshots.
  function openPreview() {
    var overlay = $("panels-drawer"), body = $("panels-drawer-body"), title = $("panels-drawer-title");
    if (!overlay || !body || !S.cfg.previewUrl) return;
    title.textContent = "Panel preview";
    body.dataset.eid = "";
    body.innerHTML = '<div class="note" style="padding:12px">Rendering at panel resolution…</div>';
    overlay.classList.add("open");
    fetch(S.cfg.saveUrl, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(S.doc),
    })
      .then(function () { return fetch(S.cfg.previewUrl + "?t=" + Date.now()); })
      .then(function (r) { if (!r.ok) return Promise.reject(r.status); return r.blob(); })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        body.textContent = "";
        var img = el("img"); img.src = url;
        img.style.cssText = "width:100%;height:auto;display:block;border:1px solid var(--t-border);border-radius:8px";
        body.appendChild(img);
        var note = el("div", "note"); note.style.padding = "8px 2px";
        note.textContent = "Rendered at the panel resolution via the same pipeline a Send uses.";
        body.appendChild(note);
      })
      .catch(function () {
        body.innerHTML = '<div class="note" style="padding:12px">Preview failed (the headless renderer may be unavailable).</div>';
      });
  }

  // ---- canvas switcher --------------------------------------------------
  function editorUrl(id) { return S.cfg.compBase + "/c/" + id; }
  function compUrl(id, action) { return S.cfg.compBase + "/c/" + id + "/" + action; }
  function canvasMenuEsc(ev) { if (ev.key === "Escape") closeCanvasMenu(); }

  function closeCanvasMenu() {
    var m = document.querySelector(".canvas-menu");
    if (m) m.remove();
    document.removeEventListener("pointerdown", closeCanvasMenu);
    document.removeEventListener("keydown", canvasMenuEsc);
  }

  function toggleCanvasMenu(anchor) {
    if (document.querySelector(".canvas-menu")) { closeCanvasMenu(); return; }
    var menu = el("div", "canvas-menu");
    menu.innerHTML =
      '<div class="cm-head"><span>Canvases</span><button class="cm-new"><i class="ph-bold ph-plus"></i>New</button></div>' +
      '<div class="cm-list"><div class="note" style="padding:10px 12px">Loading…</div></div>';
    document.body.appendChild(menu);
    var r = anchor.getBoundingClientRect();
    menu.style.left = Math.round(r.left) + "px";
    menu.style.top = Math.round(r.bottom + 6) + "px";
    menu.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
    menu.querySelector(".cm-new").addEventListener("click", createCanvas);
    setTimeout(function () { document.addEventListener("pointerdown", closeCanvasMenu); }, 0);
    document.addEventListener("keydown", canvasMenuEsc);
    loadCanvasList(menu.querySelector(".cm-list"));
  }

  function iconBtn(icon, title, fn) {
    var b = el("button", "cm-act", '<i class="ph-bold ' + icon + '"></i>');
    b.title = title;
    b.addEventListener("click", function (ev) { ev.stopPropagation(); fn(); });
    return b;
  }

  function loadCanvasList(list) {
    fetch(S.cfg.compBase + "/canvases.json")
      .then(function (r) { return r.json(); })
      .then(function (p) {
        var items = (p && p.canvases) || [];
        list.textContent = "";
        if (!items.length) {
          list.innerHTML = '<div class="note" style="padding:10px 12px">No canvases yet.</div>';
          return;
        }
        items.forEach(function (c) {
          var row = el("div", "cm-row" + (c.id === S.cfg.canvasId ? " on" : ""));
          var open = el("button", "cm-open",
            '<span class="cm-name"></span><span class="cm-meta">' + c.w + "×" + c.h + " · " + c.elements + " el</span>");
          open.querySelector(".cm-name").textContent = c.name;
          open.addEventListener("click", function () {
            if (c.id === S.cfg.canvasId) closeCanvasMenu();
            else location.href = editorUrl(c.id);
          });
          var acts = el("div", "cm-acts");
          acts.appendChild(iconBtn("ph-pencil-simple", "Rename", function () { renameCanvas(c); }));
          acts.appendChild(iconBtn("ph-copy", "Duplicate", function () { duplicateCanvas(c); }));
          acts.appendChild(iconBtn("ph-trash", "Delete", function () { deleteCanvas(c); }));
          row.appendChild(open);
          row.appendChild(acts);
          list.appendChild(row);
        });
      })
      .catch(function () { list.innerHTML = '<div class="note" style="padding:10px 12px">Failed to load.</div>'; });
  }

  function createCanvas() {
    fetch(S.cfg.compBase + "/new", { method: "POST" })
      .then(function (r) { location.href = r.url; })
      .catch(function () {});
  }

  function renameCanvas(c) {
    var name = window.prompt("Rename canvas", c.name);
    if (name === null) return;
    name = name.trim();
    if (!name) return;
    fetch(compUrl(c.id, "rename"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name }),
    }).then(function () {
      if (c.id === S.cfg.canvasId) {
        if (S.doc) S.doc.name = name;
        var t = $("panels-title"); if (t) t.textContent = name;
      }
      var m = document.querySelector(".canvas-menu");
      if (m) loadCanvasList(m.querySelector(".cm-list"));
    });
  }

  function duplicateCanvas(c) {
    fetch(compUrl(c.id, "duplicate"), { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (p) { if (p && p.id) location.href = editorUrl(p.id); });
  }

  function deleteCanvas(c) {
    if (!window.confirm('Delete "' + c.name + '"? This cannot be undone.')) return;
    fetch(compUrl(c.id, "delete"), { method: "POST" }).then(function () {
      if (c.id === S.cfg.canvasId) { location.href = S.cfg.compBase + "/"; return; }
      var m = document.querySelector(".canvas-menu");
      if (m) loadCanvasList(m.querySelector(".cm-list"));
    });
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
    paint(); // show/hide low-contrast warnings
  }

  // ---- boot -------------------------------------------------------------
  function init() {
    var root = document.querySelector(".ed");
    if (!root) return;
    S.cfg = {
      docUrl: root.dataset.docUrl,
      saveUrl: root.dataset.saveUrl,
      catalogUrl: root.dataset.catalogUrl,
      dataUrl: root.dataset.dataUrl,
      iconCssUrl: root.dataset.iconCssUrl,
      devicesUrl: root.dataset.devicesUrl,
      sendUrl: root.dataset.sendUrl,
      previewUrl: root.dataset.previewUrl,
      sourceFormUrl: root.dataset.sourceFormUrl,
      sourceOptionsUrl: root.dataset.sourceOptionsUrl,
      canvasId: root.dataset.canvasId,
    };
    // Canvas-management URLs derive from this editor's own path
    // (…/c/<id>), so they carry any ingress prefix for free.
    S.cfg.compBase = location.pathname.replace(/\/c\/[^/]+$/, "");
    artboard = $("panels-artboard");
    scaler = $("panels-scaler");

    // Zoom controls + wheel-zoom + space-drag pan.
    var vp = scaler.parentElement;
    if (vp) {
      var zc = el("div", "zoombar");
      zc.innerHTML =
        '<button class="zb" data-z="out" title="Zoom out"><i class="ph-bold ph-minus"></i></button>' +
        '<input type="range" id="panels-zoom-range" class="zrange" min="20" max="400" step="1" value="100" title="Zoom">' +
        '<span id="panels-zoom-label" class="zlab">Fit</span>' +
        '<button class="zb" data-z="in" title="Zoom in"><i class="ph-bold ph-plus"></i></button>' +
        '<button class="zb" data-z="fit" title="Fit to view"><i class="ph-bold ph-corners-in"></i></button>';
      zc.addEventListener("click", function (ev) {
        var t = ev.target.closest ? ev.target.closest("[data-z]") : null;
        if (!t) return;
        if (t.dataset.z === "fit") setZoom(null);
        else { var cur = currentZoom(); setZoom(t.dataset.z === "in" ? cur * 1.25 : cur / 1.25); }
      });
      var zrange = zc.querySelector("#panels-zoom-range");
      if (zrange) zrange.addEventListener("input", function () { setZoom(Number(zrange.value) / 100); });
      vp.appendChild(zc);
      vp.addEventListener("wheel", function (ev) {
        if (!(ev.ctrlKey || ev.metaKey)) return;
        ev.preventDefault();
        setZoom(currentZoom() * (ev.deltaY < 0 ? 1.1 : 0.9));
      }, { passive: false });
      vp.addEventListener("pointerdown", function (ev) {
        if (!S.spaceDown) return;
        ev.stopPropagation(); ev.preventDefault();
        var sx = ev.clientX, sy = ev.clientY, ox = S.panX || 0, oy = S.panY || 0;
        vp.setPointerCapture(ev.pointerId);
        function pm(m) { S.panX = ox + (m.clientX - sx); S.panY = oy + (m.clientY - sy); applyTransform(currentZoom()); }
        function pu() { vp.removeEventListener("pointermove", pm); vp.removeEventListener("pointerup", pu); }
        vp.addEventListener("pointermove", pm); vp.addEventListener("pointerup", pu);
      }, true);
    }
    document.addEventListener("keyup", function (ev) {
      if (ev.code === "Space") { S.spaceDown = false; document.body.classList.remove("space-pan"); }
    });

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
    var previewBtn = $("panels-preview");
    if (previewBtn) previewBtn.addEventListener("click", openPreview);
    var saveBtn = $("panels-save-btn");
    if (saveBtn) saveBtn.addEventListener("click", saveNow);
    initThemeToggle();
    var canvasBtn = $("panels-canvas-menu");
    if (canvasBtn) canvasBtn.addEventListener("click", function (ev) {
      ev.stopPropagation(); toggleCanvasMenu(canvasBtn);
    });
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
      if (ev.code === "Space" && !typing && !mod) {
        if (!S.spaceDown) { S.spaceDown = true; document.body.classList.add("space-pan"); }
        ev.preventDefault(); return;
      }
      if (mod && (ev.key === "s" || ev.key === "S")) { ev.preventDefault(); saveNow(); return; }
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
        S.appearance = (res[0] && res[0].appearance) || { themes: [], styles: [], fonts: [] };
        S.doc = res[1];
        if (!S.doc.els) S.doc.els = [];
        var palette = $("panels-palette");
        if (palette) renderPalette(palette);
        renderAppearance();
        applyAppearance();
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
