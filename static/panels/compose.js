/* Panels canvas compose bootstrap (issue #60).
 *
 * Runs on the headless render page (panels_compose.html). Reads the element
 * list + resolved data injected as JSON, paints each element read-only with
 * the shared PanelsRender, then raises window.__tesseraeComposed so the
 * Playwright renderer knows the frame is ready to screenshot.
 */
(function () {
  "use strict";

  function ready() { window.__tesseraeComposed = true; }

  try {
    var els = JSON.parse(document.getElementById("panels-els").textContent || "[]");
    var data = JSON.parse(document.getElementById("panels-data").textContent || "{}");
    var resolve = function (b) { return data[b]; };
    var board = document.getElementById("panels-board");
    els.forEach(function (e) {
      if (e.visible === false) return;
      var node = document.createElement("div");
      node.style.cssText =
        "position:absolute;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w + "px;height:" + e.h + "px";
      node.appendChild(PanelsRender.element(e, resolve));
      board.appendChild(node);
      if (e.type === "spark" || e.type === "bar") {
        var c = node.querySelector("canvas");
        if (c) PanelsRender.chart(e, c, resolve);
      }
    });
  } catch (err) {
    // Signal anyway so a malformed doc can't hang the renderer for 15s.
  }
  // Charts (animation:false) paint synchronously; give one frame to settle.
  requestAnimationFrame(function () { requestAnimationFrame(ready); });
})();
