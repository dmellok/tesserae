/* Decoration primitives for the Panels canvas (issue #60).
 *
 * Renders static shape / line / icon elements. Shared by the editor (live
 * preview) and the compose page (Send / preview.png) so both draw identically.
 * A decoration's ``color`` is any CSS colour or a Spectra token like
 * "var(--accent-1)", which resolves from the artboard's data-theme so shapes
 * can follow the theme. Returns a node that fills its element box.
 */
(function () {
  "use strict";

  function px(v, fallback) {
    var n = Number(v);
    return (isFinite(n) ? n : fallback);
  }

  function render(el) {
    var kind = el.kind || "rect";
    var color = el.color || "var(--text-primary, #1B1A16)";

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

  window.PanelsDecorate = { render: render };
})();
