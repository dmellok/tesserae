// Follow-the-agent toast for the admin shell.
//
// Any Tesserae tab watches /agent/activity.json for MCP activity (see
// app/agent_activity.py) and, when the agent starts building a canvas
// dashboard, raises a toast naming it, narrating each step as it lands, and
// offering to jump to the editor. Auto-follow is on by default and can be
// switched off from the toast; the choice sticks in localStorage.
//
// Deliberately a poll, not an SSE stream: every open tab would otherwise pin a
// waitress worker thread for the life of the tab (see app/main.py), and the
// toast doesn't need sub-second latency. The editor's rail, which does, uses
// the stream. Polling stops entirely while the tab is hidden.
(function () {
  var root = document.getElementById("agent-follow");
  if (!root) return;

  var ACTIVITY_URL = root.dataset.activityUrl;
  var EDITOR_URL = root.dataset.editorUrl || ""; // "" when the canvas editor is off
  var FOLLOW_KEY = "tesserae-agent-follow";
  // Cadence. A run is "warm" while the surface was busy in the last few
  // seconds; otherwise back off so an idle install is nearly free.
  var POLL_WARM_MS = 1800;
  var POLL_COLD_MS = 6000;
  var WARM_IDLE_S = 12;
  // What a gap between calls means, in the same two stages the editor's rail
  // uses: a few seconds is the model thinking mid-build and the toast says so
  // rather than freezing on a step that has already finished; a longer silence
  // is the build being over, and the toast leaves.
  var THINK_MS = 9000;
  var HIDE_MS = 26000;
  var WORD_MS = 3800;
  var COUNTDOWN_S = 4;
  // Steps kept on the tape. Three is what fits without the toast growing into
  // a panel: what it just did, and what it is doing now.
  var TAPE = 3;
  var THINKING = [
    "Thinking", "Pondering", "Mulling it over", "Working it out", "Deliberating",
    "Musing", "Considering", "Weighing it up", "Ruminating", "Puzzling it out",
    "Percolating", "Chewing on it", "Turning it over", "Composing",
  ];

  var seq = 0;
  var run = null;
  var pageId = null;
  var pageName = "";
  var steps = 0;
  var tape = [];        // the last few steps, newest last
  var startedAt = 0;
  var lastStepAt = 0;
  var word = "";
  var wordAt = 0;
  var pollTimer = null;
  var stateTimer = null;
  var countdown = 0;
  var navigated = false;
  var userTyped = false;
  var primed = false;   // the first poll seeds state without acting on it

  // Any typing on the current page vetoes auto-navigation for the rest of the
  // session: whatever the operator is filling in outranks the agent.
  ["input", "change"].forEach(function (evt) {
    document.addEventListener(evt, function (e) {
      var t = e.target;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) userTyped = true;
    }, true);
  });

  function following() {
    try { return localStorage.getItem(FOLLOW_KEY) !== "off"; } catch { return true; }
  }
  function setFollowing(on) {
    try { localStorage.setItem(FOLLOW_KEY, on ? "on" : "off"); } catch { /* private mode */ }
  }
  function onEditorFor(id) {
    return EDITOR_URL && window.location.pathname === EDITOR_URL.replace("__ID__", id);
  }
  function editorHref(id) { return EDITOR_URL.replace("__ID__", id); }

  // ---- toast ----------------------------------------------------------
  function show() {
    root.hidden = false;
    // Force a frame so the entry transition runs on first raise.
    requestAnimationFrame(function () { root.classList.add("is-up"); });
    clearInterval(stateTimer);
    stateTimer = setInterval(tick, 1000);
  }
  function hide() {
    root.classList.remove("is-up");
    countdown = 0;
    clearInterval(stateTimer);
    stateTimer = null;
    setTimeout(function () { if (!root.classList.contains("is-up")) root.hidden = true; }, 220);
  }

  // One second of wall time. The toast decides its own state from how long the
  // surface has been quiet rather than being told: a step arriving is the only
  // event there is, and the two things that happen in its absence (the agent
  // thinking, the build being over) are both just elapsed time.
  function tick() {
    var quiet = Date.now() - lastStepAt;
    if (quiet >= HIDE_MS) { cancelCountdown(); hide(); return; }
    if (countdown > 0) {
      countdown -= 1;
      if (countdown <= 0) { go(); return; }
    }
    render();
  }

  function thinkingWord() {
    if (!word || Date.now() - wordAt > WORD_MS) {
      var pick = THINKING[Math.floor(Math.random() * THINKING.length)];
      if (pick === word) pick = THINKING[(THINKING.indexOf(pick) + 1) % THINKING.length];
      word = pick;
      wordAt = Date.now();
    }
    return word;
  }

  function fmtSecs(ms) { return (ms / 1000).toFixed(1) + "s"; }

  // What the agent touched, as the line's object.
  function objectOf(step) {
    return [step.target, step.detail].filter(Boolean).map(esc).join(" · ");
  }

  // The tape: the last few steps, newest last, repeats folded. An agent
  // streaming a code element makes one call per chunk, and eight identical
  // lines would push everything else off a three-line window.
  function record(step) {
    var last = tape[tape.length - 1];
    if (last && last.endpoint === step.endpoint && last.err === (step.status === "error")) {
      last.n += 1;
      last.obj = objectOf(step);
      return;
    }
    tape.push({
      endpoint: step.endpoint,
      label: esc(step.label || step.endpoint || "Working"),
      verb: esc(step.verb || step.label || "Working"),
      obj: objectOf(step),
      err: step.status === "error",
      n: 1,
    });
    while (tape.length > TAPE) tape.shift();
  }

  function lineHtml(entry, live) {
    var text = (live ? entry.verb : entry.label) + (entry.n > 1 ? " ×" + entry.n : "");
    return '<div class="af-l' + (entry.err ? " is-err" : "") + (live ? " is-now" : "") + '">' +
      '<span class="af-m">' + (entry.err ? "✗" : live ? "›" : "✓") + "</span>" +
      '<span class="af-lt">' + text + "</span>" +
      (entry.obj ? '<span class="af-lo">' + entry.obj + "</span>" : "") +
      "</div>";
  }

  function render() {
    var canOpen = !!EDITOR_URL && !!pageId;
    var quiet = Date.now() - lastStepAt;
    var thinking = quiet >= THINK_MS;
    // Thinking takes the live line's place rather than adding a fourth: the
    // tape is a window, not a list.
    var visible = thinking ? tape.slice(Math.max(0, tape.length - (TAPE - 1))) : tape;
    var lines = "";
    visible.forEach(function (entry, i) {
      lines += lineHtml(entry, !thinking && i === visible.length - 1);
    });
    if (thinking) {
      lines +=
        '<div class="af-l is-think"><span class="af-m">…</span>' +
        '<span class="af-lt">' + esc(thinkingWord()) + "</span>" +
        '<span class="af-lo">no calls for ' + fmtSecs(quiet) + "</span></div>";
    }
    if (!lines) lines = '<div class="af-l is-now"><span class="af-m">›</span>' +
      '<span class="af-lt">Starting up</span></div>';

    root.innerHTML =
      '<div class="af-card">' +
        '<div class="af-head">' +
          '<span class="af-pulse' + (thinking ? " is-slow" : "") + '" aria-hidden="true"></span>' +
          // Named after the dashboard as soon as one is known. Until then the
          // agent may not be building anything yet (it reads before it writes),
          // so the header doesn't claim it is.
          '<span class="af-name">' + esc(pageName || "Agent at work") + "</span>" +
          '<span class="af-count">' + steps + " step" + (steps === 1 ? "" : "s") +
            (startedAt ? " · " + fmtSecs(Date.now() - startedAt) : "") + "</span>" +
          '<button type="button" class="af-x" data-dismiss aria-label="Dismiss">&times;</button>' +
        "</div>" +
        '<div class="af-tape">' + lines + "</div>" +
        '<div class="af-acts">' +
          (canOpen
            ? '<button type="button" class="af-btn accent" data-open>' +
                (countdown > 0 ? "Opening in " + countdown + "…" : "Open editor") +
              "</button>"
            : "") +
          (countdown > 0
            ? '<button type="button" class="af-btn" data-stay>Stay here</button>'
            : '<button type="button" class="af-btn" data-dismiss>Dismiss</button>') +
          '<label class="af-follow"><input type="checkbox" ' + (following() ? "checked" : "") +
            " data-follow> follow</label>" +
        "</div>" +
      "</div>";

    var open = root.querySelector("[data-open]");
    if (open) open.addEventListener("click", function () { go(); });
    root.querySelectorAll("[data-dismiss]").forEach(function (b) {
      b.addEventListener("click", function () { cancelCountdown(); hide(); });
    });
    var stay = root.querySelector("[data-stay]");
    if (stay) stay.addEventListener("click", function () { cancelCountdown(); render(); });
    var f = root.querySelector("[data-follow]");
    if (f) {
      f.addEventListener("change", function () {
        setFollowing(this.checked);
        if (!this.checked) cancelCountdown();
        render();
      });
    }
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ---- navigation -----------------------------------------------------
  function go() {
    if (!EDITOR_URL || !pageId) return;
    navigated = true;
    window.location.href = editorHref(pageId);
  }
  function cancelCountdown() {
    countdown = 0;
  }
  // The countdown rides the toast's own one-second tick rather than keeping a
  // timer of its own, so the number on the button and the clock in the header
  // can never drift apart.
  function startCountdown() {
    if (countdown > 0 || navigated) return;
    if (!following() || !EDITOR_URL || !pageId) return;
    if (userTyped || onEditorFor(pageId)) return;
    // A modal / drawer open on this page means the operator is mid-task.
    if (document.querySelector("dialog[open], .drawer.open, .ed-modal:not([hidden])")) return;
    countdown = COUNTDOWN_S;
    render();
  }

  // ---- poll -----------------------------------------------------------
  function schedule(idleS) {
    clearTimeout(pollTimer);
    if (document.visibilityState === "hidden") return; // resumed by the listener below
    pollTimer = setTimeout(poll, idleS < WARM_IDLE_S ? POLL_WARM_MS : POLL_COLD_MS);
  }

  function poll() {
    fetch(ACTIVITY_URL + "?since=" + seq, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        seq = data.seq || seq;
        var fresh = data.steps || [];
        // The first poll of a tab returns whatever the bus already holds. That
        // is history: a page loaded a minute after a build finished must not
        // raise a toast, let alone start a countdown. Seed and wait for what
        // happens next, unless the agent is demonstrably still working.
        if (!primed) {
          primed = true;
          if (!fresh.length || (data.idle_s != null && data.idle_s > WARM_IDLE_S)) {
            if (fresh.length) run = data.run;
            schedule(data.idle_s == null ? 999 : data.idle_s);
            return;
          }
        }
        if (fresh.length) {
          if (data.run !== run) { // a new burst of agent work
            run = data.run;
            steps = 0;
            tape = [];
            startedAt = Date.now();
            pageId = null;
            pageName = "";
            navigated = false;
            cancelCountdown();
          }
          steps += fresh.length;
          lastStepAt = Date.now();
          // Joining a build already in progress: backdate the clock by the span
          // the batch covers, from the server's own timestamps, so the header
          // doesn't report a two-minute build as having just started.
          if (!startedAt || fresh.length > 1) {
            var span = (fresh[fresh.length - 1].ts - fresh[0].ts) * 1000;
            if (span > 0 && Date.now() - startedAt < span) startedAt = Date.now() - span;
          }
          // Name the run after the canvas it touches, whenever one shows up.
          // The server only sends a page_id it can actually open, so this is
          // never an id that is about to 404 (a page the agent has not created
          // yet, or has just deleted).
          fresh.forEach(function (s) {
            record(s);
            if (s.page_id) { pageId = s.page_id; pageName = s.page_name || pageName; }
          });
          show();
          render();
          startCountdown();
        }
        schedule(data.idle_s == null ? 999 : data.idle_s);
      })
      .catch(function () {
        // 404 (experiment switched off) or a blip: back off, don't spin.
        schedule(999);
      });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") poll();
    else clearTimeout(pollTimer);
  });
  poll();
})();
