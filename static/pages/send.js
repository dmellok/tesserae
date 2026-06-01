// Live History updates for /send. The Saved-page / Resend / URL / etc
// POST handlers now background the push and redirect immediately, so
// the new history row arrives a few seconds *after* the page renders.
// Subscribe to the same SSE stream the Events tab uses, filtered to
// push events, and swap the History list when one lands.
//
// We do a full partial re-fetch rather than building the row client-
// side: the row markup includes a thumbnail, friendly page name,
// per-renderer status pills, and a duration — duplicating all of that
// in JS would drift the moment the server-side template changes. One
// extra GET per push is cheap.

(function () {
  if (typeof EventSource === "undefined") return;

  function swapHistory() {
    // Re-fetch /send?tab=history and swap just the <ul.history> node.
    // The fetch hits the same admin auth as the page itself, so a
    // logged-out tab gracefully fails (the fetch redirects to the
    // login page, the DOMParser finds no .history, and we bail).
    fetch(window.location.pathname + "?tab=history", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then((r) => (r.ok ? r.text() : null))
      .then((html) => {
        if (!html) return;
        const doc = new DOMParser().parseFromString(html, "text/html");
        const fresh =
          doc.querySelector(".history") ||
          doc.querySelector("[data-history-empty]");
        const current =
          document.querySelector(".history") ||
          document.querySelector("[data-history-empty]");
        if (fresh && current) {
          current.replaceWith(fresh);
        }
      })
      .catch(() => {
        // Network blip is fine — EventSource will deliver the next
        // event and we'll try again then.
      });
  }

  // Debounce — a one-off send fires a single push event but a fan-out
  // to multiple devices emits one per target. Collapse rapid bursts
  // into one re-fetch instead of N.
  let pending = null;
  function scheduleSwap() {
    if (pending !== null) return;
    pending = setTimeout(() => {
      pending = null;
      swapHistory();
    }, 300);
  }

  const es = new EventSource("/events/stream?type=push");
  // The SSE endpoint names its event "log" (see app/events_routes.py:
  // ``event: log\ndata: …``). Listening for "event" or default
  // ``onmessage`` would silently miss every push.
  es.addEventListener("log", scheduleSwap);
})();
