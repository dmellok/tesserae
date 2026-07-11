/* Decoration primitives for the Panels canvas (issue #60).
 *
 * Renders shape / line / icon / text elements, plus two data-aware kinds:
 *   - "data": a widget data field shown as text / number / line / bar /
 *     sparkline (the resolved widget data is passed as the second arg).
 *   - "html": agent- or user-authored markup, isolated in a sandboxed iframe
 *     (no scripts, no network) so it can't touch the page it renders in.
 * Shared by the editor (live preview) and the compose page (Send / preview.png)
 * so both draw identically. A ``color`` is any CSS colour or a Spectra token
 * like "var(--accent-1)" that resolves from the artboard's data-theme. Returns
 * a node that fills its element box.
 */
(function () {
  "use strict";

  function px(v, fallback) {
    var n = Number(v);
    return (isFinite(n) ? n : fallback);
  }

  // Resolve "a.b.0.c" against a nested object/array; undefined if any hop misses.
  function resolvePath(obj, path) {
    if (obj == null || !path) return undefined;
    var parts = String(path).split(".");
    var cur = obj;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null) return undefined;
      var k = parts[i];
      cur = Array.isArray(cur) && /^\d+$/.test(k) ? cur[Number(k)] : cur[k];
    }
    return cur;
  }

  function render(el, data) {
    var kind = el.kind || "rect";
    var color = el.color || "var(--text-primary, #1B1A16)";

    if (kind === "html") return renderHtml(el);
    if (kind === "data") return renderData(el, data, color);

    if (kind === "icon") {
      var i = document.createElement("i");
      // Phosphor weight class: regular is the bare "ph", others are "ph-<weight>".
      var weight = el.weight || "bold";
      var wcls = weight === "regular" ? "ph" : "ph-" + weight;
      i.className = wcls + " ph-" + (el.icon || "star");
      var size = Math.max(8, Math.round(Math.min(px(el.w, 64), px(el.h, 64)) * 0.82));
      i.style.cssText = "display:flex;align-items:center;justify-content:center;" +
        "width:100%;height:100%;line-height:1;color:" + color + ";font-size:" + size + "px";
      return i;
    }

    if (kind === "text") {
      var t = document.createElement("div");
      var size = el.size && el.size > 0 ? el.size : Math.max(10, Math.round(px(el.h, 40) * 0.5));
      var wmap = { thin: 300, light: 300, regular: 400, bold: 700, fill: 800, duotone: 700 };
      var fw = wmap[el.weight] || 700;
      var align = el.align || "left";
      var justify = align === "center" ? "center" : align === "right" ? "flex-end" : "flex-start";
      t.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:" + justify +
        ";color:" + color + ";font-size:" + size + "px;font-weight:" + fw + ";line-height:1.15;" +
        "text-align:" + align + ";overflow:hidden;font-family:var(--font-family, inherit)";
      t.textContent = el.text != null && el.text !== "" ? el.text : "Text";
      return t;
    }

    if (kind === "line") {
      var wrap = document.createElement("div");
      wrap.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center";
      var bar = document.createElement("div");
      var t = Math.max(1, px(el.stroke, 2));
      // Draw along the box's longer axis: wide box = horizontal rule, tall box
      // = vertical rule.
      if (px(el.w, 0) >= px(el.h, 0)) {
        bar.style.cssText = "width:100%;height:" + t + "px;background:" + color + ";border-radius:" + (t / 2) + "px";
      } else {
        bar.style.cssText = "height:100%;width:" + t + "px;background:" + color + ";border-radius:" + (t / 2) + "px";
      }
      wrap.appendChild(bar);
      return wrap;
    }

    // rect / ellipse
    var d = document.createElement("div");
    var radius = kind === "ellipse" ? "50%" : (Math.max(0, px(el.radius, 0)) + "px");
    var css = "width:100%;height:100%;box-sizing:border-box;border-radius:" + radius + ";";
    if (el.fill === false) {
      css += "border:" + Math.max(1, px(el.stroke, 2)) + "px solid " + color + ";background:transparent;";
    } else {
      css += "background:" + color + ";";
    }
    d.style.cssText = css;
    return d;
  }

  // Custom HTML/CSS in a locked-down iframe: sandbox="" disables scripts, the
  // network, and same-origin, so authored markup renders but can't run code or
  // reach anything. pointer-events:none keeps the element selectable in the editor.
  function renderHtml(el) {
    var f = document.createElement("iframe");
    f.setAttribute("sandbox", "");
    f.setAttribute("scrolling", "no");
    f.style.cssText =
      "width:100%;height:100%;border:0;background:transparent;display:block;pointer-events:none";
    var reset =
      "*{box-sizing:border-box}html,body{margin:0;padding:0;width:100%;height:100%;" +
      "overflow:hidden;font-family:var(--font-family,-apple-system,'Segoe UI',Roboto,sans-serif);" +
      "color:#1B1A16}";
    f.srcdoc =
      "<!doctype html><html><head><meta charset='utf-8'><style>" +
      reset + (el.css || "") + "</style></head><body>" + (el.html || "") + "</body></html>";
    return f;
  }

  // A widget data field, shown as text / number or a small chart.
  function renderData(el, data, color) {
    var value = resolvePath(data, el.field);
    var display = el.display || "text";
    if (display === "line" || display === "bar" || display === "sparkline") {
      return renderChart(el, value, display, color);
    }
    var align = el.align || "left";
    var box = document.createElement("div");
    box.style.cssText =
      "width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;overflow:hidden;" +
      "font-family:var(--font-family, inherit);color:" + color + ";align-items:" +
      (align === "center" ? "center" : align === "right" ? "flex-end" : "flex-start");
    var size = el.size && el.size > 0
      ? el.size
      : Math.max(10, Math.round(px(el.h, 40) * (el.label ? 0.42 : 0.58)));
    var main = document.createElement("div");
    main.style.cssText =
      "font-size:" + size + "px;font-weight:700;line-height:1.05;white-space:nowrap;text-align:" + align;
    var text;
    if (value == null || (Array.isArray(value) && !value.length)) {
      text = "—";
    } else if (display === "number") {
      var n = Number(value);
      text = isFinite(n) ? n.toFixed(Math.max(0, px(el.precision, 0))) : String(value);
    } else {
      text = String(value);
    }
    main.textContent = text + (el.unit || "");
    box.appendChild(main);
    if (el.label) {
      var lab = document.createElement("div");
      lab.style.cssText =
        "font-size:" + Math.max(8, Math.round(size * 0.32)) + "px;font-weight:600;opacity:.68;" +
        "margin-top:2px;white-space:nowrap;text-align:" + align;
      lab.textContent = el.label;
      box.appendChild(lab);
    }
    return box;
  }

  // Concrete colour for a canvas (fillStyle can't resolve var()/tokens): use the
  // element colour when it's a literal, else a dark-ink default (e-ink friendly).
  function inkColor(color) {
    return color && color.charAt(0) !== "v" ? color : "#1B1A16";
  }

  function renderChart(el, value, display, color) {
    var wrap = document.createElement("div");
    wrap.style.cssText = "width:100%;height:100%;position:relative";
    var values = Array.isArray(value)
      ? value.map(Number).filter(function (n) { return isFinite(n); })
      : [];
    if (!window.Chart || !values.length) {
      wrap.style.cssText +=
        ";display:flex;align-items:center;justify-content:center;color:var(--text-secondary,#8a8a82);" +
        "font:600 12px var(--font-family,sans-serif)";
      wrap.textContent = window.Chart ? "no data" : "chart unavailable";
      return wrap;
    }
    var ink = inkColor(color);
    var spark = display === "sparkline";
    var cv = document.createElement("canvas");
    cv.width = Math.max(1, Math.round(px(el.w, 200)));
    cv.height = Math.max(1, Math.round(px(el.h, 100)));
    cv.style.cssText = "width:100%;height:100%";
    wrap.appendChild(cv);
    new window.Chart(cv, {
      type: display === "bar" ? "bar" : "line",
      data: {
        labels: values.map(function (_, i) { return i; }),
        datasets: [{
          data: values,
          borderColor: ink,
          backgroundColor: spark ? "color-mix(in srgb, " + ink + " 20%, transparent)" : ink,
          borderWidth: 2,
          pointRadius: 0,
          fill: spark,
          tension: spark ? 0.35 : 0.2,
        }],
      },
      options: {
        responsive: false,
        animation: false,
        maintainAspectRatio: false,
        layout: { padding: spark ? 1 : 2 },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: {
          x: { display: !spark, grid: { display: false }, ticks: { color: ink, font: { size: 9 } } },
          y: { display: !spark, grid: { display: false }, ticks: { color: ink, font: { size: 9 } } },
        },
      },
    });
    return wrap;
  }

  window.PanelsDecorate = { render: render, resolvePath: resolvePath };
})();
