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

  // Layout picker — apply server-side (it reshapes cells), then reload
  // the page so the cell list reflects the new layout. The iframe
  // refreshes naturally on reload.
  function watchLayoutForms() {
    document.querySelectorAll(".layout-form").forEach((form) => {
      if (form.dataset.layoutBound) return;
      form.dataset.layoutBound = "1";
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        setStatus("saving");
        try {
          await submit(form);
          location.reload();
        } catch (err) {
          setStatus("error");
          console.error("[editor] layout swap failed:", err);
        }
      });
    });
  }

  // Icon picker — popover dropdown with a search filter over every
  // available Phosphor icon. Lazily fetches the manifest the first
  // time the user opens the popover.
  function watchIconPicker() {
    const picker = document.querySelector("[data-icon-picker]");
    if (!picker) return;
    const trigger = picker.querySelector("[data-icon-trigger]");
    const popover = picker.querySelector("[data-icon-popover]");
    const grid = picker.querySelector("[data-icon-grid]");
    const search = picker.querySelector("[data-icon-search]");
    const empty = picker.querySelector("[data-icon-empty]");
    const labelEl = picker.querySelector("[data-icon-label]");
    const current = picker.querySelector(".icon-picker-current");
    const input = picker.parentElement.querySelector("[data-icon-value]");
    const form = picker.closest("form[data-editor-form]");
    let icons = null;

    function pick(name) {
      if (input) {
        input.value = name || "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (current) {
        current.innerHTML = name
          ? '<i class="ph ph-' + name + '" aria-hidden="true"></i>'
          : '<i class="ph ph-prohibit" aria-hidden="true"></i>';
      }
      if (labelEl) labelEl.textContent = name || "No icon";
      const headerIcon = document.querySelector("[data-editor-name-icon]");
      if (headerIcon) {
        headerIcon.innerHTML = name
          ? '<i class="ph ph-' + name + '" aria-hidden="true"></i>'
          : "";
      }
      grid.querySelectorAll(".icon-pick").forEach((b) =>
        b.classList.toggle("is-active", (b.dataset.icon || "") === (name || "")),
      );
      if (form) {
        setDirty(true);
        schedulePreview();
      }
    }

    function render(filter) {
      if (!icons) return;
      const q = (filter || "").trim().toLowerCase();
      const matched = q
        ? icons.filter((n) => n.indexOf(q) !== -1)
        : icons;
      const cap = 600;
      const slice = matched.slice(0, cap);
      const chosen = (input && input.value) || "";
      let html = '';
      // "No icon" tile is always first.
      if (!q) {
        html += '<button type="button" class="icon-pick' +
          (chosen === '' ? ' is-active' : '') +
          '" data-icon="" title="No icon" aria-label="No icon">' +
          '<i class="ph ph-prohibit" aria-hidden="true"></i></button>';
      }
      for (const name of slice) {
        html += '<button type="button" class="icon-pick' +
          (chosen === name ? ' is-active' : '') +
          '" data-icon="' + name + '" title="' + name + '" aria-label="' + name + '">' +
          '<i class="ph ph-' + name + '" aria-hidden="true"></i></button>';
      }
      grid.innerHTML = html;
      empty.hidden = slice.length > 0 || !q;
    }

    async function loadIcons() {
      if (icons) return;
      try {
        const resp = await fetch("/static/icons/phosphor/manifest.json");
        icons = await resp.json();
      } catch (err) {
        console.error("[editor] icon manifest fetch failed:", err);
        icons = [];
      }
      render(search.value);
    }

    function open() {
      popover.hidden = false;
      picker.classList.add("is-open");
      loadIcons().then(() => {
        search.value = "";
        search.focus();
      });
    }
    function close() {
      popover.hidden = true;
      picker.classList.remove("is-open");
    }

    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      popover.hidden ? open() : close();
    });
    search.addEventListener("input", () => render(search.value));
    grid.addEventListener("click", (e) => {
      const btn = e.target.closest(".icon-pick");
      if (!btn) return;
      pick(btn.dataset.icon || "");
      close();
    });
    document.addEventListener("click", (e) => {
      if (!picker.contains(e.target)) close();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !popover.hidden) close();
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", saveAll);
    saveBtn.disabled = true;
  }
  watchForms();
  watchLayoutForms();
  watchIconPicker();
  setStatus("saved");
})();
