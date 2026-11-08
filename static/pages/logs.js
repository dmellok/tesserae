// Settings → Logs. Streams new server log lines over SSE (/logs/stream),
// falling back to polling /logs/lines when EventSource is missing or the
// stream drops. The watch level comes from the URL (?level=) and is the
// server-side filter for both. Text search only hides rows on the page.

(function () {
  const list = document.querySelector("[data-log-list]");
  if (!list) return;
  const view = document.querySelector("[data-log-view]");
  const empty = document.querySelector("[data-log-empty]");
  const live = document.querySelector("[data-live-indicator]");
  const search = document.querySelector("[data-log-search]");
  const follow = document.querySelector("[data-log-follow]");
  const count = document.querySelector("[data-log-count]");
  const jump = document.querySelector("[data-log-jump]");
  const jumpLabel = document.querySelector("[data-log-jump-label]");
  const download = document.querySelector("[data-log-download]");
  const prefix = window.TESSERAE_URL_PREFIX || "";
  const watch = list.dataset.watch || "info";
  const MAX_ROWS = 2000;
  let lastSeq = Number(list.dataset.lastSeq || 0);
  let unseen = 0;

  function setLive(state) {
    if (!live) return;
    live.dataset.state = state;
    const label =
      state === "connected"
        ? "LIVE"
        : state === "polling"
          ? "Polling"
          : state === "connecting"
            ? "Connecting…"
            : "Offline";
    live.innerHTML =
      '<span class="dx-listening-dot"></span><span class="dx-listening-label">' + label + "</span>";
  }

  // Time of day on each line, in the app's clock style; the full date and
  // time is the tooltip. The ring rarely spans more than a day.
  function fmtTime(ts) {
    const c = window.tesseraeComponents;
    if (c && c.clockTime) return c.clockTime(Number(ts), { seconds: true });
    return new Date(Number(ts) * 1000).toLocaleTimeString();
  }
  function stamp(t, ts) {
    t.textContent = fmtTime(ts);
    t.title = new Date(Number(ts) * 1000).toLocaleString();
  }

  function atBottom() {
    return view.scrollHeight - view.scrollTop - view.clientHeight < 24;
  }

  function scrollToEnd() {
    view.scrollTop = view.scrollHeight;
    unseen = 0;
    jump.hidden = true;
  }

  function matches(row) {
    const q = (search.value || "").trim().toLowerCase();
    return !q || (row.dataset.text || "").includes(q);
  }

  function refreshCount() {
    const rows = list.children;
    let shown = 0;
    for (const row of rows) if (!row.hidden) shown++;
    const q = (search.value || "").trim();
    count.textContent = q
      ? `${shown} of ${rows.length} lines`
      : `${rows.length} line${rows.length === 1 ? "" : "s"}`;
    empty.hidden = rows.length > 0;
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function buildRow(line) {
    const li = el("li", `lg-line lg-line--${line.level}`);
    li.dataset.seq = line.seq;
    li.dataset.text = `${line.logger} ${line.message}`.toLowerCase();
    const t = el("time", "lg-time");
    t.dataset.ts = line.ts;
    stamp(t, line.ts);
    li.append(
      t,
      el("span", "lg-level", line.level.toUpperCase()),
      el("span", "lg-logger", line.logger),
      el("span", "lg-msg", line.message)
    );
    if (line.exc) {
      const d = el("details", "lg-exc");
      d.append(el("summary", null, "Traceback"), el("pre", null, line.exc));
      li.append(d);
    }
    li.hidden = !matches(li);
    return li;
  }

  function addLines(lines) {
    if (!lines.length) return;
    const stick = follow.checked && atBottom();
    for (const line of lines) {
      if (line.seq <= lastSeq) continue;
      lastSeq = line.seq;
      list.append(buildRow(line));
      unseen++;
    }
    while (list.children.length > MAX_ROWS) list.firstElementChild.remove();
    refreshCount();
    if (stick) {
      scrollToEnd();
    } else if (unseen) {
      jumpLabel.textContent = `${unseen} new line${unseen === 1 ? "" : "s"}`;
      jump.hidden = false;
    }
  }

  // --- transport ---------------------------------------------------------

  let pollTimer = null;
  function poll() {
    fetch(`${prefix}/logs/lines?level=${encodeURIComponent(watch)}&after=${lastSeq}`, {
      credentials: "same-origin",
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((body) => {
        setLive("polling");
        addLines(body.lines || []);
      })
      .catch(() => setLive("offline"))
      .finally(() => {
        pollTimer = setTimeout(poll, 3000);
      });
  }

  function connect() {
    if (typeof EventSource === "undefined") {
      poll();
      return;
    }
    setLive("connecting");
    const es = new EventSource(
      `${prefix}/logs/stream?level=${encodeURIComponent(watch)}&after=${lastSeq}`
    );
    let opened = false;
    es.onopen = () => {
      opened = true;
      setLive("connected");
    };
    es.addEventListener("line", (ev) => {
      try {
        addLines([JSON.parse(ev.data)]);
      } catch {
        /* a malformed frame is skipped */
      }
    });
    es.onerror = () => {
      // Never opened (a proxy that buffers or blocks SSE): poll instead.
      if (!opened) {
        es.close();
        if (!pollTimer) poll();
        return;
      }
      setLive("connecting"); // EventSource retries by itself
    };
  }

  // --- controls ----------------------------------------------------------

  search.addEventListener("input", () => {
    for (const row of list.children) row.hidden = !matches(row);
    refreshCount();
    const q = search.value.trim();
    const url = new URL(download.href, location.href);
    if (q) url.searchParams.set("q", q);
    else url.searchParams.delete("q");
    download.href = url.pathname + url.search;
  });

  view.addEventListener("scroll", () => {
    if (atBottom()) {
      unseen = 0;
      jump.hidden = true;
    }
  });
  jump.addEventListener("click", scrollToEnd);
  follow.addEventListener("change", () => {
    if (follow.checked) scrollToEnd();
  });

  document.querySelector("[data-log-clear]").addEventListener("click", () => {
    if (!confirm("Clear the kept log lines? The server's console output is not affected.")) return;
    fetch(`${prefix}/logs/clear`, { method: "POST", credentials: "same-origin" }).then((r) => {
      if (!r.ok) return;
      list.replaceChildren();
      unseen = 0;
      jump.hidden = true;
      refreshCount();
    });
  });

  function setRecordLevel(level) {
    return fetch(`${prefix}/logs/record_level`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level }),
    }).then((r) => {
      if (r.ok) location.reload();
    });
  }

  const form = document.querySelector("[data-record-form]");
  if (form) {
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      setRecordLevel(form.querySelector("[data-record-select]").value);
    });
  }
  const match = document.querySelector("[data-record-match]");
  if (match) match.addEventListener("click", () => setRecordLevel(match.dataset.recordMatch));

  for (const t of list.querySelectorAll("time[data-ts]")) stamp(t, t.dataset.ts);
  refreshCount();
  scrollToEnd();
  connect();
})();
