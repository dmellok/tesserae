// Per-entity name + icon override list.
//
// Pairs with a [data-multiselect] in the same fieldset (used by the HA
// sensor + entities widgets). Reads which entities are ticked there
// and renders one row per ticked entity: a "display name" text input
// plus an inline icon picker (powered by static/icon-picker.js). The
// hidden textarea inside [data-entity-overrides] is the source of
// truth — JS keeps it in the legacy pipe-separated format
//
//     entity_id | name | icon
//
// so the server-side parser (_parse_overrides in ha_sensor/server.py
// + ha_entities/server.py) stays unchanged.
//
// State persists across tick changes: unticking an entity removes its
// row from the UI but the override stays in the textarea. Re-ticking
// restores the values. Stale entries are harmless because the server
// only applies overrides for entities currently in the wanted list.

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
      if (entry.name || entry.icon) out.set(parts[0], entry);
    }
    return out;
  }

  function serialiseOverrides(map) {
    const lines = [];
    map.forEach((entry, eid) => {
      const name = entry.name || "";
      const icon = entry.icon || "";
      if (!name && !icon) return;
      lines.push(`${eid} | ${name} | ${icon}`);
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
    if (!multiselect || !textarea || !list) return;

    const state = parseOverrides(textarea.value);

    function pickerId(eid) {
      // CSS.escape lets the picker's own querySelector survive entity ids
      // with dots in them. The id itself doesn't need to be CSS-safe; it
      // just needs to be unique within the page.
      return field.id + "-row-" + eid.replace(/[^a-z0-9_]/gi, "-") + "-picker";
    }

    function writeStorage() {
      const next = serialiseOverrides(state);
      if (next === textarea.value) return;
      textarea.value = next;
      // Bubbles so the editor's form-watcher catches it as a dirty signal
      // and schedules a preview re-render.
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function updateState(eid, patch) {
      const e = state.get(eid) || {};
      Object.assign(e, patch);
      if (!e.name && !e.icon) state.delete(eid);
      else state.set(eid, e);
      writeStorage();
    }

    function renderRow(eid, label) {
      const existing = state.get(eid) || {};
      const pid = pickerId(eid);
      const safeLabel = escapeAttr(label);
      const safeEid = escapeAttr(eid);
      const safeName = escapeAttr(existing.name || "");
      const safeIcon = escapeAttr(existing.icon || "");
      const iconMarkup = existing.icon
        ? `<i class="ph ph-${escapeAttr(existing.icon)}" aria-hidden="true"></i>`
        : `<i class="ph ph-prohibit" aria-hidden="true"></i>`;

      const div = document.createElement("div");
      div.className = "entity-override-row";
      div.dataset.entityId = eid;
      div.innerHTML = `
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
      `;

      const nameInput = div.querySelector(".entity-override-name");
      const iconHidden = div.querySelector("[data-icon-value]");
      nameInput.addEventListener("input", () => {
        updateState(eid, { name: nameInput.value.trim() });
      });
      iconHidden.addEventListener("input", () => {
        updateState(eid, { icon: iconHidden.value.trim() });
      });

      return div;
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

    function render() {
      const entities = tickedEntities();
      list.innerHTML = "";
      if (!entities.length) {
        if (emptyTpl) list.appendChild(emptyTpl.cloneNode(true));
        return;
      }
      entities.forEach(({ value, label }) => {
        list.appendChild(renderRow(value, label));
      });
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindAll);
  } else {
    bindAll();
  }
})();
