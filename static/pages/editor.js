// Dashboard editor — explicit save model.
//
// Every form tagged `data-editor-form` (page metadata, cells) is left
// alone until the user clicks the Save button in the header. Clicking
// Save fans out one fetch per form, refreshes the preview iframe, and
// flips the save-status pill back to "Saved".
//
// The plugin <select> on a cell is a special case — when it changes
// we still POST that one cell immediately so the option schema can
// re-render against the new plugin. Everything else is held until Save.

(function () {
  const status = document.querySelector("[data-save-status]");
  const iframe = document.getElementById("preview-iframe");
  const saveBtn = document.querySelector("[data-save-all]");
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

  // Dirty-tracking: any change in any editor-form flips the indicator.
  function watchForms() {
    forms().forEach((form) => {
      if (form.dataset.dirtyBound) return;
      form.dataset.dirtyBound = "1";
      form.addEventListener("input", () => setDirty(true));
      form.addEventListener("change", (ev) => {
        // The plugin picker reshapes the cell's option fields, so we
        // POST it immediately and reload so the new schema appears.
        const field = ev.target;
        if (field && field.dataset && field.dataset.reloadOnChange) {
          submit(form).then(() => location.reload()).catch((err) => {
            setStatus("error");
            console.error("[editor] plugin swap failed:", err);
          });
          return;
        }
        setDirty(true);
      });
      // Hitting Enter inside a form should still save (instead of
      // performing a native multipart submit that leaves the page).
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
