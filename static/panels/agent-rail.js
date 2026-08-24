// Agent pipeline rail for the canvas editor, "beacon" treatment.
//
// Subscribes to /agent/stream (app/agent_activity.py) and narrates what the MCP
// agent is doing to THIS canvas. One loud "now" at the top, phrased as the
// sentence the server supplies (step.verb), with a ring that sweeps while the
// step is in flight. Everything already done compresses into ticks below, so a
// glance answers "what is it doing" rather than "what did it do".
//
// Owns two pieces of the editor chrome, both created here so the editor's
// template carries none of it when the MCP experiment is off:
//   * the rail card, docked at the top of the right sidebar
//   * a throbber pill in the toolbar, live while the agent is working
//
// The host passes hooks rather than the rail reaching into the editor:
// onMoved(pageId, pageName) fires when the agent starts touching a different
// canvas, so the editor decides whether to follow.
window.PanelsAgentRail = (function () {
  "use strict";

  // A run is treated as finished after this long with no step. The server
  // splits runs far more coarsely (45s); this is just when the ring stops.
  var QUIET_MS = 7000;
  // Ticks kept in the DOM. Nobody scrolls back past this in a 300px column,
  // and the header keeps the true count.
  var MAX_TICKS = 80;

  var cfg = null, hooks = null;
  var card = null, ticksEl = null, nowEl = null, countEl = null, barEl = null, pill = null;
  var es = null;
  var run = null;
  var total = 0;      // every step this run, including ones that never showed
  var startedAt = 0;
  var quietT = null, tickT = null;
  var current = null; // the step the "now" block is showing
  var movedTo = null;
  var lastSeq = 0;    // highest step seq seen; EventSource reconnects replay

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmtMs(ms) {
    if (ms == null) return "";
    return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";
  }
  function fmtSecs(ms) { return (ms / 1000).toFixed(1) + "s"; }
  // What the step touched, as the sentence's object.
  function objectOf(step) {
    return [step.target, step.detail].filter(Boolean).map(esc).join(" &middot; ");
  }

  // ---- chrome ---------------------------------------------------------

  function build() {
    var right = document.getElementById("panels-right");
    if (!right) return false;

    card = el("div", "card2 ag");
    card.id = "panels-agent";
    card.hidden = true;
    card.innerHTML =
      '<div class="pnh"><span class="t"><i class="ph-bold ph-sparkle"></i>Agent</span>' +
      '<span class="x ag-count"></span></div>' +
      '<div class="ag-bar" hidden><i></i></div>';
    nowEl = el("div", "ag-now");
    ticksEl = el("div", "pnb scroll ag-ticks");
    card.appendChild(nowEl);
    card.appendChild(ticksEl);
    right.insertBefore(card, right.firstChild);
    countEl = card.querySelector(".ag-count");
    barEl = card.querySelector(".ag-bar");

    // Toolbar throbber, so a build is visible with the sidebar collapsed.
    var menu = document.getElementById("panels-canvas-menu");
    pill = el("div", "ag-pill",
      '<span class="ag-bars"><i></i><i></i><i></i></span><span class="ag-pill-t">agent</span>');
    pill.hidden = true;
    pill.title = "The agent is building this dashboard";
    pill.addEventListener("click", function () {
      card.hidden = false;
      card.scrollIntoView({ block: "nearest" });
    });
    if (menu && menu.parentNode) menu.parentNode.insertBefore(pill, menu);
    return true;
  }

  // ---- the "now" block ------------------------------------------------

  // An indeterminate ring: the agent never says how many calls a build will
  // take, so the sweep means "working" and the header carries the count.
  function renderNow(step) {
    current = step;
    nowEl.className = "ag-now is-live";
    nowEl.innerHTML =
      '<div class="ag-ring">' +
        '<span class="ag-ring-sweep"></span>' +
        '<i class="ph-bold ph-' + esc(step.icon || "circle") + '"></i>' +
      "</div>" +
      '<div class="ag-txt">' +
        '<div class="ag-verb">' + esc(step.verb || step.label) + "</div>" +
        '<div class="ag-obj">' + (objectOf(step) || "&nbsp;") + "</div>" +
      "</div>";
  }

  function renderDone() {
    nowEl.className = "ag-now is-done";
    nowEl.innerHTML =
      '<div class="ag-ring is-done"><i class="ph-bold ph-check"></i></div>' +
      '<div class="ag-txt"><div class="ag-verb">Agent finished</div>' +
      '<div class="ag-obj">' + total + " step" + (total === 1 ? "" : "s") +
      " in " + fmtSecs(Date.now() - startedAt) + "</div></div>";
  }

  function renderElsewhere() {
    nowEl.className = "ag-now is-away";
    nowEl.innerHTML =
      '<div class="ag-ring"><span class="ag-ring-sweep"></span>' +
      '<i class="ph-bold ph-arrow-square-out"></i></div>' +
      '<div class="ag-txt"><div class="ag-verb">Working elsewhere</div>' +
      '<div class="ag-obj">on another dashboard</div></div>';
  }

  function renderCount() {
    countEl.textContent = total
      ? total + " step" + (total === 1 ? "" : "s") + " · " + fmtSecs(Date.now() - startedAt)
      : "";
  }

  // ---- ticks ----------------------------------------------------------

  // Whatever the "now" block was showing has finished, so it drops into the
  // tick list. Repeats of the same step fold into one counted tick: an agent
  // reads far more than it writes, and streaming a code element is one call per
  // chunk. Folding keeps the history a summary rather than a transcript, and
  // the folded tick shows the LATEST detail, which is the running total.
  function pushTick(step) {
    if (!step) return;
    var probe = step.kind === "probe";
    var last = ticksEl.lastElementChild;
    // Reads fold together whatever they were (the operator counts them, not
    // reads them); everything else folds only with a repeat of itself. An
    // error never folds into a success.
    var sameAsLast = last &&
      last.classList.contains("is-err") === (step.status === "error") &&
      (probe ? last.classList.contains("is-probe") : last.dataset.ep === step.endpoint);
    if (sameAsLast) {
      var n = Number(last.dataset.n || 1) + 1;
      last.dataset.n = String(n);
      last.querySelector(".ag-tick-l").textContent =
        probe ? "read " + n + " things" : step.label + " ×" + n;
      var t = last.querySelector(".ag-tick-t");
      if (t) t.innerHTML = objectOf(step);
      last.querySelector(".ag-tick-ms").textContent = fmtMs(step.duration_ms);
      ticksEl.scrollTop = ticksEl.scrollHeight;
      return;
    }
    var tick = el("div", "ag-tick" +
      (probe ? " is-probe" : "") +
      (step.status === "error" ? " is-err" : "") +
      (step.kind === "send" ? " is-send" : ""));
    tick.dataset.n = "1";
    tick.dataset.ep = step.endpoint || "";
    tick.innerHTML =
      '<span class="ag-tick-m">' +
        (step.status === "error"
          ? '<i class="ph-bold ph-x"></i>'
          : probe ? "" : '<i class="ph-bold ph-check"></i>') +
      "</span>" +
      '<span class="ag-tick-l">' + esc(probe ? "read 1 thing" : step.label) + "</span>" +
      (probe ? "" : '<span class="ag-tick-t">' + objectOf(step) + "</span>") +
      '<span class="ag-tick-ms">' + fmtMs(step.duration_ms) + "</span>";
    ticksEl.appendChild(tick);
    while (ticksEl.children.length > MAX_TICKS) ticksEl.removeChild(ticksEl.firstChild);
    ticksEl.scrollTop = ticksEl.scrollHeight;
  }

  // ---- run lifecycle --------------------------------------------------

  function beginRun(n) {
    run = n;
    total = 0;
    movedTo = null;
    current = null;
    startedAt = Date.now();
    ticksEl.innerHTML = "";
    card.hidden = false;
    barEl.hidden = false;
    pill.hidden = false;
    pill.classList.add("is-live");
    clearInterval(tickT);
    tickT = setInterval(renderCount, 200);
  }

  function endRun() {
    clearInterval(tickT);
    tickT = null;
    pushTick(current);
    current = null;
    pill.classList.remove("is-live");
    pill.hidden = true;
    barEl.hidden = true;
    renderDone();
    renderCount();
  }

  function armQuiet() {
    clearTimeout(quietT);
    quietT = setTimeout(endRun, QUIET_MS);
  }

  // ``replay`` marks a step from the opening snapshot: it already happened, so
  // it fills the rail in but must never drive navigation. Without that, opening
  // an editor midway through a run would follow the agent to wherever its
  // HISTORY last pointed, which is not where it is now.
  function onStep(step, replay) {
    // An EventSource reconnect re-sends the run's snapshot, so a step already
    // rendered must not land twice. Sequence numbers come from the bus and only
    // ever increase.
    if (step.seq && step.seq <= lastSeq) return;
    if (step.seq) lastSeq = step.seq;
    if (step.run !== run) beginRun(step.run);
    total += 1;

    // A step on a different canvas: the agent has moved on. Tell the host once
    // per target and keep the rail showing this canvas's work.
    if (step.page_id && cfg.canvasId && step.page_id !== cfg.canvasId) {
      if (!replay && movedTo !== step.page_id) {
        movedTo = step.page_id;
        pushTick(current);
        current = null;
        renderElsewhere();
        if (hooks && hooks.onMoved) hooks.onMoved(step.page_id, step.page_name || "");
      }
      renderCount();
      armQuiet();
      return;
    }
    if (step.page_id === cfg.canvasId) movedTo = null;

    pushTick(current);   // the step it replaces is now history
    renderNow(step);
    renderCount();
    armQuiet();
  }

  // ---- wiring ---------------------------------------------------------

  function init(config, callbacks) {
    cfg = config || {};
    hooks = callbacks || {};
    if (!cfg.streamUrl || typeof EventSource === "undefined") return;
    if (!build()) return;
    try { es = new EventSource(cfg.streamUrl); } catch { return; }
    es.addEventListener("snapshot", function (ev) {
      var data;
      try { data = JSON.parse(ev.data); } catch { return; }
      var got = data.steps || [];
      got.forEach(function (s) { onStep(s, true); });
      if (!got.length) return;
      // A snapshot is history. Backdate the clock by the span it covers (from
      // the server's own timestamps, so no clock skew creeps in) and let the
      // quiet timer settle the rail if the agent has already stopped.
      var span = (got[got.length - 1].ts - got[0].ts) * 1000;
      if (span > 0) startedAt -= span;
      armQuiet();
    });
    es.addEventListener("step", function (ev) {
      var step;
      try { step = JSON.parse(ev.data); } catch { return; }
      onStep(step);
    });
  }

  return { init: init };
})();
