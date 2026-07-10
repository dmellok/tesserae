/* Panels canvas editor (issue #60).
 *
 * The core editing loop: hydrate a canvas document, render its elements on
 * the artboard, create elements by dragging from the palette, select and
 * move them (grid snap), delete, and autosave. Following the design
 * handoff's performance model, an in-flight drag mutates the DOM node
 * directly via transforms and commits to state only on pointer-up, so
 * dragging stays at 60fps regardless of element count.
 *
 * Deferred to later phases: resize handles, grouping, undo/redo, the layers
 * panel interactions, alignment guides, live-data binding preview, simulate
 * mode, and the server-side compose render. Plain vanilla JS, no build step.
 */
(function () {
  "use strict";

  var GRID = 4; // snap step; e-ink quantization is cleaner on 4px bounds.
  var INKS = ["#1B1A16", "#F7F5F0", "#A84B2A", "#C28A04", "#3F5A88", "#4F6F36"];

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

  function $(id) {
    return document.getElementById(id);
  }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function snap(v) {
    return Math.round(v / GRID) * GRID;
  }
  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }
  function uid() {
    return "el_" + Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-3);
  }

  // ---- editor state -----------------------------------------------------
  var S = {
    cfg: null, // {docUrl, saveUrl, catalogUrl}
    doc: null, // the CanvasPage
    catalog: [], // widget catalog
    sel: null, // selected element id
    saveTimer: null,
  };

  // ---- element defaults + rendering ------------------------------------
  function defaultsFor(type, x, y, w, h) {
    return {
      id: uid(),
      type: type,
      name: type,
      x: x,
      y: y,
      w: w,
      h: h,
      binding: null,
      text: type === "text" ? "Label" : "",
      prefix: "",
      suffix: "",
      upper: false,
      weight: 700,
      color: "#1B1A16",
      align: "left",
      font_size: type === "big" ? 56 : type === "small" ? 28 : 18,
      icon: type === "icon" ? "star" : "",
      dither: true,
      visible: true,
      locked: false,
      group: null,
      shape_kind: "rect",
      mode: "fill",
      stroke: 2,
      radius: 0,
    };
  }

  // Resolve an element's bound value from the catalog samples, or its own
  // literal text. Live-data editing is a later phase; samples suffice to
  // show a faithful preview now.
  function valueOf(e) {
    if (e.binding) {
      var parts = e.binding.split(".");
      var w = S.catalog.filter(function (c) { return c.key === parts[0]; })[0];
      if (w && w.sample && parts[1] in w.sample) return w.sample[parts[1]];
      return "";
    }
    return e.text;
  }

  function renderElement(e) {
    var val = valueOf(e);
    var align = e.align || "left";
    var base = "width:100%;height:100%;display:flex;align-items:center;overflow:hidden;";
    var justify = align === "center" ? "center" : align === "right" ? "flex-end" : "flex-start";
    if (e.type === "big" || e.type === "small") {
      var d = el("div", null,
        (e.prefix || "") +
        (val === "" || val == null ? "0" : String(val)) +
        (e.suffix || ""));
      d.style.cssText = base +
        "justify-content:" + justify +
        ";font-weight:" + e.weight +
        ";font-size:" + e.font_size + "px;color:" + e.color +
        ";line-height:.92;letter-spacing:-.02em;font-variant-numeric:tabular-nums";
      return d;
    }
    if (e.type === "text") {
      var t = el("div", null, String(e.upper ? String(val).toUpperCase() : val));
      t.style.cssText = base +
        "justify-content:" + justify +
        ";font-weight:" + e.weight +
        ";font-size:" + e.font_size + "px;color:" + e.color +
        (e.upper ? ";letter-spacing:.05em" : "");
      return t;
    }
    if (e.type === "chip") {
      var c = el("div", null,
        (e.icon ? '<i class="ph-bold ph-' + e.icon + '" style="margin-right:6px"></i>' : "") +
        (val === "" ? "Chip" : String(val)));
      c.style.cssText =
        "display:inline-flex;align-items:center;padding:4px 12px;border:2px solid " +
        e.color + ";border-radius:999px;color:" + e.color +
        ";font-weight:700;font-size:" + Math.min(e.font_size, 18) + "px;white-space:nowrap";
      var wrap = el("div", null);
      wrap.style.cssText = base + "justify-content:" + justify;
      wrap.appendChild(c);
      return wrap;
    }
    if (e.type === "icon") {
      var glyph = e.binding ? String(val || e.icon) : e.icon || "star";
      var i = el("div", null, '<i class="ph-bold ph-' + glyph + '"></i>');
      i.style.cssText = base + "justify-content:" + justify +
        ";font-size:" + Math.min(e.w, e.h) + "px;color:" + e.color;
      return i;
    }
    if (e.type === "progress") {
      var pct = clamp(Number(val) || 0, 0, 100);
      var track = el("div", null, '<div style="width:' + pct + "%;height:100%;background:" + e.color + '"></div>');
      track.style.cssText =
        "width:100%;height:100%;border-radius:999px;background:#E1DDD2;overflow:hidden";
      return track;
    }
    if (e.type === "shape") {
      var s = el("div", null);
      var isRound = e.shape_kind === "ellipse";
      s.style.cssText =
        "width:100%;height:100%;" +
        (e.mode === "outline"
          ? "background:transparent;border:" + e.stroke + "px solid " + e.color
          : "background:" + e.color) +
        ";border-radius:" + (isRound ? "50%" : e.radius + "px");
      return s;
    }
    // list / image / spark / bar: placeholder box until their renderers land.
    var ph = el("div", null,
      '<span style="font-size:11px;color:var(--eink-muted,#837F73)">' + e.type + "</span>");
    ph.style.cssText = base + "justify-content:center;border:1.5px dashed #B6B1A4;border-radius:6px";
    return ph;
  }

  // ---- artboard rendering ----------------------------------------------
  var artboard, scaler;

  function elNode(e) {
    var node = el("div", "el" + (e.id === S.sel ? " psel" : ""));
    node.dataset.id = e.id;
    node.style.cssText =
      "position:absolute;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w +
      "px;height:" + e.h + "px;" + (e.visible ? "" : "opacity:.4;");
    node.appendChild(renderElement(e));
    if (e.id === S.sel) node.appendChild(el("div", "ring"));
    node.addEventListener("pointerdown", onElDown);
    return node;
  }

  function paint() {
    artboard.style.width = S.doc.w + "px";
    artboard.style.height = S.doc.h + "px";
    // Wipe element nodes (keep nothing; overlays are added by interactions).
    artboard.textContent = "";
    S.doc.els.forEach(function (e) {
      artboard.appendChild(elNode(e));
    });
    fitZoom();
    renderLayers();
    renderProps();
  }

  function fitZoom() {
    var vp = scaler.parentElement;
    var z = Math.min(
      (vp.clientWidth - 56) / S.doc.w,
      (vp.clientHeight - 56) / S.doc.h,
      1
    );
    z = Math.max(z, 0.5);
    scaler.style.transform = "scale(" + z + ")";
    scaler.dataset.zoom = z;
  }

  function currentZoom() {
    return Number(scaler.dataset.zoom || 1);
  }

  // ---- selection + move -------------------------------------------------
  function select(id) {
    S.sel = id;
    paint();
  }

  function onElDown(ev) {
    ev.stopPropagation();
    var id = ev.currentTarget.dataset.id;
    select(id);
    var e = byId(id);
    if (!e || e.locked) return;
    var node = ev.currentTarget;
    var z = currentZoom();
    var startX = ev.clientX;
    var startY = ev.clientY;
    var origX = e.x;
    var origY = e.y;
    var moved = false;
    node.setPointerCapture(ev.pointerId);

    function move(m) {
      var dx = (m.clientX - startX) / z;
      var dy = (m.clientY - startY) / z;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 2) return;
      moved = true;
      var nx = clamp(snap(origX + dx), 0, S.doc.w - e.w);
      var ny = clamp(snap(origY + dy), 0, S.doc.h - e.h);
      node.style.left = nx + "px";
      node.style.top = ny + "px";
      node._nx = nx;
      node._ny = ny;
    }
    function up() {
      node.releasePointerCapture(ev.pointerId);
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
      if (moved && node._nx != null) {
        e.x = node._nx;
        e.y = node._ny;
        scheduleSave();
        renderProps();
      }
    }
    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", up);
  }

  // ---- create-drag from palette ----------------------------------------
  function renderPalette(mount) {
    PALETTE.forEach(function (p) {
      var tile = el("div", "pi");
      tile.dataset.type = p.type;
      tile.title = p.label;
      tile.innerHTML =
        '<span class="ico"><i class="ph-bold ' + p.icon + '"></i></span><span class="lab"></span>';
      tile.querySelector(".lab").textContent = p.label;
      tile.addEventListener("pointerdown", function (ev) {
        onPaletteDown(ev, p);
      });
      mount.appendChild(tile);
    });
  }

  function onPaletteDown(ev, p) {
    ev.preventDefault();
    var ghost = el("div", "ghost", p.label);
    ghost.style.cssText =
      "position:fixed;pointer-events:none;z-index:9999;left:0;top:0;padding:6px 10px;" +
      "background:var(--t-surface);border:1px solid var(--t-border-strong);border-radius:8px;" +
      "font-size:12px;font-weight:600;box-shadow:0 4px 12px rgba(16,12,8,.14)";
    document.body.appendChild(ghost);
    function move(m) {
      ghost.style.transform = "translate(" + (m.clientX + 8) + "px," + (m.clientY + 8) + "px)";
    }
    function up(m) {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      ghost.remove();
      var r = artboard.getBoundingClientRect();
      var z = currentZoom();
      var cx = (m.clientX - r.left) / z;
      var cy = (m.clientY - r.top) / z;
      if (cx < 0 || cy < 0 || cx > S.doc.w || cy > S.doc.h) return; // dropped off-board
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

  // ---- layers + properties (minimal) -----------------------------------
  function renderLayers() {
    var mount = $("panels-layers");
    if (!mount) return;
    mount.textContent = "";
    if (!S.doc.els.length) {
      var empty = el("div", "note");
      empty.style.padding = "14px";
      empty.textContent = "Drag an element from the palette onto the artboard.";
      mount.appendChild(empty);
      return;
    }
    // top-most (last painted) first
    S.doc.els.slice().reverse().forEach(function (e) {
      var row = el("div", "lrow" + (e.id === S.sel ? " psel" : ""));
      row.innerHTML =
        '<i class="ph-bold ph-square ic"></i><span class="nm"></span>';
      row.querySelector(".nm").textContent = e.name || e.type;
      row.addEventListener("pointerdown", function () {
        select(e.id);
      });
      mount.appendChild(row);
    });
  }

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

  function renderProps() {
    var mount = $("panels-data");
    var count = $("panels-source-count");
    if (!mount) return;
    var e = S.sel ? byId(S.sel) : null;

    if (!e) {
      // Data panel (nothing selected): list catalog widgets + fields.
      if (count) {
        count.textContent = S.catalog.length + " source" + (S.catalog.length === 1 ? "" : "s");
      }
      mount.textContent = "";
      if (!S.catalog.length) {
        var none = el("div", "note");
        none.style.padding = "14px";
        none.textContent = "No widgets declare a data schema yet.";
        mount.appendChild(none);
        return;
      }
      S.catalog.forEach(function (w) {
        var head = el("div", "wgh");
        var badge = el("span", "wi");
        badge.style.background = w.color || "#256E6B";
        badge.innerHTML = '<i class="ph-bold ' + (w.icon || "ph-puzzle-piece") + '"></i>';
        head.appendChild(badge);
        head.appendChild(document.createTextNode(w.name || w.key));
        var ct = el("span", "ct");
        ct.textContent = (w.fields || []).length;
        head.appendChild(ct);
        mount.appendChild(head);
        (w.fields || []).forEach(function (f) {
          var row = el("div", "fld");
          var k = el("span", "fk");
          k.textContent = f.label || f.name;
          var v = el("span", "dfield-val");
          var sv = w.sample && w.sample[f.name];
          v.innerHTML = '<span class="v"></span>';
          v.querySelector(".v").textContent =
            f.type === "arr" ? (Array.isArray(sv) ? sv.length : 0) + " items" : sv == null ? "—" : String(sv);
          row.appendChild(k);
          row.appendChild(v);
          mount.appendChild(row);
        });
      });
      return;
    }

    // One element selected: minimal properties (binding, colour, delete).
    mount.textContent = "";
    var sec = el("div", "psec", '<i class="ph-bold ph-database"></i>Binding');
    mount.appendChild(sec);
    var row = el("div", "prow");
    row.innerHTML = '<span class="plab">Field</span>';
    var sel = el("select", "psel");
    sel.innerHTML = bindOptions();
    sel.value = e.binding || "";
    sel.addEventListener("change", function () {
      e.binding = sel.value || null;
      scheduleSave();
      paint();
    });
    row.appendChild(sel);
    mount.appendChild(row);

    var csec = el("div", "psec", '<i class="ph-bold ph-palette"></i>Colour');
    mount.appendChild(csec);
    var swatches = el("div", "prow");
    INKS.forEach(function (ink) {
      var sw = el("span");
      sw.style.cssText =
        "width:22px;height:22px;border-radius:6px;margin-right:6px;cursor:pointer;background:" +
        ink + (ink === e.color ? ";outline:2px solid var(--t-accent);outline-offset:2px" : ";border:1px solid var(--t-border)");
      sw.addEventListener("click", function () {
        e.color = ink;
        scheduleSave();
        paint();
      });
      swatches.appendChild(sw);
    });
    mount.appendChild(swatches);

    var del = el("div", "prow");
    var btn = el("button", "minibtn", '<i class="ph-bold ph-trash"></i> Delete');
    btn.addEventListener("click", function () {
      deleteEl(e.id);
    });
    del.appendChild(btn);
    mount.appendChild(del);
  }

  // ---- helpers ----------------------------------------------------------
  function byId(id) {
    return S.doc.els.filter(function (e) { return e.id === id; })[0] || null;
  }
  function deleteEl(id) {
    S.doc.els = S.doc.els.filter(function (e) { return e.id !== id; });
    if (S.sel === id) S.sel = null;
    scheduleSave();
    paint();
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
      .then(function () {
        var status = $("panels-status");
        if (status) status.textContent = "saved";
      })
      .catch(function () {
        var status = $("panels-status");
        if (status) status.textContent = "save failed";
      });
  }

  // ---- boot -------------------------------------------------------------
  function init() {
    var root = document.querySelector(".ed");
    if (!root) return;
    S.cfg = {
      docUrl: root.dataset.docUrl,
      saveUrl: root.dataset.saveUrl,
      catalogUrl: root.dataset.catalogUrl,
    };
    artboard = $("panels-artboard");
    scaler = $("panels-scaler");
    var palette = $("panels-palette");
    if (palette) renderPalette(palette);

    artboard.addEventListener("pointerdown", function (ev) {
      if (ev.target === artboard) select(null); // click empty board clears selection
    });
    document.addEventListener("keydown", function (ev) {
      if ((ev.key === "Delete" || ev.key === "Backspace") && S.sel) {
        var t = document.activeElement;
        if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
        ev.preventDefault();
        deleteEl(S.sel);
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
      .catch(function () {
        var status = $("panels-status");
        if (status) status.textContent = "load failed";
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
