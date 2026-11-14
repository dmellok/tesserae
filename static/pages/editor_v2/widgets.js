// Dashboard editor v2: step 3, the list of cells. A filled row opens in
// place with that widget's settings (autosaved through update_cell); an
// empty row, or Replace, opens the catalog (catalog.js). Drag a row onto
// another to swap the two widgets (swap_cells).
(function () {
  "use strict";
  const EV2 = window.EV2;
  if (!EV2) return;
  const root = EV2.root;
  const list = root.querySelector("[data-ev2-cells]");
  if (!list) return;

  const rows = () => [...list.querySelectorAll(".ev2-cl[data-cell-id]")];
  const rowFor = (id) => list.querySelector(`.ev2-cl[data-cell-id="${CSS.escape(id)}"]`);
  let openId = null;

  function paintRow(li, isOpen) {
    li.classList.toggle("open", isOpen);
    const body = li.querySelector(".ev2-cl-b");
    if (body) body.hidden = !isOpen;
    li.querySelector("[data-ev2-row]")?.setAttribute("aria-expanded", isOpen ? "true" : "false");
    const rep = li.querySelector("[data-ev2-replace]");
    if (rep) rep.hidden = !isOpen;
    const grip = li.querySelector(".ev2-grip:not(.none)");
    if (grip) grip.setAttribute("draggable", isOpen ? "false" : "true");
  }

  function openRow(id, opts) {
    const o = opts || {};
    const li = rowFor(id);
    if (!li) return;
    if (!li.dataset.widget && !li.dataset.bar) {
      EV2.catalog && EV2.catalog.open(id);
      return;
    }
    rows().forEach((r) => paintRow(r, r === li));
    openId = id;
    EV2.ui.cell = id;
    wireTouch(li);
    hoverId = null;
    syncFocus();
    if (o.scroll) li.scrollIntoView({ block: o.block || "nearest", behavior: "smooth" });
  }

  function closeAll(opts) {
    const was = openId;
    rows().forEach((r) => paintRow(r, false));
    openId = null;
    EV2.ui.cell = null;
    if (was && !(opts && opts.silent)) syncFocus();
  }

  // The preview outlines the cell of the row under the pointer or the
  // keyboard, else the open row's (preview.js, ev2:cell-focus).
  let hoverId = null;
  let keyId = null;
  let shownId;
  function syncFocus() {
    const id = hoverId || keyId || openId || null;
    if (id === shownId) return;
    shownId = id;
    EV2.emit("ev2:cell-focus", { cellId: id });
  }
  function hover(id) {
    hoverId = id || null;
    syncFocus();
  }
  // The catalog outlines its cell too; keep track of what is showing.
  EV2.on("ev2:cell-focus", (d) => {
    shownId = (d && d.cellId) || null;
  });
  list.addEventListener("pointerover", (ev) => {
    if (ev.pointerType === "touch") return;
    hover(ev.target.closest(".ev2-cl[data-cell-id]")?.dataset.cellId);
  });
  list.addEventListener("pointerleave", () => hover(null));
  list.addEventListener("focusin", (ev) => {
    keyId = ev.target.closest(".ev2-cl[data-cell-id]")?.dataset.cellId || null;
    syncFocus();
  });
  list.addEventListener("focusout", (ev) => {
    if (ev.relatedTarget && list.contains(ev.relatedTarget)) return;
    keyId = null;
    syncFocus();
  });

  function ensureStep() {
    if (!EV2.steps) return;
    if (EV2.isNew) EV2.steps.setProgress(3);
    if (EV2.steps.current() !== "widgets") EV2.steps.open("widgets");
  }

  list.addEventListener("click", (ev) => {
    const li = ev.target.closest(".ev2-cl");
    if (!li) return;
    const id = li.dataset.cellId;
    if (ev.target.closest("[data-ev2-replace]")) {
      EV2.catalog && EV2.catalog.open(id, { replace: true });
      return;
    }
    if (ev.target.closest("[data-ev2-choose]")) {
      EV2.catalog && EV2.catalog.open(id);
      return;
    }
    if (ev.target.closest("[data-ev2-row], [data-ev2-go]")) {
      if (openId === id) closeAll();
      else openRow(id);
      return;
    }
    if (ev.target.closest("[data-ev2-done]")) {
      closeAll();
      li.querySelector("[data-ev2-row]")?.focus({ preventScroll: true });
      li.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return;
    }
    if (ev.target.closest("[data-ev2-empty]")) {
      const name = li.dataset.item || li.dataset.widget || "this";
      if (
        !window.confirm(
          `Empty cell ${li.dataset.n}? This removes its ${name} widget and the settings you made for it.`
        )
      )
        return;
      const form = li.querySelector("[data-ev2-cell-form]");
      form.querySelector("[data-ev2-plugin]").value = "";
      EV2.saveForm(form)
        .then(() => EV2.reload({ cell: null }))
        .catch(() => {});
      return;
    }
    const dith = ev.target.closest("[data-ev2-dither-set]");
    if (dith) {
      const box = dith.closest("[data-ev2-dither]");
      const v = dith.dataset.ev2DitherSet;
      box.querySelector("[data-ev2-dither-on]").checked = Boolean(v);
      if (v) box.querySelector("[data-ev2-dither-mode]").value = v;
      box.querySelectorAll("[data-ev2-dither-set]").forEach((b) => b.setAttribute("aria-pressed", b === dith ? "true" : "false"));
      EV2.saveForm(dith.form, 0).catch(() => {});
    }
  });

  // ------------------------------------------------------------ autosave
  function onEdit(ev) {
    const el = ev.target;
    const form = el.closest && el.closest("[data-ev2-cell-form]");
    if (!form || el.name === "plugin") return;
    // Controls with no name only drive a named one (padding pair, filters).
    // (the padding choice saves itself below, once the hidden controls follow it)
    if (!el.name) return;
    const delay = EV2.debounceDelay(el);
    if (ev.type === "input" && delay < 0) return; // text: save on change
    if (ev.type === "change" && delay > 0 && el.type === "range") return; // input already queued it
    EV2.saveForm(form, ev.type === "change" ? 0 : delay).catch(() => {});
  }
  list.addEventListener("input", onEdit);
  list.addEventListener("change", onEdit);

  // Zoom readout, daily-refresh reveal, padding choice.
  list.addEventListener("input", (ev) => {
    const z = ev.target.closest("[data-ev2-zoom]");
    if (z) {
      const out = z.parentElement.querySelector("[data-ev2-zoom-out]");
      if (out) out.textContent = Number(z.value).toFixed(2) + "×";
    }
    if (ev.target.matches('[data-ev2-pair^="pad-"]')) {
      const box = ev.target.closest("[data-ev2-pad]");
      box.querySelector("[data-ev2-pad-on]").checked = true;
      if (ev.target.type === "range") EV2.saveForm(ev.target.form, 450).catch(() => {});
    }
  });
  list.addEventListener("change", (ev) => {
    const t = ev.target;
    if (t.dataset.ev2Reveal) {
      const target = document.getElementById(t.dataset.ev2Reveal);
      if (target) target.hidden = !t.checked;
    }
    if (t.matches("[data-ev2-pad-choice]")) {
      const box = t.closest("[data-ev2-pad]");
      const on = box.querySelector("[data-ev2-pad-on]");
      const custom = box.querySelector("[data-ev2-pad-custom]");
      const v = t.value;
      on.checked = v !== "";
      custom.hidden = v !== "custom";
      if (v !== "" && v !== "custom") {
        box.querySelectorAll("[data-ev2-pair]").forEach((i) => (i.value = v));
      }
      if (v === "custom") custom.querySelector("input[type=range]")?.focus();
      // The preset value is "" for "use the dashboard padding": the choice
      // itself is the edit, so save after the hidden controls have followed it.
      EV2.saveForm(t.form || t.closest("form"), 0).catch(() => {});
    }
    if (t.matches("[data-ev2-ov]")) EV2.look && EV2.look.refreshOverrides();
  });

  // ------------------------------------------- "More" group summaries
  // Each group says how many of its settings differ from the default, or
  // "defaults". Amber (.ovw) only where that overrides the dashboard look.
  function readValue(fields) {
    const own = fields.filter((f) => f.type !== "hidden");
    const f = own[0] || fields[0];
    if (f.type === "checkbox" && own.length === 1) return f.checked;
    if (f.type === "checkbox") return own.filter((x) => x.checked).map((x) => x.value).sort();
    if (f.type === "radio") return (own.find((x) => x.checked) || {}).value || "";
    if (f.tagName === "SELECT" && f.multiple) return [...f.selectedOptions].map((o) => o.value).sort();
    return f.value;
  }
  function differs(fields, def) {
    const v = readValue(fields);
    if (typeof v === "boolean") return v !== (def === true || def === "true" || def === 1);
    if (Array.isArray(v)) {
      const d = Array.isArray(def) ? def : def === null || def === undefined || def === "" ? [] : [def];
      return JSON.stringify(v) !== JSON.stringify(d.map(String).sort());
    }
    if (typeof def === "number") {
      if (v === "") return false;
      // A range snaps its value to its step grid (1 can read 1.01): within
      // half a step of the default is the default.
      const f = fields.find((x) => x.type === "range");
      const slack = f ? (Number(f.step) || 1) / 2 + 1e-9 : 1e-9;
      return Math.abs(Number(v) - def) > slack;
    }
    return String(v) !== String(def === null || def === undefined ? "" : def);
  }
  function refreshSums(form) {
    if (!form) return;
    // A half-width select may hold a longer choice than it can show (a
    // theme or font override): it ends in an ellipsis, and its title
    // carries the whole name.
    form.querySelectorAll(".ev2-oi--short select").forEach((s) => {
      s.title = s.selectedOptions[0] ? s.selectedOptions[0].text : "";
    });
    form.querySelectorAll("details[data-ev2-adv]").forEach((d) => {
      const out = d.querySelector("[data-ev2-adv-sum]");
      if (!out) return;
      let n = 0;
      let ovw = false;
      const kind = d.dataset.ev2Adv;
      if (kind === "more") {
        let defs = {};
        try {
          defs = JSON.parse(d.dataset.ev2Defaults || "{}") || {};
        } catch (e) {
          defs = {};
        }
        Object.keys(defs).forEach((name) => {
          const fields = [...d.querySelectorAll(`[name="${CSS.escape(name)}"]`)];
          if (!fields.length || fields[0].closest("[data-show-if][hidden]")) return;
          if (differs(fields, defs[name])) n += 1;
        });
      } else if (kind === "look") {
        const own = [...d.querySelectorAll("[data-ev2-ov]")].filter((s) => s.value).length;
        const zoom = d.querySelector("[data-ev2-zoom]");
        n = own + (zoom && Math.abs(Number(zoom.value) - 1) > 1e-6 ? 1 : 0);
        ovw = own > 0;
      } else if (kind === "extras") {
        const on = (sel) => Boolean(d.querySelector(sel)?.checked);
        let touch = {};
        try {
          touch = JSON.parse(d.querySelector("[data-touch-input]")?.value || "{}") || {};
        } catch (e) {
          touch = {};
        }
        const pad = on("[data-ev2-pad-on]");
        n = [
          on('[name="update_on_change"]'),
          on('[name="update_schedule_enabled"]'),
          Object.values(touch).some(Boolean),
          pad,
          on("[data-ev2-dither-on]"),
        ].filter(Boolean).length;
        ovw = pad;
      }
      out.textContent = n ? `${n} changed` : "defaults";
      out.classList.toggle("ovw", ovw);
    });
  }
  ["input", "change", "click"].forEach((name) =>
    list.addEventListener(name, (ev) => {
      const form = ev.target.closest && ev.target.closest("[data-ev2-cell-form]");
      if (form) queueMicrotask(() => refreshSums(form));
    })
  );

  // ------------------------------------------------------- touch actions
  function wireTouch(li) {
    if (typeof window.TouchInteraction === "undefined") return;
    li.querySelectorAll("[data-touch-mount]").forEach((mount) => {
      if (mount.dataset.touchBound) return;
      mount.dataset.touchBound = "1";
      const input = mount.parentElement.querySelector("[data-touch-input]");
      let value = {};
      try {
        value = JSON.parse(mount.dataset.touchValue || "{}") || {};
      } catch (e) {
        value = {};
      }
      mount.appendChild(
        window.TouchInteraction.render({
          value,
          pagesUrl: mount.dataset.pagesUrl,
          haActionsUrl: mount.dataset.haUrl,
          defaultAxis: mount.dataset.defaultAxis === "x" ? "x" : "y",
          onChange: (v) => {
            if (!input) return;
            const out = {};
            if (v.on_tap) out.on_tap = v.on_tap;
            if (v.on_swipe) out.on_swipe = v.on_swipe;
            if (v.on_slide) out.on_slide = v.on_slide;
            input.value = JSON.stringify(out);
            refreshSums(input.form);
            EV2.saveForm(input.form, 300).catch(() => {});
          },
        })
      );
    });
  }

  // ------------------------------------------------------ drag to swap
  let dragFrom = null;
  list.addEventListener("dragstart", (ev) => {
    const grip = ev.target.closest && ev.target.closest(".ev2-grip");
    const li = grip && grip.closest(".ev2-cl[data-swap-url]");
    if (!li || li.classList.contains("open")) {
      ev.preventDefault();
      return;
    }
    dragFrom = li;
    li.classList.add("dragging");
    if (ev.dataTransfer) {
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", li.dataset.cellId);
      ev.dataTransfer.setDragImage(li, 24, 20);
    }
  });
  const clearDrop = () => list.querySelectorAll(".ev2-cl.drop").forEach((x) => x.classList.remove("drop"));
  list.addEventListener("dragover", (ev) => {
    const li = ev.target.closest && ev.target.closest(".ev2-cl[data-swap-url]");
    if (!dragFrom || !li || li === dragFrom) return;
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
    clearDrop();
    li.classList.add("drop");
  });
  list.addEventListener("drop", (ev) => {
    const li = ev.target.closest && ev.target.closest(".ev2-cl[data-swap-url]");
    const from = dragFrom;
    dragFrom = null;
    clearDrop();
    if (!from || !li || li === from) return;
    ev.preventDefault();
    EV2.flush()
      .then(() => EV2.post(from.dataset.swapUrl, { target: li.dataset.cellId }, { json: true }))
      .then(() => EV2.reload({ cell: null }))
      .catch(() => {});
  });
  // Keyboard: Alt+Up / Alt+Down on a row swaps it with its neighbour.
  const live = root.querySelector("[data-ev2-live]");
  function announce(msg) {
    if (!live) return;
    live.textContent = "";
    setTimeout(() => (live.textContent = msg), 60);
  }
  EV2.announce = announce;
  const rowName = (li) => (li.dataset.widget ? li.dataset.item || li.dataset.widget : "Empty cell");
  // Swap a row with its neighbour above or below (keyboard and touch).
  function moveRow(li, dir) {
    const swappable = rows().filter((r) => r.dataset.swapUrl);
    const other = swappable[swappable.indexOf(li) + (dir === "up" ? -1 : 1)];
    if (!other) {
      announce(`${rowName(li)} is already ${dir === "up" ? "first" : "last"}.`);
      return;
    }
    const msg = `Swapped cell ${li.dataset.n} and cell ${other.dataset.n}. ${rowName(li)} is now in cell ${other.dataset.n}.`;
    EV2.flush()
      .then(() => EV2.post(li.dataset.swapUrl, { target: other.dataset.cellId }, { json: true }))
      .then(() => EV2.reload({ cell: null, focusCell: other.dataset.cellId, announce: msg }))
      .catch(() => {});
  }
  list.addEventListener("keydown", (ev) => {
    if (!ev.altKey || (ev.key !== "ArrowUp" && ev.key !== "ArrowDown")) return;
    const btn = ev.target.closest && ev.target.closest("[data-ev2-row]");
    const li = btn && btn.closest(".ev2-cl[data-swap-url]");
    if (!li) return;
    ev.preventDefault();
    moveRow(li, ev.key === "ArrowUp" ? "up" : "down");
  });
  list.addEventListener("click", (ev) => {
    const mv = ev.target.closest("[data-ev2-move]");
    const li = mv && mv.closest(".ev2-cl[data-swap-url]");
    if (li) moveRow(li, mv.dataset.ev2Move);
  });

  list.addEventListener("dragend", () => {
    dragFrom = null;
    clearDrop();
    list.querySelectorAll(".ev2-cl.dragging").forEach((x) => x.classList.remove("dragging"));
  });

  // ------------------------------------------------- preview -> steps
  EV2.on("ev2:cell-select", (d) => {
    if (!d.cellId || !rowFor(d.cellId)) return;
    ensureStep();
    const li = rowFor(d.cellId);
    if (!li.dataset.widget && !li.dataset.bar) {
      EV2.catalog && EV2.catalog.open(d.cellId);
      return;
    }
    if (openId !== d.cellId) openRow(d.cellId);
    li.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });

  function openFirstEmpty() {
    const li = rows().find((r) => r.dataset.n && !r.dataset.widget);
    if (li) EV2.catalog && EV2.catalog.open(li.dataset.cellId);
  }

  EV2.widgets = {
    openRow: (id, opts) => {
      ensureStep();
      openRow(id, opts);
    },
    closeAll,
    openFirstEmpty,
    openId: () => openId,
    rowFor,
    rows,
    refreshSums,
  };

  // Back from a reload: reopen the row that was open.
  EV2.ready(() => {
    list.querySelectorAll("[data-ev2-cell-form]").forEach(refreshSums);
    // After a keyboard swap: the moved widget keeps focus, and the result is said.
    const moved = EV2.restored.focusCell;
    if (moved && rowFor(moved)) {
      rowFor(moved).querySelector("[data-ev2-row]")?.focus({ preventScroll: true });
      if (EV2.restored.announce) announce(EV2.restored.announce);
    }
    const id = EV2.restored.cell;
    if (id && rowFor(id) && EV2.steps && EV2.steps.current() === "widgets") {
      const li = rowFor(id);
      if (li.dataset.widget || li.dataset.bar) openRow(id);
    }
  });
})();
