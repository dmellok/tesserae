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
  // One preview iframe per distinct aspect ratio (multi-device pages).
  const previewFrames = () => document.querySelectorAll(".preview-frame iframe");
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
      state === "error" ? "ph-fill ph-warning-circle" :
      state === "dirty" ? "ph ph-circle-dashed" :
      "ph-fill ph-check-circle";
    status.innerHTML =
      '<i class="' + iconClass + '" aria-hidden="true"></i><span>' + label + "</span>";
  }

  function setDirty(dirty) {
    setStatus(dirty ? "dirty" : "saved");
    if (saveBtn) saveBtn.disabled = !dirty;
  }

  // Reload the preview iframe in place. A short opacity fade hides
  // the blank-during-load state without doubling the live iframe.
  //
  // (We used to double-buffer with a cloned iframe + swap, but that
  // briefly held two full widget compositions in memory at once and
  // froze the page on iPad Pro.)
  function reloadPreview() {
    previewFrames().forEach((iframe) => {
      const url = new URL(iframe.src, location.origin);
      url.searchParams.set("_t", String(Date.now()));
      iframe.style.transition = "opacity 140ms ease";
      iframe.style.opacity = "0.35";
      iframe.addEventListener(
        "load",
        () => {
          iframe.style.opacity = "1";
        },
        { once: true },
      );
      iframe.src = url.pathname + url.search;
    });
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
    if (!pageId || !previewFrames().length) return;
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
        // A reload-on-change field (cell plugin picker, device checkboxes)
        // reshapes the page server-side, so we persist then reload. Save
        // EVERY form first — not just this one — or unsaved edits
        // elsewhere (e.g. a theme override on another cell) get discarded
        // when the page re-renders from disk.
        if (field && field.dataset && field.dataset.reloadOnChange) {
          setStatus("saving");
          (async () => {
            try {
              for (const f of forms()) await submit(f);
              location.reload();
            } catch (err) {
              setStatus("error");
              console.error("[editor] save-before-reload failed:", err);
            }
          })();
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
    const form = picker.closest("form[data-editor-form]");
    // The hidden value input is a direct child of the form, not inside the
    // picker's .field — so search the form, not picker.parentElement.
    const input = form ? form.querySelector("[data-icon-value]") : null;
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

  // Multi-select checklists (e.g. the HA entity pickers): a client-side
  // filter box narrows the list, and a count shows how many are ticked.
  // The filter input has no name (never submitted) and stops its own
  // events from bubbling to the form so typing doesn't fire a preview.
  function watchMultiSelect() {
    document.querySelectorAll("[data-multiselect]").forEach((ms) => {
      if (ms.dataset.msBound) return;
      ms.dataset.msBound = "1";
      const filter = ms.querySelector("[data-ms-filter]");
      const opts = Array.from(ms.querySelectorAll(".multiselect-opt"));
      const emptyEl = ms.querySelector("[data-ms-empty]");
      const countEl = ms.querySelector("[data-ms-count]");

      function updateCount() {
        if (!countEl) return;
        const n = opts.filter((o) => o.querySelector("input").checked).length;
        countEl.textContent = n ? `${n} selected` : "";
      }
      function applyFilter() {
        const q = (filter ? filter.value : "").trim().toLowerCase();
        let shown = 0;
        opts.forEach((o) => {
          const match = !q || (o.dataset.msText || "").indexOf(q) !== -1;
          o.hidden = !match;
          if (match) shown += 1;
        });
        if (emptyEl) emptyEl.hidden = shown > 0;
      }
      if (filter) {
        // Keep filter keystrokes local — don't dirty the form or trigger
        // a preview POST (the filter isn't part of the saved value).
        ["input", "change", "keyup", "keydown"].forEach((evt) =>
          filter.addEventListener(evt, (e) => e.stopPropagation()),
        );
        filter.addEventListener("input", applyFilter);
      }
      ms.addEventListener("change", updateCount);
      updateCount();
    });
  }

  // Live preview -> editor: the preview iframe (compose.html) posts
  // {type:'tesserae-cell-clicked', cellId} when a cell is clicked. Focus
  // that cell's card here — scroll it into view, highlight it, focus its
  // first control — and echo 'tesserae-focus-cell' back so the preview
  // outlines the same cell.
  function focusCellCard(cellId) {
    let target = null;
    document.querySelectorAll(".cell-card").forEach((card) => {
      const match = card.dataset.cellId === cellId;
      card.classList.toggle("is-focused", match);
      if (match) target = card;
    });
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    const field = target.querySelector("select, input, textarea, button");
    if (field) {
      try { field.focus({ preventScroll: true }); } catch (e) { field.focus(); }
    }
    previewFrames().forEach((iframe) => {
      try {
        iframe.contentWindow.postMessage(
          { type: "tesserae-focus-cell", cellId },
          location.origin,
        );
      } catch (e) { /* iframe not ready / cross-origin */ }
    });
  }

  window.addEventListener("message", (ev) => {
    if (ev.origin !== location.origin) return;
    const d = ev.data;
    if (!d || d.type !== "tesserae-cell-clicked" || !d.cellId) return;
    focusCellCard(d.cellId);
  });

  if (saveBtn) {
    saveBtn.addEventListener("click", saveAll);
    saveBtn.disabled = true;
  }
  watchForms();
  watchLayoutForms();
  watchIconPicker();
  watchMultiSelect();
  setStatus("saved");
})();
