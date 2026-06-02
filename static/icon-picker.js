// Reusable Phosphor icon picker — searchable popover that swaps a
// hidden form input. Used by the page editor (where it also triggers
// editor.js's setDirty + schedulePreview through standard 'input'
// events) and the device card in Settings.
//
// Markup expectations (see templates/page_editor.html /
// templates/settings.html for canonical examples):
//
//   <input type="hidden" name="icon" value="…" data-icon-value
//          data-icon-picker-id="…">
//   <div class="icon-picker" data-icon-picker data-icon-picker-id="…">
//     <button class="icon-picker-trigger" data-icon-trigger>…</button>
//     <div class="icon-picker-popover" data-icon-popover hidden>
//       <div class="icon-picker-search"><input data-icon-search></div>
//       <div data-icon-grid></div>
//       <div data-icon-empty hidden>No icons match that search.</div>
//     </div>
//   </div>
//
// The hidden input is paired with its picker via matching
// data-icon-picker-id attributes — that way multiple pickers can
// coexist on the same page (e.g. a per-cell picker + a per-page one
// in the editor, or the device-card picker in settings). When the id
// is absent, the picker uses the nearest hidden input in the same
// form, the same way the editor originally wired its single picker.

(function () {
  // Shared icon manifest — fetched once per page, cached for the life
  // of the page. Every picker shares the same promise so opening N
  // pickers triggers exactly one network request.
  let iconsPromise = null;
  function loadIcons() {
    if (iconsPromise) return iconsPromise;
    const prefix = window.TESSERAE_URL_PREFIX || "";
    iconsPromise = fetch(`${prefix}/static/icons/phosphor/manifest.json`)
      .then((r) => r.json())
      .catch((err) => {
        console.error("[icon-picker] manifest fetch failed:", err);
        return [];
      });
    return iconsPromise;
  }

  function findHiddenInput(picker) {
    const id = picker.dataset.iconPickerId;
    if (id) {
      const byId = document.querySelector(
        `input[data-icon-value][data-icon-picker-id="${CSS.escape(id)}"]`,
      );
      if (byId) return byId;
    }
    // Fallback: same form. Matches the editor's original behaviour where
    // there's only one picker per form so the lookup can be loose.
    const form = picker.closest("form");
    if (form) return form.querySelector("[data-icon-value]");
    return null;
  }

  function bindOne(picker) {
    if (picker.dataset.iconPickerBound) return;
    picker.dataset.iconPickerBound = "1";

    const trigger = picker.querySelector("[data-icon-trigger]");
    const popover = picker.querySelector("[data-icon-popover]");
    const grid = picker.querySelector("[data-icon-grid]");
    const search = picker.querySelector("[data-icon-search]");
    const empty = picker.querySelector("[data-icon-empty]");
    const labelEl = picker.querySelector("[data-icon-label]");
    const current = picker.querySelector(".icon-picker-current");
    const hidden = findHiddenInput(picker);
    if (!trigger || !popover || !grid || !search || !hidden) return;

    let icons = null;

    function pick(name) {
      hidden.value = name || "";
      // 'input' bubbles, so the editor's form-watcher catches it as a
      // dirty signal without us having to know about it here.
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
      if (current) {
        current.innerHTML = name
          ? '<i class="ph ph-' + name + '" aria-hidden="true"></i>'
          : '<i class="ph ph-prohibit" aria-hidden="true"></i>';
      }
      if (labelEl) labelEl.textContent = name || "No icon";
      // Page-editor convenience: if there's a header icon following the
      // page-name input, mirror the picked icon there too. Harmless on
      // pages that don't have that header (the queryNodeList is empty).
      const headerIcon = document.querySelector("[data-editor-name-icon]");
      if (headerIcon) {
        headerIcon.innerHTML = name
          ? '<i class="ph ph-' + name + '" aria-hidden="true"></i>'
          : "";
      }
      grid.querySelectorAll(".icon-pick").forEach((b) =>
        b.classList.toggle("is-active", (b.dataset.icon || "") === (name || "")),
      );
    }

    function render(filter) {
      if (!icons) return;
      const q = (filter || "").trim().toLowerCase();
      const matched = q ? icons.filter((n) => n.indexOf(q) !== -1) : icons;
      const cap = 600;
      const slice = matched.slice(0, cap);
      const chosen = hidden.value || "";
      let html = "";
      // "No icon" tile is always first (only with empty query).
      if (!q) {
        html +=
          '<button type="button" class="icon-pick' +
          (chosen === "" ? " is-active" : "") +
          '" data-icon="" title="No icon" aria-label="No icon">' +
          '<i class="ph ph-prohibit" aria-hidden="true"></i></button>';
      }
      for (const name of slice) {
        html +=
          '<button type="button" class="icon-pick' +
          (chosen === name ? " is-active" : "") +
          '" data-icon="' +
          name +
          '" title="' +
          name +
          '" aria-label="' +
          name +
          '">' +
          '<i class="ph ph-' +
          name +
          '" aria-hidden="true"></i></button>';
      }
      grid.innerHTML = html;
      if (empty) empty.hidden = slice.length > 0 || !q;
    }

    function open() {
      popover.hidden = false;
      picker.classList.add("is-open");
      loadIcons().then((list) => {
        icons = list;
        render(search.value);
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

  function bindAll() {
    document.querySelectorAll("[data-icon-picker]").forEach(bindOne);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindAll);
  } else {
    bindAll();
  }
})();
