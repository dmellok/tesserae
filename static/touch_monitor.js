/*
 * Per-device touch monitor (issue #49).
 *
 * Draws the panel as an SVG at its true aspect ratio, overlays the last
 * render's touch regions, and plots touch events (taps as dots, swipes as
 * arrows, sliders as a dot + value), colour-coded by outcome. Seeds from
 * data.json, then streams live off the events SSE feed.
 */
(function () {
  "use strict";
  var SVGNS = "http://www.w3.org/2000/svg";

  var stage = document.querySelector("[data-tm-stage]");
  if (!stage) return;
  var panelEl = stage.querySelector("[data-tm-panel]");
  var deviceId = stage.dataset.deviceId;
  var W = parseInt(stage.dataset.panelW, 10) || 1;
  var H = parseInt(stage.dataset.panelH, 10) || 1;
  var dataUrl = stage.dataset.dataUrl;
  var streamUrl = stage.dataset.streamUrl;

  var showRegions = document.querySelector("[data-tm-regions]");
  var showLabels = document.querySelector("[data-tm-labels]");
  var clearBtn = document.querySelector("[data-tm-clear]");
  var countEl = document.querySelector("[data-tm-count]");
  var liveEl = document.querySelector("[data-tm-live]");
  var emptyEl = document.querySelector("[data-tm-empty]");

  // Radius / stroke expressed in panel units so they scale with the SVG.
  var R = Math.max(6, Math.round(Math.min(W, H) * 0.012));

  var svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.classList.add("tm-svg");
  var gRegions = document.createElementNS(SVGNS, "g");
  var gMarks = document.createElementNS(SVGNS, "g");
  // Arrowhead marker for swipes.
  var defs = document.createElementNS(SVGNS, "defs");
  defs.innerHTML =
    '<marker id="tm-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
    '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"></path></marker>';
  svg.appendChild(defs);
  svg.appendChild(gRegions);
  svg.appendChild(gMarks);
  panelEl.appendChild(svg);

  function statusClass(status) {
    if (["dispatched", "ha_dispatched", "webhook_dispatched", "noop"].indexOf(status) >= 0) return "ok";
    if (["blocked", "error", "ha_failed"].indexOf(status) >= 0) return "bad";
    return "miss"; // no_target / stale / deduped / no_frame
  }

  var count = 0;
  function bumpCount() {
    count += 1;
    if (countEl) countEl.textContent = count + (count === 1 ? " event" : " events");
    if (emptyEl) emptyEl.hidden = count > 0;
  }

  function el(name, attrs) {
    var n = document.createElementNS(SVGNS, name);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }

  function renderRegions(regions) {
    gRegions.textContent = "";
    (regions || []).forEach(function (r) {
      var bad = (r.invalid && r.invalid.length) ? " tm-region--invalid" : "";
      gRegions.appendChild(el("rect", {
        x: r.x, y: r.y, width: r.w, height: r.h,
        class: "tm-region" + bad,
        rx: 4,
      }));
    });
  }

  function addMark(ev) {
    var t = ev.touch;
    if (!t || t.x0 == null) return;
    var cls = statusClass(ev.status);
    var g = el("g", { class: "tm-mark tm-mark--" + cls });
    var gesture = ev.gesture || "tap";
    var moved = t.x1 != null && (t.x1 !== t.x0 || t.y1 !== t.y0);
    if (gesture.indexOf("swipe") === 0 && moved) {
      g.appendChild(el("line", {
        x1: t.x0, y1: t.y0, x2: t.x1, y2: t.y1,
        class: "tm-swipe", "marker-end": "url(#tm-arrow)",
      }));
    }
    g.appendChild(el("circle", { cx: t.x0, cy: t.y0, r: R, class: "tm-dot" }));
    // Slider value + action label.
    var label = "";
    if (gesture === "slide" && ev.value != null) label = String(ev.value);
    else if (showLabels && showLabels.checked && ev.action_spec) {
      label = typeof ev.action_spec === "string" ? ev.action_spec : "ha";
    }
    if (label) {
      var text = el("text", { x: t.x0 + R * 1.4, y: t.y0 + R * 0.5, class: "tm-label" });
      text.textContent = label.length > 40 ? label.slice(0, 39) + "…" : label;
      g.appendChild(text);
    }
    gMarks.appendChild(g);
    bumpCount();
    // Fade the oldest marks so the view doesn't fill up forever.
    while (gMarks.childNodes.length > 400) gMarks.removeChild(gMarks.firstChild);
  }

  function applyToggles() {
    gRegions.style.display = (showRegions && showRegions.checked) ? "" : "none";
  }
  if (showRegions) showRegions.addEventListener("change", applyToggles);
  if (showLabels) showLabels.addEventListener("change", function () {
    // Re-render labels by reloading marks is overkill; just toggle a class.
    svg.classList.toggle("tm-hide-labels", !showLabels.checked);
  });
  function clearView() {
    gMarks.textContent = ""; count = 0;
    if (countEl) countEl.textContent = "";
    if (emptyEl) emptyEl.hidden = false;
  }
  if (clearBtn) clearBtn.addEventListener("click", function () {
    if (!window.confirm(
      "Clear the recorded touch events for this device? This removes them " +
      "from the monitor and the Events history and can't be undone."
    )) return;
    var url = clearBtn.dataset.clearUrl;
    if (!url) { clearView(); return; }
    // Delete server-side first so the clear survives a refresh; only wipe
    // the view once the rows are actually gone.
    fetch(url, { method: "POST" })
      .then(function (r) { if (r.ok) clearView(); })
      .catch(function () { /* leave the view as-is on failure */ });
  });

  // Initial paint.
  fetch(dataUrl)
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (j) {
      renderRegions(j.regions);
      applyToggles();
      // Oldest first so the newest end up on top.
      (j.events || []).slice().reverse().forEach(addMark);
      if (emptyEl) emptyEl.hidden = count > 0;
    })
    .catch(function () { if (emptyEl) { emptyEl.hidden = false; emptyEl.textContent = "Couldn't load touch data."; } });

  // Live stream: the shared events SSE feed, filtered to this device.
  if (streamUrl && typeof EventSource !== "undefined") {
    var es = new EventSource(streamUrl);
    es.addEventListener("log", function (e) {
      var row;
      try { row = JSON.parse(e.data); } catch (err) { return; }
      if (!row || row.target !== deviceId) return;
      var x = row.extra || {};
      addMark({
        status: row.status,
        gesture: x.gesture,
        value: x.value,
        touch: x.touch,
        action_spec: x.action_spec,
      });
      if (liveEl) {
        liveEl.classList.add("tm-live--flash");
        setTimeout(function () { liveEl.classList.remove("tm-live--flash"); }, 250);
      }
    });
    es.onerror = function () { if (liveEl) liveEl.classList.add("tm-live--down"); };
    es.onopen = function () { if (liveEl) liveEl.classList.remove("tm-live--down"); };
  }
})();
