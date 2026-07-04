// Dashboard editor, explicit save with live preview.
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
// The plugin <select> on a cell is still a special case, when it
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

  // v0.69.6 (issue #52 item 7): snapshot every form's current values
  // whenever we go clean (initial load + after each successful save),
  // then diff at ``beforeunload`` to catch only real edits. FormData
  // iteration order is insertion order (matches the DOM), stable
  // enough for a raw-string comparison, and cheap enough to run on
  // both save + unload.
  function _snapshotForms() {
    const snap = new Map();
    forms().forEach((form) => {
      const rows = [];
      new FormData(form).forEach((value, key) => {
        rows.push(key + "=" + String(value));
      });
      snap.set(form, rows.join("\n"));
    });
    return snap;
  }
  let cleanSnapshot = new Map();
  function _hasRealFormDiff() {
    const current = _snapshotForms();
    if (current.size !== cleanSnapshot.size) return true;
    for (const [form, snap] of cleanSnapshot) {
      if (current.get(form) !== snap) return true;
    }
    return false;
  }

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
    // v0.69.6 (issue #52 item 7): re-snapshot the clean form state
    // whenever we transition to "not dirty" (initial load + after
    // every successful save). The beforeunload guard compares live
    // FormData against this snapshot so spurious ``input`` events
    // (autofill, focus, browser extensions) don't trigger the
    // "Leave site?" popup for a page the user hasn't actually edited.
    if (!dirty) {
      cleanSnapshot = _snapshotForms();
    }
  }

  // Text-like inputs (<input type="text|search|email|url|password|tel">
  // and <textarea>) defer preview to the 'change' event (fires on blur
  // or Enter). Everything else keeps live preview on 'input'.
  // Number inputs are special-cased because they're often used for
  // step-wise widget options where each keystroke isn't a meaningful
  // value; the 'change' (blur) commit is the natural preview point.
  const _DEFER_INPUT_TYPES = new Set([
    "text", "search", "email", "url", "password", "tel", "number",
  ]);
  function _deferToBlur(el) {
    if (!el) return false;
    if (el.tagName === "TEXTAREA") return true;
    if (el.tagName !== "INPUT") return false;
    return _DEFER_INPUT_TYPES.has((el.type || "text").toLowerCase());
  }

  // Cache of the last hydrated state we know each iframe is rendering,
  // keyed by the iframe element. Used to compute postMessage patches so
  // a theme nudge / gap tweak / single-cell option change doesn't tear
  // down the whole iframe DOM, see applyPreviewGroups below.
  const lastStateByFrame = new WeakMap();

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
          // The iframe's contents are fresh, drop the cached state so
          // the next preview cycle diffs against what's actually painted.
          lastStateByFrame.delete(iframe);
        },
        { once: true },
      );
      iframe.src = url.pathname + url.search;
    });
  }

  // Bounded-lifetime preview iframes.
  //
  // The composer iframe is mounted once when the editor opens, then
  // runs forever, each widget's setInterval ticks (clock, F1
  // countdown, public-transport refresh) accumulate small allocations
  // every minute, and the webpage widget's auto-refresh swaps a
  // foreign document in repeatedly. Over a long idle session those
  // compound into multi-GB tab memory (saw 6.5 GB in the wild, then
  // a "page was reloaded because it was using significant memory"
  // warning even after the 4-hour reset was added). A hard reset
  // every hour discards all accumulated state, the user sees the
  // same brief opacity fade as a normal save-driven reload, but
  // about:blank in between forces the browser to fully release the
  // previous document instead of cache-keeping it.
  const _PREVIEW_RESET_MS = 60 * 60 * 1000;
  let _previewResetTimer = null;
  function hardResetPreview() {
    previewFrames().forEach((iframe) => {
      const finalUrl = (() => {
        const url = new URL(iframe.src, location.origin);
        url.searchParams.set("_t", String(Date.now()));
        return url.pathname + url.search;
      })();
      iframe.style.transition = "opacity 140ms ease";
      iframe.style.opacity = "0.35";
      // about:blank first → the browser unmounts the document, dropping
      // every interval/listener/ResizeObserver/embedded-iframe that
      // belonged to it. Then the real URL loads a fresh composition.
      iframe.addEventListener(
        "load",
        function onBlank() {
          iframe.removeEventListener("load", onBlank);
          iframe.addEventListener(
            "load",
            () => {
              iframe.style.opacity = "1";
              lastStateByFrame.delete(iframe);
            },
            { once: true },
          );
          iframe.src = finalUrl;
        },
        { once: true },
      );
      iframe.src = "about:blank";
    });
  }
  function schedulePreviewReset() {
    if (_previewResetTimer) clearTimeout(_previewResetTimer);
    _previewResetTimer = setTimeout(() => {
      hardResetPreview();
      schedulePreviewReset();
    }, _PREVIEW_RESET_MS);
  }
  schedulePreviewReset();

  // Parse "WxH" out of an iframe's src so each frame can be matched up
  // with the matching group in the /preview response.
  function frameSize(iframe) {
    const u = new URL(iframe.src, location.origin);
    const w = Number(u.searchParams.get("w"));
    const h = Number(u.searchParams.get("h"));
    return Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0
      ? { w, h }
      : null;
  }

  // Decide whether two hydrated states can be reconciled in-place via a
  // postMessage patch, or whether the iframe needs a full reload. Hard
  // requirements for a patch:
  //   - same set of cell ids (no add/remove)
  //   - same plugin id per cell (plugin swap needs a fresh module import)
  //   - same full_bleed flag (changes the cell's CSS shape)
  // Anything looser is handled by the patch handler in composer.js.
  function canPatch(prev, next) {
    if (!prev || !next) return false;
    if (prev.cells.length !== next.cells.length) return false;
    for (let i = 0; i < prev.cells.length; i++) {
      const a = prev.cells[i];
      const b = next.cells[i];
      if (a.id !== b.id) return false;
      if ((a.plugin || "") !== (b.plugin || "")) return false;
      if (Boolean(a.full_bleed) !== Boolean(b.full_bleed)) return false;
    }
    return true;
  }

  // Build the postMessage patch the iframe applies. We always send the
  // full next-state (the iframe walks it and updates anything that
  // changed), simpler than a true JSON-diff and the payload is small.
  function buildPatch(next) {
    return {
      type: "tesserae-patch",
      page: {
        theme: next.theme,
        style: next.style,
        // Carry both the raw picker value (`font`) and the resolved
        // family name (`font_family`). The iframe needs `font` to decide
        // whether to write an inline --font-family override on body -
        // see composer.js applyPagePatch for the rationale.
        font: next.font || "",
        font_family: next.font_family,
        font_face_css: next.font_face_css,
        bleed_color: next.bleed_color,
        corner_radius: next.corner_radius,
        gap: next.gap,
        panel: next.panel,
      },
      cells: next.cells.map((c) => ({
        id: c.id,
        plugin: c.plugin || "",
        x: c.x, y: c.y, w: c.w, h: c.h,
        zoom: typeof c.zoom === "number" ? c.zoom : 1,
        options: c.options,
        data: c.data,
        theme: c.theme || "",
        style: c.style || "",
        font: c.font || "",
        font_family: c.font_family,
        full_bleed: Boolean(c.full_bleed),
      })),
    };
  }

  // Hand each /preview group off to its matching iframe, patch if we
  // can, otherwise full reload. If the response carries no groups (server
  // didn't get a panels[] hint, or hydration failed) we fall back to a
  // blanket reload so we never silently desync.
  function applyPreviewGroups(groups) {
    const frames = Array.from(previewFrames());
    if (!Array.isArray(groups) || groups.length === 0) {
      reloadPreview();
      return;
    }
    let appliedAny = false;
    frames.forEach((iframe) => {
      const size = frameSize(iframe);
      if (!size) return;
      const match = groups.find((g) => g.w === size.w && g.h === size.h);
      if (!match || !match.state) return;
      const prev = lastStateByFrame.get(iframe);
      if (canPatch(prev, match.state)) {
        try {
          iframe.contentWindow.postMessage(
            buildPatch(match.state),
            location.origin,
          );
          lastStateByFrame.set(iframe, match.state);
          appliedAny = true;
        } catch (err) {
          console.warn("[editor] patch postMessage failed, will reload:", err);
        }
      }
    });
    // Reload every frame that didn't accept a patch (structural change,
    // no cached prev state, or postMessage threw).
    let needsReload = false;
    frames.forEach((iframe) => {
      const size = frameSize(iframe);
      if (!size) return;
      const match = groups.find((g) => g.w === size.w && g.h === size.h);
      if (!match || !match.state) {
        needsReload = true;
        return;
      }
      const prev = lastStateByFrame.get(iframe);
      if (!canPatch(prev, match.state)) {
        // Reload this specific frame; record the next state so the
        // *next* cycle (post-load) can patch against it.
        const u = new URL(iframe.src, location.origin);
        u.searchParams.set("_t", String(Date.now()));
        iframe.style.transition = "opacity 140ms ease";
        iframe.style.opacity = "0.35";
        iframe.addEventListener(
          "load",
          () => {
            iframe.style.opacity = "1";
            lastStateByFrame.set(iframe, match.state);
          },
          { once: true },
        );
        iframe.src = u.pathname + u.search;
      }
    });
    if (needsReload && !appliedAny) reloadPreview();
  }

  // Build a single FormData containing every editor-form's fields,
  // with each cell form's fields namespaced `cell_<id>__<field>` so
  // the preview endpoint can demux them back to per-cell buckets. Also
  // appends ``panels[]=WxH`` for every preview iframe so the server can
  // hydrate the draft per panel size and return state envelopes the
  // client uses to compute postMessage patches.
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
    previewFrames().forEach((iframe) => {
      const size = frameSize(iframe);
      if (size) combined.append("panels[]", `${size.w}x${size.h}`);
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
        const prefix = window.TESSERAE_URL_PREFIX || "";
        const resp = await fetch(`${prefix}/pages/${pageId}/preview`, {
          method: "POST",
          body: aggregateForms(),
          headers: { "X-Requested-With": "fetch" },
          credentials: "same-origin",
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        // Server returns the hydrated state per requested panel size when
        // the request was AJAX (it is here, see X-Requested-With). Hand
        // each group to its matching iframe; the patch path postMessages
        // CSS / cell updates in place, the fallback path full-reloads.
        const body = await resp.json().catch(() => null);
        applyPreviewGroups(body && body.groups);
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
      form.addEventListener("input", (ev) => {
        setDirty(true);
        // Text-like inputs defer preview to the 'change' event (which
        // fires on blur / Enter) so the preview doesn't re-render on
        // every keystroke. Matters for expensive widgets like
        // fal_image whose render calls a paid image API. Sliders /
        // colour pickers / number steppers / checkboxes / selects all
        // keep live preview because their 'input' event is the natural
        // commit point.
        if (_deferToBlur(ev.target)) return;
        schedulePreview();
      });
      form.addEventListener("change", (ev) => {
        const field = ev.target;
        // A reload-on-change field (cell plugin picker, device checkboxes)
        // reshapes the page server-side, so we persist then reload. Save
        // EVERY form first, not just this one, or unsaved edits
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

  // Layout picker, apply server-side (it reshapes cells), then reload
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
          // Persist every cell form first — picking a layout preset
          // reloads the page, and without this any typed-but-unsaved
          // cell options / theme overrides on OTHER cells get wiped
          // when the editor renders fresh from disk.
          for (const f of forms()) await submit(f);
          await submit(form);
          location.reload();
        } catch (err) {
          setStatus("error");
          console.error("[editor] layout swap failed:", err);
        }
      });
    });
  }

  // The "Refit to current panel" button inside the layout editor.
  // Native form submit would lose any in-flight cell edits the user
  // hadn't pressed Save on yet (and the page reload that follows is
  // the same eat-everything reload the layout-form picker had).
  // Intercept, persist every cell form, then POST the refit and
  // navigate to the redirect target on success.
  document.querySelectorAll(".layout-editor-refit").forEach((form) => {
    if (form.dataset.refitBound) return;
    form.dataset.refitBound = "1";
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      setStatus("saving");
      try {
        for (const f of forms()) await submit(f);
        const resp = await fetch(form.action, {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-Requested-With": "fetch" },
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        location.reload();
      } catch (err) {
        setStatus("error");
        console.error("[editor] refit failed:", err);
      }
    });
  });

  // Helper consumed by layout_editor.js's structural-change reload
  // path (insert/delete cell). The layout editor lives in its own IIFE
  // and doesn't have a handle to forms() / submit() directly, so we
  // expose this thin wrapper that does the same "save every cell form,
  // then reload" the reload-on-change handlers do. Returns a Promise so
  // callers can await it before they trigger their own reload.
  window.tesseraeSaveAllForms = async function () {
    const all = forms();
    for (const f of all) await submit(f);
    setDirty(false);
  };

  // Same shape as ``tesseraeSaveAllForms``: a small hook that custom
  // form-builder components (location_search, future ones) can call
  // when they've programmatically updated an input value and need the
  // preview to refresh, without relying on the form-level event
  // listeners catching synthetic ``input`` / ``change`` events. The
  // listeners have a ``_deferToBlur`` gate that early-returns for text
  // inputs, which means a synthesised input event on a sibling
  // ``<input type="text">`` doesn't trigger the preview. Calling this
  // helper bypasses the gate.
  window.tesseraeSchedulePreview = function () {
    setDirty(true);
    schedulePreview();
  };

  // Heavier hammer for custom form components that need to GUARANTEE
  // an iframe refresh after a programmatic update (no relying on the
  // patch path's fingerprint cache, which can skip a re-render when
  // the widget's ``server.py`` returns cached data so the data column
  // looks unchanged from the iframe's perspective). Posts the
  // aggregated forms to ``/preview`` synchronously and then triggers
  // a full iframe reload so the widget re-runs against the latest
  // cached state. Slower than the patch path; only use it when the
  // patch path's been observed to NOT re-render in practice.
  window.tesseraeForcePreview = async function () {
    if (!pageId || !previewFrames().length) return;
    setDirty(true);
    try {
      const prefix = window.TESSERAE_URL_PREFIX || "";
      await fetch(`${prefix}/pages/${pageId}/preview`, {
        method: "POST",
        body: aggregateForms(),
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin",
      });
    } catch (err) {
      console.warn("[editor] force-preview POST failed:", err);
    }
    reloadPreview();
  };

  window.addEventListener("beforeunload", (ev) => {
    // v0.69.6 (issue #52 item 7): saveBtn state is only a hint,
    // it re-enables on any ``input`` event (autofill, focus,
    // extensions) even when the underlying values are unchanged. Real
    // gate is a raw-string comparison of the current form values
    // against the last known-clean snapshot; only warn when something
    // actually differs.
    if (!(saveBtn && !saveBtn.disabled)) return;
    if (!_hasRealFormDiff()) return;
    ev.preventDefault();
    ev.returnValue = "";
  });

  // Icon picker lives in static/icon-picker.js as a reusable shared
  // module, it auto-binds every [data-icon-picker] on page load. The
  // editor's only contribution is the existing 'input' listener on the
  // hidden value field, which already fires setDirty + schedulePreview
  // because watchForms() listens on the whole form for 'input' events.

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
        // Keep filter keystrokes local, don't dirty the form or trigger
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
  // that cell's card here, scroll it into view, highlight it, focus its
  // first control, and echo 'tesserae-focus-cell' back so the preview
  // outlines the same cell.
  function focusCellCard(cellId) {
    let target = null;
    document.querySelectorAll(".cell-card").forEach((card) => {
      const match = card.dataset.cellId === cellId;
      card.classList.toggle("is-focused", match);
      if (match) target = card;
    });
    if (!target) return;
    // ``block: "nearest"`` only scrolls when the card is actually out of
    // view, previously ``"center"`` would jump the page whenever the
    // user clicked any cell-area item, which was disorienting on long
    // list widgets (ha_entities, ha_history) where the user clicked an
    // entity row to inspect it.
    target.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
  watchMultiSelect();
  // ``setDirty(false)`` here (not just ``setStatus("saved")``) so the
  // beforeunload snapshot is populated once forms are watched. Without
  // this the snapshot stays empty and every navigation triggers the
  // "Leave site?" prompt because ``current.size !== 0``. (v0.69.6,
  // issue #52 item 7.)
  setDirty(false);
})();
