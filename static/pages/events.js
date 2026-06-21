// Live events feed for /events. Subscribes via SSE; mirrors the v0.56
// server-rendered row markup (.dx-event-row + per-type swatch class
// + dx-event-summary / dx-event-body / dx-pill / dx-event-meta) so new
// events look identical to existing ones.
//
// v0.63.0: rewrote to target the new .dx-events-list / .dx-event-row
// vocabulary. Pre-v0.63 the script targeted the legacy `.events`
// selector (which the v0.56 template stopped emitting), causing it
// to silently spawn a phantom unstyled list at the bottom of the
// page on every load.
//
// If EventSource isn't available (very old browser) the page degrades
// gracefully to the static list, refresh to see new events.

(function () {
  if (typeof EventSource === "undefined") return;
  const live = document.querySelector("[data-live-indicator]");
  // The type filter is in the URL query string; the SSE endpoint accepts
  // the same param so we get matched filtering server-side.
  const params = new URLSearchParams(location.search);
  const typeFilter = params.get("type") || "all";
  const prefix = window.TESSERAE_URL_PREFIX || "";
  const streamUrl =
    typeFilter && typeFilter !== "all"
      ? `${prefix}/events/stream?type=${encodeURIComponent(typeFilter)}`
      : `${prefix}/events/stream`;

  // Type → Phosphor icon (keep in sync with TYPE_ICONS in events.html).
  const TYPE_ICONS = {
    push: "paper-plane-tilt",
    renderer: "image-square",
    render: "image-square",
    rotation: "arrows-clockwise",
    heartbeat: "heartbeat",
    scheduler: "clock-clockwise",
    schedule: "clock-clockwise",
    auth: "shield-check",
    plugin: "puzzle-piece",
    transport: "broadcast",
    telemetry: "broadcast",
    conditions: "funnel",
    device: "device-mobile",
  };

  function setLive(state) {
    if (!live) return;
    live.dataset.state = state;
    const label =
      state === "connected"
        ? "LIVE"
        : state === "connecting"
          ? "Connecting…"
          : "Offline";
    live.innerHTML =
      '<span class="dx-listening-dot"></span><span class="dx-listening-label">' +
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
        })[c],
    );
  }

  // Match the server-side fmt_time filter: "Jun  1 14:23:45" (local).
  const MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  function fmtTime(ts) {
    const d = new Date(Number(ts) * 1000);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${MONTHS[d.getMonth()]} ${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function statusPill(status) {
    const cls =
      status === "sent" || status === "ok"
        ? "is-ok"
        : status === "busy" || status === "denied"
          ? "is-warn"
          : "is-danger";
    return (
      '<span class="dx-pill ' +
      cls +
      '"><span class="dx-pill-dot"></span>' +
      escapeHtml(status) +
      "</span>"
    );
  }

  function rowHtml(ev) {
    // Build the same dx-event-row body the server emits. The expanded
    // panel is intentionally empty — the page-level click handler
    // (events.html bottom script) lazy-hydrates from the stashed JSON
    // on first open. For SSE-appended rows there's no stash, so the
    // expanded view just shows the empty-payload string until the
    // user refreshes the page and the server re-renders with extras.
    const icon = TYPE_ICONS[ev.type] || "circle";
    const durationBit = ev.duration_s
      ? '<span class="muted">·</span><span>' +
        ev.duration_s.toFixed(3) +
        " s</span>"
      : "";
    const digestBit = ev.digest
      ? '<span class="muted">·</span><code class="dx-event-digest">' +
        escapeHtml(ev.digest) +
        "</code>"
      : "";
    const errorBit = ev.error
      ? '<span class="muted">·</span><span class="dx-event-err">' +
        escapeHtml(ev.error) +
        "</span>"
      : "";
    return (
      '<button type="button" class="dx-event-summary" data-event-toggle aria-expanded="false">' +
      '  <span class="dx-event-icon"><i class="ph ph-' +
      icon +
      '" aria-hidden="true"></i></span>' +
      '  <div class="dx-event-body">' +
      '    <div class="dx-event-head">' +
      '      <span class="dx-pill dx-event-type">' +
      escapeHtml(ev.type) +
      "</span>" +
      statusPill(ev.status) +
      '      <span class="dx-event-source">' +
      escapeHtml(ev.source) +
      "</span>" +
      '      <code class="dx-event-target">' +
      escapeHtml(ev.target) +
      "</code>" +
      "    </div>" +
      '    <div class="dx-event-meta">' +
      '      <time datetime="' +
      escapeHtml(String(ev.timestamp)) +
      '">' +
      escapeHtml(fmtTime(ev.timestamp)) +
      "</time>" +
      durationBit +
      digestBit +
      errorBit +
      "    </div>" +
      "  </div>" +
      '  <i class="ph ph-caret-right dx-event-caret" aria-hidden="true"></i>' +
      "</button>" +
      '<div class="dx-event-expand" data-event-expand hidden>' +
      '  <div class="dx-event-expand-grid">' +
      '    <p class="dx-event-empty-payload">No payload recorded for this event.</p>' +
      "  </div>" +
      "</div>"
    );
  }

  function ensureList() {
    let l = document.querySelector(".dx-events-list");
    if (l) return l;
    // Empty state: server rendered the .dx-empty placeholder instead
    // of the list. Replace it with a fresh <ul> so live events have
    // a home.
    const empty = document.querySelector(".dx-event-empty, .dx-empty");
    if (empty) empty.remove();
    l = document.createElement("ul");
    l.className = "dx-discovered-list dx-events-list";
    const card = document.querySelector(".dx-section-card .dx-section-body");
    if (card) {
      card.appendChild(l);
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
    li.className = "dx-inset-row dx-event-row dx-event-row--" + ev.type;
    li.dataset.eventId = String(ev.id);
    li.innerHTML = rowHtml(ev);
    root.insertBefore(li, root.firstChild);
    // Bound the DOM so a flood doesn't blow up the page. Matches the
    // server-side default limit (100).
    while (root.children.length > 100) root.removeChild(root.lastChild);
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
      // EventSource auto-reconnects, but on hard errors (server down)
      // it can spin; back off and reconnect manually.
      setTimeout(connect, Math.min(backoff, 15000));
      backoff = Math.min(backoff * 2, 15000);
    });
  }

  // Tag existing server-rendered rows so we don't duplicate them when
  // SSE delivers an event the server happened to render in this same
  // response. v0.56+ rows already carry data-event-id; the loop is
  // defensive for any pre-v0.56 fallback markup.
  document.querySelectorAll(".dx-event-row").forEach((li, idx) => {
    if (!li.dataset.eventId) li.dataset.eventId = `srv-${idx}`;
  });

  connect();
})();
