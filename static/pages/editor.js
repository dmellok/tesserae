// Single-page editor wiring: auto-save on field changes, save-status
// indicator, preview iframe reload, click-cell-in-preview-to-jump-to-editor.
//
// All forms with `data-autosave` submit themselves via fetch on input
// change (300ms debounce). On success the preview iframe reloads and the
// "Saved" indicator flips back to green.
//
// Forms with `data-reload-on-change` on a select (plugin picker) force a
// full page reload after save — needed because the cell card has to
// re-render against the new plugin's option schema.

(function () {
  const status = document.querySelector("[data-save-status]");
  const iframe = document.getElementById("preview-iframe");
  const grid = document.querySelector(".editor-grid");

  function setStatus(state) {
    if (!status) return;
    status.dataset.state = state;
    status.textContent =
      state === "saving" ? "Saving…" : state === "error" ? "Save failed" : "Saved";
  }

  function reloadPreview() {
    if (!iframe) return;
    // Bust the iframe by re-pointing src. Resetting .src triggers a
    // fresh navigation; querystring nonce keeps it cacheless.
    const url = new URL(iframe.src, location.origin);
    url.searchParams.set("_t", String(Date.now()));
    iframe.src = url.pathname + url.search;
  }

  function focusedCellOnPreview(cellId) {
    if (!iframe || !iframe.contentWindow) return;
    try {
      iframe.contentWindow.postMessage(
        { type: "tesserae-focus-cell", cellId },
        location.origin,
      );
    } catch (e) { /* iframe not loaded yet */ }
  }

  // Auto-save -------------------------------------------------------

  const timers = new WeakMap();

  async function submit(form) {
    setStatus("saving");
    const fd = new FormData(form);
    try {
      const resp = await fetch(form.action, {
        method: "POST",
        body: fd,
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json().catch(() => ({ ok: true }));
      if (data.ok === false) throw new Error(data.message || "save failed");
      setStatus("saved");
      reloadPreview();
      // The plugin picker swaps the cell's option schema; the page
      // needs to re-render the form to pick up the new options.
      if (form.dataset.reloadOnSave === "1") {
        location.reload();
      }
    } catch (err) {
      setStatus("error");
      console.error("[editor] save failed:", err);
    }
  }

  function watch(form) {
    if (!form.matches("[data-autosave]")) return;
    form.querySelectorAll("input, select, textarea").forEach((field) => {
      // Plugin picker reloads on save so the option fields re-render
      // against the new plugin's schema.
      if (field.dataset.reloadOnChange) {
        field.addEventListener("change", () => {
          form.dataset.reloadOnSave = "1";
          submit(form);
        });
        return;
      }
      const handler = () => {
        clearTimeout(timers.get(form));
        timers.set(form, setTimeout(() => submit(form), 300));
      };
      field.addEventListener("input", handler);
      field.addEventListener("change", handler);
    });
    // Native submit (no-JS fallback or Enter key) just runs the same path.
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      clearTimeout(timers.get(form));
      submit(form);
    });
  }

  document.querySelectorAll("form[data-autosave]").forEach(watch);

  // Layout cards confirm before overwriting cell assignments. -------

  const cellCount = Number(grid && grid.dataset.cellCount);
  document.querySelectorAll(".layout-form[data-confirm-if-cells]").forEach((form) => {
    form.addEventListener("submit", (ev) => {
      if (cellCount > 0) {
        if (!confirm(
          "Apply this layout? Cells in order are reused; extras get added or dropped.",
        )) {
          ev.preventDefault();
        }
      }
    });
  });

  // Click cell in preview → scroll to editor card -------------------

  window.addEventListener("message", (ev) => {
    if (!ev.data || ev.data.type !== "tesserae-cell-clicked") return;
    const card = document.getElementById(`cell-${ev.data.cellId}`);
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.classList.add("is-focused");
    setTimeout(() => card.classList.remove("is-focused"), 1200);
    focusedCellOnPreview(ev.data.cellId);
  });

  // Wait for the iframe to finish loading before we send any messages
  // back to it. After that, focusing a cell from the editor mirrors
  // through too.
  document.querySelectorAll(".cell-card").forEach((card) => {
    card.addEventListener("click", (ev) => {
      // Only mirror the focus when the user clicks the card chrome,
      // not when interacting with a field inside it.
      if (ev.target.closest("input, select, textarea, button, label")) return;
      focusedCellOnPreview(card.dataset.cellId);
    });
  });
})();
