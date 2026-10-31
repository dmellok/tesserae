// Lineups page: every request the page makes, in one place.
//
// The endpoints belong to app/deck_routes.py. Per-lineup URLs come from the
// view-model (actions.window_url, days_url, push_step_url); the page-wide
// ones from data attributes on [data-lu] (data-view-url, data-edits-url).
// The fallbacks below build the same paths under the HA ingress prefix.
// Requests are same-origin with the session cookie; the server's provenance
// check (app/csrf.py) needs no token.
(function () {
  "use strict";
  const PREFIX = window.TESSERAE_URL_PREFIX || "";
  const root = () => document.querySelector("[data-lu]");
  const attr = (name, fallback) => (root() && root().getAttribute(name)) || PREFIX + fallback;

  async function send(method, url, body) {
    const opts = {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    let data = null;
    try {
      data = await resp.json();
    } catch {
      data = null;
    }
    if (!resp.ok || (data && data.ok === false)) {
      const msg = (data && (data.error || data.message)) || `The server said ${resp.status}.`;
      const e = new Error(msg);
      e.status = resp.status;
      e.data = data;
      throw e;
    }
    return data || {};
  }

  window.TesseraeLineupsAPI = {
    /** GET /decks/view.json?day=<n>: the whole view-model, same shape as the page's. */
    view(day) {
      const url = attr("data-view-url", "/decks/view.json");
      return send(
        "GET",
        day === undefined || day === null ? url : `${url}?day=${encodeURIComponent(day)}`
      );
    },
    /** POST /decks/edits {edits: [{id, start, end, days}]}: all or nothing (409 on a clash). */
    saveEdits(edits) {
      return send("POST", attr("data-edits-url", "/decks/edits"), { edits });
    },
    /** POST /decks/<id>/window {start, end}: one lineup's time window. */
    setWindow(lineup, start, end) {
      const url =
        (lineup.actions && lineup.actions.window_url) ||
        `${PREFIX}/decks/${encodeURIComponent(lineup.id)}/window`;
      return send("POST", url, { start, end });
    },
    /** POST /decks/<id>/days {days: [0..6]}: the weekdays it runs. */
    setDays(lineup, days) {
      const url =
        (lineup.actions && lineup.actions.days_url) ||
        `${PREFIX}/decks/${encodeURIComponent(lineup.id)}/days`;
      return send("POST", url, { days });
    },
    /** POST /decks/<id>/bind-pages {page_ids, display_id, also}: bind off-display steps to
        the display they sit under, keeping their other displays (the click is the consent). */
    bindPages(lineup, pageIds, displayId) {
      const url =
        (lineup.actions && lineup.actions.bind_url) ||
        `${PREFIX}/decks/${encodeURIComponent(lineup.id)}/bind-pages`;
      return send("POST", url, { page_ids: pageIds, display_id: displayId, also: true });
    },
    /** POST /decks/<id>/push-step {index, composition_digest, display_id?}: push one step now.
        Replies {lands: "lands now" | "lands ~18:18", message, fresh}. */
    pushStep(lineup, step, displayId) {
      const url =
        (lineup.actions && lineup.actions.push_step_url) ||
        `${PREFIX}/decks/${encodeURIComponent(lineup.id)}/push-step`;
      const body = { index: step.index, composition_digest: step.composition_digest || null };
      if (displayId) body.display_id = displayId;
      return send("POST", url, body);
    },
  };
})();
