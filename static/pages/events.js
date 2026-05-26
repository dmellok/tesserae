// Live events feed for /events. Subscribes via SSE; mirrors the server-
// rendered row markup so new events look identical to existing ones.
//
// If EventSource isn't available (very old browser) the page degrades
// gracefully to the static list — refresh to see new events.

(function () {
  if (typeof EventSource === "undefined") return;
  const list = document.querySelector(".events");
  const live = document.querySelector("[data-live-indicator]");
  // The type filter is in the URL query string; the SSE endpoint accepts
  // the same param so we get matched filtering server-side.
  const params = new URLSearchParams(location.search);
  const typeFilter = params.get("type") || "all";
  const streamUrl =
    typeFilter && typeFilter !== "all"
      ? `/events/stream?type=${encodeURIComponent(typeFilter)}`
      : "/events/stream";

  function setLive(state) {
    if (!live) return;
    live.dataset.state = state;
    const label =
      state === "connected"
        ? "live"
        : state === "connecting"
          ? "connecting…"
          : "offline";
    live.innerHTML =
      '<i class="ph-fill ph-circle" aria-hidden="true"></i><span>' +
      label +
      "</span>";
  }

  function escapeHtml(s) {
    return String(s).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }[c]),
    );
  }

  function statusPill(status) {
    const cls =
      status === "sent" || status === "ok"
        ? "is-ok"
        : status === "busy" || status === "denied"
          ? "is-warn"
          : "is-danger";
    return `<span class="pill ${cls}">${escapeHtml(status)}</span>`;
  }

  function rowHtml(ev) {
    const thumb =
      ev.type === "push" && ev.digest
        ? `<a class="event-thumb" href="/renders/${encodeURIComponent(
            ev.digest,
          )}.png" target="_blank"><img src="/renders/${encodeURIComponent(
            ev.digest,
          )}.png" alt=""></a>`
        : "";
    const extraBlock =
      ev.extra && Object.keys(ev.extra).length > 0 && ev.type !== "push"
        ? `<pre class="event-extra">${escapeHtml(
            JSON.stringify(ev.extra),
          )}</pre>`
        : "";
    const errorBit = ev.error
      ? `<span class="muted">·</span><span class="event-err">${escapeHtml(
          ev.error,
        )}</span>`
      : "";
    const durationBit = ev.duration_s
      ? `<span class="muted">·</span><span>${ev.duration_s.toFixed(3)} s</span>`
      : "";
    const digestBit = ev.digest
      ? `<span class="muted">·</span><code class="digest">${escapeHtml(
          ev.digest,
        )}</code>`
      : "";
    return `
      <div class="event-type">${escapeHtml(ev.type)}</div>
      <div class="event-body">
        <div class="event-line">
          ${statusPill(ev.status)}
          <span class="event-source">${escapeHtml(ev.source)}</span>
          <code class="event-target">${escapeHtml(ev.target)}</code>
        </div>
        <div class="event-sub">
          <time datetime="${ev.timestamp}">${Math.round(ev.timestamp)}</time>
          ${durationBit}${digestBit}${errorBit}
        </div>
        ${extraBlock}
      </div>
      ${thumb}
    `;
  }

  function ensureList() {
    let l = document.querySelector(".events");
    if (l) return l;
    // The server-rendered empty state has a <p class="lede">No events…</p>
    // when there are no rows yet; remove it and create the <ul>.
    const empty = document.querySelector("p.lede:last-of-type");
    if (empty && /no events of this kind yet/i.test(empty.textContent)) {
      empty.remove();
    }
    l = document.createElement("ul");
    l.className = "events";
    // Insert after the tabs nav.
    const tabs = document.querySelector("nav.tabs");
    if (tabs && tabs.parentNode) {
      tabs.parentNode.insertBefore(l, tabs.nextSibling);
    } else {
      document.querySelector(".page").appendChild(l);
    }
    return l;
  }

  function appendRow(ev) {
    const root = ensureList();
    // Skip if this id is already shown (server-rendered or earlier SSE).
    if (root.querySelector(`[data-event-id="${ev.id}"]`)) return;
    const li = document.createElement("li");
    li.className = "event-row event--" + ev.type;
    li.dataset.eventId = String(ev.id);
    li.innerHTML = rowHtml(ev);
    root.insertBefore(li, root.firstChild);
    // Keep the DOM bounded so a flood doesn't blow up the page.
    while (root.children.length > 200) root.removeChild(root.lastChild);
  }

  let backoff = 1000;
  function connect() {
    setLive("connecting");
    const es = new EventSource(streamUrl);
    es.addEventListener("open", () => {
      setLive("connected");
      backoff = 1000;
    });
    es.addEventListener("log", (msg) => {
      try {
        appendRow(JSON.parse(msg.data));
      } catch (err) {
        console.error("[events] bad SSE payload", err);
      }
    });
    es.addEventListener("error", () => {
      setLive("offline");
      es.close();
      // EventSource auto-reconnects, but on hard errors (server down) it
      // can spin; back off and reconnect manually.
      setTimeout(connect, Math.min(backoff, 15000));
      backoff = Math.min(backoff * 2, 15000);
    });
  }

  // Tag existing server-rendered rows so we don't duplicate them when SSE
  // delivers an event the server happened to render in this same response.
  document.querySelectorAll(".event-row").forEach((li, idx) => {
    if (!li.dataset.eventId) li.dataset.eventId = `srv-${idx}`;
  });

  connect();
})();
