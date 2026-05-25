// Dashboard editor — explicit save with live preview.
//
// Each editor-form is left unsaved until the user clicks Save in the
// header. As the user makes changes we debounce-POST the aggregated
// form data to /pages/<id>/preview, which stashes a draft Page in the
// app's PREVIEW_CACHE. The preview iframe re-loads the composer URL;
// the composer reads from the cache first, so the user sees their
// pending changes without anything being written to disk.
//
// Save → fans out one fetch per form to the real persist endpoints;
// each persist clears the preview cache so the saved version becomes
// authoritative again.
//
// The plugin <select> on a cell is still a special case — when it
// changes we POST it immediately so the option schema can re-render
// against the new plugin.

(function () {
  const status = document.querySelector("[data-save-status]");
  const iframe = document.getElementById("preview-iframe");
  const saveBtn = document.querySelector("[data-save-all]");
  const grid = document.querySelector(".editor-grid");
  const pageId = grid ? grid.dataset.pageId : null;
  const forms = () => document.querySelectorAll("form[data-editor-form]");

  function setStatus(state) {
    if (!status) return;
    status.dataset.state = state;
    const label =
      state === "saving" ? "Saving…" :
      state === "error" ? "Save failed" :
      state === "dirty" ? "Unsaved changes" :
      "Saved";
    const iconClass =
      state === "saving" ? "ph ph-arrows-clockwise" :
      state === "error" ? "ph-fill ph-fill-warning-circle" :
      state === "dirty" ? "ph ph-circle-dashed" :
      "ph-fill ph-fill-check-circle";
    status.innerHTML =
      '<i class="' + iconClass + '" aria-hidden="true"></i><span>' + label + "</span>";
  }

  function setDirty(dirty) {
    setStatus(dirty ? "dirty" : "saved");
    if (saveBtn) saveBtn.disabled = !dirty;
  }

  function reloadPreview() {
    if (!iframe) return;
    const url = new URL(iframe.src, location.origin);
    url.searchParams.set("_t", String(Date.now()));
    iframe.src = url.pathname + url.search;
  }

  // Build a single FormData containing every editor-form's fields,
  // with each cell form's fields namespaced `cell_<id>__<field>` so
  // the preview endpoint can demux them back to per-cell buckets.
  function aggregateForms() {
    const combined = new FormData();
    forms().forEach((form) => {
      const cellId = form.dataset.cellId || null;
      const fd = new FormData(form);
      for (const [key, value] of fd.entries()) {
        const outKey = cellId ? `cell_${cellId}__${key}` : key;
        combined.append(outKey, value);
      }
    });
    return combined;
  }

  let previewTimer;
  let previewInFlight = null;
  function schedulePreview() {
    if (!pageId || !iframe) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      // Coalesce: if one is already running, wait for it before issuing
      // the next so the iframe doesn't thrash.
      if (previewInFlight) await previewInFlight.catch(() => {});
      previewInFlight = (async () => {
        const resp = await fetch(`/pages/${pageId}/preview`, {
          method: "POST",
          body: aggregateForms(),
          headers: { "X-Requested-With": "fetch" },
          credentials: "same-origin",
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        reloadPreview();
      })();
      try {
        await previewInFlight;
      } catch (err) {
        console.warn("[editor] preview update failed:", err);
      } finally {
        previewInFlight = null;
      }
    }, 250);
  }

  async function submit(form) {
    const fd = new FormData(form);
    const resp = await fetch(form.action, {
      method: "POST",
      body: fd,
      headers: { "X-Requested-With": "fetch" },
      credentials: "same-origin",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status} from ${form.action}`);
    const data = await resp.json().catch(() => ({ ok: true }));
    if (data.ok === false) throw new Error(data.message || "save failed");
  }

  async function saveAll() {
    setStatus("saving");
    if (saveBtn) saveBtn.disabled = true;
    try {
      for (const form of forms()) {
        await submit(form);
      }
      setDirty(false);
      reloadPreview();
    } catch (err) {
      setStatus("error");
      if (saveBtn) saveBtn.disabled = false;
      console.error("[editor] save failed:", err);
    }
  }

  function watchForms() {
    forms().forEach((form) => {
      if (form.dataset.dirtyBound) return;
      form.dataset.dirtyBound = "1";
      form.addEventListener("input", () => {
        setDirty(true);
        schedulePreview();
      });
      form.addEventListener("change", (ev) => {
        const field = ev.target;
        // The plugin picker reshapes the cell's option fields, so we
        // POST it immediately (persist) and reload so the new schema
        // appears in the editor.
        if (field && field.dataset && field.dataset.reloadOnChange) {
          submit(form).then(() => location.reload()).catch((err) => {
            setStatus("error");
            console.error("[editor] plugin swap failed:", err);
          });
          return;
        }
        setDirty(true);
        schedulePreview();
      });
      form.addEventListener("submit", (ev) => {
        ev.preventDefault();
        saveAll();
      });
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", saveAll);
    saveBtn.disabled = true;
  }
  watchForms();
  setStatus("saved");
})();
