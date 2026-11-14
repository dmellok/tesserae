// Dashboard editor v2: the status line of an open widget row.
//
// Every cell form has one slot for live feedback (capture status, a check's
// result, "loading…", an error): <p data-ev2-cell-status> at the top of the
// row's footer, above "Empty this cell" and Done. It is a polite live region
// and stays in the page while empty (CSS hides it visually), so the first
// message is announced too. Nothing else may put text between the row's
// sections; tests/test_editor_v2_status_browser.py checks that.
//
// API (window.EV2.cellStatus):
//   set(cellId, text, tone)  tone: "" | "busy" | "error"; empty text clears
//   clear(cellId)
//   provide(kind, fn)        fn(data) -> {text, tone} | null. A slot with
//                            data-ev2-status-kind="<kind>" is filled from the
//                            cell's widget data each time the preview loads.
//   adopt(root)              moves stray [data-ev2-status] / legacy
//                            [data-capture-status] text inside a cell form
//                            into its slot (run on load for every form).
(function () {
  "use strict";
  const EV2 = window.EV2;
  if (!EV2) return;

  const slotFor = (cellId) =>
    document.querySelector(
      `[data-ev2-cell-form][data-cell-id="${CSS.escape(String(cellId))}"] [data-ev2-cell-status]`
    );

  function setSlot(slot, text, tone) {
    if (!slot) return;
    const t = text ? String(text) : "";
    if (slot.textContent !== t) slot.textContent = t;
    if (tone && t) slot.dataset.tone = tone;
    else delete slot.dataset.tone;
  }

  const providers = {
    // ha_dashboard: the background capture behind the frame. Same wording as
    // the classic editor (static/pages/editor.js updateCaptureStatus).
    capture(data) {
      const error = data.error || data.capture_error;
      const last = data.rendered_at
        ? "Last capture: " + new Date(data.rendered_at * 1000).toLocaleString()
        : "";
      if (error)
        return {
          text: [last, "Capture failed: " + error].filter(Boolean).join(". "),
          tone: "error",
        };
      if (data.pending || data.capturing)
        return { text: [last, "Capturing dashboard…"].filter(Boolean).join(". "), tone: "busy" };
      return { text: last || "Waiting for a capture.", tone: "" };
    },
  };

  function adopt(root) {
    (root || document).querySelectorAll("[data-ev2-cell-form]").forEach((form) => {
      const slot = form.querySelector("[data-ev2-cell-status]");
      if (!slot) return;
      form.querySelectorAll("[data-ev2-status], [data-capture-status]").forEach((el) => {
        if (el === slot || slot.contains(el)) return;
        const kind =
          el.dataset.ev2Status || (el.hasAttribute("data-capture-status") ? "capture" : "");
        if (kind && !slot.dataset.ev2StatusKind) slot.dataset.ev2StatusKind = kind;
        if (el.textContent.trim() && !slot.textContent.trim())
          setSlot(slot, el.textContent.trim(), el.dataset.error === "true" ? "error" : "");
        el.remove();
      });
    });
  }

  // While a provider says "busy", re-read the cells' data from the composer
  // every few seconds (a GET, the frame itself is left alone) and reload the
  // preview once the work is done so the new frame shows.
  let pollTimer = null;
  let pollSrc = "";
  let polls = 0;
  const POLL_MS = 6000;
  const MAX_POLLS = 30;

  function apply(cells) {
    let busy = false;
    (cells || []).forEach((c) => {
      const slot = slotFor(c.id);
      const fn = slot && providers[slot.dataset.ev2StatusKind];
      if (!fn || !c.data) return;
      let out = null;
      try {
        out = fn(c.data);
      } catch {
        out = null;
      }
      if (!out) return;
      setSlot(slot, out.text, out.tone);
      if (out.tone === "busy") busy = true;
    });
    return busy;
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!pollSrc || polls >= MAX_POLLS) return;
    pollTimer = setTimeout(poll, POLL_MS);
  }

  async function poll() {
    if (document.hidden) return schedulePoll();
    polls++;
    try {
      const u = new URL(pollSrc, location.origin);
      u.searchParams.set("_t", String(Date.now()));
      const r = await fetch(u.pathname + u.search, {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!r.ok) return schedulePoll();
      const doc = new DOMParser().parseFromString(await r.text(), "text/html");
      const cells = [...doc.querySelectorAll(".cell[data-cell-id]")].map((el) => {
        let data = null;
        try {
          data = JSON.parse(el.dataset.data || "null");
        } catch {
          data = null;
        }
        return { id: el.dataset.cellId, data };
      });
      if (apply(cells)) schedulePoll();
      else if (window.EV2Preview) window.EV2Preview.reload();
    } catch {
      schedulePoll();
    }
  }

  document.addEventListener("ev2:frame-data", (ev) => {
    const d = ev.detail || {};
    pollSrc = d.src || "";
    polls = 0;
    if (apply(d.cells)) schedulePoll();
    else clearTimeout(pollTimer);
  });

  EV2.cellStatus = {
    set: (cellId, text, tone) => setSlot(slotFor(cellId), text, tone || ""),
    clear: (cellId) => setSlot(slotFor(cellId), "", ""),
    provide: (kind, fn) => {
      providers[kind] = fn;
    },
    adopt,
  };
  adopt(document);
})();
