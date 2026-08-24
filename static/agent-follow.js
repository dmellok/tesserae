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
  // How long the toast lingers after the agent stops, and how long the
  // countdown runs before it navigates. The linger outlasts an agent's pause
  // between calls, so a build that stops to think doesn't flicker the toast
  // away and back.
  var LINGER_MS = 14000;
  var COUNTDOWN_S = 4;

  var seq = 0;
  var run = null;
  var pageId = null;
  var pageName = "";
  var steps = 0;
  var pollTimer = null;
  var lingerTimer = null;
  var countdownTimer = null;
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
  }
  function hide() {
    root.classList.remove("is-up");
    clearInterval(countdownTimer);
    countdownTimer = null;
    setTimeout(function () { if (!root.classList.contains("is-up")) root.hidden = true; }, 220);
  }
  function render(step) {
    var canOpen = !!EDITOR_URL && !!pageId;
    var what = pageName || "a dashboard";
    // Same phrasing the editor's rail uses: the verb sentence the server
    // supplies, then what it touched.
    var line = step
      ? esc(step.verb || step.label) +
        (step.target ? '<span class="af-dot">·</span>' + esc(step.target) : "") +
        (step.detail ? '<span class="af-dot">·</span>' + esc(step.detail) : "")
      : "starting up";
    root.innerHTML =
      '<div class="af-card' + (steps ? " is-live" : "") + '">' +
        '<div class="af-throb" aria-hidden="true"><i></i><i></i><i></i></div>' +
        '<div class="af-body">' +
          '<div class="af-head">Agent is building <b>' + esc(what) + "</b></div>" +
          '<div class="af-step">' + line + "</div>" +
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
        "</div>" +
        '<button type="button" class="af-x" data-dismiss aria-label="Dismiss">&times;</button>' +
      "</div>";

    var open = root.querySelector("[data-open]");
    if (open) open.addEventListener("click", function () { go(); });
    root.querySelectorAll("[data-dismiss]").forEach(function (b) {
      b.addEventListener("click", function () { cancelCountdown(); hide(); });
    });
    var stay = root.querySelector("[data-stay]");
    if (stay) stay.addEventListener("click", function () { cancelCountdown(); render(step); });
    var f = root.querySelector("[data-follow]");
    if (f) {
      f.addEventListener("change", function () {
        setFollowing(this.checked);
        if (!this.checked) cancelCountdown();
        render(step);
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
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
  function startCountdown(step) {
    if (countdownTimer || navigated) return;
    if (!following() || !EDITOR_URL || !pageId) return;
    if (userTyped || onEditorFor(pageId)) return;
    // A modal / drawer open on this page means the operator is mid-task.
    if (document.querySelector("dialog[open], .drawer.open, .ed-modal:not([hidden])")) return;
    countdown = COUNTDOWN_S;
    render(step);
    countdownTimer = setInterval(function () {
      countdown -= 1;
      if (countdown <= 0) { cancelCountdown(); go(); return; }
      render(step);
    }, 1000);
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
            pageId = null;
            pageName = "";
            navigated = false;
            cancelCountdown();
          }
          steps += fresh.length;
          var last = fresh[fresh.length - 1];
          // Name the run after the canvas it touches, whenever one shows up.
          fresh.forEach(function (s) {
            if (s.page_id) { pageId = s.page_id; pageName = s.page_name || pageName; }
          });
          show();
          render(last);
          startCountdown(last);
          clearTimeout(lingerTimer);
          lingerTimer = setTimeout(hide, LINGER_MS);
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
