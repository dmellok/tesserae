/* Asks before a schedule form saves a dashboard onto a display it is not
 * bound to (see bind_prompt.js). The server refuses such a save without
 * consent, so this only has to ask: it checks the picked dashboards against
 * the picked displays, shows the prompt, and submits again with the answer as
 * hidden `bind_pages` / `bind_also` fields. Cancelling saves nothing.
 *
 * Opt in with `data-bind-guard` (+ `data-bind-check-url`) on a form that has `page_id` (and optionally
 * `fallback_page_id`) selects and `device_ids` checkboxes. A form with no
 * displays ticked delivers to the dashboard's own displays, so nothing to ask.
 */
(function () {
  "use strict";

  const picked = (form) => ({
    page_ids: ["page_id", "fallback_page_id"]
      .map((n) => form.elements[n])
      .filter((f) => f && f.value)
      .map((f) => f.value),
    device_ids: Array.from(form.querySelectorAll('input[name="device_ids"]:checked')).map(
      (c) => c.value
    ),
  });
  const keyOf = (sel) => JSON.stringify([sel.page_ids, sel.device_ids.slice().sort()]);

  function guard(form) {
    const initial = keyOf(picked(form));
    const editing = form.dataset.bindGuard === "edit";
    let go = false;
    form.addEventListener("submit", (e) => {
      if (go) return;
      const sel = picked(form);
      if (!sel.device_ids.length || (editing && keyOf(sel) === initial)) return;
      e.preventDefault();
      const submitter = e.submitter || null;
      fetch(form.dataset.bindCheckUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(sel),
      })
        .then((r) => r.json())
        .catch(() => null)
        .then(async (res) => {
          // If the check itself fails, let the server's own refusal speak.
          let mode = "none";
          if (res && res.unbound_pages && res.unbound_pages.length) {
            mode = await window.TesseraeBindPrompt.ask({
              display: res.display,
              pages: res.unbound_pages,
            });
            if (!mode) return;
            form.querySelectorAll("input[data-bind-consent]").forEach((n) => n.remove());
            res.unbound_pages.forEach((p) => {
              const names = mode === "also" ? ["bind_pages", "bind_also"] : ["bind_pages"];
              names.forEach((name) => {
                const i = document.createElement("input");
                i.type = "hidden";
                i.name = name;
                i.value = p.id;
                i.dataset.bindConsent = "1";
                form.appendChild(i);
              });
            });
          }
          go = true;
          form.requestSubmit(submitter);
        });
    });
  }

  document.querySelectorAll("form[data-bind-guard]").forEach(guard);
})();
