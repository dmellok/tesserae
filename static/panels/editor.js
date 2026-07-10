/* Panels canvas editor (issue #60), phase 0 shell.
 *
 * The full editor (drag / resize / snap / bind / render) lands in later
 * phases; this bootstrap proves the vertical slice is wired end to end:
 * it renders the element palette and loads the widget catalog (the
 * data_schema layer) into the Data panel, so binding sources are real
 * widgets, not mocks.
 *
 * Plain ES module-free vanilla JS, matching the rest of static/. The
 * interactive build will follow the design handoff's model (transient DOM
 * refs + pointer events + rAF, commit on pointer-up) for 60fps.
 */
(function () {
  "use strict";

  // The visual element primitives users place on the canvas. Static for
  // now (the palette is drag-source only once interactions land).
  var PALETTE = [
    { type: "big", label: "Big number", icon: "ph-number-square-one" },
    { type: "small", label: "Small number", icon: "ph-number-square-two" },
    { type: "text", label: "Text label", icon: "ph-text-t" },
    { type: "icon", label: "Icon", icon: "ph-smiley" },
    { type: "spark", label: "Sparkline", icon: "ph-chart-line" },
    { type: "bar", label: "Bar chart", icon: "ph-chart-bar" },
    { type: "chip", label: "Chip / pill", icon: "ph-seal" },
    { type: "progress", label: "Progress", icon: "ph-gauge" },
    { type: "list", label: "List", icon: "ph-list-bullets" },
    { type: "image", label: "Image", icon: "ph-image" },
    { type: "shape", label: "Shape", icon: "ph-square" },
  ];

  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function renderPalette(mount) {
    PALETTE.forEach(function (p) {
      var tile = el("div", "pi");
      tile.dataset.type = p.type;
      tile.title = p.label;
      tile.innerHTML =
        '<span class="ico"><i class="ph-bold ' +
        p.icon +
        '"></i></span><span class="lab"></span>';
      tile.querySelector(".lab").textContent = p.label;
      mount.appendChild(tile);
    });
  }

  function fieldCount(w) {
    return (w.fields || []).length;
  }

  function renderData(mount, countEl, widgets) {
    mount.textContent = "";
    countEl.textContent =
      widgets.length + " source" + (widgets.length === 1 ? "" : "s");

    if (!widgets.length) {
      var empty = el("div", "note");
      empty.style.padding = "14px";
      empty.textContent =
        "No widgets declare a data schema yet. Add a data_schema block to a widget's plugin.json to make its fields bindable here.";
      mount.appendChild(empty);
      return;
    }

    widgets.forEach(function (w) {
      var head = el("div", "wgh");
      var badge = el("span", "wi");
      badge.style.background = w.color || "#256E6B";
      badge.innerHTML = '<i class="ph-bold ' + (w.icon || "ph-puzzle-piece") + '"></i>';
      head.appendChild(badge);
      head.appendChild(document.createTextNode(w.name || w.key));
      var count = el("span", "ct");
      count.textContent = fieldCount(w);
      head.appendChild(count);
      mount.appendChild(head);

      (w.fields || []).forEach(function (f) {
        var row = el("div", "fld");
        var key = el("span", "fk");
        key.textContent = f.label || f.name;
        var val = el("span", "dfield-val");
        var sample = w.sample && w.sample[f.name];
        var shown =
          f.type === "arr"
            ? (Array.isArray(sample) ? sample.length : 0) + " items"
            : sample == null
              ? "—"
              : String(sample);
        var v = el("span", "v");
        v.textContent = shown;
        val.appendChild(v);
        row.appendChild(key);
        row.appendChild(val);
        mount.appendChild(row);
      });
    });
  }

  function init() {
    var palette = document.getElementById("panels-palette");
    var data = document.getElementById("panels-data");
    var count = document.getElementById("panels-source-count");
    if (palette) renderPalette(palette);

    if (!data || !count) return;
    fetch("catalog.json", { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("catalog " + r.status);
        return r.json();
      })
      .then(function (payload) {
        renderData(data, count, (payload && payload.widgets) || []);
      })
      .catch(function () {
        data.textContent = "";
        var err = el("div", "note");
        err.style.padding = "14px";
        err.textContent = "Could not load the widget catalog.";
        data.appendChild(err);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
