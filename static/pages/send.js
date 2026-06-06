// Live History updates for /send. The Saved-page / Resend / URL / etc
// POST handlers now background the push and redirect immediately, so
// the new history row arrives a few seconds *after* the page renders.
// Subscribe to the same SSE stream the Events tab uses, filtered to
// push events, and swap the History list when one lands.
//
// We do a full partial re-fetch rather than building the row client-
// side: the row markup includes a thumbnail, friendly page name,
// per-renderer status pills, and a duration, duplicating all of that
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
        // Network blip is fine, EventSource will deliver the next
        // event and we'll try again then.
      });
  }

  // Debounce, a one-off send fires a single push event but a fan-out
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

  const prefix = window.TESSERAE_URL_PREFIX || "";
  const es = new EventSource(`${prefix}/events/stream?type=push`);
  // The SSE endpoint names its event "log" (see app/events_routes.py:
  // ``event: log\ndata: …``). Listening for "event" or default
  // ``onmessage`` would silently miss every push.
  es.addEventListener("log", scheduleSwap);
})();

// Device-pick guard.
//
// Each tab's Send button is marked ``data-requires-device-pick``; each
// device-checklist is marked ``data-send-device-checklist``. We keep
// the button disabled until at least one checkbox in the matching form
// is ticked. The server-side ``_require_target_devices`` still catches
// a missed pick (and re-renders with state preserved), but this gives
// the user a clear "I forgot something" cue before they hit the
// button, no round-trip, no flash banner, just a disabled control.
(function () {
  function syncFormButtons(form) {
    const checklist = form.querySelector("[data-send-device-checklist]");
    const buttons = form.querySelectorAll("[data-requires-device-pick]");
    if (!buttons.length) return;
    // No checklist in the form (e.g. Saved tab, that route picks the
    // device through page bindings, not a checklist). Leave the
    // buttons as the server rendered them.
    if (!checklist) return;
    const hasPick = !!checklist.querySelector('input[type="checkbox"]:checked');
    buttons.forEach((btn) => {
      btn.disabled = !hasPick;
      btn.title = hasPick
        ? ""
        : "Pick at least one target device above before sending.";
    });
  }

  document.querySelectorAll("form").forEach((form) => {
    if (!form.querySelector("[data-send-device-checklist]")) return;
    syncFormButtons(form);
    form.addEventListener("change", (ev) => {
      if (ev.target && ev.target.matches('input[name="device_id"]')) {
        syncFormButtons(form);
      }
    });
  });
})();
