// Background-job tray for the admin shell.
//
// Anything that works in the background reports to app/jobs.py, and this draws
// one card per job at the bottom right of every admin page: a title, a detail
// line, a progress bar, and a Stop button when the job can be stopped. The jobs
// live in the server, so a page load simply asks again; the last answer is kept
// in sessionStorage and drawn at once, so moving between pages does not blink
// the tray out and back in.
//
// A poll, not a stream, for the reason agent-follow.js gives: every open tab
// would pin a server thread. It is quick while jobs are running, slow when
// there are none, and stops while the tab is hidden.
//
// Pages can call window.TesseraeJobs.poke() right after starting something so
// the card appears without waiting for the slow poll.
(function () {
  var root = document.getElementById("job-tray");
  if (!root) return;

  var URL_ = root.dataset.url;
  var STOP_URL = root.dataset.stopUrl;
  var ACT_URL = root.dataset.actUrl;
  var KEEP_KEY = "tesserae-jobs";
  var FOLD_KEY = "tesserae-jobs-folded";
  var WARM_MS = 1200;
  var COLD_MS = 8000;
  var WARM_FOR_MS = 30000;
  var LEAVE_MS = 260;

  var cards = {}; // job id -> { el, parts, started }
  var dismissed = {}; // job id -> started, hidden by the person
  var last = [];
  var timer = null;
  var warmUntil = 0;
  var folded = false;
  var busy = false;

  try {
    folded = localStorage.getItem(FOLD_KEY) === "1";
  } catch {
    /* private mode */
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name) {
    return el("i", "ph ph-" + name);
  }

  function span(seconds) {
    if (seconds < 60) return seconds + "s";
    var m = Math.floor(seconds / 60);
    if (m < 60) return m + "m " + (seconds % 60) + "s";
    return Math.floor(m / 60) + "h " + (m % 60) + "m";
  }

  // ---- chrome: the fold pill ------------------------------------------
  var list = el("div", "jt-list");
  var pill = el("button", "jt-pill");
  pill.type = "button";
  pill.title = "Show background jobs";
  var ring = el("span", "jt-ring-dot");
  var pillText = el("span", "jt-pill-text");
  pill.append(ring, pillText);
  var foldBtn = el("button", "jt-fold");
  foldBtn.type = "button";
  foldBtn.title = "Fold the job list away";
  foldBtn.setAttribute("aria-label", "Fold the job list away");
  foldBtn.appendChild(icon("caret-down"));
  root.append(list, foldBtn, pill);

  function setFolded(on) {
    folded = on;
    try {
      localStorage.setItem(FOLD_KEY, on ? "1" : "0");
    } catch {
      /* private mode */
    }
    root.classList.toggle("is-folded", on);
  }
  pill.addEventListener("click", function () {
    setFolded(false);
  });
  foldBtn.addEventListener("click", function () {
    setFolded(true);
  });

  // ---- one card --------------------------------------------------------
  function build(job) {
    var card = el("div", "jt-card");
    var head = el("div", "jt-head");
    var ico = el("span", "jt-ico");
    var text = el("div", "jt-text");
    var title = el("div", "jt-title");
    var detail = el("div", "jt-detail");
    text.append(title, detail);
    var pct = el("span", "jt-pct");
    var stop = el("button", "jt-stop", "Stop");
    stop.type = "button";
    // the job's own button, when it has one: its label comes with the job
    var act = el("button", "jt-stop jt-act", "");
    act.type = "button";
    act.hidden = true;
    var x = el("button", "jt-x");
    x.type = "button";
    x.title = "Hide";
    x.setAttribute("aria-label", "Hide");
    x.textContent = "×";
    head.append(ico, text, pct, act, stop, x);
    var bar = el("div", "jt-bar");
    var fill = el("i", "jt-fill");
    bar.appendChild(fill);
    card.append(head, bar);
    var entry = {
      el: card,
      ico: ico,
      title: title,
      detail: detail,
      pct: pct,
      stop: stop,
      act: act,
      fill: fill,
      bar: bar,
      started: job.started,
      job: job,
    };
    act.addEventListener("click", function () {
      act.disabled = true;
      fetch(ACT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: entry.job.id }),
      }).then(
        function () {
          poke();
        },
        function () {
          act.disabled = false;
        }
      );
    });
    stop.addEventListener("click", function () {
      stop.disabled = true;
      stop.textContent = "Stopping";
      fetch(STOP_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: entry.job.id }),
      }).then(
        function () {
          poke();
        },
        function () {
          stop.disabled = false;
          stop.textContent = "Stop";
        }
      );
    });
    x.addEventListener("click", function () {
      dismissed[entry.job.id] = entry.started;
      leave(entry.job.id);
    });
    return entry;
  }

  function paint(entry, job) {
    entry.job = job;
    var running = job.state === "running";
    entry.el.dataset.state = job.state;
    var indeterminate = running && job.progress == null;
    entry.el.classList.toggle("is-indeterminate", indeterminate);
    var name = job.label ? job.label + " · " : "";
    // The title is a link when the job names a page to go and look at.
    if (job.href) {
      if (!entry.link) {
        entry.link = el("a");
        entry.title.replaceChildren(entry.link);
      }
      // A root-relative page needs the ingress prefix under Home Assistant.
      var rooted = job.href.charAt(0) === "/" && job.href.charAt(1) !== "/";
      entry.link.href = rooted ? (window.TESSERAE_URL_PREFIX || "") + job.href : job.href;
      entry.link.textContent = job.title;
    } else {
      entry.title.textContent = job.title;
    }
    var bits = [];
    if (job.detail) bits.push(job.detail);
    if (running) bits.push(span(Math.max(0, Math.round(Date.now() / 1000 - job.started))));
    entry.detail.textContent = name + bits.join(" · ");
    entry.detail.hidden = !(name || bits.length);
    var p = job.state === "done" ? 1 : job.progress;
    entry.fill.style.width = indeterminate ? "" : Math.round((p == null ? 0 : p) * 100) + "%";
    entry.pct.textContent = !indeterminate && running ? Math.round((p || 0) * 100) + "%" : "";
    entry.stop.hidden = !job.stoppable;
    entry.act.hidden = !job.action;
    if (job.action && entry.act.textContent !== job.action) {
      entry.act.textContent = job.action;
      entry.act.disabled = false;
    }
    if (job.stoppable && entry.stop.textContent !== "Stop" && !entry.stop.disabled)
      entry.stop.textContent = "Stop";
    var glyph =
      job.state === "done"
        ? "check"
        : job.state === "failed"
          ? "warning"
          : job.state === "stopped"
            ? "stop"
            : "";
    if (entry.glyph !== glyph) {
      entry.glyph = glyph;
      entry.ico.replaceChildren(glyph ? icon(glyph) : el("span", "jt-spin-dot"));
    }
  }

  function leave(id) {
    var entry = cards[id];
    if (!entry) return;
    delete cards[id];
    entry.el.classList.add("is-leaving");
    setTimeout(function () {
      entry.el.remove();
      sync();
    }, LEAVE_MS);
  }

  // ---- the whole tray --------------------------------------------------
  function sync() {
    var shown = Object.keys(cards).length;
    root.hidden = shown === 0;
    root.classList.toggle("is-folded", folded && shown > 0);
    root.classList.toggle("has-many", shown > 1);
    if (!shown) return;
    var running = 0;
    var sum = 0;
    var known = 0;
    Object.keys(cards).forEach(function (id) {
      var j = cards[id].job;
      if (j.state !== "running") return;
      running += 1;
      if (j.progress != null) {
        sum += j.progress;
        known += 1;
      }
    });
    pillText.textContent = running
      ? running +
        (running === 1 ? " job running" : " jobs running") +
        (known === running ? " · " + Math.round((sum / known) * 100) + "%" : "")
      : shown + (shown === 1 ? " job finished" : " jobs finished");
    ring.classList.toggle("is-idle", !running);
    avoidAgent();
  }

  // The agent toast sits in the same corner; stack above it when it is up.
  function avoidAgent() {
    var af = document.getElementById("agent-follow");
    if (af && af.classList.contains("is-up") && !af.hidden) {
      root.style.bottom = window.innerHeight - af.getBoundingClientRect().top + 10 + "px";
    } else {
      root.style.bottom = "";
    }
  }

  function apply(jobs, fresh) {
    last = jobs;
    var seen = {};
    jobs.forEach(function (job) {
      if (dismissed[job.id] === job.started) return;
      seen[job.id] = true;
      var entry = cards[job.id];
      if (entry && entry.started !== job.started) {
        entry.el.remove();
        delete cards[job.id];
        entry = null;
      }
      if (!entry) {
        entry = build(job);
        cards[job.id] = entry;
        // Cards drawn from the kept copy on a new page do not replay the entrance.
        if (!fresh) entry.el.classList.add("no-anim");
        list.appendChild(entry.el);
        if (fresh)
          requestAnimationFrame(function () {
            entry.el.classList.add("is-in");
          });
      }
      paint(entry, job);
    });
    Object.keys(cards).forEach(function (id) {
      if (!seen[id]) leave(id);
    });
    sync();
    var anyRunning = jobs.some(function (j) {
      return j.state === "running";
    });
    if (anyRunning) warmUntil = Math.max(warmUntil, Date.now() + 5000);
    try {
      sessionStorage.setItem(KEEP_KEY, JSON.stringify({ at: Date.now(), jobs: jobs }));
    } catch {
      /* private mode */
    }
  }

  // Elapsed times tick between polls so the card does not look frozen.
  setInterval(function () {
    if (document.hidden) return;
    avoidAgent();
    Object.keys(cards).forEach(function (id) {
      if (cards[id].job.state === "running") paint(cards[id], cards[id].job);
    });
  }, 1000);

  // ---- polling ---------------------------------------------------------
  function poll() {
    clearTimeout(timer);
    if (document.hidden || busy) return;
    busy = true;
    fetch(URL_, { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) {
        return r.ok ? r.json() : { jobs: last };
      })
      .then(function (data) {
        apply(data.jobs || [], true);
      })
      .catch(function () {
        /* a restart in progress: keep what is drawn */
      })
      .then(function () {
        busy = false;
        var warm = Date.now() < warmUntil || Object.keys(cards).length > 0;
        timer = setTimeout(poll, warm ? WARM_MS : COLD_MS);
      });
  }
  function poke() {
    warmUntil = Date.now() + WARM_FOR_MS;
    busy = false;
    poll();
  }
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();
  });
  window.TesseraeJobs = { poke: poke };

  // Posting a form usually starts a job and reloads; the next page's first poll finds it.
  var kept = null;
  try {
    kept = JSON.parse(sessionStorage.getItem(KEEP_KEY) || "null");
  } catch {
    kept = null;
  }
  if (kept && Date.now() - kept.at < 20000 && kept.jobs && kept.jobs.length)
    apply(kept.jobs, false);
  poll();
})();
