// Per-entity name + icon override list.
//
// Pairs with a [data-multiselect] in the same fieldset (used by the HA
// sensor + entities widgets). Reads which entities are ticked there
// and renders one row per ticked entity: a "display name" text input,
// an inline icon picker (powered by static/icon-picker.js), and a
// drag handle. The hidden textarea inside [data-entity-overrides] is
// the source of truth for the name/icon, JS keeps it in the legacy
// pipe-separated format
//
//     entity_id | name | icon | format
//
// so the server-side parser (_parse_overrides in ha_sensor/server.py
// + ha_entities/server.py) stays unchanged. The 4th field (a number
// format like ``0.0``) is only written when set, and the per-row
// input is only shown when the field opts in via
// ``data-overrides-formats`` (the HA sensor widget).
//
// Drag-to-reorder: rows are HTML5-draggable. On drop, the matching
// .multiselect-opt elements in the linked multiselect are reordered
// too, that's how the entities[] form submission ends up in the new
// order (FormData encodes inputs in DOM order, and the widget's
// fetch() iterates the wanted list in order).
//
// State persists across tick changes: unticking an entity removes
// its row from the UI but the override stays in the textarea.
// Re-ticking restores the values. Stale entries are harmless because
// the server only applies overrides for entities currently in the
// wanted list.

(function () {
  function parseOverrides(text) {
    const out = new Map();
    for (const raw of String(text || "").split("\n")) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const parts = line.split("|").map((p) => p.trim());
      if (!parts[0]) continue;
      const entry = {};
      if (parts.length > 1 && parts[1]) entry.name = parts[1];
      if (parts.length > 2 && parts[2]) entry.icon = parts[2];
      if (parts.length > 3 && parts[3]) entry.format = parts[3];
      if (entry.name || entry.icon || entry.format) out.set(parts[0], entry);
    }
    return out;
  }

  function serialiseOverrides(map) {
    const lines = [];
    map.forEach((entry, eid) => {
      const name = entry.name || "";
      const icon = entry.icon || "";
      const format = entry.format || "";
      if (!name && !icon && !format) return;
      // Only emit the 4th (format) field when it's set, so name/icon-only
      // rows keep the legacy three-field shape.
      lines.push(format ? `${eid} | ${name} | ${icon} | ${format}` : `${eid} | ${name} | ${icon}`);
    });
    return lines.join("\n");
  }

  function escapeAttr(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function bindOne(field) {
    if (field.dataset.entityOverridesBound) return;
    field.dataset.entityOverridesBound = "1";

    const fieldset = field.closest("fieldset, .form-group") || field.parentElement;
    if (!fieldset) return;
    const multiselect = fieldset.querySelector("[data-multiselect]");
    const textarea = field.querySelector("[data-overrides-storage]");
    const list = field.querySelector("[data-overrides-list]");
    const emptyTpl = field.querySelector("[data-overrides-empty]");
    const showFormats = field.dataset.overridesFormats === "1";
    if (!multiselect || !textarea || !list) return;

    const state = parseOverrides(textarea.value);
    let draggedEid = null;

    function pickerId(eid) {
      return field.id + "-row-" + eid.replace(/[^a-z0-9_]/gi, "-") + "-picker";
    }

    function writeStorage() {
      const next = serialiseOverrides(state);
      if (next === textarea.value) return;
      textarea.value = next;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function markFormDirty() {
      // The reorder doesn't change the textarea value, but it does
      // change the order entities submit in (FormData reads form
      // inputs in DOM order, and we've just reordered the
      // multiselect's .multiselect-opt). Bump the form watcher so the
      // preview re-renders with the new order.
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function updateState(eid, patch) {
      const e = state.get(eid) || {};
      Object.assign(e, patch);
      if (!e.name && !e.icon && !e.format) state.delete(eid);
      else state.set(eid, e);
      writeStorage();
    }

    function tickedEntities() {
      const out = [];
      multiselect.querySelectorAll(".multiselect-opt").forEach((opt) => {
        const cb = opt.querySelector("input[type=checkbox]");
        if (!cb || !cb.checked) return;
        const lbl = opt.querySelector(".multiselect-label");
        out.push({
          value: cb.value,
          label: lbl ? lbl.textContent.trim() : cb.value,
        });
      });
      return out;
    }

    function bindRowDrag(row, eid) {
      row.draggable = true;
      const dragHandle = row.querySelector(".entity-override-drag");
      if (dragHandle) {
        // Pointer-cursor while hovering the handle, the handle is
        // the only place the row is intended to be grabbed, but the
        // whole row is draggable so the keyboard-fallback flow works.
        dragHandle.addEventListener("mousedown", () => {
          row.style.cursor = "grabbing";
        });
        row.addEventListener("mouseup", () => {
          row.style.cursor = "";
        });
      }

      row.addEventListener("dragstart", (e) => {
        draggedEid = eid;
        row.classList.add("is-dragging");
        if (e.dataTransfer) {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", eid);
        }
      });
      row.addEventListener("dragend", () => {
        row.classList.remove("is-dragging");
        list.querySelectorAll(".is-drop-above, .is-drop-below").forEach((r) => {
          r.classList.remove("is-drop-above", "is-drop-below");
        });
        draggedEid = null;
      });
      row.addEventListener("dragover", (e) => {
        if (!draggedEid || row.dataset.entityId === draggedEid) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
        const rect = row.getBoundingClientRect();
        const above = e.clientY - rect.top < rect.height / 2;
        row.classList.toggle("is-drop-above", above);
        row.classList.toggle("is-drop-below", !above);
      });
      row.addEventListener("dragleave", () => {
        row.classList.remove("is-drop-above", "is-drop-below");
      });
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        row.classList.remove("is-drop-above", "is-drop-below");
        if (!draggedEid || row.dataset.entityId === draggedEid) return;
        const draggedRow = list.querySelector(
          `.entity-override-row[data-entity-id="${CSS.escape(draggedEid)}"]`,
        );
        if (!draggedRow) return;
        const rect = row.getBoundingClientRect();
        const above = e.clientY - rect.top < rect.height / 2;
        if (above) {
          list.insertBefore(draggedRow, row);
        } else {
          list.insertBefore(draggedRow, row.nextSibling);
        }
        syncMultiselectOrder();
        markFormDirty();
      });
    }

    function renderRow(eid, label) {
      const existing = state.get(eid) || {};
      const pid = pickerId(eid);
      const safeLabel = escapeAttr(label);
      const safeEid = escapeAttr(eid);
      const safeName = escapeAttr(existing.name || "");
      const safeIcon = escapeAttr(existing.icon || "");
      const safeFormat = escapeAttr(existing.format || "");
      const iconMarkup = existing.icon
        ? `<i class="ph ph-${escapeAttr(existing.icon)}" aria-hidden="true"></i>`
        : `<i class="ph ph-prohibit" aria-hidden="true"></i>`;
      const formatMarkup = showFormats
        ? `<input type="text" class="entity-override-format"
                 placeholder="auto" value="${safeFormat}"
                 title="Number format, e.g. 0.0 (blank = auto)"
                 aria-label="Number format for ${safeLabel}"
                 autocomplete="off" spellcheck="false">`
        : "";

      const div = document.createElement("div");
      div.className = showFormats ? "entity-override-row has-formats" : "entity-override-row";
      div.dataset.entityId = eid;
      div.innerHTML = `
        <button type="button" class="entity-override-drag"
                title="Drag to reorder" aria-label="Drag to reorder ${safeLabel}">
          <i class="ph ph-dots-six-vertical" aria-hidden="true"></i>
        </button>
        <div class="entity-override-id">
          <span class="entity-override-label" title="${safeLabel}">${safeLabel}</span>
          <span class="entity-override-eid" title="${safeEid}">${safeEid}</span>
        </div>
        <input type="text" class="entity-override-name"
               placeholder="${safeLabel}" value="${safeName}"
               aria-label="Display name for ${safeLabel}">
        <input type="hidden" data-icon-value data-icon-picker-id="${pid}"
               value="${safeIcon}">
        <div class="icon-picker icon-picker--inline" data-icon-picker data-icon-picker-id="${pid}">
          <button type="button" class="icon-picker-trigger" data-icon-trigger
                  title="Choose icon for ${safeLabel}" aria-label="Choose icon for ${safeLabel}">
            <span class="icon-picker-current">${iconMarkup}</span>
          </button>
          <div class="icon-picker-popover" data-icon-popover hidden>
            <div class="icon-picker-search">
              <input type="search" data-icon-search placeholder="Filter icons…"
                     autocomplete="off" spellcheck="false" aria-label="Filter icons">
            </div>
            <div class="icon-picker-grid" data-icon-grid></div>
            <p class="icon-picker-empty" data-icon-empty hidden>No icons match that search.</p>
          </div>
        </div>
        ${formatMarkup}
      `;

      const nameInput = div.querySelector(".entity-override-name");
      const iconHidden = div.querySelector("[data-icon-value]");
      nameInput.addEventListener("input", () => {
        updateState(eid, { name: nameInput.value.trim() });
      });
      iconHidden.addEventListener("input", () => {
        updateState(eid, { icon: iconHidden.value.trim() });
      });
      const formatInput = div.querySelector(".entity-override-format");
      if (formatInput) {
        formatInput.addEventListener("input", () => {
          updateState(eid, { format: formatInput.value.trim() });
        });
      }
      bindRowDrag(div, eid);

      return div;
    }

    function syncMultiselectOrder() {
      // Mirror the override list's new order onto the multiselect's
      // .multiselect-opt children. FormData reads the resulting
      // checkbox values in DOM order, so submitting the form sends
      // the entities in the user-defined order.
      const newOrder = Array.from(list.querySelectorAll(".entity-override-row")).map(
        (r) => r.dataset.entityId,
      );
      const msList = multiselect.querySelector("[data-ms-list]");
      if (!msList) return;
      // Walk in reverse and insert each at the top, that ends with
      // them in user order, ahead of any unticked options.
      newOrder
        .slice()
        .reverse()
        .forEach((eid) => {
          const cb = msList.querySelector(
            `input[type=checkbox][value="${CSS.escape(eid)}"]`,
          );
          const opt = cb ? cb.closest(".multiselect-opt") : null;
          if (opt) msList.insertBefore(opt, msList.firstChild);
        });
    }

    function render() {
      // Smart render: add rows for newly-ticked entities, remove rows
      // for newly-unticked ones, leave the rest in their current
      // (possibly user-dragged) order. A naive innerHTML="" rebuild
      // would clobber any drag-reorder the user has performed.
      const ticked = tickedEntities();
      const tickedIds = new Set(ticked.map((e) => e.value));
      const labelById = new Map(ticked.map((e) => [e.value, e.label]));

      // Drop the empty placeholder if it's the only child.
      list.querySelectorAll(".entity-overrides-empty").forEach((p) => p.remove());

      // Remove rows whose entity is no longer ticked.
      Array.from(list.querySelectorAll(".entity-override-row")).forEach((r) => {
        if (!tickedIds.has(r.dataset.entityId)) r.remove();
      });

      // Append rows for newly-ticked entities (in multiselect order).
      const present = new Set(
        Array.from(list.querySelectorAll(".entity-override-row")).map(
          (r) => r.dataset.entityId,
        ),
      );
      ticked.forEach(({ value, label }) => {
        if (!present.has(value)) {
          list.appendChild(renderRow(value, labelById.get(value) || label));
        }
      });

      if (!list.querySelector(".entity-override-row")) {
        if (emptyTpl) list.appendChild(emptyTpl.cloneNode(true));
      }

      if (typeof window.tesseraeIconPickerBindAll === "function") {
        window.tesseraeIconPickerBindAll();
      }
    }

    multiselect.addEventListener("change", render);
    render();
  }

  function bindAll() {
    document.querySelectorAll("[data-entity-overrides]").forEach(bindOne);
  }

  // Exposed so editors that inject the options form after page load (the
  // canvas config drawer, #130) can bind the newly-added overrides field.
  // bindOne is idempotent, so calling bindAll again is cheap.
  window.tesseraeEntityOverridesBindAll = bindAll;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindAll);
  } else {
    bindAll();
  }
})();
