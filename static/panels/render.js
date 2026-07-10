/* Panels shared element renderer (issue #60).
 *
 * ONE renderer, two hosts: the live editor (editor.js) and the server-side
 * compose page (panels_compose.html, screenshotted by Playwright). Both call
 * PanelsRender so what you design is exactly what the panel paints, no drift.
 *
 * Pure: no editor state. Each function takes the element plus a `resolve`
 * function that maps a binding path ("widget.field") to its value, so the
 * editor can resolve from catalog samples / live edits and the compose page
 * from real fetch() data, with identical rendering.
 */
(function () {
  "use strict";

  var DEMO_SERIES = [4, 6, 5, 8, 7, 9, 6, 8, 7, 10, 9, 11];

  function el(tag, html) {
    var n = document.createElement(tag);
    if (html != null) n.innerHTML = html;
    return n;
  }
  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  function valueOf(e, resolve) {
    if (e.binding) {
      var v = resolve(e.binding);
      return v == null ? "" : v;
    }
    return e.text;
  }
  function seriesOf(e, resolve) {
    var v = e.binding ? resolve(e.binding) : null;
    return Array.isArray(v) && v.length
      ? v.filter(function (n) { return typeof n === "number"; })
      : null;
  }
  function listRowsOf(e, resolve) {
    var v = e.binding ? resolve(e.binding) : null;
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

  function element(e, resolve) {
    var val = valueOf(e, resolve);
    var justify = e.align === "center" ? "center" : e.align === "right" ? "flex-end" : "flex-start";
    var base = "width:100%;height:100%;display:flex;align-items:center;overflow:hidden;justify-content:" + justify + ";";

    if (e.type === "big" || e.type === "small") {
      var d = el("div", (e.prefix || "") + (val === "" ? "0" : String(val)) + (e.suffix || ""));
      d.style.cssText = base + "font-weight:" + e.weight + ";font-size:" + e.font_size +
        "px;color:" + e.color + ";line-height:.92;letter-spacing:-.02em;font-variant-numeric:tabular-nums";
      return d;
    }
    if (e.type === "text") {
      var t = el("div", String(e.upper ? String(val).toUpperCase() : val));
      t.style.cssText = base + "font-weight:" + e.weight + ";font-size:" + e.font_size +
        "px;color:" + e.color + (e.upper ? ";letter-spacing:.05em" : "");
      return t;
    }
    if (e.type === "chip") {
      var chip = el("div",
        (e.icon ? '<i class="ph-bold ph-' + e.icon + '" style="margin-right:6px"></i>' : "") +
        (val === "" ? "Chip" : String(val)));
      chip.style.cssText = "display:inline-flex;align-items:center;padding:4px 12px;border:2px solid " +
        e.color + ";border-radius:999px;color:" + e.color + ";font-weight:700;font-size:" +
        Math.min(e.font_size, 18) + "px;white-space:nowrap";
      var w = el("div"); w.style.cssText = base; w.appendChild(chip); return w;
    }
    if (e.type === "icon") {
      var glyph = e.binding ? String(val || e.icon) : e.icon || "star";
      var i = el("div", '<i class="ph-bold ph-' + glyph + '"></i>');
      i.style.cssText = base + "font-size:" + Math.min(e.w, e.h) + "px;color:" + e.color;
      return i;
    }
    if (e.type === "progress") {
      var pct = clamp(Number(val) || 0, 0, 100);
      var track = el("div", '<div style="width:' + pct + "%;height:100%;background:" + e.color + '"></div>');
      track.style.cssText = "width:100%;height:100%;border-radius:999px;background:#E1DDD2;overflow:hidden";
      return track;
    }
    if (e.type === "spark" || e.type === "bar") {
      var boxc = el("div", "<canvas></canvas>");
      boxc.style.cssText = "width:100%;height:100%;position:relative";
      return boxc;
    }
    if (e.type === "list") {
      var rows = listRowsOf(e, resolve);
      var ul = el("div");
      ul.style.cssText = "width:100%;height:100%;overflow:hidden;color:" + e.color + ";font-size:14px";
      rows.forEach(function (r, idx) {
        var row = el("div",
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
      var img = el("div", '<i class="ph-bold ph-image" style="font-size:' + Math.min(e.w, e.h) / 3 + "px;color:" + e.color + '"></i>');
      img.style.cssText = base + "justify-content:center;background:#E1DDD2;border:2px solid " + e.color + ";border-radius:8px";
      return img;
    }
    if (e.type === "shape") {
      var s = el("div");
      var round = e.shape_kind === "ellipse";
      s.style.cssText = "width:100%;height:100%;" +
        (e.mode === "outline" ? "background:transparent;border:" + e.stroke + "px solid " + e.color : "background:" + e.color) +
        ";border-radius:" + (round ? "50%" : e.radius + "px");
      return s;
    }
    var ph = el("div", e.type);
    ph.style.cssText = base + "justify-content:center;border:1.5px dashed #B6B1A4;border-radius:6px";
    return ph;
  }

  function chart(e, canvas, resolve) {
    var arr = seriesOf(e, resolve) || DEMO_SERIES;
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

  window.PanelsRender = { element: element, chart: chart, seriesOf: seriesOf, DEMO_SERIES: DEMO_SERIES };
})();
